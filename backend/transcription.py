import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional

from ai_clients import AIClientError, AnthropicClient, OpenRouterTranscriptionClient
from audio_extraction import AudioExtractionError, extract_and_chunk_audio
from history_store import HistoryStore
from settings_store import SettingsStore
from transcription_errors import humanize_transcription_error, is_retryable

logger = logging.getLogger("clippull")

# Bounds simultaneous spend/CPU (ffmpeg) pressure from full transcription
# jobs, and simultaneous in-flight requests to OpenRouter per job -- both
# start conservative since a user's actual rate-limit tier isn't knowable
# in advance.
MAX_CONCURRENT_TRANSCRIPTIONS = 2
MAX_CONCURRENT_CHUNK_TRANSCRIPTIONS = 3
CHUNK_RETRY_ATTEMPTS = 3
CHUNK_RETRY_BACKOFF_SECONDS = 2.0


def format_timestamp(total_seconds: float) -> str:
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def stitch_transcript(chunk_results: list[dict]) -> str:
    """Combines per-chunk Whisper verbose_json responses into one
    continuously-timestamped transcript. Each chunk's own segments are
    timestamped relative to that chunk's start at 0 -- offsetting by the
    cumulative duration of prior chunks (as Whisper itself reports it, not
    an estimate) makes the result read as one continuous timeline."""
    lines = []
    cumulative_offset = 0.0
    for result in chunk_results:
        segments = result.get("segments") or []
        for segment in segments:
            text = (segment.get("text") or "").strip()
            if not text:
                continue
            start = (segment.get("start") or 0.0) + cumulative_offset
            lines.append(f"[{format_timestamp(start)}] {text}")
        if not segments:
            text = (result.get("text") or "").strip()
            if text:
                lines.append(f"[{format_timestamp(cumulative_offset)}] {text}")
        cumulative_offset += result.get("duration") or 0.0
    return "\n".join(lines)


class TranscriptionOrchestrator:
    def __init__(
        self,
        history_store: HistoryStore,
        settings_store: SettingsStore,
        broadcast: Optional[Callable[[dict], None]] = None,
        openrouter_client_cls=OpenRouterTranscriptionClient,
        anthropic_client_cls=AnthropicClient,
        extract_fn: Callable[..., list] = extract_and_chunk_audio,
        max_concurrent_jobs: int = MAX_CONCURRENT_TRANSCRIPTIONS,
        max_concurrent_chunks: int = MAX_CONCURRENT_CHUNK_TRANSCRIPTIONS,
    ):
        self.history_store = history_store
        self.settings_store = settings_store
        self.broadcast = broadcast or (lambda message: None)
        self.openrouter_client_cls = openrouter_client_cls
        self.anthropic_client_cls = anthropic_client_cls
        self.extract_fn = extract_fn
        self.max_concurrent_chunks = max_concurrent_chunks
        self._job_semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self._active_tasks: dict[int, asyncio.Task] = {}

    def request_transcription(self, history_id: int) -> bool:
        """True if no transcription is already in flight for this History
        entry. Doesn't itself reserve a slot -- transcribe_entry registers
        itself as its very first line, before any await, so there's no
        window for a second call in between to slip through."""
        task = self._active_tasks.get(history_id)
        return task is None or task.done()

    def mark_running(self, history_id: int) -> Optional[dict]:
        """Broadcasts the "running" transition immediately when a
        transcription is requested, before the scheduled task actually
        starts -- same "broadcast the transition immediately" reasoning
        already applied to pause/resume, so the row doesn't sit looking
        unchanged for a beat after the click."""
        updated = self.history_store.update_transcript(history_id, status="running")
        if updated is not None:
            self._broadcast_update(history_id, "running", "Starting…", entry=updated)
        return updated

    def _broadcast_update(
        self,
        history_id: int,
        status: str,
        detail: Optional[str] = None,
        entry: Optional[dict] = None,
    ) -> None:
        # entry carries the full updated row on terminal states (done/error)
        # so the frontend can render the finished transcript/summary (or the
        # error message) straight from this one message -- no separate
        # fetch needed, the same way history_added already includes its row.
        message = {
            "type": "transcript_update",
            "history_id": history_id,
            "status": status,
            "detail": detail,
        }
        if entry is not None:
            message["entry"] = entry
        self.broadcast(message)

    def _fail(self, history_id: int, message: str) -> None:
        updated = self.history_store.update_transcript(history_id, status="error", error=message)
        self._broadcast_update(history_id, "error", message, entry=updated)

    async def transcribe_entry(self, history_id: int) -> None:
        self._active_tasks[history_id] = asyncio.current_task()
        loop = asyncio.get_running_loop()
        work_dir: Optional[str] = None
        try:
            async with self._job_semaphore:
                entry = self.history_store.get(history_id)
                if entry is None:
                    return  # row is gone (e.g. deleted from History before this slot freed up)
                if entry.get("status") != "done" or not entry.get("output_path"):
                    self._fail(
                        history_id,
                        "This download hasn't finished successfully, so there's nothing to transcribe.",
                    )
                    return
                if not Path(entry["output_path"]).exists():
                    self._fail(
                        history_id,
                        "The downloaded file is missing from disk, so it can't be transcribed.",
                    )
                    return

                settings = self.settings_store.get()
                openrouter_key = settings.get("openrouter_api_key")
                anthropic_key = settings.get("anthropic_api_key")
                if not openrouter_key:
                    self._fail(
                        history_id,
                        "Add an OpenRouter API key in Settings to enable transcription.",
                    )
                    return

                self._broadcast_update(history_id, "running", "Extracting audio…")
                work_dir = tempfile.mkdtemp(prefix="clippull_transcribe_")
                try:
                    chunks = await loop.run_in_executor(
                        None, self.extract_fn, entry["output_path"], work_dir
                    )
                except AudioExtractionError as exc:
                    self._fail(history_id, humanize_transcription_error(exc))
                    return

                try:
                    chunk_results = await self._transcribe_chunks(
                        history_id, chunks, openrouter_key, loop
                    )
                except AIClientError as exc:
                    self._fail(history_id, humanize_transcription_error(exc))
                    return

                transcript_text = stitch_transcript(chunk_results)

                summary_text = None
                if anthropic_key:
                    self._broadcast_update(history_id, "running", "Summarizing…")
                    try:
                        anthropic_client = self.anthropic_client_cls(anthropic_key)
                        summary_text = await loop.run_in_executor(
                            None, anthropic_client.summarize, transcript_text
                        )
                    except AIClientError:
                        # Partial success -- the transcript is still valuable
                        # on its own, so keep it rather than failing the
                        # whole job over a summarization hiccup.
                        logger.exception(
                            "Summarization failed for history entry %s", history_id
                        )
                        summary_text = None

                updated = self.history_store.update_transcript(
                    history_id, status="done", transcript=transcript_text, summary=summary_text
                )
                self._broadcast_update(history_id, "done", entry=updated)
        except Exception:
            logger.exception("Transcription failed for history entry %s", history_id)
            self._fail(history_id, "Something went wrong during transcription. Please try again.")
        finally:
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)
            self._active_tasks.pop(history_id, None)

    async def _transcribe_chunks(
        self, history_id: int, chunks: list[Path], openrouter_key: str, loop: asyncio.AbstractEventLoop
    ) -> list[dict]:
        openrouter_client = self.openrouter_client_cls(openrouter_key)
        chunk_semaphore = asyncio.Semaphore(self.max_concurrent_chunks)
        total = len(chunks)

        async def transcribe_one(index: int, chunk_path: Path) -> dict:
            async with chunk_semaphore:
                self._broadcast_update(
                    history_id, "running", f"Transcribing chunk {index + 1}/{total}…"
                )
                last_exc: Optional[AIClientError] = None
                for attempt in range(CHUNK_RETRY_ATTEMPTS):
                    try:
                        return await loop.run_in_executor(
                            None, openrouter_client.transcribe_chunk, chunk_path
                        )
                    except AIClientError as exc:
                        last_exc = exc
                        if not is_retryable(exc) or attempt == CHUNK_RETRY_ATTEMPTS - 1:
                            raise
                        await asyncio.sleep(CHUNK_RETRY_BACKOFF_SECONDS * (attempt + 1))
                raise last_exc  # pragma: no cover -- unreachable, loop above always returns or raises

        return list(await asyncio.gather(*[transcribe_one(i, c) for i, c in enumerate(chunks)]))
