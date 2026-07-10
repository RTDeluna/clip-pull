import asyncio

from ws_manager import ConnectionManager, QueueBroadcaster


class _FakeSocket:
    def __init__(self):
        self.received = []

    async def send_json(self, message: dict) -> None:
        self.received.append(message)


class _RecordingSocket:
    """Fake WebSocket that records how many send_json calls are in-flight
    concurrently, so we can prove ConnectionManager.broadcast serializes them."""

    def __init__(self, delay: float = 0.01):
        self.delay = delay
        self.received = []
        self._active = 0
        self.max_concurrent_sends = 0

    async def send_json(self, message: dict) -> None:
        self._active += 1
        self.max_concurrent_sends = max(self.max_concurrent_sends, self._active)
        await asyncio.sleep(self.delay)
        self.received.append(message)
        self._active -= 1


def test_connection_manager_broadcast_serializes_concurrent_sends():
    manager = ConnectionManager()
    socket = _RecordingSocket()
    manager.active.append(socket)

    async def run():
        await asyncio.gather(
            manager.broadcast({"n": 1}),
            manager.broadcast({"n": 2}),
        )

    asyncio.run(run())

    assert socket.max_concurrent_sends == 1
    assert len(socket.received) == 2


def test_queue_broadcaster_coalesces_rapid_updates_into_single_batch_message():
    manager = ConnectionManager()
    socket = _FakeSocket()
    manager.active.append(socket)
    broadcaster = QueueBroadcaster(manager, flush_interval=0.02)

    async def run():
        broadcaster.notify({"id": "e1", "status": "downloading", "percent": 10})
        broadcaster.notify({"id": "e1", "status": "downloading", "percent": 20})
        broadcaster.notify({"id": "e2", "status": "queued", "percent": 0})
        await asyncio.sleep(0.05)

    asyncio.run(run())

    assert len(socket.received) == 1
    batch_message = socket.received[0]
    assert batch_message["type"] == "update_batch"
    entries_by_id = {e["id"]: e for e in batch_message["entries"]}
    assert entries_by_id["e1"]["percent"] == 20
    assert "e2" in entries_by_id


def test_queue_broadcaster_does_nothing_with_no_running_loop():
    manager = ConnectionManager()
    broadcaster = QueueBroadcaster(manager)
    broadcaster.notify({"id": "e1", "status": "queued"})  # must not raise
