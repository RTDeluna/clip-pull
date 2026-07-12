import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional

from ai_clients import AIClientError, AnthropicClient, GeminiTranscriptionClient
from audio_extraction import AudioExtractionError, extract_and_chunk_audio
from history_store import HistoryStore
from settings_store import SettingsStore
from transcription_errors import humanize_transcription_error, is_retryable

logger = logging.getLogger("clippull")

# Bounds simultaneous spend/CPU (ffmpeg) pressure from full transcription
# jobs, and simultaneous in-flight requests to Gemini per job -- both
# start conservative since a user's actual rate-limit tier isn't knowable
# in advance. Summarization is a single lightweight API call (no ffmpeg,
# no chunking), so it gets its own more generous concurrency limit rather
# than competing with transcription jobs for the same slots.
MAX_CONCURRENT_TRANSCRIPTIONS = 2
MAX_CONCURRENT_CHUNK_TRANSCRIPTIONS = 3
MAX_CONCURRENT_SUMMARIES = 4
CHUNK_RETRY_ATTEMPTS = 3
CHUNK_RETRY_BACKOFF_SECONDS = 2.0

# Transcription and summarization are independent, separately-triggered
# jobs (see TranscriptionOrchestrator) -- each gets its own WS message type
# so the frontend never has to guess which job a given update belongs to.
JOB_MESSAGE_TYPES = {"transcript": "transcript_update", "summary": "summary_update"}


def format_timestamp(total_seconds: float) -> str:
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def stitch_transcript(chunk_results: list[dict]) -> str:
    """Combines per-chunk transcription results (each a {"duration": float,
    "segments": [{"start": float, "text": str}]} dict -- the common shape
    every transcription client reshapes its provider's own response into)
    into one continuously-timestamped transcript. Each chunk's own segments
    are timestamped relative to that chunk's start at 0 -- offsetting by
    the cumulative duration of prior chunks (as reported by the chunk's own
    result, not re-derived) makes the result read as one continuous
    timeline."""
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
        gemini_client_cls=GeminiTranscriptionClient,
        anthropic_client_cls=AnthropicClient,
        extract_fn: Callable[..., list] = extract_and_chunk_audio,
        max_concurrent_jobs: int = MAX_CONCURRENT_TRANSCRIPTIONS,
        max_concurrent_chunks: int = MAX_CONCURRENT_CHUNK_TRANSCRIPTIONS,
        max_concurrent_summaries: int = MAX_CONCURRENT_SUMMARIES,
    ):
        self.history_store = history_store
        self.settings_store = settings_store
        self.broadcast = broadcast or (lambda message: None)
        self.gemini_client_cls = gemini_client_cls
        self.anthropic_client_cls = anthropic_client_cls
        self.extract_fn = extract_fn
        self.max_concurrent_chunks = max_concurrent_chunks
        self._transcription_semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self._summary_semaphore = asyncio.Semaphore(max_concurrent_summaries)
        self._active_transcription_tasks: dict[int, asyncio.Task] = {}
        self._active_summarization_tasks: dict[int, asyncio.Task] = {}

    # -- Shared helpers -----------------------------------------------

    def _broadcast(
        self,
        history_id: int,
        job: str,
        status: str,
        detail: Optional[str] = None,
        *,
        percent: Optional[float] = None,
        entry: Optional[dict] = None,
    ) -> None:
        # entry carries the full updated row on terminal states (done/error)
        # so the frontend can render the finished transcript/summary (or the
        # error message) straight from this one message -- no separate
        # fetch needed, the same way history_added already includes its row.
        message = {
            "type": JOB_MESSAGE_TYPES[job],
            "history_id": history_id,
            "status": status,
            "detail": detail,
        }
        if percent is not None:
            message["percent"] = percent
        if entry is not None:
            message["entry"] = entry
        self.broadcast(message)

    # -- Transcription --------------------------------------------------

    def request_transcription(self, history_id: int) -> bool:
        """True if no transcription is already in flight for this History
        entry. Doesn't itself reserve a slot -- transcribe_entry registers
        itself as its very first line, before any await, so there's no
        window for a second call in between to slip through."""
        task = self._active_transcription_tasks.get(history_id)
        return task is None or task.done()

    def mark_transcription_running(self, history_id: int) -> Optional[dict]:
        """Broadcasts the "running" transition immediately when transcription
        is requested, before the scheduled task actually starts -- same
        "broadcast the transition immediately" reasoning already applied to
        pause/resume, so the row doesn't sit looking unchanged for a beat
        after the click."""
        updated = self.history_store.update_transcript(history_id, status="running")
        if updated is not None:
            self._broadcast(history_id, "transcript", "running", "Starting…", entry=updated)
        return updated

    def _fail_transcription(self, history_id: int, message: str) -> None:
        updated = self.history_store.update_transcript(history_id, status="error", error=message)
        self._broadcast(history_id, "transcript", "error", message, entry=updated)

    async def transcribe_entry(self, history_id: int) -> None:
        self._active_transcription_tasks[history_id] = asyncio.current_task()
        loop = asyncio.get_running_loop()
        work_dir: Optional[str] = None
        try:
            async with self._transcription_semaphore:
                entry = self.history_store.get(history_id)
                if entry is None:
                    return  # row is gone (e.g. deleted from History before this slot freed up)
                if entry.get("status") != "done" or not entry.get("output_path"):
                    self._fail_transcription(
                        history_id,
                        "This download hasn't finished successfully, so there's nothing to transcribe.",
                    )
                    return
                if not Path(entry["output_path"]).exists():
                    self._fail_transcription(
                        history_id,
                        "The downloaded file is missing from disk, so it can't be transcribed.",
                    )
                    return

                settings = self.settings_store.get()
                gemini_key = settings.get("gemini_api_key")
                if not gemini_key:
                    self._fail_transcription(
                        history_id,
                        "Add a Gemini API key in Settings to enable transcription.",
                    )
                    return

                self._broadcast(history_id, "transcript", "running", "Extracting audio…")
                work_dir = tempfile.mkdtemp(prefix="clippull_transcribe_")
                try:
                    chunks = await loop.run_in_executor(
                        None, self.extract_fn, entry["output_path"], work_dir
                    )
                except AudioExtractionError as exc:
                    self._fail_transcription(history_id, humanize_transcription_error(exc))
                    return

                try:
                    chunk_results = await self._transcribe_chunks(
                        history_id, chunks, gemini_key, loop
                    )
                except AIClientError as exc:
                    self._fail_transcription(history_id, humanize_transcription_error(exc))
                    return

                transcript_text = stitch_transcript(chunk_results)
                updated = self.history_store.update_transcript(
                    history_id, status="done", transcript=transcript_text
                )
                self._broadcast(history_id, "transcript", "done", percent=100, entry=updated)
        except Exception:
            logger.exception("Transcription failed for history entry %s", history_id)
            self._fail_transcription(
                history_id, "Something went wrong during transcription. Please try again."
            )
        finally:
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)
            self._active_transcription_tasks.pop(history_id, None)

    async def _transcribe_chunks(
        self, history_id: int, chunks: list[Path], gemini_key: str, loop: asyncio.AbstractEventLoop
    ) -> list[dict]:
        gemini_client = self.gemini_client_cls(gemini_key)
        chunk_semaphore = asyncio.Semaphore(self.max_concurrent_chunks)
        total = len(chunks)
        completed = 0

        async def transcribe_one(index: int, chunk_path: Path) -> dict:
            nonlocal completed
            async with chunk_semaphore:
                self._broadcast(
                    history_id, "transcript", "running",
                    f"Transcribing chunk {index + 1}/{total}…",
                    percent=round(completed / total * 100),
                )
                last_exc: Optional[AIClientError] = None
                for attempt in range(CHUNK_RETRY_ATTEMPTS):
                    try:
                        result = await loop.run_in_executor(
                            None, gemini_client.transcribe_chunk, chunk_path
                        )
                        completed += 1
                        self._broadcast(
                            history_id, "transcript", "running",
                            f"Transcribed chunk {index + 1}/{total}",
                            percent=round(completed / total * 100),
                        )
                        return result
                    except AIClientError as exc:
                        last_exc = exc
                        if not is_retryable(exc) or attempt == CHUNK_RETRY_ATTEMPTS - 1:
                            raise
                        await asyncio.sleep(CHUNK_RETRY_BACKOFF_SECONDS * (attempt + 1))
                raise last_exc  # pragma: no cover -- unreachable, loop above always returns or raises

        return list(await asyncio.gather(*[transcribe_one(i, c) for i, c in enumerate(chunks)]))

    # -- Summarization ----------------------------------------------------

    def request_summarization(self, history_id: int) -> bool:
        """True if no summarization is already in flight for this History
        entry. Same registration pattern as request_transcription."""
        task = self._active_summarization_tasks.get(history_id)
        return task is None or task.done()

    def mark_summarization_running(self, history_id: int) -> Optional[dict]:
        updated = self.history_store.update_summary(history_id, status="running")
        if updated is not None:
            self._broadcast(history_id, "summary", "running", "Starting…", entry=updated)
        return updated

    def _fail_summarization(self, history_id: int, message: str) -> None:
        updated = self.history_store.update_summary(history_id, status="error", error=message)
        self._broadcast(history_id, "summary", "error", message, entry=updated)

    async def summarize_entry(self, history_id: int) -> None:
        """Summarizes an existing transcript. A separate, optional,
        user-triggered job that runs after transcription is already done --
        never bundled into transcribe_entry, so a user without an Anthropic
        key (or who simply doesn't want a summary) still gets a usable
        transcript with no extra cost or wait."""
        self._active_summarization_tasks[history_id] = asyncio.current_task()
        try:
            async with self._summary_semaphore:
                entry = self.history_store.get(history_id)
                if entry is None:
                    return
                if entry.get("transcript_status") != "done" or not entry.get("transcript"):
                    self._fail_summarization(
                        history_id,
                        "This video hasn't been transcribed yet — transcribe it first.",
                    )
                    return

                settings = self.settings_store.get()
                anthropic_key = settings.get("anthropic_api_key")
                if not anthropic_key:
                    self._fail_summarization(
                        history_id,
                        "Add an Anthropic API key in Settings to enable summaries.",
                    )
                    return

                self._broadcast(history_id, "summary", "running", "Summarizing…")
                loop = asyncio.get_running_loop()
                try:
                    anthropic_client = self.anthropic_client_cls(anthropic_key)
                    summary_text = await loop.run_in_executor(
                        None, anthropic_client.summarize, entry["transcript"]
                    )
                except AIClientError as exc:
                    self._fail_summarization(history_id, humanize_transcription_error(exc))
                    return

                updated = self.history_store.update_summary(
                    history_id, status="done", summary=summary_text
                )
                self._broadcast(history_id, "summary", "done", percent=100, entry=updated)
        except Exception:
            logger.exception("Summarization failed for history entry %s", history_id)
            self._fail_summarization(
                history_id, "Something went wrong during summarization. Please try again."
            )
        finally:
            self._active_summarization_tasks.pop(history_id, None)
