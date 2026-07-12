import asyncio
import threading

import transcription as transcription_module
from ai_clients import AIClientError
from audio_extraction import AudioExtractionError
from history_store import HistoryStore
from settings_store import SettingsStore
from transcription import TranscriptionOrchestrator, format_timestamp, stitch_transcript


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
    def fake_extract(video_path, work_dir):
        return [f"{work_dir}/chunk_{i:04d}.mp3" for i in range(chunk_count)]
    return fake_extract


def _make_orchestrator(**overrides):
    broadcasts = []
    defaults = dict(
        history_store=HistoryStore(),
        settings_store=SettingsStore(),
        broadcast=lambda message: broadcasts.append(message),
        gemini_client_cls=FakeGeminiClient,
        anthropic_client_cls=FakeAnthropicClient,
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

    def failing_extract(video_path, work_dir):
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
    assert updated["summary"] == "A short summary."
    # summarize_entry never touches the already-set transcript state.
    assert updated["transcript_status"] == "done"
    assert all(b["type"] == "summary_update" for b in broadcasts)
    assert broadcasts[-1]["status"] == "done"
    assert broadcasts[-1]["entry"]["summary"] == "A short summary."


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
