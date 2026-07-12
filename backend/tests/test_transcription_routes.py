from fastapi import FastAPI
from fastapi.testclient import TestClient

from history_store import HistoryStore
from settings_store import SettingsStore
from transcription import TranscriptionOrchestrator
from transcription_routes import build_transcription_router


async def fake_transcribe_entry(history_id):
    return None


async def fake_summarize_entry(history_id):
    return None


def _make_client():
    history_store = HistoryStore()
    settings_store = SettingsStore()
    orchestrator = TranscriptionOrchestrator(history_store, settings_store)
    orchestrator.transcribe_entry = fake_transcribe_entry
    orchestrator.summarize_entry = fake_summarize_entry
    app = FastAPI()
    app.include_router(build_transcription_router(history_store, orchestrator))
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
