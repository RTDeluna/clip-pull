import asyncio

import main as main_module
from fastapi.testclient import TestClient


class _FakeSocket:
    def __init__(self):
        self.received = []

    async def send_json(self, message: dict) -> None:
        self.received.append(message)


def test_health_returns_ok():
    client = TestClient(main_module.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_headers_present_for_cross_origin_request():
    client = TestClient(main_module.app)
    response = client.get("/health", headers={"Origin": "http://example.com"})
    assert response.headers.get("access-control-allow-origin") == "*"


def test_completed_download_broadcasts_history_added_over_websocket():
    # This is what makes a finished download show up in the History tab
    # instantly instead of only on the next manual refresh/search.
    socket = _FakeSocket()
    main_module.connection_manager.active.append(socket)
    try:
        async def run():
            row = main_module.record_history_and_broadcast(
                entry_id="abc123",
                batch_id=None,
                url="https://example.com/video",
                title="Example Video",
                output_path="/tmp/example.mp4",
                total_size="12.3 MB",
                status="done",
                error_reason=None,
                retry_count=0,
            )
            await asyncio.sleep(0.01)  # let the scheduled broadcast task run
            return row

        row = asyncio.run(run())
    finally:
        main_module.connection_manager.active.remove(socket)

    assert row["url"] == "https://example.com/video"
    assert socket.received == [{"type": "history_added", "entry": row}]
