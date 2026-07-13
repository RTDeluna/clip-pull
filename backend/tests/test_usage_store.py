from usage_store import UsageStore


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
