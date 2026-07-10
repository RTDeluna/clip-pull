import asyncio
from typing import Optional

from fastapi import WebSocket

from background_tasks import track_task

FLUSH_INTERVAL_SECONDS = 0.05


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


class QueueBroadcaster:
    """Coalesces rapid per-entry notify() calls into a single WS message,
    flushed on a short interval — prevents a big batch (100 pasted links)
    from producing one WS frame per entry mutation."""

    def __init__(
        self,
        connection_manager: ConnectionManager,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
    ):
        self.connection_manager = connection_manager
        self.flush_interval = flush_interval
        self._pending: dict[str, dict] = {}
        self._flush_scheduled = False

    def notify(self, entry_dict: dict) -> None:
        self._pending[entry_dict["id"]] = entry_dict
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no running event loop (e.g. called directly in tests)
        if not self._flush_scheduled:
            self._flush_scheduled = True
            track_task(asyncio.create_task(self._flush_after_delay()))

    async def _flush_after_delay(self) -> None:
        await asyncio.sleep(self.flush_interval)
        entries = list(self._pending.values())
        self._pending.clear()
        self._flush_scheduled = False
        if entries:
            await self.connection_manager.broadcast(
                {"type": "update_batch", "entries": entries}
            )
