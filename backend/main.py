import os
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from downloader import DownloadOrchestrator, check_ffmpeg_available
from history_routes import build_history_router
from history_store import HistoryStore
from queue_manager import QueueManager
from queue_routes import AppState, build_queue_router
from settings_routes import build_settings_router
from settings_store import SettingsStore
from ws_manager import ConnectionManager, QueueBroadcaster

DB_PATH = os.environ.get(
    "CLIP_PULL_DB_PATH", str(Path(__file__).parent / "data" / "clip_pull.db")
)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

connection_manager = ConnectionManager()
broadcaster = QueueBroadcaster(connection_manager)

history_store = HistoryStore(DB_PATH)
settings_store = SettingsStore(DB_PATH)

queue_manager = QueueManager(on_update=broadcaster.notify)
orchestrator = DownloadOrchestrator(queue_manager)

state = AppState()

app.include_router(build_queue_router(queue_manager, orchestrator, state))
app.include_router(build_history_router(history_store))
app.include_router(build_settings_router(settings_store))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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
