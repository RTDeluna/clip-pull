import asyncio
import sys
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from downloader import DownloadOrchestrator, check_ffmpeg_available
from queue_manager import QueueManager
from url_validation import parse_url_list


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []
        self._send_lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        async with self._send_lock:
            stale = []
            for connection in self.active:
                try:
                    await connection.send_json(message)
                except Exception:
                    stale.append(connection)
            for connection in stale:
                self.disconnect(connection)


class AppState:
    def __init__(self):
        self.output_folder: Optional[str] = None
        self.referer: Optional[str] = None


app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
connection_manager = ConnectionManager()
state = AppState()

_background_tasks: set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _notify_websocket_clients(entry_dict: dict) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # no running event loop (e.g. called outside a request, such as in tests)
    _track_task(
        asyncio.create_task(
            connection_manager.broadcast({"type": "update", "entry": entry_dict})
        )
    )


queue_manager = QueueManager(on_update=_notify_websocket_clients)
orchestrator = DownloadOrchestrator(queue_manager)


class QueueRequest(BaseModel):
    urls_text: str
    output_folder: str
    referer: Optional[str] = None


class RetryRequest(BaseModel):
    referer: Optional[str] = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/queue")
def get_queue() -> dict:
    return {"entries": queue_manager.to_list()}


@app.post("/queue", status_code=202)
async def post_queue(request: QueueRequest) -> dict:
    valid_urls, invalid_lines = parse_url_list(request.urls_text)
    state.output_folder = request.output_folder
    state.referer = request.referer
    entries = queue_manager.add_entries(valid_urls)
    if entries:
        _track_task(
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


@app.post("/queue/{entry_id}/retry", status_code=202)
async def retry_entry(entry_id: str, request: RetryRequest) -> dict:
    queue_manager.reset_for_retry(entry_id)
    referer = request.referer or state.referer
    _track_task(
        asyncio.create_task(
            orchestrator.download_all([entry_id], state.output_folder, referer)
        )
    )
    return {"entry": queue_manager.to_dict(entry_id)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await connection_manager.connect(websocket)
    await websocket.send_json({"type": "sync", "entries": queue_manager.to_list()})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    if not check_ffmpeg_available():
        print(
            "WARNING: ffmpeg not found on PATH. High-quality downloads "
            "require ffmpeg to merge video+audio streams; downloads may fail "
            "or fall back to lower quality without it.",
            file=sys.stderr,
        )

    uvicorn.run(app, host="127.0.0.1", port=8934)
