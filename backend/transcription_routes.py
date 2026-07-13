import asyncio
import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ai_clients import (
    PROVIDER_API_KEY_SETTINGS,
    PROVIDER_DISPLAY_NAMES,
    PROVIDER_INDEFINITE_ARTICLES,
    AIClientError,
)
from background_tasks import track_task
from courses import (
    COURSE_OVERVIEW_INSTRUCTION,
    assemble_course_context,
    build_course_chat_question,
    entries_for_course,
    list_courses,
    write_study_guide,
)
from export_notes import VALID_EXPORT_FORMATS, write_exports
from history_store import HistoryStore
from license_store import LicenseStore
from settings_store import SettingsStore
from transcription import (
    MAX_CHAT_TRANSCRIPT_CHARS,
    TranscriptionOrchestrator,
    truncate_transcript,
)
from transcription_errors import humanize_transcription_error

DB_BUSY_MESSAGE = "The app's local database is busy — try again in a moment."
EXPORT_PRO_MESSAGE = "Exporting notes is a CLIP.PULL Pro feature. Upgrade to unlock it."
CHAT_PRO_MESSAGE = "Chatting with your lessons is a CLIP.PULL Pro feature. Upgrade to unlock it."
BATCH_PRO_MESSAGE = "Batch processing is a CLIP.PULL Pro feature. Upgrade to unlock it."
COURSE_PRO_MESSAGE = "Course Workspace is a CLIP.PULL Pro feature. Upgrade to unlock it."
# A course is a folder shared by 2+ finished downloads; 1 video is just the
# existing single-video experience.
COURSE_NOT_A_COURSE_MESSAGE = (
    "A course needs at least 2 downloaded lessons in the same folder."
)
# Zero ready lessons -> nudge toward the existing folder-scoped Batch AI action.
COURSE_NO_READY_MESSAGE = (
    "None of this course's lessons have Lesson Notes yet — batch-summarize the "
    "folder first, then try again."
)
# Server-side ceilings so a single request can't hand us an unbounded payload
# (chat) or flood the transcription queue (batch).
MAX_CHAT_HISTORY_TURNS = 20
BATCH_ELIGIBLE_LIMIT = 50


class ExportRequest(BaseModel):
    formats: list[str]


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    history: list[ChatTurn] = []


class BatchProcessRequest(BaseModel):
    # None means "all eligible entries" (finished downloads without a usable
    # transcript yet); an explicit list targets exactly those ids.
    entry_ids: Optional[list[int]] = None
    summarize: bool = False


class CourseChatRequest(BaseModel):
    folder: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    # Reuses the single-video chat's exact {role, content} turn shape/validation.
    history: list[ChatTurn] = []
    # "chat" (conversational answer) or "search" (compact list of matching
    # lessons); anything else is a 400 (validated in the route, not the schema,
    # so it's a clean 400 rather than a 422).
    mode: str = "chat"


class CourseDigestRequest(BaseModel):
    folder: str = Field(..., min_length=1)


def build_transcription_router(
    history_store: HistoryStore,
    transcription_orchestrator: TranscriptionOrchestrator,
    license_store: LicenseStore,
    settings_store: SettingsStore,
) -> APIRouter:
    router = APIRouter()

    @router.post("/history/{entry_id}/transcribe", status_code=202)
    async def start_transcription(entry_id: int) -> dict:
        try:
            entry = history_store.get(entry_id)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if entry is None:
            raise HTTPException(status_code=404, detail="History entry not found")
        if entry["status"] != "done" or not entry["output_path"]:
            raise HTTPException(
                status_code=400,
                detail="This download hasn't finished successfully, so there's nothing to transcribe.",
            )
        if not transcription_orchestrator.request_transcription(entry_id):
            raise HTTPException(status_code=400, detail="This entry is already being transcribed.")

        try:
            updated = transcription_orchestrator.mark_transcription_running(entry_id)
        except sqlite3.OperationalError:
            # Give back the slot request_transcription() just reserved --
            # otherwise this entry is stuck "already being transcribed"
            # forever, since no task is ever created to release it.
            transcription_orchestrator.release_transcription(entry_id)
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)

        def _on_transcription_death(_exc: BaseException) -> None:
            # If the fire-and-forget transcription task dies before its own
            # try/except can mark the row failed, the UI would otherwise sit
            # on "running" until the next app restart's stuck-job reset.
            # Reuse _fail_transcription (not a bare broadcast) so the DB row
            # actually flips to "error" AND the broadcast includes the
            # updated `entry` -- the frontend's transcript_update handler
            # only re-renders a row when `entry` is present, matching every
            # other error path's contract.
            transcription_orchestrator._fail_transcription(
                entry_id, "Transcription stopped unexpectedly. Please try again."
            )

        track_task(
            asyncio.create_task(transcription_orchestrator.transcribe_entry(entry_id)),
            on_failure=_on_transcription_death,
        )
        return {"entry": updated}

    @router.delete("/history/{entry_id}/transcript")
    def clear_transcript(entry_id: int) -> dict:
        try:
            updated = history_store.update_transcript(entry_id, status="none")
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if updated is None:
            raise HTTPException(status_code=404, detail="History entry not found")
        return {"entry": updated}

    @router.post("/history/{entry_id}/summarize", status_code=202)
    async def start_summarization(entry_id: int) -> dict:
        try:
            entry = history_store.get(entry_id)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if entry is None:
            raise HTTPException(status_code=404, detail="History entry not found")
        if entry["transcript_status"] != "done" or not entry["transcript"]:
            raise HTTPException(
                status_code=400,
                detail="This video hasn't been transcribed yet — transcribe it first.",
            )
        if not transcription_orchestrator.request_summarization(entry_id):
            raise HTTPException(status_code=400, detail="This entry is already being summarized.")

        try:
            updated = transcription_orchestrator.mark_summarization_running(entry_id)
        except sqlite3.OperationalError:
            # Same reasoning as start_transcription above -- release the
            # reservation before this route errors out, or it never clears.
            transcription_orchestrator.release_summarization(entry_id)
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)

        def _on_summarization_death(_exc: BaseException) -> None:
            # Same as transcription above: reuse _fail_summarization so the
            # DB row flips to "error" and the broadcast carries `entry`,
            # instead of a bare message the frontend would silently ignore.
            transcription_orchestrator._fail_summarization(
                entry_id, "Summarizing stopped unexpectedly. Please try again."
            )

        track_task(
            asyncio.create_task(transcription_orchestrator.summarize_entry(entry_id)),
            on_failure=_on_summarization_death,
        )
        return {"entry": updated}

    @router.delete("/history/{entry_id}/summary")
    def clear_summary(entry_id: int) -> dict:
        try:
            updated = history_store.update_summary(entry_id, status="none")
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if updated is None:
            raise HTTPException(status_code=404, detail="History entry not found")
        return {"entry": updated}

    @router.post("/history/{entry_id}/export")
    def export_notes_route(entry_id: int, request: ExportRequest) -> dict:
        unknown = [fmt for fmt in request.formats if fmt not in VALID_EXPORT_FORMATS]
        if unknown or not request.formats:
            raise HTTPException(
                status_code=400,
                detail=f"formats must be a non-empty subset of {list(VALID_EXPORT_FORMATS)}.",
            )
        try:
            entry = history_store.get(entry_id)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if entry is None:
            raise HTTPException(status_code=404, detail="History entry not found")
        # Exporting is Pro-gated. is_pro() is the cheap cached check -- no network.
        if not license_store.is_pro():
            raise HTTPException(status_code=402, detail=EXPORT_PRO_MESSAGE)
        if entry["transcript_status"] != "done" or not entry["transcript"]:
            raise HTTPException(
                status_code=400,
                detail="This video hasn't been transcribed yet — transcribe it first.",
            )
        try:
            written_paths = write_exports(entry, request.formats)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"paths": [str(p) for p in written_paths]}

    @router.post("/history/{entry_id}/chat")
    async def chat_with_lesson(entry_id: int, request: ChatRequest) -> dict:
        # A single chat turn is fast and the user is actively waiting for a
        # reply, so unlike transcribe/summarize this runs inline (async def +
        # run_in_executor) and returns the answer directly -- no background
        # task, no WS-progress machinery.
        try:
            entry = history_store.get(entry_id)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if entry is None:
            raise HTTPException(status_code=404, detail="History entry not found")
        # Pro-gate before revealing anything about the entry's contents.
        if not license_store.is_pro():
            raise HTTPException(status_code=402, detail=CHAT_PRO_MESSAGE)
        if entry["transcript_status"] != "done" or not entry["transcript"]:
            raise HTTPException(
                status_code=400,
                detail="This video hasn't been transcribed yet — transcribe it first.",
            )

        settings = settings_store.get()
        provider = settings.get("summarization_provider") or "anthropic"
        client_cls = transcription_orchestrator.summarization_client_classes.get(provider)
        if client_cls is None:
            raise HTTPException(
                status_code=400, detail=f"Unknown summarization provider configured: {provider}."
            )
        api_key = settings.get(PROVIDER_API_KEY_SETTINGS[provider])
        if not api_key:
            display_name = PROVIDER_DISPLAY_NAMES.get(provider, provider)
            article = PROVIDER_INDEFINITE_ARTICLES.get(provider, "a")
            raise HTTPException(
                status_code=400,
                detail=f"Add {article} {display_name} API key in Settings to chat with your lessons.",
            )

        transcript_text = truncate_transcript(entry["transcript"], MAX_CHAT_TRANSCRIPT_CHARS)
        history = [turn.model_dump() for turn in request.history[-MAX_CHAT_HISTORY_TURNS:]]
        loop = asyncio.get_running_loop()
        client = client_cls(api_key)
        try:
            answer = await loop.run_in_executor(
                None, client.chat, transcript_text, request.question, history
            )
        except AIClientError as exc:
            # The AI provider call failed reaching us synchronously (unlike the
            # background transcribe/summarize jobs, which store the error on the
            # row). 502 signals an upstream/provider failure rather than a
            # client mistake; humanize_transcription_error reuses the exact same
            # friendly wording those background jobs surface.
            raise HTTPException(status_code=502, detail=humanize_transcription_error(exc))
        transcription_orchestrator._record_usage(client, "chat", entry_id)
        return {"answer": answer}

    @router.post("/history/batch-process")
    async def batch_process(request: BatchProcessRequest) -> dict:
        # Pro-gate FIRST, before any DB read, so a non-Pro caller learns nothing
        # about their history contents from this route.
        if not license_store.is_pro():
            raise HTTPException(status_code=402, detail=BATCH_PRO_MESSAGE)

        if request.entry_ids is None:
            try:
                eligible = history_store.find_transcribable(limit=BATCH_ELIGIBLE_LIMIT)
            except sqlite3.OperationalError:
                raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
            entry_ids = [row["id"] for row in eligible]
        else:
            # request_transcription()'s double-start guard only registers a
            # task once transcribe_entry actually starts running -- within
            # this synchronous loop that hasn't happened yet, so a duplicate
            # id in the caller's own list would slip past the guard and
            # start the same entry transcribing twice. De-duplicate up front
            # (order-preserving) rather than relying on a guard that can't
            # see same-request duplicates.
            entry_ids = list(dict.fromkeys(request.entry_ids))

        started: list[int] = []
        skipped: list[dict] = []
        for entry_id in entry_ids:
            try:
                entry = history_store.get(entry_id)
            except sqlite3.OperationalError:
                raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
            if entry is None:
                skipped.append({"id": entry_id, "reason": "not found"})
                continue
            if entry["status"] != "done" or not entry["output_path"]:
                skipped.append({"id": entry_id, "reason": "download didn't finish"})
                continue
            if not transcription_orchestrator.request_transcription(entry_id):
                skipped.append({"id": entry_id, "reason": "already being transcribed"})
                continue
            try:
                transcription_orchestrator.mark_transcription_running(entry_id)
            except sqlite3.OperationalError:
                transcription_orchestrator.release_transcription(entry_id)
                raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
            track_task(
                asyncio.create_task(
                    transcription_orchestrator.transcribe_and_maybe_summarize(
                        entry_id, request.summarize
                    )
                )
            )
            started.append(entry_id)
        return {"started": started, "skipped": skipped}

    # -- Course Workspace -------------------------------------------------

    def _resolve_summarization_client(action_phrase: str):
        """Resolves the configured summarization provider's client class + API
        key, exactly as the single-video chat/summarize paths do (same provider
        default, same "Add a/an {Provider} API key…" wording). Raises the same
        400s on an unknown provider or a missing key. `action_phrase` tails the
        missing-key message (e.g. "chat about this course")."""
        settings = settings_store.get()
        provider = settings.get("summarization_provider") or "anthropic"
        client_cls = transcription_orchestrator.summarization_client_classes.get(provider)
        if client_cls is None:
            raise HTTPException(
                status_code=400, detail=f"Unknown summarization provider configured: {provider}."
            )
        api_key = settings.get(PROVIDER_API_KEY_SETTINGS[provider])
        if not api_key:
            display_name = PROVIDER_DISPLAY_NAMES.get(provider, provider)
            article = PROVIDER_INDEFINITE_ARTICLES.get(provider, "a")
            raise HTTPException(
                status_code=400,
                detail=f"Add {article} {display_name} API key in Settings to {action_phrase}.",
            )
        return client_cls, api_key

    def _load_course_entries(folder: str) -> list[dict]:
        """The finished-download entries for `folder`, with the shared course
        validation applied: 400 if fewer than 2 (not a course) and 503 if the DB
        is busy. The zero-ready-lessons check is left to each caller, since chat
        and digest word that nudge identically but check it at slightly
        different points."""
        try:
            entries = entries_for_course(history_store, folder)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        if len(entries) < 2:
            raise HTTPException(status_code=400, detail=COURSE_NOT_A_COURSE_MESSAGE)
        return entries

    @router.get("/courses")
    def get_courses() -> dict:
        # No Pro gate: this only reports which folders exist (the frontend
        # decides what to show locked vs. unlocked). The gated content -- chat
        # and digest -- is Pro-checked on its own routes below.
        try:
            courses = list_courses(history_store)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        return {"courses": courses}

    @router.post("/courses/chat")
    async def course_chat(request: CourseChatRequest) -> dict:
        # Pro-gate FIRST, before any course content is read (matches export/batch
        # and the design's precedence: a non-Pro caller learns nothing here).
        if not license_store.is_pro():
            raise HTTPException(status_code=402, detail=COURSE_PRO_MESSAGE)
        if request.mode not in ("chat", "search"):
            raise HTTPException(status_code=400, detail='mode must be "chat" or "search".')

        entries = _load_course_entries(request.folder)
        ready = [entry for entry in entries if entry.get("summary_status") == "done"]
        if not ready:
            raise HTTPException(status_code=400, detail=COURSE_NO_READY_MESSAGE)

        client_cls, api_key = _resolve_summarization_client("chat about this course")
        context = assemble_course_context(entries)
        question = build_course_chat_question(request.question, request.mode)
        history = [turn.model_dump() for turn in request.history[-MAX_CHAT_HISTORY_TURNS:]]

        loop = asyncio.get_running_loop()
        client = client_cls(api_key)
        try:
            answer = await loop.run_in_executor(None, client.chat, context, question, history)
        except AIClientError as exc:
            # Mirror single-video chat exactly: a provider failure reaches us
            # synchronously, surfaced as a 502 with the same friendly wording.
            raise HTTPException(status_code=502, detail=humanize_transcription_error(exc))
        transcription_orchestrator._record_usage(client, "course_chat", history_id=None)
        return {"answer": answer}

    @router.post("/courses/digest")
    async def course_digest(request: CourseDigestRequest) -> dict:
        if not license_store.is_pro():
            raise HTTPException(status_code=402, detail=COURSE_PRO_MESSAGE)

        entries = _load_course_entries(request.folder)
        ready = [entry for entry in entries if entry.get("summary_status") == "done"]
        if not ready:
            raise HTTPException(status_code=400, detail=COURSE_NO_READY_MESSAGE)

        client_cls, api_key = _resolve_summarization_client("generate a course study guide")
        context = assemble_course_context(entries)

        loop = asyncio.get_running_loop()
        client = client_cls(api_key)
        try:
            # The ONLY AI-generated part of the document -- a short thematic
            # overview. Every lesson section below is assembled deterministically
            # from stored notes by write_study_guide.
            overview = await loop.run_in_executor(
                None, client.chat, context, COURSE_OVERVIEW_INSTRUCTION, []
            )
        except AIClientError as exc:
            raise HTTPException(status_code=502, detail=humanize_transcription_error(exc))
        transcription_orchestrator._record_usage(client, "course_digest", history_id=None)

        try:
            target = write_study_guide(request.folder, entries, overview)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"Couldn't write the study guide: {exc}")
        return {"path": str(target)}

    return router
