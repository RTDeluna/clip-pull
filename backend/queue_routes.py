import asyncio
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from background_tasks import track_task
from downloader import DownloadOrchestrator, resolve_output_folder
from history_store import HistoryStore
from queue_manager import QueueManager
from settings_store import SettingsStore
from url_validation import parse_url_list


class QueueRequest(BaseModel):
    urls_text: str
    output_folder: str
    referer: Optional[str] = None
    subfolder: Optional[str] = None


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
        state.referer = request.referer

        resolved_folder = resolve_output_folder(request.output_folder, request.subfolder)
        Path(resolved_folder).mkdir(parents=True, exist_ok=True)

        previously_downloaded_urls = history_store.was_previously_downloaded(valid_urls)

        skip_duplicates = settings_store.get()["skip_duplicates"]
        skipped_duplicate_urls: list[str] = []
        if skip_duplicates and previously_downloaded_urls:
            skipped_duplicate_urls = [u for u in valid_urls if u in previously_downloaded_urls]
            valid_urls = [u for u in valid_urls if u not in previously_downloaded_urls]
            previously_downloaded_urls = set()

        batch_id = uuid.uuid4().hex if valid_urls else None
        entries = queue_manager.add_entries(
            valid_urls,
            batch_id=batch_id,
            output_folder=resolved_folder,
            previously_downloaded_urls=previously_downloaded_urls,
        )
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
        }

    @router.post("/queue/{entry_id}/retry", status_code=202)
    async def retry_entry(entry_id: str, request: RetryRequest) -> dict:
        entry = queue_manager.get(entry_id)
        queue_manager.reset_for_retry(entry_id)
        referer = request.referer or state.referer
        track_task(
            asyncio.create_task(
                orchestrator.download_all([entry_id], entry.output_folder, referer)
            )
        )
        return {"entry": queue_manager.to_dict(entry_id)}

    return router
