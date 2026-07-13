import asyncio
import json
import threading

import transcription as transcription_module
from ai_clients import AIClientError
from audio_extraction import AudioExtractionError
from history_store import HistoryStore
from license_store import LicenseStore
from settings_store import SettingsStore
from transcription import (
    MAX_SUMMARY_TRANSCRIPT_CHARS,
    TranscriptionOrchestrator,
    format_timestamp,
    parse_structured_notes,
    stitch_transcript,
)
from usage_store import UsageStore


def test_format_timestamp_formats_hh_mm_ss():
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(65) == "00:01:05"
    assert format_timestamp(3661) == "01:01:01"


def test_format_timestamp_clamps_negative_to_zero():
    assert format_timestamp(-5) == "00:00:00"


def test_stitch_transcript_offsets_timestamps_across_chunks():
    chunk_results = [
        {"duration": 10.0, "segments": [{"start": 0.0, "text": "Hello"}, {"start": 5.0, "text": "world"}]},
        {"duration": 8.0, "segments": [{"start": 0.0, "text": "Second chunk"}]},
    ]
    transcript = stitch_transcript(chunk_results)
    lines = transcript.splitlines()
    assert lines[0] == "[00:00:00] Hello"
    assert lines[1] == "[00:00:05] world"
    # Offset by the first chunk's own reported duration (10s), not an estimate.
    assert lines[2] == "[00:00:10] Second chunk"


def test_stitch_transcript_falls_back_to_full_text_when_no_segments():
    chunk_results = [{"duration": 5.0, "text": "Just plain text.", "segments": []}]
    assert stitch_transcript(chunk_results) == "[00:00:00] Just plain text."


def _make_video_file(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video bytes")
    return video


class FakeGeminiClient:
    def __init__(self, api_key):
        self.api_key = api_key

    def transcribe_chunk(self, chunk_path, response_format="verbose_json"):
        return {"text": "hello", "duration": 5.0, "segments": [{"start": 0.0, "text": "hello"}]}


class FakeAnthropicClient:
    def __init__(self, api_key):
        self.api_key = api_key

    def summarize(self, transcript_text):
        return "A short summary."


def _fake_extract_fn_factory(chunk_count=1):
    def fake_extract(video_path, work_dir, provider="gemini"):
        return [f"{work_dir}/chunk_{i:04d}.mp3" for i in range(chunk_count)]
    return fake_extract


def _make_orchestrator(**overrides):
    broadcasts = []
    # Test call sites pass single classes (gemini_client_cls=...) rather
    # than the real constructor's provider->class dicts -- keeps every
    # existing test call site unchanged while still exercising the real
    # provider-keyed dict shape underneath.
    gemini_client_cls = overrides.pop("gemini_client_cls", FakeGeminiClient)
    anthropic_client_cls = overrides.pop("anthropic_client_cls", FakeAnthropicClient)
    defaults = dict(
        history_store=HistoryStore(),
        settings_store=SettingsStore(),
        usage_store=UsageStore(),
        license_store=LicenseStore(),
        broadcast=lambda message: broadcasts.append(message),
        transcription_client_classes={"gemini": gemini_client_cls},
        summarization_client_classes={"anthropic": anthropic_client_cls},
        extract_fn=_fake_extract_fn_factory(1),
    )
    defaults.update(overrides)
    orchestrator = TranscriptionOrchestrator(**defaults)
    return orchestrator, broadcasts


def _seed_done_entry(history_store, output_path):
    return history_store.record(
        entry_id="e1", batch_id=None, url="https://vimeo.com/1", title="Lesson 1",
        output_path=str(output_path), total_size="10MB", status="done",
        error_reason=None, retry_count=0,
    )


def _seed_transcribed_entry(history_store, output_path="/tmp/video.mp4", transcript="[00:00:00] hello"):
    entry = _seed_done_entry(history_store, output_path)
    return history_store.update_transcript(entry["id"], status="done", transcript=transcript)


# -- Transcription ----------------------------------------------------------


def test_transcribe_entry_success_persists_transcript_only(tmp_path):
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="sk-gemini")
    entry = _seed_done_entry(history_store, video)

    orchestrator, broadcasts = _make_orchestrator(history_store=history_store, settings_store=settings_store)
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["transcript_status"] == "done"
    assert "hello" in updated["transcript"]
    # transcribe_entry never touches the independent summary job/state.
    assert updated["summary_status"] == "none"
    assert updated["summary"] is None
    assert all(b["type"] == "transcript_update" for b in broadcasts)
    assert broadcasts[0]["status"] == "running"
    assert broadcasts[-1]["status"] == "done"
    assert broadcasts[-1]["percent"] == 100
    # The final broadcast carries the full row so the frontend can render
    # the finished transcript without a separate fetch.
    assert broadcasts[-1]["entry"]["transcript_status"] == "done"


def test_transcribe_entry_broadcasts_increasing_percent_across_chunks(tmp_path):
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="sk-gemini")
    entry = _seed_done_entry(history_store, video)

    orchestrator, broadcasts = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        extract_fn=_fake_extract_fn_factory(2),
    )
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    percents = [b["percent"] for b in broadcasts if "percent" in b]
    assert percents == sorted(percents)  # monotonically non-decreasing
    assert percents[-1] == 100


def test_transcribe_entry_fails_when_no_gemini_key(tmp_path):
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    entry = _seed_done_entry(history_store, video)

    orchestrator, broadcasts = _make_orchestrator(history_store=history_store, settings_store=settings_store)
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["transcript_status"] == "error"
    assert "Gemini API key" in updated["transcript_error"]
    assert broadcasts[-1]["status"] == "error"
    assert broadcasts[-1]["entry"]["transcript_error"] == updated["transcript_error"]


def test_transcribe_entry_fails_when_download_not_done():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="sk-gemini")
    entry = history_store.record(
        entry_id="e1", batch_id=None, url="https://vimeo.com/1", title=None,
        output_path=None, total_size=None, status="error", error_reason="failed",
        retry_count=0,
    )

    orchestrator, _ = _make_orchestrator(history_store=history_store, settings_store=settings_store)
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["transcript_status"] == "error"
    assert "hasn't finished" in updated["transcript_error"]


def test_transcribe_entry_fails_when_output_file_missing_from_disk(tmp_path):
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="sk-gemini")
    entry = _seed_done_entry(history_store, tmp_path / "does-not-exist.mp4")

    orchestrator, _ = _make_orchestrator(history_store=history_store, settings_store=settings_store)
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["transcript_status"] == "error"
    assert "missing from disk" in updated["transcript_error"]


def test_transcribe_entry_is_a_no_op_for_unknown_history_id():
    orchestrator, broadcasts = _make_orchestrator()
    asyncio.run(orchestrator.transcribe_entry(999))
    assert broadcasts == []


def test_transcribe_entry_persists_friendly_error_on_audio_extraction_failure(tmp_path):
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="sk-gemini")
    entry = _seed_done_entry(history_store, video)

    def failing_extract(video_path, work_dir, provider="gemini"):
        raise AudioExtractionError("ffmpeg isn't available, so audio can't be extracted for transcription.")

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store, extract_fn=failing_extract
    )
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["transcript_status"] == "error"
    assert "ffmpeg isn't available" in updated["transcript_error"]


def test_transcribe_entry_persists_friendly_error_on_non_retryable_api_failure(tmp_path):
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="bad-key")
    entry = _seed_done_entry(history_store, video)

    class UnauthorizedClient:
        def __init__(self, api_key):
            pass

        def transcribe_chunk(self, chunk_path, response_format="verbose_json"):
            raise AIClientError("unauthorized", provider="gemini", status_code=401)

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store, gemini_client_cls=UnauthorizedClient
    )
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["transcript_status"] == "error"
    assert "rejected the API key" in updated["transcript_error"]


def test_transcribe_entry_retries_transient_failures_before_succeeding(tmp_path):
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="sk-gemini")
    entry = _seed_done_entry(history_store, video)

    call_count = {"n": 0}

    class FlakyClient:
        def __init__(self, api_key):
            pass

        def transcribe_chunk(self, chunk_path, response_format="verbose_json"):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise AIClientError("rate limited", provider="gemini", status_code=429)
            return {"text": "recovered", "duration": 3.0, "segments": [{"start": 0.0, "text": "recovered"}]}

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store, gemini_client_cls=FlakyClient
    )
    original_backoff = transcription_module.CHUNK_RETRY_BACKOFF_SECONDS
    transcription_module.CHUNK_RETRY_BACKOFF_SECONDS = 0.001
    try:
        asyncio.run(orchestrator.transcribe_entry(entry["id"]))
    finally:
        transcription_module.CHUNK_RETRY_BACKOFF_SECONDS = original_backoff

    updated = history_store.get(entry["id"])
    assert updated["transcript_status"] == "done"
    assert "recovered" in updated["transcript"]
    assert call_count["n"] == 2


class FakeOpenAIWhisperClient:
    def __init__(self, api_key):
        self.api_key = api_key

    def transcribe_chunk(self, chunk_path, response_format="verbose_json"):
        return {"text": "hi", "duration": 4.0, "segments": [{"start": 0.0, "text": "hi"}]}


def test_transcribe_entry_uses_the_configured_transcription_provider(tmp_path):
    # transcription_provider="openai" must select the OpenAI client and the
    # OpenAI key -- not silently fall back to Gemini.
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(transcription_provider="openai", openai_api_key="sk-openai")
    entry = _seed_done_entry(history_store, video)

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        transcription_client_classes={"openai": FakeOpenAIWhisperClient},
    )
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["transcript_status"] == "done"
    assert "hi" in updated["transcript"]


def test_transcribe_entry_fails_naming_the_configured_providers_missing_key(tmp_path):
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(transcription_provider="openai")  # no openai_api_key set
    entry = _seed_done_entry(history_store, video)

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        transcription_client_classes={"openai": FakeOpenAIWhisperClient},
    )
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["transcript_status"] == "error"
    assert "an OpenAI API key" in updated["transcript_error"]


def test_request_transcription_guards_against_double_start(tmp_path):
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="sk-gemini")
    entry = _seed_done_entry(history_store, video)

    orchestrator, _ = _make_orchestrator(history_store=history_store, settings_store=settings_store)

    async def scenario():
        assert orchestrator.request_transcription(entry["id"]) is True
        task = asyncio.ensure_future(orchestrator.transcribe_entry(entry["id"]))
        # Give the task a tick to register itself before checking the guard.
        await asyncio.sleep(0)
        assert orchestrator.request_transcription(entry["id"]) is False
        await task
        assert orchestrator.request_transcription(entry["id"]) is True

    asyncio.run(scenario())


# -- Summarization ------------------------------------------------------


def test_summarize_entry_success_persists_summary_only():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(anthropic_api_key="sk-anthropic")
    entry = _seed_transcribed_entry(history_store)

    orchestrator, broadcasts = _make_orchestrator(history_store=history_store, settings_store=settings_store)
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["summary_status"] == "done"
    # The summary column now holds a JSON string of the structured-notes shape.
    # The fake returns non-JSON prose, so parse_structured_notes falls back to
    # treating the whole response as the TL;DR.
    stored = json.loads(updated["summary"])
    assert stored == {"tldr": "A short summary.", "key_points": [], "chapters": []}
    # summarize_entry never touches the already-set transcript state.
    assert updated["transcript_status"] == "done"
    assert all(b["type"] == "summary_update" for b in broadcasts)
    assert broadcasts[-1]["status"] == "done"
    assert json.loads(broadcasts[-1]["entry"]["summary"])["tldr"] == "A short summary."


def test_summarize_entry_fails_when_no_anthropic_key():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    entry = _seed_transcribed_entry(history_store)

    orchestrator, broadcasts = _make_orchestrator(history_store=history_store, settings_store=settings_store)
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["summary_status"] == "error"
    assert "Anthropic API key" in updated["summary_error"]
    assert broadcasts[-1]["type"] == "summary_update"


def test_summarize_entry_fails_when_not_yet_transcribed():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(anthropic_api_key="sk-anthropic")
    entry = _seed_done_entry(history_store, "/tmp/video.mp4")  # transcript_status still "none"

    orchestrator, _ = _make_orchestrator(history_store=history_store, settings_store=settings_store)
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["summary_status"] == "error"
    assert "transcribe it first" in updated["summary_error"]


def test_summarize_entry_persists_friendly_error_on_api_failure():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(anthropic_api_key="bad-key")
    entry = _seed_transcribed_entry(history_store)

    class UnauthorizedClient:
        def __init__(self, api_key):
            pass

        def summarize(self, transcript_text):
            raise AIClientError("unauthorized", provider="anthropic", status_code=401)

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store, anthropic_client_cls=UnauthorizedClient
    )
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["summary_status"] == "error"
    assert "rejected the API key" in updated["summary_error"]


class FakeOpenAISummaryClient:
    def __init__(self, api_key):
        self.api_key = api_key

    def summarize(self, transcript_text):
        return "An OpenAI summary."


def test_summarize_entry_uses_the_configured_summarization_provider():
    # summarization_provider="openai" must select the OpenAI client and the
    # OpenAI key -- not silently fall back to Anthropic.
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(summarization_provider="openai", openai_api_key="sk-openai")
    entry = _seed_transcribed_entry(history_store)

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        summarization_client_classes={"openai": FakeOpenAISummaryClient},
    )
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["summary_status"] == "done"
    assert json.loads(updated["summary"])["tldr"] == "An OpenAI summary."


def test_summarize_entry_fails_naming_the_configured_providers_missing_key():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(summarization_provider="openai")  # no openai_api_key set
    entry = _seed_transcribed_entry(history_store)

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        summarization_client_classes={"openai": FakeOpenAISummaryClient},
    )
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["summary_status"] == "error"
    assert "an OpenAI API key" in updated["summary_error"]


def test_summarize_entry_is_a_no_op_for_unknown_history_id():
    orchestrator, broadcasts = _make_orchestrator()
    asyncio.run(orchestrator.summarize_entry(999))
    assert broadcasts == []


class _BlockingAnthropicClient:
    """Blocks inside summarize() (on a real thread, since run_in_executor
    runs it off the event loop) until the test explicitly releases it --
    makes "the job is still in flight" deterministic to observe, unlike
    racing a trivial synchronous fake against a single event-loop tick."""

    def __init__(self, api_key):
        self.release = threading.Event()

    def summarize(self, transcript_text):
        self.release.wait(timeout=5)
        return "A short summary."


def test_request_summarization_guards_against_double_start():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(anthropic_api_key="sk-anthropic")
    entry = _seed_transcribed_entry(history_store)

    blocking_client_holder = {}

    def blocking_client_cls(api_key):
        client = _BlockingAnthropicClient(api_key)
        blocking_client_holder["client"] = client
        return client

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        anthropic_client_cls=blocking_client_cls,
    )

    async def scenario():
        assert orchestrator.request_summarization(entry["id"]) is True
        task = asyncio.ensure_future(orchestrator.summarize_entry(entry["id"]))
        while "client" not in blocking_client_holder:
            await asyncio.sleep(0)
        assert orchestrator.request_summarization(entry["id"]) is False
        blocking_client_holder["client"].release.set()
        await task
        assert orchestrator.request_summarization(entry["id"]) is True

    asyncio.run(scenario())


def test_transcription_and_summarization_guards_are_independent():
    # A summarize job in flight must not block a transcribe request for
    # the same entry, and vice versa -- they're tracked separately.
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="sk-gemini", anthropic_api_key="sk-anthropic")
    entry = _seed_transcribed_entry(history_store)

    blocking_client_holder = {}

    def blocking_client_cls(api_key):
        client = _BlockingAnthropicClient(api_key)
        blocking_client_holder["client"] = client
        return client

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        anthropic_client_cls=blocking_client_cls,
    )

    async def scenario():
        summarize_task = asyncio.ensure_future(orchestrator.summarize_entry(entry["id"]))
        while "client" not in blocking_client_holder:
            await asyncio.sleep(0)
        assert orchestrator.request_summarization(entry["id"]) is False
        assert orchestrator.request_transcription(entry["id"]) is True
        blocking_client_holder["client"].release.set()
        await summarize_task

    asyncio.run(scenario())


def test_summarize_entry_stores_normalized_structured_json():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(anthropic_api_key="sk-anthropic")
    entry = _seed_transcribed_entry(history_store)

    class StructuredClient:
        def __init__(self, api_key):
            pass

        def summarize(self, transcript_text):
            return json.dumps({
                "tldr": "A structured summary.",
                "key_points": [{"seconds": 5, "text": "First point"}],
                "chapters": [{"seconds": 0, "title": "Intro"}],
            })

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        anthropic_client_cls=StructuredClient,
    )
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    stored = json.loads(history_store.get(entry["id"])["summary"])
    assert stored["tldr"] == "A structured summary."
    assert stored["key_points"] == [{"seconds": 5.0, "text": "First point"}]
    assert stored["chapters"] == [{"seconds": 0.0, "title": "Intro"}]


class _StructuredClient:
    def __init__(self, api_key):
        pass

    def summarize(self, transcript_text):
        return json.dumps({
            "tldr": "A structured summary.",
            "key_points": [{"seconds": 5, "text": "First point"}],
            "chapters": [{"seconds": 0, "title": "Intro"}],
        })


def test_summarize_entry_broadcast_strips_key_points_and_chapters_for_free_user():
    # The DB always stores the full structured notes regardless of license
    # status (see the test above) -- only what's broadcast to the frontend
    # is Pro-gated, so a free user's own DevTools/Network tab can't reveal
    # Pro-only content the UI is merely hiding.
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(anthropic_api_key="sk-anthropic")
    entry = _seed_transcribed_entry(history_store)

    orchestrator, broadcasts = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        anthropic_client_cls=_StructuredClient, license_store=LicenseStore(),
    )
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    broadcast_summary = json.loads(broadcasts[-1]["entry"]["summary"])
    assert broadcast_summary["tldr"] == "A structured summary."
    assert broadcast_summary["key_points"] == []
    assert broadcast_summary["chapters"] == []


def test_summarize_entry_broadcast_includes_key_points_and_chapters_for_pro_user():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(anthropic_api_key="sk-anthropic")
    entry = _seed_transcribed_entry(history_store)

    pro_license_store = LicenseStore()
    pro_license_store.set_active(license_key="TEST-PRO-KEY", purchase_email=None)
    orchestrator, broadcasts = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        anthropic_client_cls=_StructuredClient, license_store=pro_license_store,
    )
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    broadcast_summary = json.loads(broadcasts[-1]["entry"]["summary"])
    assert broadcast_summary["key_points"] == [{"seconds": 5.0, "text": "First point"}]
    assert broadcast_summary["chapters"] == [{"seconds": 0.0, "title": "Intro"}]


def test_summarize_entry_truncates_overlong_transcript_before_sending():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(anthropic_api_key="sk-anthropic")
    long_transcript = "[00:00:00] " + "word " * 20000  # far over the char cap
    assert len(long_transcript) > MAX_SUMMARY_TRANSCRIPT_CHARS
    entry = _seed_transcribed_entry(history_store, transcript=long_transcript)

    received = {}

    class RecordingClient:
        def __init__(self, api_key):
            pass

        def summarize(self, transcript_text):
            received["text"] = transcript_text
            return '{"tldr": "ok", "key_points": [], "chapters": []}'

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        anthropic_client_cls=RecordingClient,
    )
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    sent = received["text"]
    marker = "\n\n[Transcript truncated for length.]"
    assert sent.endswith(marker)
    assert len(sent) == MAX_SUMMARY_TRANSCRIPT_CHARS + len(marker)


# -- parse_structured_notes ---------------------------------------------


def test_parse_structured_notes_parses_valid_json():
    raw = json.dumps({
        "tldr": "A summary.",
        "key_points": [{"seconds": 125, "text": "A point"}],
        "chapters": [{"seconds": 0, "title": "Intro"}],
    })
    assert parse_structured_notes(raw) == {
        "tldr": "A summary.",
        "key_points": [{"seconds": 125.0, "text": "A point"}],
        "chapters": [{"seconds": 0.0, "title": "Intro"}],
    }


def test_parse_structured_notes_strips_markdown_code_fence():
    raw = '```json\n{"tldr": "Fenced summary.", "key_points": [], "chapters": []}\n```'
    result = parse_structured_notes(raw)
    assert result["tldr"] == "Fenced summary."
    assert result["key_points"] == []


def test_parse_structured_notes_falls_back_on_malformed_json():
    assert parse_structured_notes("This is not JSON at all.") == {
        "tldr": "This is not JSON at all.", "key_points": [], "chapters": [],
    }


def test_parse_structured_notes_falls_back_when_not_a_dict():
    assert parse_structured_notes("[1, 2, 3]") == {
        "tldr": "[1, 2, 3]", "key_points": [], "chapters": [],
    }


def test_parse_structured_notes_falls_back_when_tldr_missing():
    result = parse_structured_notes('{"key_points": []}')
    assert result["tldr"] == '{"key_points": []}'
    assert result["key_points"] == []


def test_parse_structured_notes_drops_malformed_entries_not_whole_response():
    raw = json.dumps({
        "tldr": "A summary.",
        "key_points": [
            {"seconds": 10, "text": "kept"},
            {"seconds": 20},                  # missing text -> dropped
            {"text": "no seconds"},           # missing seconds -> dropped
            {"seconds": "bad", "text": "x"},  # non-numeric seconds -> dropped
            "not a dict",                     # dropped
        ],
        "chapters": [
            {"seconds": 0, "title": "kept chapter"},
            {"seconds": 5},                   # missing title -> dropped
        ],
    })
    result = parse_structured_notes(raw)
    assert result["tldr"] == "A summary."
    assert result["key_points"] == [{"seconds": 10.0, "text": "kept"}]
    assert result["chapters"] == [{"seconds": 0.0, "title": "kept chapter"}]


def test_parse_structured_notes_defaults_missing_lists_to_empty():
    assert parse_structured_notes('{"tldr": "Only a tldr."}') == {
        "tldr": "Only a tldr.", "key_points": [], "chapters": [],
    }


# -- Usage recording ----------------------------------------------------


class _UsageReportingGeminiClient:
    def __init__(self, api_key):
        self.last_usage = {
            "provider": "gemini", "model": "gemini-3.5-flash",
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
            "audio_seconds": None,
        }

    def transcribe_chunk(self, chunk_path, response_format="verbose_json"):
        return {"text": "hello", "duration": 5.0, "segments": [{"start": 0.0, "text": "hello"}]}


class _UsageReportingAnthropicClient:
    def __init__(self, api_key):
        self.last_usage = {
            "provider": "anthropic", "model": "claude-sonnet-4-5",
            "input_tokens": 20, "output_tokens": 8, "total_tokens": 28,
            "audio_seconds": None,
        }

    def summarize(self, transcript_text):
        return "A short summary."


def _raising_record(**kwargs):
    raise RuntimeError("usage store exploded")


def test_transcribe_entry_records_usage_from_client_last_usage(tmp_path):
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="sk-gemini")
    entry = _seed_done_entry(history_store, video)
    usage_store = UsageStore()

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        usage_store=usage_store, gemini_client_cls=_UsageReportingGeminiClient,
    )
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    summary = usage_store.summary()
    assert summary["total_calls"] == 1
    assert summary["providers"]["gemini"]["input_tokens"] == 10
    assert summary["providers"]["gemini"]["total_tokens"] == 15


class _PerChunkUsageGeminiClient:
    """Reports a usage value unique to whichever chunk file it's asked to
    transcribe (derived from the chunk's own filename), rather than a fixed
    value. Used to prove concurrent chunks don't clobber each other's
    recorded usage -- if _transcribe_chunks ever went back to sharing one
    client instance across concurrently-running chunks, one chunk's
    last_usage could be overwritten by another's before it's read, and the
    summed totals below would come out wrong (not just off by a little --
    duplicated/dropped values), even though this test's chunks all "arrive"
    around the same time via asyncio.gather."""

    def __init__(self, api_key):
        self.api_key = api_key
        self.last_usage = None

    def transcribe_chunk(self, chunk_path, response_format="verbose_json"):
        index = int(str(chunk_path).rsplit("_", 1)[-1].split(".")[0])
        tokens = 10 * (index + 1)
        self.last_usage = {
            "provider": "gemini", "model": "gemini-3.5-flash",
            "input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens,
            "audio_seconds": None,
        }
        return {"text": "hello", "duration": 5.0, "segments": [{"start": 0.0, "text": "hello"}]}


def test_transcribe_entry_records_distinct_usage_per_concurrent_chunk(tmp_path):
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="sk-gemini")
    entry = _seed_done_entry(history_store, video)
    usage_store = UsageStore()

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        usage_store=usage_store, gemini_client_cls=_PerChunkUsageGeminiClient,
        extract_fn=_fake_extract_fn_factory(3),
    )
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    summary = usage_store.summary()
    # Chunks report 10, 20, 30 input tokens respectively -- if any chunk's
    # usage were dropped or duplicated due to shared client state, this sum
    # would land somewhere other than the exact total of all three.
    assert summary["total_calls"] == 3
    assert summary["providers"]["gemini"]["input_tokens"] == 60
    assert summary["providers"]["gemini"]["total_tokens"] == 60


def test_summarize_entry_records_usage_from_client_last_usage():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(anthropic_api_key="sk-anthropic")
    entry = _seed_transcribed_entry(history_store)
    usage_store = UsageStore()

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        usage_store=usage_store, anthropic_client_cls=_UsageReportingAnthropicClient,
    )
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    summary = usage_store.summary()
    assert summary["total_calls"] == 1
    assert summary["providers"]["anthropic"]["input_tokens"] == 20
    assert summary["providers"]["anthropic"]["total_tokens"] == 28


def test_transcribe_entry_completes_even_when_usage_recording_fails(tmp_path):
    # Usage telemetry is best-effort -- a failure recording it must never
    # fail or interrupt the actual transcription job.
    video = _make_video_file(tmp_path)
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(gemini_api_key="sk-gemini")
    entry = _seed_done_entry(history_store, video)
    usage_store = UsageStore()
    usage_store.record = _raising_record

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        usage_store=usage_store, gemini_client_cls=_UsageReportingGeminiClient,
    )
    asyncio.run(orchestrator.transcribe_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["transcript_status"] == "done"
    assert "hello" in updated["transcript"]


def test_summarize_entry_completes_even_when_usage_recording_fails():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    settings_store.update(anthropic_api_key="sk-anthropic")
    entry = _seed_transcribed_entry(history_store)
    usage_store = UsageStore()
    usage_store.record = _raising_record

    orchestrator, _ = _make_orchestrator(
        history_store=history_store, settings_store=settings_store,
        usage_store=usage_store, anthropic_client_cls=_UsageReportingAnthropicClient,
    )
    asyncio.run(orchestrator.summarize_entry(entry["id"]))

    updated = history_store.get(entry["id"])
    assert updated["summary_status"] == "done"
    assert json.loads(updated["summary"])["tldr"] == "A short summary."


# -- Auto-process chaining (transcribe_and_maybe_summarize) -----------------


def _orchestrator_with_fake_jobs():
    history_store = HistoryStore()
    entry = _seed_done_entry(history_store, "/tmp/video.mp4")
    orchestrator, _ = _make_orchestrator(history_store=history_store)
    return orchestrator, history_store, entry


def test_transcribe_and_maybe_summarize_chains_summary_after_success():
    orchestrator, history_store, entry = _orchestrator_with_fake_jobs()
    calls = []

    async def fake_transcribe(history_id):
        history_store.update_transcript(history_id, status="done", transcript="[00:00:00] hi")
        calls.append(("transcribe", history_id))

    async def fake_summarize(history_id):
        calls.append(("summarize", history_id))

    orchestrator.transcribe_entry = fake_transcribe
    orchestrator.summarize_entry = fake_summarize

    asyncio.run(orchestrator.transcribe_and_maybe_summarize(entry["id"], summarize=True))

    assert calls == [("transcribe", entry["id"]), ("summarize", entry["id"])]


def test_transcribe_and_maybe_summarize_skips_summary_after_failed_transcription():
    orchestrator, history_store, entry = _orchestrator_with_fake_jobs()
    summarize_calls = []

    async def fake_transcribe(history_id):
        # Transcription failed -> the row lands in 'error', so nothing to summarize.
        history_store.update_transcript(history_id, status="error", error="boom")

    async def fake_summarize(history_id):
        summarize_calls.append(history_id)

    orchestrator.transcribe_entry = fake_transcribe
    orchestrator.summarize_entry = fake_summarize

    asyncio.run(orchestrator.transcribe_and_maybe_summarize(entry["id"], summarize=True))

    assert summarize_calls == []


def test_transcribe_and_maybe_summarize_skips_summary_when_not_requested():
    orchestrator, history_store, entry = _orchestrator_with_fake_jobs()
    summarize_calls = []

    async def fake_transcribe(history_id):
        history_store.update_transcript(history_id, status="done", transcript="[00:00:00] hi")

    async def fake_summarize(history_id):
        summarize_calls.append(history_id)

    orchestrator.transcribe_entry = fake_transcribe
    orchestrator.summarize_entry = fake_summarize

    asyncio.run(orchestrator.transcribe_and_maybe_summarize(entry["id"], summarize=False))

    assert summarize_calls == []
