import sqlite3
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gumroad_client
from gumroad_client import GumroadClientError
from license_routes import build_license_router
from license_store import LicenseStore

# --------------------------------------------------------------------------
# LicenseStore round-trip
# --------------------------------------------------------------------------

def test_get_returns_defaults_when_not_activated():
    store = LicenseStore()
    entry = store.get()
    assert entry["status"] == "none"
    assert entry["license_key_last4"] is None
    assert entry["purchase_email"] is None
    assert entry["activated_at"] is None
    assert entry["last_validated_at"] is None
    assert store.is_pro() is False


def test_set_active_marks_pro_and_records_details():
    store = LicenseStore()
    entry = store.set_active(license_key="ABCD-1234-EFGH-5678", purchase_email="buyer@example.com")
    assert entry["status"] == "active"
    assert entry["purchase_email"] == "buyer@example.com"
    assert entry["activated_at"] is not None
    assert entry["last_validated_at"] is not None
    assert entry["license_key_last4"] == "5678"
    assert store.is_pro() is True


def test_get_masks_key_but_internal_accessor_returns_raw():
    store = LicenseStore()
    store.set_active(license_key="SECRET-KEY-9999", purchase_email=None)
    entry = store.get()
    assert "license_key" not in entry
    assert entry["license_key_last4"] == "9999"
    # The raw key is only reachable through the internal accessor.
    assert store.get_license_key() == "SECRET-KEY-9999"


def test_set_invalid_keeps_key_and_email_but_drops_pro():
    store = LicenseStore()
    store.set_active(license_key="KEY-1111", purchase_email="x@y.com")
    entry = store.set_invalid()
    assert entry["status"] == "invalid"
    assert entry["purchase_email"] == "x@y.com"
    assert entry["license_key_last4"] == "1111"
    assert store.get_license_key() == "KEY-1111"
    assert store.is_pro() is False


def test_touch_validated_keeps_active_and_refreshes_timestamp():
    store = LicenseStore()
    store.set_active(license_key="KEY-2222", purchase_email=None)
    entry = store.touch_validated()
    assert entry["status"] == "active"
    assert entry["license_key_last4"] == "2222"
    assert entry["last_validated_at"] is not None
    assert store.is_pro() is True


def test_clear_resets_everything():
    store = LicenseStore()
    store.set_active(license_key="KEY-3333", purchase_email="a@b.com")
    entry = store.clear()
    assert entry["status"] == "none"
    assert entry["license_key_last4"] is None
    assert entry["purchase_email"] is None
    assert entry["activated_at"] is None
    assert entry["last_validated_at"] is None
    assert store.get_license_key() is None
    assert store.is_pro() is False


def test_license_persists_across_store_instances_pointing_at_same_file(tmp_path):
    db_path = tmp_path / "license.db"
    store1 = LicenseStore(db_path)
    store1.set_active(license_key="PERSIST-4444", purchase_email="p@q.com")

    store2 = LicenseStore(db_path)
    assert store2.is_pro() is True
    assert store2.get()["license_key_last4"] == "4444"


# --------------------------------------------------------------------------
# gumroad_client.verify_license (no real network)
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload, *, bad_json=False):
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


def test_verify_license_returns_parsed_body_and_sends_expected_form(monkeypatch):
    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return _FakeResponse({"success": True, "purchase": {"email": "a@b.com"}})

    monkeypatch.setattr(gumroad_client.httpx, "post", fake_post)
    result = gumroad_client.verify_license("KEY-123", increment_uses_count=False)
    assert result["success"] is True
    assert captured["url"] == gumroad_client.GUMROAD_VERIFY_URL
    assert captured["data"]["license_key"] == "KEY-123"
    assert captured["data"]["increment_uses_count"] == "false"
    assert captured["data"]["product_permalink"] == gumroad_client.GUMROAD_PRODUCT_PERMALINK


def test_verify_license_omits_product_id_when_not_configured(monkeypatch):
    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured["data"] = data
        return _FakeResponse({"success": True})

    monkeypatch.setattr(gumroad_client.httpx, "post", fake_post)
    monkeypatch.setattr(gumroad_client, "GUMROAD_PRODUCT_ID", None)
    gumroad_client.verify_license("KEY-123")
    assert "product_id" not in captured["data"]


def test_verify_license_includes_product_id_when_configured(monkeypatch):
    # Newer Gumroad products reportedly need product_id instead of
    # product_permalink for license verification -- sent alongside the
    # permalink (not replacing it) whenever it's configured, so this
    # works regardless of which one Gumroad actually checks.
    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured["data"] = data
        return _FakeResponse({"success": True})

    monkeypatch.setattr(gumroad_client.httpx, "post", fake_post)
    monkeypatch.setattr(gumroad_client, "GUMROAD_PRODUCT_ID", "abc123")
    gumroad_client.verify_license("KEY-123")
    assert captured["data"]["product_id"] == "abc123"
    assert captured["data"]["product_permalink"] == gumroad_client.GUMROAD_PRODUCT_PERMALINK


def test_verify_license_falls_back_when_body_is_not_json(monkeypatch):
    monkeypatch.setattr(
        gumroad_client.httpx, "post", lambda *a, **k: _FakeResponse(None, bad_json=True)
    )
    result = gumroad_client.verify_license("KEY-123")
    assert result["success"] is False
    assert "message" in result


def test_verify_license_raises_on_network_error(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(gumroad_client.httpx, "post", boom)
    with pytest.raises(GumroadClientError):
        gumroad_client.verify_license("KEY-123")


# --------------------------------------------------------------------------
# License routes (verify_license mocked -- no real network)
# --------------------------------------------------------------------------

def _make_client():
    store = LicenseStore()
    app = FastAPI()
    app.include_router(build_license_router(store))
    return TestClient(app), store


def test_get_license_reports_not_pro_by_default():
    client, _ = _make_client()
    response = client.get("/license")
    assert response.status_code == 200
    assert response.json() == {
        "status": "none",
        "pro": False,
        "purchase_email": None,
        "activated_at": None,
        "last_validated_at": None,
    }


def test_activate_with_valid_license_returns_pro():
    client, store = _make_client()
    fake = {
        "success": True,
        "uses": 1,
        "purchase": {
            "email": "buyer@example.com",
            "refunded": False,
            "chargebacked": False,
            "disputed": False,
        },
    }
    with patch("license_routes.verify_license", return_value=fake) as mock_verify:
        response = client.post("/license/activate", json={"license_key": "VALID-KEY-1234"})
    assert response.status_code == 200
    entry = response.json()["entry"]
    assert entry["status"] == "active"
    assert entry["pro"] is True
    assert entry["purchase_email"] == "buyer@example.com"
    assert "license_key" not in entry
    assert store.is_pro() is True
    # Routine activation should increment Gumroad's usage counter.
    assert mock_verify.call_args.kwargs["increment_uses_count"] is True


def test_activate_with_invalid_license_returns_400_with_gumroad_message():
    client, _ = _make_client()
    fake = {"success": False, "message": "That license does not exist."}
    with patch("license_routes.verify_license", return_value=fake):
        response = client.post("/license/activate", json={"license_key": "BAD-KEY"})
    assert response.status_code == 400
    assert response.json()["detail"] == "That license does not exist."


def test_activate_with_refunded_license_returns_400():
    client, store = _make_client()
    fake = {
        "success": True,
        "purchase": {
            "email": "buyer@example.com",
            "refunded": True,
            "chargebacked": False,
            "disputed": False,
        },
    }
    with patch("license_routes.verify_license", return_value=fake):
        response = client.post("/license/activate", json={"license_key": "REFUNDED-KEY"})
    assert response.status_code == 400
    assert "refunded" in response.json()["detail"].lower()
    assert store.is_pro() is False


def test_activate_when_gumroad_unreachable_returns_503():
    client, _ = _make_client()
    with patch("license_routes.verify_license", side_effect=GumroadClientError("boom")):
        response = client.post("/license/activate", json={"license_key": "SOME-KEY"})
    assert response.status_code == 503
    assert "gumroad" in response.json()["detail"].lower()


def test_activate_rejects_empty_license_key():
    client, _ = _make_client()
    response = client.post("/license/activate", json={"license_key": ""})
    assert response.status_code == 422


def test_deactivate_clears_state():
    client, store = _make_client()
    store.set_active(license_key="KEY-TO-CLEAR", purchase_email="a@b.com")
    response = client.post("/license/deactivate")
    assert response.status_code == 200
    entry = response.json()["entry"]
    assert entry["status"] == "none"
    assert entry["pro"] is False
    assert store.is_pro() is False


def test_get_license_never_leaks_raw_key():
    client, store = _make_client()
    store.set_active(license_key="TOP-SECRET-KEY-8888", purchase_email="s@e.com")
    response = client.get("/license")
    body = response.json()
    assert "TOP-SECRET-KEY-8888" not in response.text
    assert "license_key" not in body


def test_get_license_returns_503_when_db_is_busy():
    client, store = _make_client()
    with patch.object(store, "get", side_effect=sqlite3.OperationalError("database is locked")):
        response = client.get("/license")
    assert response.status_code == 503
    assert "busy" in response.json()["detail"].lower()


# --------------------------------------------------------------------------
# Dev-only license bypass (CLIP_PULL_DEV_LICENSE_KEY) -- no real Gumroad
# product exists yet, so this lets the activation flow be exercised locally
# without a network call. Must stay inert whenever the env var isn't set.
# --------------------------------------------------------------------------


def test_activate_with_matching_dev_key_activates_without_calling_gumroad():
    client, store = _make_client()
    with patch("license_routes.DEV_LICENSE_KEY", "DEV-TEST-0000"), \
         patch("license_routes.verify_license") as mock_verify:
        response = client.post("/license/activate", json={"license_key": "DEV-TEST-0000"})
    assert response.status_code == 200
    entry = response.json()["entry"]
    assert entry["status"] == "active"
    assert entry["pro"] is True
    assert store.is_pro() is True
    mock_verify.assert_not_called()


def test_activate_with_non_matching_key_still_goes_through_gumroad_when_dev_key_set():
    client, _ = _make_client()
    fake = {"success": False, "message": "That license does not exist."}
    with patch("license_routes.DEV_LICENSE_KEY", "DEV-TEST-0000"), \
         patch("license_routes.verify_license", return_value=fake) as mock_verify:
        response = client.post("/license/activate", json={"license_key": "SOME-OTHER-KEY"})
    assert response.status_code == 400
    mock_verify.assert_called_once()


def test_activate_dev_bypass_is_inert_when_env_var_unset():
    # DEV_LICENSE_KEY defaults to None (see license_config.py) -- confirms
    # the bypass can't accidentally fire in a build that never sets it.
    client, _ = _make_client()
    with patch("license_routes.DEV_LICENSE_KEY", None), \
         patch("license_routes.verify_license", side_effect=GumroadClientError("boom")) as mock_verify:
        response = client.post("/license/activate", json={"license_key": "DEV-TEST-0000"})
    assert response.status_code == 503
    mock_verify.assert_called_once()
