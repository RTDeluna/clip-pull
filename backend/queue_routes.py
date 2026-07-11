import asyncio
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from background_tasks import track_task
from downloader import DownloadOrchestrator, resolve_output_folder
from history_store import HistoryStore
from queue_manager import QueueManager
from settings_store import SettingsStore
from url_validation import parse_url_list

# A generous cap for a course-links paste, not a hard technical limit -- past
# this, a single batch would grow the in-memory queue and every WS
# sync/update_batch payload without bound, and is far more likely to be a
# mistake (pasted the wrong thing) than a real batch.
MAX_URLS_PER_BATCH = 500


class QueueRequest(BaseModel):
    urls_text: str
    output_folder: str
    referer: Optional[str] = None
    subfolder: Optional[str] = None
    duplicate_action: Optional[Literal["queue_all", "skip_duplicates"]] = None
    # Set when this submission is a History tab retry of a single failed
    # entry -- lets the new download's completion update that same History
    # row in place instead of adding a second, duplicate entry for it.
    retry_of_history_id: Optional[int] = None


class RetryRequest(BaseModel):
    referer: Optional[str] = None


class AppState:
    def __init__(self):
        self.referer: Optional[str] = None


def build_queue_router(
    queue_manager: QueueManager,
    orchestrator: DownloadOrchestrator,
    history_store: HistoryStore,
    settings_store: SettingsStore,
    state: AppState,
) -> APIRouter:
    router = APIRouter()

    @router.get("/queue")
    def get_queue() -> dict:
        return {"entries": queue_manager.to_list()}

    @router.post("/queue", status_code=202)
    async def post_queue(request: QueueRequest) -> dict:
        valid_urls, invalid_lines = parse_url_list(request.urls_text)
        if len(valid_urls) + len(invalid_lines) > MAX_URLS_PER_BATCH:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"That's {len(valid_urls) + len(invalid_lines)} lines — "
                    f"batches are capped at {MAX_URLS_PER_BATCH} links. "
                    "Split it into smaller batches."
                ),
            )
        state.referer = request.referer

        previously_downloaded_urls = history_store.was_previously_downloaded(valid_urls)
        duplicate_urls_in_batch = [u for u in valid_urls if u in previously_downloaded_urls]

        skip_duplicates_setting = settings_store.get()["skip_duplicates"]
        skipped_duplicate_urls: list[str] = []

        if skip_duplicates_setting and duplicate_urls_in_batch:
            skipped_duplicate_urls = duplicate_urls_in_batch
            valid_urls = [u for u in valid_urls if u not in previously_downloaded_urls]
            previously_downloaded_urls = set()
        elif duplicate_urls_in_batch and request.duplicate_action is None:
            return {
                "entries": [],
                "invalid_lines": invalid_lines,
                "skipped_duplicate_urls": [],
                "skipped_inflight_urls": [],
                "needs_confirmation": True,
                "duplicate_urls": duplicate_urls_in_batch,
            }
        elif duplicate_urls_in_batch and request.duplicate_action == "skip_duplicates":
            skipped_duplicate_urls = duplicate_urls_in_batch
            valid_urls = [u for u in valid_urls if u not in previously_downloaded_urls]
            previously_downloaded_urls = set()
        # duplicate_action == "queue_all" (or no duplicates in this batch): fall through
        # and queue valid_urls as-is, keeping previously_downloaded_urls for the badge flag.

        resolved_folder = resolve_output_folder(request.output_folder, request.subfolder)
        try:
            Path(resolved_folder).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Couldn't create the output folder: {exc.strerror or exc}",
            ) from exc

        batch_id = uuid.uuid4().hex if valid_urls else None
        entries = queue_manager.add_entries(
            valid_urls,
            batch_id=batch_id,
            output_folder=resolved_folder,
            previously_downloaded_urls=previously_downloaded_urls,
            history_id=request.retry_of_history_id,
        )
        created_urls = {entry.url for entry in entries}
        skipped_inflight_urls = [u for u in valid_urls if u not in created_urls]
        if entries:
            track_task(
                asyncio.create_task(
                    orchestrator.download_all(
                        [entry.id for entry in entries],
                        resolved_folder,
                        request.referer,
                    )
                )
            )
        return {
            "entries": [entry.to_dict() for entry in entries],
            "invalid_lines": invalid_lines,
            "skipped_duplicate_urls": skipped_duplicate_urls,
            "skipped_inflight_urls": skipped_inflight_urls,
            "needs_confirmation": False,
            "duplicate_urls": [],
        }

    # Entries are auto-removed from the queue a few seconds after finishing
    # (see DownloadOrchestrator._remove_from_queue_after_delay), so a
    # pause/retry/resume click racing that timer -- or a stale/duplicate
    # click, or a leftover browser tab -- can reference an entry_id that's
    # already gone. queue_manager.get()/to_dict() raise a bare KeyError for
    # that, which without this would surface as an unhandled 500.
    @router.post("/queue/{entry_id}/retry", status_code=202)
    async def retry_entry(entry_id: str, request: RetryRequest) -> dict:
        try:
            entry = queue_manager.get(entry_id)
            queue_manager.reset_for_retry(entry_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="This download is no longer in the queue.")
        referer = request.referer or state.referer
        track_task(
            asyncio.create_task(
                orchestrator.download_all([entry_id], entry.output_folder, referer)
            )
        )
        return {"entry": queue_manager.to_dict(entry_id)}

    @router.post("/queue/{entry_id}/pause", status_code=202)
    def pause_entry(entry_id: str) -> dict:
        # Actually stopping the download can lag a moment behind this call
        # (see DownloadOrchestrator.request_pause's docstring) -- marking
        # "pausing" here broadcasts an immediate status change over the
        # WebSocket instead of leaving the UI looking unresponsive until
        # the worker thread's next progress tick catches up.
        paused = orchestrator.request_pause(entry_id)
        try:
            if paused:
                queue_manager.mark_pausing(entry_id)
            return {"entry": queue_manager.to_dict(entry_id)}
        except KeyError:
            raise HTTPException(status_code=404, detail="This download is no longer in the queue.")

    @router.post("/queue/{entry_id}/resume", status_code=202)
    async def resume_entry(entry_id: str, request: RetryRequest) -> dict:
        try:
            entry = queue_manager.get(entry_id)
            # Broadcast the "resuming" transition immediately rather than
            # waiting for the scheduled download task to actually start
            # (download_entry only sets "downloading" once it acquires a
            # concurrency slot) -- otherwise the row visibly sits on
            # "Paused" for a beat after Resume is clicked.
            queue_manager.mark_resuming(entry_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="This download is no longer in the queue.")
        referer = request.referer or state.referer
        track_task(
            asyncio.create_task(
                orchestrator.download_all([entry_id], entry.output_folder, referer)
            )
        )
        return {"entry": queue_manager.to_dict(entry_id)}

    return router
