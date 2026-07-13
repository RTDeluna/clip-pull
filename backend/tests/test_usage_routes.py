import sqlite3
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from usage_routes import DB_BUSY_MESSAGE, build_usage_router
from usage_store import UsageStore


def _make_client(usage_store=None):
    usage_store = usage_store or UsageStore()
    app = FastAPI()
    app.include_router(build_usage_router(usage_store))
    return TestClient(app), usage_store


def test_get_usage_empty_shape():
    client, _ = _make_client()
    resp = client.get("/usage")
    assert resp.status_code == 200
    assert resp.json() == {"providers": {}, "total_calls": 0}


def test_get_usage_includes_estimated_cost_for_known_provider():
    store = UsageStore()
    # gemini-3.5-flash: 0.075/M input, 0.30/M output.
    store.record(
        provider="gemini", operation="summarize", model="gemini-3.5-flash",
        input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000,
    )
    client, _ = _make_client(store)

    body = client.get("/usage").json()
    assert body["total_calls"] == 1
    gemini = body["providers"]["gemini"]
    assert gemini["input_tokens"] == 1_000_000
    assert gemini["output_tokens"] == 1_000_000
    assert gemini["total_tokens"] == 2_000_000
    assert gemini["audio_seconds"] == 0.0
    assert gemini["calls"] == 1
    # 0.075 + 0.30 = 0.375
    assert gemini["estimated_cost_usd"] == 0.375


def test_get_usage_estimates_openai_across_token_and_duration_models():
    # openai spans a token-priced summary model AND a duration-priced Whisper
    # model -- the provider-level estimate sums both contributions.
    store = UsageStore()
    store.record(
        provider="openai", operation="summarize", model="gpt-5-mini",
        input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000,
    )
    store.record(
        provider="openai", operation="transcribe_chunk", model="whisper-1",
        audio_seconds=120.0,
    )
    client, _ = _make_client(store)

    openai = client.get("/usage").json()["providers"]["openai"]
    # gpt-5-mini: 0.25 + 2.00 = 2.25 ; whisper-1: 2 min * 0.006 = 0.012
    assert openai["estimated_cost_usd"] == round(2.25 + 0.012, 6)


def test_get_usage_returns_503_when_db_busy():
    store = UsageStore()
    with patch.object(store, "summary", side_effect=sqlite3.OperationalError("locked")):
        client, _ = _make_client(store)
        resp = client.get("/usage")
    assert resp.status_code == 503
    assert resp.json()["detail"] == DB_BUSY_MESSAGE
