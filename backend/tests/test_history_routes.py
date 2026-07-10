from fastapi import FastAPI
from fastapi.testclient import TestClient

from history_store import HistoryStore
from history_routes import build_history_router


def _make_client():
    store = HistoryStore()
    app = FastAPI()
    app.include_router(build_history_router(store))
    return TestClient(app), store


def test_get_history_returns_recorded_entries():
    client, store = _make_client()
    store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="Video 1",
        output_path="C:/downloads/Video 1.mp4", total_size="10MB",
        status="done", error_reason=None, retry_count=0,
    )
    response = client.get("/history")
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["url"] == "https://vimeo.com/1"


def test_get_history_filters_by_query_param():
    client, store = _make_client()
    store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="Marketing Intro",
        output_path=None, total_size=None, status="done", error_reason=None, retry_count=0,
    )
    store.record(
        entry_id="e2", batch_id="b1", url="https://vimeo.com/2", title="Sales Pitch",
        output_path=None, total_size=None, status="done", error_reason=None, retry_count=0,
    )
    response = client.get("/history", params={"q": "Marketing"})
    entries = response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["title"] == "Marketing Intro"


def test_get_history_filters_by_status():
    client, store = _make_client()
    store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="V1",
        output_path=None, total_size=None, status="done", error_reason=None, retry_count=0,
    )
    store.record(
        entry_id="e2", batch_id="b1", url="https://vimeo.com/2", title="V2",
        output_path=None, total_size=None, status="error", error_reason="failed", retry_count=0,
    )
    response = client.get("/history", params={"status": "error"})
    entries = response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["status"] == "error"


def test_delete_history_entry_removes_it():
    client, store = _make_client()
    result = store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="Video 1",
        output_path=None, total_size=None, status="done", error_reason=None, retry_count=0,
    )
    response = client.delete(f"/history/{result['id']}")
    assert response.status_code == 200
    assert response.json()["deleted"] == result["id"]
    assert client.get("/history").json()["entries"] == []


def test_delete_history_entry_404s_when_missing():
    client, _store = _make_client()
    response = client.delete("/history/999")
    assert response.status_code == 404


def test_clear_history_removes_matching_entries():
    client, store = _make_client()
    store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="V1",
        output_path=None, total_size=None, status="done", error_reason=None, retry_count=0,
    )
    store.record(
        entry_id="e2", batch_id="b1", url="https://vimeo.com/2", title="V2",
        output_path=None, total_size=None, status="error", error_reason="failed", retry_count=0,
    )
    response = client.delete("/history", params={"status": "error"})
    assert response.status_code == 200
    assert response.json()["deleted"] == 1
    remaining = client.get("/history").json()["entries"]
    assert len(remaining) == 1
    assert remaining[0]["status"] == "done"
