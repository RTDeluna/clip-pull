import asyncio
import functools
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from background_tasks import track_task
from downloader import DownloadOrchestrator, check_ffmpeg_available, probe_total_bytes
from gumroad_client import GumroadClientError, verify_license
from history_routes import build_history_router
from history_store import HistoryStore, redact_pro_summary_fields
from key_test_routes import build_key_test_router
from license_config import DEV_LICENSE_KEY
from license_routes import build_license_router
from license_store import LicenseStore
from queue_manager import QueueManager
from queue_routes import AppState, build_queue_router
from settings_routes import build_settings_router
from settings_store import SettingsStore
from transcription import TranscriptionOrchestrator
from transcription_routes import build_transcription_router
from usage_routes import build_usage_router
from usage_store import UsageStore
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
license_store = LicenseStore(DB_PATH)
usage_store = UsageStore(DB_PATH)


def record_history_and_broadcast(**kwargs) -> dict:
    # Finished downloads only land in the History tab via this WS push —
    # without it, History only ever updates on the next manual refresh/search.
    row = history_store.record(**kwargs)
    # A retry re-recorded into an existing row (update_id) can carry over a
    # summary from a previous attempt -- redact the same way GET /history
    # does, so a non-Pro caller can't see Pro-only content via this broadcast
    # either.
    broadcast_row = redact_pro_summary_fields(row, license_store.is_pro())
    track_task(
        asyncio.create_task(
            connection_manager.broadcast({"type": "history_added", "entry": broadcast_row})
        )
    )
    _maybe_auto_transcribe(row)
    return row


def _maybe_auto_transcribe(row: dict) -> None:
    # Kick off transcription (and optionally a chained summary) for a
    # just-finished download when a Pro user has opted into auto-processing.
    # The whole decision is wrapped so any failure here is logged but NEVER
    # propagates: the download has already been recorded/broadcast as done by
    # this point (the actually-important outcome), and a bug in this
    # convenience trigger must not undo that. Reuses the same orchestrator
    # methods the manual transcribe/summarize routes call -- just from an
    # automatic trigger point instead of an HTTP POST.
    try:
        if row.get("status") != "done":
            return
        settings = settings_store.get()
        if not settings.get("auto_transcribe_on_download"):
            return
        if not license_store.is_pro():
            return
        history_id = row["id"]
        if not transcription_orchestrator.request_transcription(history_id):
            return
        transcription_orchestrator.mark_transcription_running(history_id)
        summarize = bool(settings.get("auto_summarize_after_transcribe"))
        track_task(
            asyncio.create_task(
                transcription_orchestrator.transcribe_and_maybe_summarize(history_id, summarize)
            )
        )
    except Exception:
        logger.exception("Auto-transcribe trigger failed for history entry %s", row.get("id"))


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

transcription_orchestrator = TranscriptionOrchestrator(
    history_store,
    settings_store,
    usage_store=usage_store,
    license_store=license_store,
    broadcast=lambda message: track_task(
        asyncio.create_task(connection_manager.broadcast(message))
    ),
)

app.include_router(
    build_queue_router(queue_manager, orchestrator, history_store, settings_store, state)
)
app.include_router(build_history_router(history_store, license_store))
app.include_router(build_settings_router(settings_store))
app.include_router(build_key_test_router())
app.include_router(build_license_router(license_store))
app.include_router(build_usage_router(usage_store))
app.include_router(
    build_transcription_router(
        history_store, transcription_orchestrator, license_store, settings_store
    )
)


async def _revalidate_license(store: LicenseStore) -> None:
    # Opportunistic re-check of a cached-active license, run as a fire-and-forget
    # startup task. verify_license does a blocking httpx call, so -- exactly like
    # transcription's AI-client calls -- it runs in a thread executor to avoid
    # blocking the event loop. A network failure leaves the cached status alone
    # (offline grace: don't revoke Pro just because the user is offline); only a
    # definitive answer from Gumroad updates the cache.
    license_key = store.get_license_key()
    if not license_key:
        return
    # Same dev-only bypass as the /license/activate route (see
    # license_config.py) -- without this, a license activated locally via
    # CLIP_PULL_DEV_LICENSE_KEY gets silently revoked on the very next
    # backend restart, since Gumroad has never heard of the dev key and
    # would otherwise reject it here.
    if DEV_LICENSE_KEY and license_key == DEV_LICENSE_KEY:
        store.touch_validated()
        return
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, functools.partial(verify_license, license_key, increment_uses_count=False)
        )
    except GumroadClientError as exc:
        logger.info("Skipping license revalidation -- couldn't reach Gumroad (%s).", exc)
        return

    purchase = result.get("purchase") or {}
    if not result.get("success") or (
        purchase.get("refunded") or purchase.get("chargebacked") or purchase.get("disputed")
    ):
        store.set_invalid()
    else:
        store.touch_validated()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await connection_manager.connect(websocket)
    try:
        # Kept inside the try so a disconnect during this initial sync send
        # is caught like any other, and the finally below still removes the
        # connection from the broadcast list.
        await websocket.send_json({"type": "sync", "entries": queue_manager.to_list()})
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

    reset_count = history_store.reset_stuck_transcriptions()
    if reset_count:
        logger.warning(
            "Reset %d transcription(s) left stuck on 'running' from a previous session.",
            reset_count,
        )

    # A real HTTP re-check can't run in this synchronous pre-startup block
    # without blocking app launch, so schedule it as a fire-and-forget task
    # once the event loop is running -- and only if a license is cached active.
    @app.on_event("startup")
    async def revalidate_license_on_startup() -> None:
        if license_store.get()["status"] == "active":
            track_task(asyncio.create_task(_revalidate_license(license_store)))

    uvicorn.run(app, host="127.0.0.1", port=8934)
