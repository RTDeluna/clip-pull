from history_store import HistoryStore
from usage_store import UsageStore


def _record_history(store, url="https://vimeo.com/1", entry_id="e1", title="Test Video", **overrides):
    defaults = dict(
        entry_id=entry_id, batch_id="b1", url=url, title=title,
        output_path="C:/downloads/Test Video.mp4", total_size="10.0MB",
        status="done", error_reason=None, retry_count=0,
    )
    defaults.update(overrides)
    return store.record(**defaults)


def test_record_and_summary_aggregates_by_provider():
    store = UsageStore()
    store.record(
        provider="gemini", operation="transcribe_chunk", model="gemini-3.5-flash",
        history_id=1, input_tokens=100, output_tokens=50, total_tokens=150,
    )
    store.record(
        provider="gemini", operation="summarize", model="gemini-3.5-flash",
        history_id=1, input_tokens=200, output_tokens=80, total_tokens=280,
    )
    store.record(
        provider="openai", operation="transcribe_chunk", model="whisper-1",
        history_id=2, audio_seconds=30.0,
    )

    summary = store.summary()

    assert summary["total_calls"] == 3
    gemini = summary["providers"]["gemini"]
    assert gemini["input_tokens"] == 300
    assert gemini["output_tokens"] == 130
    assert gemini["total_tokens"] == 430
    # All gemini rows had NULL audio_seconds -> summed to 0.0, not NULL.
    assert gemini["audio_seconds"] == 0.0
    assert gemini["calls"] == 2

    openai = summary["providers"]["openai"]
    # Whisper billed by duration -- token totals are 0, audio_seconds carries.
    assert openai["input_tokens"] == 0
    assert openai["output_tokens"] == 0
    assert openai["total_tokens"] == 0
    assert openai["audio_seconds"] == 30.0
    assert openai["calls"] == 1


def test_summary_omits_providers_with_no_usage():
    store = UsageStore()
    store.record(
        provider="anthropic", operation="summarize", model="claude-sonnet-4-5",
        input_tokens=10, output_tokens=5, total_tokens=15,
    )

    summary = store.summary()

    assert set(summary["providers"]) == {"anthropic"}
    assert "gemini" not in summary["providers"]


def test_summary_is_empty_when_nothing_recorded():
    store = UsageStore()
    assert store.summary() == {"providers": {}, "total_calls": 0}


def test_record_defaults_all_optional_fields_to_null():
    store = UsageStore()
    store.record(provider="groq", operation="transcribe_chunk")

    groq = store.summary()["providers"]["groq"]
    assert groq["calls"] == 1
    assert groq["input_tokens"] == 0
    assert groq["output_tokens"] == 0
    assert groq["total_tokens"] == 0
    assert groq["audio_seconds"] == 0.0


def test_summary_accumulates_audio_seconds_across_rows():
    store = UsageStore()
    store.record(provider="openai", operation="transcribe_chunk", model="whisper-1", audio_seconds=10.5)
    store.record(provider="openai", operation="transcribe_chunk", model="whisper-1", audio_seconds=4.5)

    openai = store.summary()["providers"]["openai"]
    assert openai["audio_seconds"] == 15.0
    assert openai["calls"] == 2


def test_record_accepts_explicit_created_at():
    store = UsageStore()
    store.record(provider="gemini", operation="transcribe_chunk", created_at="2026-01-01 00:00:00")

    row = store._conn.execute("SELECT created_at FROM ai_usage").fetchone()
    assert row["created_at"] == "2026-01-01 00:00:00"


def test_summary_respects_since_until_range():
    store = UsageStore()
    store.record(
        provider="gemini", operation="transcribe_chunk", input_tokens=100,
        created_at="2026-01-01 00:00:00",
    )
    store.record(
        provider="gemini", operation="transcribe_chunk", input_tokens=200,
        created_at="2026-02-01 00:00:00",
    )

    ranged = store.summary(since="2026-01-15", until="2026-02-15")
    assert ranged["providers"]["gemini"]["input_tokens"] == 200
    assert ranged["total_calls"] == 1

    # No-arg call is still the untouched lifetime total -- the free /usage
    # endpoint's behavior must not change.
    lifetime = store.summary()
    assert lifetime["providers"]["gemini"]["input_tokens"] == 300
    assert lifetime["total_calls"] == 2


def test_daily_breakdown_groups_by_day():
    store = UsageStore()
    store.record(
        provider="gemini", model="gemini-3.5-flash", operation="transcribe_chunk",
        input_tokens=100, created_at="2026-01-01 10:00:00",
    )
    store.record(
        provider="gemini", model="gemini-3.5-flash", operation="transcribe_chunk",
        input_tokens=50, created_at="2026-01-01 15:00:00",
    )
    store.record(
        provider="gemini", model="gemini-3.5-flash", operation="transcribe_chunk",
        input_tokens=10, created_at="2026-01-02 09:00:00",
    )

    rows = store.daily_breakdown()

    by_date = {row["date"]: row for row in rows}
    assert by_date["2026-01-01"]["input_tokens"] == 150
    assert by_date["2026-01-01"]["calls"] == 2
    assert by_date["2026-01-02"]["input_tokens"] == 10
    assert [row["date"] for row in rows] == ["2026-01-01", "2026-01-02"]  # ascending


def test_daily_breakdown_respects_since_until_range():
    store = UsageStore()
    store.record(provider="gemini", operation="transcribe_chunk", created_at="2026-01-01 00:00:00")
    store.record(provider="gemini", operation="transcribe_chunk", created_at="2026-03-01 00:00:00")

    rows = store.daily_breakdown(since="2026-02-01", until="2026-04-01")
    assert [row["date"] for row in rows] == ["2026-03-01"]


def test_operation_breakdown_groups_by_operation_and_provider():
    store = UsageStore()
    store.record(provider="gemini", model="gemini-3.5-flash", operation="transcribe_chunk", input_tokens=100)
    store.record(provider="anthropic", model="claude-sonnet-4-5", operation="summarize", input_tokens=40)
    store.record(provider="anthropic", model="claude-sonnet-4-5", operation="summarize", input_tokens=60)

    rows = store.operation_breakdown()

    by_op = {(row["operation"], row["provider"]): row for row in rows}
    assert by_op[("transcribe_chunk", "gemini")]["input_tokens"] == 100
    assert by_op[("summarize", "anthropic")]["input_tokens"] == 100
    assert by_op[("summarize", "anthropic")]["calls"] == 2


def test_per_video_breakdown_joins_history_title(tmp_path):
    db_path = tmp_path / "clip_pull.db"
    history_store = HistoryStore(db_path)
    usage_store = UsageStore(db_path)
    entry = _record_history(history_store, title="Intro to Marketing")

    usage_store.record(
        provider="gemini", model="gemini-3.5-flash", operation="transcribe_chunk",
        history_id=entry["id"], input_tokens=100,
    )

    rows = usage_store.per_video_breakdown()

    assert len(rows) == 1
    assert rows[0]["history_id"] == entry["id"]
    assert rows[0]["title"] == "Intro to Marketing"
    assert rows[0]["input_tokens"] == 100


def test_per_video_breakdown_excludes_course_scoped_calls():
    # course_chat/course_digest calls carry history_id=None -- they aren't
    # attributable to a single video and must not appear here.
    store = UsageStore()
    store.record(
        provider="anthropic", model="claude-sonnet-4-5", operation="course_chat",
        history_id=None, input_tokens=100,
    )

    assert store.per_video_breakdown() == []


def test_per_video_breakdown_handles_deleted_history_row(tmp_path):
    db_path = tmp_path / "clip_pull.db"
    history_store = HistoryStore(db_path)
    usage_store = UsageStore(db_path)
    entry = _record_history(history_store)
    usage_store.record(
        provider="gemini", model="gemini-3.5-flash", operation="transcribe_chunk",
        history_id=entry["id"], input_tokens=100,
    )

    history_store.delete(entry["id"])

    rows = usage_store.per_video_breakdown()
    assert len(rows) == 1
    assert rows[0]["history_id"] == entry["id"]
    # LEFT JOIN -- the usage row survives, title/url just come back None so
    # the caller can render a "Removed from history" fallback.
    assert rows[0]["title"] is None
    assert rows[0]["url"] is None


def test_per_video_breakdown_respects_limit(tmp_path):
    db_path = tmp_path / "clip_pull.db"
    history_store = HistoryStore(db_path)
    usage_store = UsageStore(db_path)
    for i in range(3):
        entry = _record_history(history_store, url=f"https://vimeo.com/{i}", entry_id=f"e{i}")
        usage_store.record(
            provider="gemini", model="gemini-3.5-flash", operation="transcribe_chunk",
            history_id=entry["id"], input_tokens=10,
        )

    rows = usage_store.per_video_breakdown(limit=2)
    assert len(rows) == 2


def test_distinct_video_count(tmp_path):
    db_path = tmp_path / "clip_pull.db"
    history_store = HistoryStore(db_path)
    usage_store = UsageStore(db_path)
    entry1 = _record_history(history_store, url="https://vimeo.com/1", entry_id="e1")
    entry2 = _record_history(history_store, url="https://vimeo.com/2", entry_id="e2")
    usage_store.record(provider="gemini", operation="transcribe_chunk", history_id=entry1["id"])
    usage_store.record(provider="anthropic", operation="summarize", history_id=entry1["id"])
    usage_store.record(provider="gemini", operation="transcribe_chunk", history_id=entry2["id"])
    usage_store.record(provider="anthropic", operation="course_chat", history_id=None)

    assert usage_store.distinct_video_count() == 2


def test_distinct_video_count_is_zero_when_nothing_recorded():
    assert UsageStore().distinct_video_count() == 0
