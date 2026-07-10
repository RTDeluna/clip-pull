import asyncio
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from background_tasks import track_task
from downloader import DownloadOrchestrator
from queue_manager import QueueManager
from url_validation import parse_url_list


class QueueRequest(BaseModel):
    urls_text: str
    output_folder: str
    referer: Optional[str] = None


class RetryRequest(BaseModel):
    referer: Optional[str] = None


class AppState:
    def __init__(self):
        self.output_folder: Optional[str] = None
        self.referer: Optional[str] = None


def build_queue_router(
    queue_manager: QueueManager,
    orchestrator: DownloadOrchestrator,
    state: AppState,
) -> APIRouter:
    router = APIRouter()

    @router.get("/queue")
    def get_queue() -> dict:
        return {"entries": queue_manager.to_list()}

    @router.post("/queue", status_code=202)
    async def post_queue(request: QueueRequest) -> dict:
        valid_urls, invalid_lines = parse_url_list(request.urls_text)
        state.output_folder = request.output_folder
        state.referer = request.referer
        entries = queue_manager.add_entries(valid_urls)
        if entries:
            track_task(
                asyncio.create_task(
                    orchestrator.download_all(
                        [entry.id for entry in entries],
                        request.output_folder,
                        request.referer,
                    )
                )
            )
        return {
            "entries": [entry.to_dict() for entry in entries],
            "invalid_lines": invalid_lines,
        }

    @router.post("/queue/{entry_id}/retry", status_code=202)
    async def retry_entry(entry_id: str, request: RetryRequest) -> dict:
        queue_manager.reset_for_retry(entry_id)
        referer = request.referer or state.referer
        track_task(
            asyncio.create_task(
                orchestrator.download_all([entry_id], state.output_folder, referer)
            )
        )
        return {"entry": queue_manager.to_dict(entry_id)}

    return router
