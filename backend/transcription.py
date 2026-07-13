import asyncio
import functools
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional

from ai_clients import (
    PROVIDER_API_KEY_SETTINGS,
    PROVIDER_DISPLAY_NAMES,
    PROVIDER_INDEFINITE_ARTICLES,
    SUMMARIZATION_CLIENTS,
    TRANSCRIPTION_CLIENTS,
    AIClientError,
)
from audio_extraction import AudioExtractionError, extract_and_chunk_audio
from history_store import HistoryStore, redact_pro_summary_fields
from license_store import LicenseStore
from settings_store import SettingsStore
from transcription_errors import humanize_transcription_error, is_retryable
from usage_store import UsageStore

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

# Rough guardrail on how much transcript text we hand a summarization model in
# one call -- a very long transcript is both a token-cost and a context-window
# risk. This isn't exact token math (chars per token varies by model), just a
# conservative ceiling past which we truncate rather than send an unbounded
# amount. Roughly ~15k tokens of English at 4 chars/token.
MAX_SUMMARY_TRANSCRIPT_CHARS = 60000
# "Chat with your lesson" hands the model the same transcript as context, so it
# faces the exact same token-cost/context-window risk -- reuse the summary
# ceiling verbatim rather than picking a second arbitrary number.
MAX_CHAT_TRANSCRIPT_CHARS = MAX_SUMMARY_TRANSCRIPT_CHARS

# Transcription and summarization are independent, separately-triggered
# jobs (see TranscriptionOrchestrator) -- each gets its own WS message type
# so the frontend never has to guess which job a given update belongs to.
JOB_MESSAGE_TYPES = {"transcript": "transcript_update", "summary": "summary_update"}


def truncate_transcript(transcript_text: str, max_chars: int = MAX_SUMMARY_TRANSCRIPT_CHARS) -> str:
    """Caps how much transcript text we hand a summarization/chat model in one
    call (see MAX_SUMMARY_TRANSCRIPT_CHARS). Appends a marker so the model knows
    the text was cut rather than genuinely ending there. Single source of the
    truncation policy, shared by summarize_entry and the chat route."""
    if len(transcript_text) <= max_chars:
        return transcript_text
    return transcript_text[:max_chars] + "\n\n[Transcript truncated for length.]"


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


def _strip_code_fence(text: str) -> str:
    """Removes a wrapping markdown code fence if present. Some models wrap the
    JSON in ```json ... ``` despite being told not to -- a simple, defensive
    strip (not a full markdown parser) is enough to recover the raw object."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    # Drop the opening fence line (```json / ``` / ```JSON etc.)...
    lines = lines[1:]
    # ...and the closing fence line if there is one.
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_structured_notes(raw_text: str) -> dict:
    """Parses a summarization model's response into the normalized structured
    notes shape:
        {"tldr": str, "key_points": [{"seconds": float, "text": str}],
         "chapters": [{"seconds": float, "title": str}]}
    Malformed key_points/chapters entries are dropped individually rather than
    failing the whole response. On ANY failure to parse a valid object with a
    string `tldr`, falls back to treating the entire raw response as the TL;DR
    (with empty key_points/chapters) -- guaranteeing every stored summary value
    is valid JSON with this schema while degrading gracefully to "just show the
    text" when the model didn't cooperate."""
    fallback = {"tldr": raw_text.strip(), "key_points": [], "chapters": []}
    try:
        parsed = json.loads(_strip_code_fence(raw_text))
    except (ValueError, TypeError):
        return fallback
    if not isinstance(parsed, dict):
        return fallback
    tldr = parsed.get("tldr")
    if not isinstance(tldr, str):
        return fallback

    return {
        "tldr": tldr,
        "key_points": _normalize_timestamped_items(parsed.get("key_points"), "text"),
        "chapters": _normalize_timestamped_items(parsed.get("chapters"), "title"),
    }


def _normalize_timestamped_items(items: object, text_key: str) -> list[dict]:
    """Keeps only well-formed {seconds, <text_key>} entries from a list, coercing
    `seconds` to a float and requiring the text field to be a string. Anything
    malformed (missing keys, non-numeric seconds, wrong type) is dropped rather
    than failing the whole parse."""
    if not isinstance(items, list):
        return []
    normalized: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get(text_key)
        if not isinstance(value, str):
            continue
        try:
            seconds = float(item["seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        normalized.append({"seconds": seconds, text_key: value})
    return normalized


class TranscriptionOrchestrator:
    def __init__(
        self,
        history_store: HistoryStore,
        settings_store: SettingsStore,
        usage_store: UsageStore,
        license_store: LicenseStore,
        broadcast: Optional[Callable[[dict], None]] = None,
        transcription_client_classes: Optional[dict] = None,
        summarization_client_classes: Optional[dict] = None,
        extract_fn: Callable[..., list] = extract_and_chunk_audio,
        max_concurrent_jobs: int = MAX_CONCURRENT_TRANSCRIPTIONS,
        max_concurrent_chunks: int = MAX_CONCURRENT_CHUNK_TRANSCRIPTIONS,
        max_concurrent_summaries: int = MAX_CONCURRENT_SUMMARIES,
    ):
        self.history_store = history_store
        self.settings_store = settings_store
        self.usage_store = usage_store
        self.license_store = license_store
        self.broadcast = broadcast or (lambda message: None)
        self.transcription_client_classes = transcription_client_classes or TRANSCRIPTION_CLIENTS
        self.summarization_client_classes = summarization_client_classes or SUMMARIZATION_CLIENTS
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
            # A row can carry a summary from a previous job even on a
            # "transcript" broadcast (e.g. re-transcribing a video that was
            # already summarized before), so this redacts unconditionally
            # rather than only for job == "summary".
            message["entry"] = redact_pro_summary_fields(entry, self.license_store.is_pro())
        self.broadcast(message)

    def _record_usage(self, client, operation: str, history_id: Optional[int] = None) -> None:
        """Best-effort usage telemetry. Reads the client's `last_usage` side
        channel (set on its last successful call, keys lining up 1:1 with
        UsageStore.record's kwargs) and appends a row. `history_id` is None for
        course-scoped calls (course_chat/course_digest), which aren't tied to a
        single entry -- ai_usage.history_id is nullable, so that records fine. A
        failure here must NEVER interrupt the real transcription/summarization
        job -- it's wrapped and only logged, exactly like the other best-effort
        steps in this file."""
        usage = getattr(client, "last_usage", None)
        if not usage:
            return
        try:
            self.usage_store.record(operation=operation, history_id=history_id, **usage)
        except Exception:
            logger.exception("Failed to record AI usage for history entry %s", history_id)

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
                provider = settings.get("transcription_provider") or "gemini"
                client_cls = self.transcription_client_classes.get(provider)
                if client_cls is None:
                    self._fail_transcription(
                        history_id, f"Unknown transcription provider configured: {provider}."
                    )
                    return
                display_name = PROVIDER_DISPLAY_NAMES.get(provider, provider)
                article = PROVIDER_INDEFINITE_ARTICLES.get(provider, "a")
                api_key = settings.get(PROVIDER_API_KEY_SETTINGS[provider])
                if not api_key:
                    self._fail_transcription(
                        history_id,
                        f"Add {article} {display_name} API key in Settings to enable transcription.",
                    )
                    return

                self._broadcast(history_id, "transcript", "running", "Extracting audio…")
                work_dir = tempfile.mkdtemp(prefix="clippull_transcribe_")
                try:
                    extract_for_provider = functools.partial(self.extract_fn, provider=provider)
                    chunks = await loop.run_in_executor(
                        None, extract_for_provider, entry["output_path"], work_dir
                    )
                except AudioExtractionError as exc:
                    self._fail_transcription(history_id, humanize_transcription_error(exc))
                    return

                try:
                    chunk_results = await self._transcribe_chunks(
                        history_id, chunks, client_cls, api_key, loop
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
        self,
        history_id: int,
        chunks: list[Path],
        client_cls,
        api_key: str,
        loop: asyncio.AbstractEventLoop,
    ) -> list[dict]:
        # A fresh client instance is constructed per chunk (below) rather
        # than sharing one across all concurrently-running chunks --
        # transcribe_chunk() records its token/duration usage on the
        # instance's own `last_usage` attribute, and with up to
        # max_concurrent_chunks chunks in flight at once on a *shared*
        # client, one chunk's usage could be overwritten by another's
        # before _record_usage reads it back (a real race that silently
        # corrupted the usage dashboard's numbers). Construction is cheap
        # (just stores the api_key), so this costs nothing meaningful.
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
                client = client_cls(api_key)
                last_exc: Optional[AIClientError] = None
                for attempt in range(CHUNK_RETRY_ATTEMPTS):
                    try:
                        result = await loop.run_in_executor(
                            None, client.transcribe_chunk, chunk_path
                        )
                        self._record_usage(client, "transcribe_chunk", history_id)
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
                provider = settings.get("summarization_provider") or "anthropic"
                client_cls = self.summarization_client_classes.get(provider)
                if client_cls is None:
                    self._fail_summarization(
                        history_id, f"Unknown summarization provider configured: {provider}."
                    )
                    return
                display_name = PROVIDER_DISPLAY_NAMES.get(provider, provider)
                article = PROVIDER_INDEFINITE_ARTICLES.get(provider, "a")
                api_key = settings.get(PROVIDER_API_KEY_SETTINGS[provider])
                if not api_key:
                    self._fail_summarization(
                        history_id,
                        f"Add {article} {display_name} API key in Settings to enable summaries.",
                    )
                    return

                self._broadcast(history_id, "summary", "running", "Summarizing…")
                loop = asyncio.get_running_loop()
                transcript_text = truncate_transcript(entry["transcript"])
                try:
                    client = client_cls(api_key)
                    summary_text = await loop.run_in_executor(
                        None, client.summarize, transcript_text
                    )
                    self._record_usage(client, "summarize", history_id)
                except AIClientError as exc:
                    self._fail_summarization(history_id, humanize_transcription_error(exc))
                    return

                # The `summary` column stays plain TEXT; we just store a JSON
                # string of the normalized structured-notes shape inside it, so
                # no DB migration is needed. Pre-existing rows keep their old
                # plain-prose summaries -- readers handle both.
                structured = parse_structured_notes(summary_text)
                updated = self.history_store.update_summary(
                    history_id, status="done", summary=json.dumps(structured)
                )
                self._broadcast(history_id, "summary", "done", percent=100, entry=updated)
        except Exception:
            logger.exception("Summarization failed for history entry %s", history_id)
            self._fail_summarization(
                history_id, "Something went wrong during summarization. Please try again."
            )
        finally:
            self._active_summarization_tasks.pop(history_id, None)

    # -- Auto-process / batch chaining ------------------------------------

    async def transcribe_and_maybe_summarize(
        self, history_id: int, summarize: bool = False
    ) -> None:
        """Transcribes an entry and, only if `summarize` is set AND the
        transcription actually succeeded, chains a summarization right after.
        This is the shared task body behind both the auto-transcribe-on-download
        trigger (main.py) and the batch-process action (transcription_routes) --
        neither reimplements the request/mark/summarize dance, they just call
        this. The chained summary reuses request_summarization/
        mark_summarization_running exactly like the manual summarize route, so a
        summary already in flight is never double-started. transcribe_entry and
        summarize_entry each own their full try/except + row-failure handling,
        so a failure in either simply leaves that entry marked 'error' and (for
        a failed transcription) skips the summary rather than raising."""
        await self.transcribe_entry(history_id)
        if not summarize:
            return
        entry = self.history_store.get(history_id)
        if entry is None or entry.get("transcript_status") != "done":
            return  # transcription failed (or the row is gone) -- nothing to summarize
        if not self.request_summarization(history_id):
            return
        self.mark_summarization_running(history_id)
        await self.summarize_entry(history_id)
