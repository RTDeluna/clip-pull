from fastapi import FastAPI
from fastapi.testclient import TestClient

from queue_manager import QueueManager
from downloader import DownloadOrchestrator
from queue_routes import build_queue_router, AppState


async def fake_download_all(entry_ids, output_folder, referer=None):
    return None


def _make_client():
    queue_manager = QueueManager()
    orchestrator = DownloadOrchestrator(queue_manager)
    orchestrator.download_all = fake_download_all
    state = AppState()
    app = FastAPI()
    app.include_router(build_queue_router(queue_manager, orchestrator, state))
    return TestClient(app), queue_manager


def test_post_queue_creates_entries_for_valid_urls():
    client, _ = _make_client()
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
    client, _ = _make_client()
    response = client.post(
        "/queue",
        json={"urls_text": "https://vimeo.com/111\nnot a url", "output_folder": "C:/downloads"},
    )
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["invalid_lines"] == ["not a url"]


def test_post_queue_accepts_non_vimeo_urls_like_loom():
    client, _ = _make_client()
    response = client.post(
        "/queue",
        json={"urls_text": "https://www.loom.com/share/abc123", "output_folder": "C:/downloads"},
    )
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["invalid_lines"] == []


def test_get_queue_returns_current_entries():
    client, _ = _make_client()
    client.post(
        "/queue", json={"urls_text": "https://vimeo.com/333", "output_folder": "C:/downloads"}
    )
    response = client.get("/queue")
    urls = [entry["url"] for entry in response.json()["entries"]]
    assert "https://vimeo.com/333" in urls


def test_retry_entry_resets_status_to_queued():
    client, queue_manager = _make_client()
    post_response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/444", "output_folder": "C:/downloads"}
    )
    entry_id = post_response.json()["entries"][0]["id"]
    queue_manager.set_error(entry_id, "some error")

    response = client.post(f"/queue/{entry_id}/retry", json={})
    assert response.status_code == 202
    assert response.json()["entry"]["status"] == "queued"
