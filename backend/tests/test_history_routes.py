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
