from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_clients import AIClientError
from history_store import HistoryStore
from license_store import LicenseStore
from settings_store import SettingsStore
from transcription import TranscriptionOrchestrator
from transcription_routes import build_transcription_router
from usage_store import UsageStore


async def fake_transcribe_entry(history_id):
    return None


async def fake_summarize_entry(history_id):
    return None


def _make_client(pro=False):
    history_store = HistoryStore()
    settings_store = SettingsStore()
    license_store = LicenseStore()
    if pro:
        license_store.set_active(license_key="TEST-PRO-KEY", purchase_email=None)
    orchestrator = TranscriptionOrchestrator(history_store, settings_store, UsageStore(), license_store)
    orchestrator.transcribe_entry = fake_transcribe_entry
    orchestrator.summarize_entry = fake_summarize_entry
    app = FastAPI()
    app.include_router(
        build_transcription_router(history_store, orchestrator, license_store, settings_store)
    )
    return TestClient(app), history_store, orchestrator


def _seed_done_entry(history_store, output_path="C:/downloads/Video 1.mp4"):
    return history_store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="Video 1",
        output_path=output_path, total_size="10MB", status="done",
        error_reason=None, retry_count=0,
    )


def _make_incomplete_task():
    class FakeTask:
        def done(self):
            return False

    return FakeTask()


# -- Transcription ------------------------------------------------------


def test_start_transcription_returns_202_and_sets_running_status():
    client, history_store, _ = _make_client()
    entry = _seed_done_entry(history_store)

    response = client.post(f"/history/{entry['id']}/transcribe")

    assert response.status_code == 202
    assert response.json()["entry"]["transcript_status"] == "running"
    assert history_store.get(entry["id"])["transcript_status"] == "running"


def test_start_transcription_404s_for_unknown_entry():
    client, _, _ = _make_client()
    response = client.post("/history/999/transcribe")
    assert response.status_code == 404


def test_start_transcription_400s_when_download_not_done():
    client, history_store, _ = _make_client()
    entry = history_store.record(
        entry_id="e1", batch_id=None, url="https://vimeo.com/1", title=None,
        output_path=None, total_size=None, status="error", error_reason="failed",
        retry_count=0,
    )
    response = client.post(f"/history/{entry['id']}/transcribe")
    assert response.status_code == 400
    assert "hasn't finished" in response.json()["detail"]


def test_start_transcription_400s_when_already_transcribing():
    client, history_store, orchestrator = _make_client()
    entry = _seed_done_entry(history_store)
    orchestrator._active_transcription_tasks[entry["id"]] = _make_incomplete_task()

    response = client.post(f"/history/{entry['id']}/transcribe")

    assert response.status_code == 400
    assert "already being transcribed" in response.json()["detail"]


def test_clear_transcript_resets_status_to_none_without_touching_summary():
    client, history_store, _ = _make_client()
    entry = _seed_done_entry(history_store)
    history_store.update_transcript(entry["id"], status="done", transcript="hi")
    history_store.update_summary(entry["id"], status="done", summary="a summary")

    response = client.delete(f"/history/{entry['id']}/transcript")

    assert response.status_code == 200
    assert response.json()["entry"]["transcript_status"] == "none"
    updated = history_store.get(entry["id"])
    assert updated["transcript"] is None
    # Clearing the transcript is independent of the summary -- a distilled
    # summary someone already has is still useful even without the source
    # transcript still being stored.
    assert updated["summary"] == "a summary"


def test_clear_transcript_404s_for_unknown_entry():
    client, _, _ = _make_client()
    response = client.delete("/history/999/transcript")
    assert response.status_code == 404


# -- Summarization --------------------------------------------------------


def test_start_summarization_returns_202_and_sets_running_status():
    client, history_store, _ = _make_client()
    entry = _seed_done_entry(history_store)
    history_store.update_transcript(entry["id"], status="done", transcript="hi")

    response = client.post(f"/history/{entry['id']}/summarize")

    assert response.status_code == 202
    assert response.json()["entry"]["summary_status"] == "running"
    assert history_store.get(entry["id"])["summary_status"] == "running"


def test_start_summarization_404s_for_unknown_entry():
    client, _, _ = _make_client()
    response = client.post("/history/999/summarize")
    assert response.status_code == 404


def test_start_summarization_400s_when_not_yet_transcribed():
    client, history_store, _ = _make_client()
    entry = _seed_done_entry(history_store)  # transcript_status still "none"

    response = client.post(f"/history/{entry['id']}/summarize")

    assert response.status_code == 400
    assert "transcribe it first" in response.json()["detail"]


def test_start_summarization_400s_when_already_summarizing():
    client, history_store, orchestrator = _make_client()
    entry = _seed_done_entry(history_store)
    history_store.update_transcript(entry["id"], status="done", transcript="hi")
    orchestrator._active_summarization_tasks[entry["id"]] = _make_incomplete_task()

    response = client.post(f"/history/{entry['id']}/summarize")

    assert response.status_code == 400
    assert "already being summarized" in response.json()["detail"]


def test_clear_summary_resets_status_to_none_without_touching_transcript():
    client, history_store, _ = _make_client()
    entry = _seed_done_entry(history_store)
    history_store.update_transcript(entry["id"], status="done", transcript="hi")
    history_store.update_summary(entry["id"], status="done", summary="a summary")

    response = client.delete(f"/history/{entry['id']}/summary")

    assert response.status_code == 200
    assert response.json()["entry"]["summary_status"] == "none"
    updated = history_store.get(entry["id"])
    assert updated["summary"] is None
    assert updated["transcript"] == "hi"


def test_clear_summary_404s_for_unknown_entry():
    client, _, _ = _make_client()
    response = client.delete("/history/999/summary")
    assert response.status_code == 404


# -- Export ---------------------------------------------------------------


def _seed_transcribed_entry(history_store, output_path):
    entry = _seed_done_entry(history_store, output_path=str(output_path))
    return history_store.update_transcript(
        entry["id"], status="done",
        transcript="[00:00:00] hello\n[00:00:05] world",
    )


def test_export_returns_200_with_paths_when_pro_and_transcript_exists(tmp_path):
    client, history_store, _ = _make_client(pro=True)
    entry = _seed_transcribed_entry(history_store, tmp_path / "Lesson 1.mp4")

    response = client.post(f"/history/{entry['id']}/export", json={"formats": ["srt", "txt", "md"]})

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert len(paths) == 3
    for path in paths:
        assert Path(path).exists()
        assert Path(path).parent == tmp_path


def test_export_returns_402_when_not_pro(tmp_path):
    client, history_store, _ = _make_client(pro=False)
    entry = _seed_transcribed_entry(history_store, tmp_path / "Lesson 1.mp4")

    response = client.post(f"/history/{entry['id']}/export", json={"formats": ["srt"]})

    assert response.status_code == 402
    assert "Pro" in response.json()["detail"]


def test_export_returns_400_when_no_transcript(tmp_path):
    client, history_store, _ = _make_client(pro=True)
    entry = _seed_done_entry(history_store, output_path=str(tmp_path / "Lesson 1.mp4"))

    response = client.post(f"/history/{entry['id']}/export", json={"formats": ["srt"]})

    assert response.status_code == 400
    assert "transcribe it first" in response.json()["detail"]


def test_export_returns_400_on_invalid_format_value(tmp_path):
    client, history_store, _ = _make_client(pro=True)
    entry = _seed_transcribed_entry(history_store, tmp_path / "Lesson 1.mp4")

    response = client.post(f"/history/{entry['id']}/export", json={"formats": ["srt", "pdf"]})

    assert response.status_code == 400


def test_export_returns_400_on_empty_formats(tmp_path):
    client, history_store, _ = _make_client(pro=True)
    entry = _seed_transcribed_entry(history_store, tmp_path / "Lesson 1.mp4")

    response = client.post(f"/history/{entry['id']}/export", json={"formats": []})

    assert response.status_code == 400


def test_export_404s_for_unknown_entry():
    client, _, _ = _make_client(pro=True)
    response = client.post("/history/999/export", json={"formats": ["srt"]})
    assert response.status_code == 404


# -- Chat -----------------------------------------------------------------


class _FakeChatClient:
    last_answer = "Because the transcript says so."

    def __init__(self, api_key):
        self.api_key = api_key
        self.last_usage = {
            "provider": "anthropic",
            "model": "fake-model",
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "audio_seconds": None,
        }

    def chat(self, transcript_text, question, history=None):
        _FakeChatClient.received = {
            "transcript_text": transcript_text,
            "question": question,
            "history": history,
        }
        return _FakeChatClient.last_answer


def _seed_transcribed_for_chat(history_store, orchestrator):
    entry = _seed_done_entry(history_store)
    history_store.update_transcript(
        entry["id"], status="done", transcript="[00:00:00] hello world"
    )
    orchestrator.settings_store.update(anthropic_api_key="sk-ant")
    orchestrator.summarization_client_classes = {"anthropic": _FakeChatClient}
    return entry


def test_chat_returns_answer_when_pro_and_transcribed():
    client, history_store, orchestrator = _make_client(pro=True)
    entry = _seed_transcribed_for_chat(history_store, orchestrator)

    response = client.post(
        f"/history/{entry['id']}/chat",
        json={
            "question": "what is said?",
            "history": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "Because the transcript says so."}
    assert _FakeChatClient.received["question"] == "what is said?"
    assert _FakeChatClient.received["history"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_chat_returns_402_when_not_pro():
    client, history_store, orchestrator = _make_client(pro=False)
    entry = _seed_transcribed_for_chat(history_store, orchestrator)

    response = client.post(f"/history/{entry['id']}/chat", json={"question": "q?"})

    assert response.status_code == 402
    assert "Pro" in response.json()["detail"]


def test_chat_returns_404_for_unknown_entry():
    client, _, _ = _make_client(pro=True)
    response = client.post("/history/999/chat", json={"question": "q?"})
    assert response.status_code == 404


def test_chat_returns_400_when_not_transcribed():
    client, history_store, _ = _make_client(pro=True)
    entry = _seed_done_entry(history_store)  # transcript_status still "none"

    response = client.post(f"/history/{entry['id']}/chat", json={"question": "q?"})

    assert response.status_code == 400
    assert "transcribe it first" in response.json()["detail"]


def test_chat_returns_400_when_no_api_key_configured():
    client, history_store, _ = _make_client(pro=True)
    entry = _seed_done_entry(history_store)
    history_store.update_transcript(entry["id"], status="done", transcript="hi")
    # summarization_provider defaults to anthropic; no anthropic_api_key set.

    response = client.post(f"/history/{entry['id']}/chat", json={"question": "q?"})

    assert response.status_code == 400
    assert "Anthropic API key" in response.json()["detail"]


def test_chat_returns_502_when_ai_client_fails():
    client, history_store, orchestrator = _make_client(pro=True)
    entry = _seed_done_entry(history_store)
    history_store.update_transcript(entry["id"], status="done", transcript="hi")
    orchestrator.settings_store.update(anthropic_api_key="sk-ant")

    class _FailingChatClient:
        def __init__(self, api_key):
            pass

        def chat(self, transcript_text, question, history=None):
            raise AIClientError("rate limited", provider="anthropic", status_code=429)

    orchestrator.summarization_client_classes = {"anthropic": _FailingChatClient}

    response = client.post(f"/history/{entry['id']}/chat", json={"question": "q?"})

    assert response.status_code == 502
    assert "rate-limited" in response.json()["detail"]


def test_chat_returns_422_on_empty_question():
    client, history_store, orchestrator = _make_client(pro=True)
    entry = _seed_transcribed_for_chat(history_store, orchestrator)

    response = client.post(f"/history/{entry['id']}/chat", json={"question": ""})

    assert response.status_code == 422


def test_chat_caps_history_to_last_20_turns():
    client, history_store, orchestrator = _make_client(pro=True)
    entry = _seed_transcribed_for_chat(history_store, orchestrator)
    long_history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(30)
    ]

    response = client.post(
        f"/history/{entry['id']}/chat", json={"question": "q?", "history": long_history}
    )

    assert response.status_code == 200
    # Only the last 20 turns reach the client (unbounded-payload guardrail).
    assert len(_FakeChatClient.received["history"]) == 20
    assert _FakeChatClient.received["history"][0] == {"role": "user", "content": "m10"}


# -- Batch process --------------------------------------------------------


def test_batch_process_returns_402_when_not_pro():
    client, history_store, _ = _make_client(pro=False)
    _seed_done_entry(history_store)
    response = client.post("/history/batch-process", json={})
    assert response.status_code == 402
    assert "Pro" in response.json()["detail"]


def test_batch_process_starts_all_eligible_when_no_ids_given():
    client, history_store, _ = _make_client(pro=True)
    e1 = _seed_done_entry(history_store, output_path="C:/downloads/a.mp4")
    e2 = _seed_done_entry(history_store, output_path="C:/downloads/b.mp4")
    # e2 already has a transcript -> not eligible for the all-eligible default.
    history_store.update_transcript(e2["id"], status="done", transcript="done already")

    response = client.post("/history/batch-process", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["started"] == [e1["id"]]
    assert e2["id"] not in body["started"]
    assert history_store.get(e1["id"])["transcript_status"] == "running"


def test_batch_process_starts_explicit_ids():
    client, history_store, _ = _make_client(pro=True)
    e1 = _seed_done_entry(history_store, output_path="C:/downloads/a.mp4")

    response = client.post("/history/batch-process", json={"entry_ids": [e1["id"]]})

    assert response.status_code == 200
    assert response.json()["started"] == [e1["id"]]


def test_batch_process_skips_already_running_entry():
    client, history_store, orchestrator = _make_client(pro=True)
    e1 = _seed_done_entry(history_store, output_path="C:/downloads/a.mp4")
    orchestrator._active_transcription_tasks[e1["id"]] = _make_incomplete_task()

    response = client.post("/history/batch-process", json={"entry_ids": [e1["id"]]})

    assert response.status_code == 200
    body = response.json()
    assert body["started"] == []
    assert body["skipped"] == [{"id": e1["id"], "reason": "already being transcribed"}]


def test_batch_process_skips_unknown_and_unfinished_explicit_ids():
    client, history_store, _ = _make_client(pro=True)
    errored = history_store.record(
        entry_id="e1", batch_id=None, url="https://vimeo.com/1", title=None,
        output_path=None, total_size=None, status="error", error_reason="failed", retry_count=0,
    )

    response = client.post("/history/batch-process", json={"entry_ids": [999, errored["id"]]})

    assert response.status_code == 200
    skipped = response.json()["skipped"]
    assert {"id": 999, "reason": "not found"} in skipped
    assert {"id": errored["id"], "reason": "download didn't finish"} in skipped
    assert response.json()["started"] == []
