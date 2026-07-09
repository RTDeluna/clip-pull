import asyncio

import main as main_module
from fastapi.testclient import TestClient


async def fake_download_all(entry_ids, output_folder, referer=None):
    return None


def setup_function():
    main_module.queue_manager._entries.clear()
    main_module.queue_manager._order.clear()
    main_module.orchestrator.download_all = fake_download_all


client = TestClient(main_module.app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_headers_present_for_cross_origin_request():
    response = client.get("/health", headers={"Origin": "http://example.com"})
    assert response.headers.get("access-control-allow-origin") == "*"


def test_post_queue_creates_entries_for_valid_urls():
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/111\nhttps://vimeo.com/222",
            "output_folder": "C:/downloads",
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert len(body["entries"]) == 2
    assert body["invalid_lines"] == []
    assert body["entries"][0]["status"] == "queued"


def test_post_queue_reports_invalid_lines_without_blocking_valid_ones():
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/111\nnot a url",
            "output_folder": "C:/downloads",
        },
    )
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["invalid_lines"] == ["not a url"]


def test_get_queue_returns_current_entries():
    client.post(
        "/queue",
        json={"urls_text": "https://vimeo.com/333", "output_folder": "C:/downloads"},
    )
    response = client.get("/queue")
    urls = [entry["url"] for entry in response.json()["entries"]]
    assert "https://vimeo.com/333" in urls


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
    manager = main_module.ConnectionManager()
    socket = _RecordingSocket()
    manager.active.append(socket)

    async def run():
        await asyncio.gather(
            manager.broadcast({"n": 1}),
            manager.broadcast({"n": 2}),
        )

    asyncio.run(run())

    # Without serialization, two concurrent broadcasts would both be inside
    # send_json on the same socket at once (max_concurrent_sends == 2), which
    # Starlette does not support safely. With the lock, sends never overlap.
    assert socket.max_concurrent_sends == 1
    assert len(socket.received) == 2


def test_retry_entry_resets_status_to_queued():
    post_response = client.post(
        "/queue",
        json={"urls_text": "https://vimeo.com/444", "output_folder": "C:/downloads"},
    )
    entry_id = post_response.json()["entries"][0]["id"]
    main_module.queue_manager.set_error(entry_id, "some error")

    response = client.post(f"/queue/{entry_id}/retry", json={})
    assert response.status_code == 202
    assert response.json()["entry"]["status"] == "queued"
