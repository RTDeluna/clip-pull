import json
import sqlite3
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from history_store import HistoryStore
from history_routes import build_history_router
from license_store import LicenseStore


def _make_client(pro=False):
    store = HistoryStore()
    license_store = LicenseStore()
    if pro:
        license_store.set_active(license_key="TEST-PRO-KEY", purchase_email=None)
    app = FastAPI()
    app.include_router(build_history_router(store, license_store))
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


def test_get_history_strips_key_points_and_chapters_for_free_user():
    client, store = _make_client(pro=False)
    entry = store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="Video 1",
        output_path="C:/downloads/Video 1.mp4", total_size="10MB",
        status="done", error_reason=None, retry_count=0,
    )
    store.update_summary(
        entry["id"], status="done",
        summary=json.dumps({
            "tldr": "A short summary.",
            "key_points": [{"seconds": 5, "text": "A point"}],
            "chapters": [{"seconds": 0, "title": "Intro"}],
        }),
    )
    response = client.get("/history")
    summary = json.loads(response.json()["entries"][0]["summary"])
    assert summary["tldr"] == "A short summary."
    assert summary["key_points"] == []
    assert summary["chapters"] == []


def test_get_history_includes_key_points_and_chapters_for_pro_user():
    client, store = _make_client(pro=True)
    entry = store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="Video 1",
        output_path="C:/downloads/Video 1.mp4", total_size="10MB",
        status="done", error_reason=None, retry_count=0,
    )
    store.update_summary(
        entry["id"], status="done",
        summary=json.dumps({
            "tldr": "A short summary.",
            "key_points": [{"seconds": 5, "text": "A point"}],
            "chapters": [{"seconds": 0, "title": "Intro"}],
        }),
    )
    response = client.get("/history")
    summary = json.loads(response.json()["entries"][0]["summary"])
    assert summary["key_points"] == [{"seconds": 5, "text": "A point"}]
    assert summary["chapters"] == [{"seconds": 0, "title": "Intro"}]


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


def test_get_history_returns_503_with_friendly_message_when_db_is_locked():
    client, store = _make_client()
    with patch.object(store, "search", side_effect=sqlite3.OperationalError("database is locked")):
        response = client.get("/history")
    assert response.status_code == 503
    assert "busy" in response.json()["detail"].lower()


def test_delete_history_entry_returns_503_with_friendly_message_when_db_is_locked():
    client, store = _make_client()
    with patch.object(store, "delete", side_effect=sqlite3.OperationalError("database is locked")):
        response = client.delete("/history/1")
    assert response.status_code == 503
    assert "busy" in response.json()["detail"].lower()


def test_clear_history_returns_503_with_friendly_message_when_db_is_locked():
    client, store = _make_client()
    with patch.object(store, "clear", side_effect=sqlite3.OperationalError("database is locked")):
        response = client.delete("/history")
    assert response.status_code == 503
    assert "busy" in response.json()["detail"].lower()
