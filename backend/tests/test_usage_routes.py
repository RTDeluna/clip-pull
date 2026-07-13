import sqlite3
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from license_store import LicenseStore
from usage_routes import DB_BUSY_MESSAGE, USAGE_PRO_MESSAGE, build_usage_router
from usage_store import UsageStore


def _make_client(usage_store=None, pro=False):
    usage_store = usage_store or UsageStore()
    license_store = LicenseStore()
    if pro:
        license_store.set_active(license_key="TEST-PRO-KEY", purchase_email=None)
    app = FastAPI()
    app.include_router(build_usage_router(usage_store, license_store))
    return TestClient(app), usage_store, license_store


def test_get_usage_empty_shape():
    client, _, _ = _make_client()
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
    client, _, _ = _make_client(store)

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
    client, _, _ = _make_client(store)

    openai = client.get("/usage").json()["providers"]["openai"]
    # gpt-5-mini: 0.25 + 2.00 = 2.25 ; whisper-1: 2 min * 0.006 = 0.012
    assert openai["estimated_cost_usd"] == round(2.25 + 0.012, 6)


def test_get_usage_returns_503_when_db_busy():
    store = UsageStore()
    with patch.object(store, "summary", side_effect=sqlite3.OperationalError("locked")):
        client, _, _ = _make_client(store)
        resp = client.get("/usage")
    assert resp.status_code == 503
    assert resp.json()["detail"] == DB_BUSY_MESSAGE


def test_get_usage_ignores_pro_status_entirely():
    # /usage stays free/unchanged regardless of license state -- this is
    # the hard regression constraint for the Insights dashboard work.
    client, _, _ = _make_client(pro=True)
    assert client.get("/usage").status_code == 200


# -- GET /usage/dashboard ---------------------------------------------------


def test_dashboard_requires_pro_returns_402_with_preview():
    client, _, _ = _make_client(pro=False)

    resp = client.get("/usage/dashboard")

    assert resp.status_code == 402
    detail = resp.json()["detail"]
    assert detail["message"] == USAGE_PRO_MESSAGE
    assert "trend_sparkline_shape" in detail["preview"]
    assert "provider_recommendation_teaser" in detail["preview"]


def test_dashboard_pro_returns_full_shape():
    store = UsageStore()
    store.record(
        provider="gemini", model="gemini-3.5-flash", operation="transcribe_chunk",
        history_id=1, input_tokens=1000, output_tokens=500, total_tokens=1500,
    )
    client, _, _ = _make_client(store, pro=True)

    resp = client.get("/usage/dashboard")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "range", "kpis", "daily", "providers", "operations", "videos",
        "provider_recommendation", "cost_data_incomplete",
    }
    assert set(body["kpis"]) == {
        "hours_processed", "videos_processed", "total_calls",
        "total_tokens", "audio_seconds", "estimated_cost_usd",
    }
    assert body["kpis"]["total_calls"] == 1
    assert body["kpis"]["videos_processed"] == 1


def test_dashboard_range_7d_excludes_older_rows():
    from datetime import datetime, timedelta, timezone

    store = UsageStore()
    now = datetime.now(timezone.utc)
    store.record(
        provider="gemini", operation="transcribe_chunk", input_tokens=100, total_tokens=100,
        created_at=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    store.record(
        provider="gemini", operation="transcribe_chunk", input_tokens=999, total_tokens=999,
        created_at=(now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    client, _, _ = _make_client(store, pro=True)

    body = client.get("/usage/dashboard?range=7d").json()

    assert body["kpis"]["total_tokens"] == 100
    assert body["range"]["preset"] == "7d"


def test_dashboard_range_all_includes_everything():
    from datetime import datetime, timedelta, timezone

    store = UsageStore()
    now = datetime.now(timezone.utc)
    store.record(
        provider="gemini", operation="transcribe_chunk", input_tokens=100, total_tokens=100,
        created_at=(now - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    client, _, _ = _make_client(store, pro=True)

    body = client.get("/usage/dashboard?range=all").json()
    assert body["kpis"]["total_tokens"] == 100
    assert body["range"] == {"since": None, "until": None, "preset": "all"}


def test_dashboard_invalid_range_returns_400():
    client, _, _ = _make_client(pro=True)
    resp = client.get("/usage/dashboard?range=bogus")
    assert resp.status_code == 400


def test_dashboard_db_busy_returns_503():
    store = UsageStore()
    with patch.object(store, "summary", side_effect=sqlite3.OperationalError("locked")):
        client, _, _ = _make_client(store, pro=True)
        resp = client.get("/usage/dashboard")
    assert resp.status_code == 503
    assert resp.json()["detail"] == DB_BUSY_MESSAGE


def test_dashboard_provider_recommendation_present_when_cheaper_alternative_exists():
    # whisper-1 (openai): 0.006/min. groq's whisper-large-v3-turbo: 0.000667/min.
    # 10 minutes on openai costs $0.06 vs $0.00667 on groq -- a clear,
    # well-above-threshold savings the recommendation should surface.
    store = UsageStore()
    store.record(
        provider="openai", model="whisper-1", operation="transcribe_chunk",
        audio_seconds=600.0,
    )
    client, _, _ = _make_client(store, pro=True)

    body = client.get("/usage/dashboard").json()

    rec = body["provider_recommendation"]
    assert rec is not None
    assert rec["operation"] == "transcribe_chunk"
    assert rec["current_provider"] == "openai"
    assert rec["cheaper_provider"] == "groq"
    assert rec["savings_usd"] > 0.01


def test_dashboard_provider_recommendation_null_when_no_usage():
    client, _, _ = _make_client(pro=True)
    body = client.get("/usage/dashboard").json()
    assert body["provider_recommendation"] is None


def test_dashboard_cost_data_incomplete_true_when_model_unpriced():
    store = UsageStore()
    store.record(
        provider="gemini", model="some-future-model", operation="transcribe_chunk",
        input_tokens=100,
    )
    client, _, _ = _make_client(store, pro=True)

    body = client.get("/usage/dashboard").json()
    assert body["cost_data_incomplete"] is True


def test_dashboard_cost_data_incomplete_false_when_all_models_priced():
    store = UsageStore()
    store.record(
        provider="gemini", model="gemini-3.5-flash", operation="transcribe_chunk",
        input_tokens=100,
    )
    client, _, _ = _make_client(store, pro=True)

    body = client.get("/usage/dashboard").json()
    assert body["cost_data_incomplete"] is False


# -- GET /usage/export.csv ---------------------------------------------------


def test_export_csv_requires_pro():
    client, _, _ = _make_client(pro=False)
    resp = client.get("/usage/export.csv")
    assert resp.status_code == 402
    assert resp.json()["detail"] == USAGE_PRO_MESSAGE


def test_export_csv_returns_csv_with_video_rows(tmp_path):
    from history_store import HistoryStore

    db_path = tmp_path / "clip_pull.db"
    history_store = HistoryStore(db_path)
    store = UsageStore(db_path)
    entry = history_store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="Intro to Marketing",
        output_path="C:/downloads/Intro.mp4", total_size="10.0MB",
        status="done", error_reason=None, retry_count=0,
    )
    store.record(
        provider="gemini", model="gemini-3.5-flash", operation="transcribe_chunk",
        history_id=entry["id"], input_tokens=1_000_000, output_tokens=0,
    )
    client, _, _ = _make_client(store, pro=True)

    resp = client.get("/usage/export.csv")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.strip().splitlines()
    assert lines[0] == "title,url,total_tokens,audio_minutes,calls,estimated_cost_usd"
    assert "Intro to Marketing" in lines[1]
    assert "https://vimeo.com/1" in lines[1]


def test_export_csv_returns_503_when_db_busy():
    store = UsageStore()
    with patch.object(store, "per_video_breakdown", side_effect=sqlite3.OperationalError("locked")):
        client, _, _ = _make_client(store, pro=True)
        resp = client.get("/usage/export.csv")
    assert resp.status_code == 503
    assert resp.json()["detail"] == DB_BUSY_MESSAGE


def test_export_csv_empty_when_no_usage():
    client, _, _ = _make_client(pro=True)
    resp = client.get("/usage/export.csv")
    assert resp.status_code == 200
    lines = resp.text.strip().splitlines()
    assert len(lines) == 1  # header only
