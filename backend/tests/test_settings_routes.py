import sqlite3
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from settings_store import SettingsStore
from settings_routes import build_settings_router


def _make_client():
    store = SettingsStore()
    app = FastAPI()
    app.include_router(build_settings_router(store))
    return TestClient(app), store


def test_get_settings_returns_current_values():
    client, _ = _make_client()
    response = client.get("/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["max_concurrent_downloads"] == 3
    assert "aria2c_detected" in body


def test_patch_settings_updates_and_returns_new_values():
    client, _ = _make_client()
    response = client.patch("/settings", json={"max_concurrent_downloads": 5})
    assert response.status_code == 200
    assert response.json()["max_concurrent_downloads"] == 5


def test_get_settings_returns_503_with_friendly_message_when_db_is_locked():
    client, store = _make_client()
    with patch.object(store, "get", side_effect=sqlite3.OperationalError("database is locked")):
        response = client.get("/settings")
    assert response.status_code == 503
    assert "busy" in response.json()["detail"].lower()


def test_patch_settings_returns_503_with_friendly_message_when_db_is_locked():
    client, store = _make_client()
    with patch.object(store, "update", side_effect=sqlite3.OperationalError("database is locked")):
        response = client.patch("/settings", json={"max_concurrent_downloads": 5})
    assert response.status_code == 503
    assert "busy" in response.json()["detail"].lower()


def test_patch_settings_rejects_out_of_range_concurrency():
    client, _ = _make_client()
    response = client.patch("/settings", json={"max_concurrent_downloads": 100})
    assert response.status_code == 422


def test_patch_settings_only_updates_provided_fields():
    client, store = _make_client()
    client.patch("/settings", json={"aria2c_enabled": False})
    settings = store.get()
    assert settings["aria2c_enabled"] is False
    assert settings["max_concurrent_downloads"] == 3
