import asyncio
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from background_tasks import track_task
from downloader import DownloadOrchestrator, check_ffmpeg_available, probe_total_bytes
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

# The packaged app has no visible console (stdio is hidden), so a plain
# print()/stderr warning is invisible to end users and to us -- everything
# meaningful goes to a log file next to the database instead. Skipped for
# ":memory:" (test) DB paths so the test suite doesn't scatter log files.
logger = logging.getLogger("clippull")
logger.setLevel(logging.INFO)
if DB_PATH != ":memory:":
    log_dir = Path(DB_PATH).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "clippull.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(file_handler)
stream_handler = logging.StreamHandler(sys.stderr)
stream_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
logger.addHandler(stream_handler)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

connection_manager = ConnectionManager()
broadcaster = QueueBroadcaster(connection_manager)

history_store = HistoryStore(DB_PATH)
settings_store = SettingsStore(DB_PATH)


def record_history_and_broadcast(**kwargs) -> dict:
    # Finished downloads only land in the History tab via this WS push —
    # without it, History only ever updates on the next manual refresh/search.
    row = history_store.record(**kwargs)
    track_task(
        asyncio.create_task(
            connection_manager.broadcast({"type": "history_added", "entry": row})
        )
    )
    return row


queue_manager = QueueManager(on_update=broadcaster.notify, on_remove=broadcaster.notify_removed)
orchestrator = DownloadOrchestrator(
    queue_manager,
    probe_fn=probe_total_bytes,
    get_max_concurrent=lambda: settings_store.get()["max_concurrent_downloads"],
    get_fragment_concurrency=lambda: settings_store.get()["concurrent_fragment_downloads"],
    get_aria2c_enabled=lambda: settings_store.get()["aria2c_enabled"],
    record_history=record_history_and_broadcast,
    on_batch_complete=lambda batch_id, summary: track_task(
        asyncio.create_task(
            connection_manager.broadcast(
                {"type": "batch_complete", "batch_id": batch_id, "summary": summary}
            )
        )
    ),
)

state = AppState()

app.include_router(
    build_queue_router(queue_manager, orchestrator, history_store, settings_store, state)
)
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
        pass
    except Exception:
        # Any other transport-level error (not a clean WebSocketDisconnect)
        # still needs the connection removed from the broadcast list -- left
        # unhandled, broadcast()'s own dead-connection cleanup would
        # eventually self-heal this, but only on the next message sent.
        pass
    finally:
        connection_manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    if not check_ffmpeg_available():
        logger.warning(
            "ffmpeg not found on PATH. Downloads will use a single "
            "pre-muxed format instead of the highest available quality."
        )

    uvicorn.run(app, host="127.0.0.1", port=8934)
