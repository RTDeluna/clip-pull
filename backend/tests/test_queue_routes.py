from fastapi import FastAPI
from fastapi.testclient import TestClient

from queue_manager import QueueManager
from downloader import DownloadOrchestrator
from history_store import HistoryStore
from settings_store import SettingsStore
from queue_routes import build_queue_router, AppState


async def fake_download_all(entry_ids, output_folder, referer=None):
    return None


def _make_client():
    queue_manager = QueueManager()
    orchestrator = DownloadOrchestrator(queue_manager)
    orchestrator.download_all = fake_download_all
    history_store = HistoryStore()
    settings_store = SettingsStore()
    state = AppState()
    app = FastAPI()
    app.include_router(
        build_queue_router(queue_manager, orchestrator, history_store, settings_store, state)
    )
    return TestClient(app), queue_manager, orchestrator, history_store, settings_store


def test_post_queue_creates_entries_for_valid_urls():
    client, _, _, _, _ = _make_client()
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/111\nhttps://vimeo.com/222",
            "output_folder": "C:/downloads",
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert len(body["entries"]) == 2
    assert body["invalid_lines"] == []
    assert body["entries"][0]["status"] == "queued"


def test_post_queue_reports_invalid_lines_without_blocking_valid_ones():
    client, _, _, _, _ = _make_client()
    response = client.post(
        "/queue",
        json={"urls_text": "https://vimeo.com/111\nnot a url", "output_folder": "C:/downloads"},
    )
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["invalid_lines"] == ["not a url"]


def test_post_queue_accepts_non_vimeo_urls_like_loom():
    client, _, _, _, _ = _make_client()
    response = client.post(
        "/queue",
        json={"urls_text": "https://www.loom.com/share/abc123", "output_folder": "C:/downloads"},
    )
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["invalid_lines"] == []


def test_get_queue_returns_current_entries():
    client, _, _, _, _ = _make_client()
    client.post(
        "/queue", json={"urls_text": "https://vimeo.com/333", "output_folder": "C:/downloads"}
    )
    response = client.get("/queue")
    urls = [entry["url"] for entry in response.json()["entries"]]
    assert "https://vimeo.com/333" in urls


def test_retry_entry_resets_status_to_queued():
    client, queue_manager, _, _, _ = _make_client()
    post_response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/444", "output_folder": "C:/downloads"}
    )
    entry_id = post_response.json()["entries"][0]["id"]
    queue_manager.set_error(entry_id, "some error")

    response = client.post(f"/queue/{entry_id}/retry", json={})
    assert response.status_code == 202
    assert response.json()["entry"]["status"] == "queued"


def test_pause_entry_flags_the_active_download_for_cancellation():
    client, queue_manager, orchestrator, _, _ = _make_client()
    post_response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/555", "output_folder": "C:/downloads"}
    )
    entry_id = post_response.json()["entries"][0]["id"]
    queue_manager.set_status(entry_id, "downloading")

    class FakeTask:
        def done(self):
            return False

    orchestrator._active_tasks[entry_id] = FakeTask()

    response = client.post(f"/queue/{entry_id}/pause")
    assert response.status_code == 202
    assert entry_id in orchestrator._pause_requested


def test_pause_entry_is_a_no_op_when_nothing_is_actively_downloading():
    client, _, _, _, _ = _make_client()
    post_response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/556", "output_folder": "C:/downloads"}
    )
    entry_id = post_response.json()["entries"][0]["id"]

    response = client.post(f"/queue/{entry_id}/pause")
    assert response.status_code == 202
    assert response.json()["entry"]["status"] == "queued"


def test_resume_entry_triggers_a_new_download_using_the_entrys_output_folder():
    client, queue_manager, orchestrator, _, _ = _make_client()

    post_response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/557", "output_folder": "C:/folder-a"}
    )
    entry_id = post_response.json()["entries"][0]["id"]
    queue_manager.mark_paused(entry_id)

    calls = []

    async def recording_download_all(entry_ids, output_folder, referer=None):
        calls.append((entry_ids, output_folder))

    # Swap the recording fake onto this test's orchestrator instance only
    # after the initial queue POST above, so its own (unrelated) download_all
    # call isn't captured too.
    orchestrator.download_all = recording_download_all

    response = client.post(f"/queue/{entry_id}/resume", json={})
    assert response.status_code == 202
    assert calls == [([entry_id], "C:/folder-a")]


def test_resume_entry_does_not_reset_progress_fields():
    client, queue_manager, _, _, _ = _make_client()
    post_response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/558", "output_folder": "C:/downloads"}
    )
    entry_id = post_response.json()["entries"][0]["id"]
    queue_manager.update_progress(entry_id, 42.0, "1MiB/s", 30, "42MB", "100MB")
    queue_manager.mark_paused(entry_id)

    response = client.post(f"/queue/{entry_id}/resume", json={})
    assert response.status_code == 202
    entry = response.json()["entry"]
    assert entry["percent"] == 42.0
    assert entry["downloaded_size"] == "42MB"
    assert entry["total_size"] == "100MB"


def test_post_queue_requires_confirmation_when_duplicate_present_and_setting_disabled():
    client, _, _, history_store, _ = _make_client()
    history_store.record(
        entry_id="e0", batch_id=None, url="https://vimeo.com/999", title="Old",
        output_path="C:/out/Old.mp4", total_size="10MB", status="done",
        error_reason=None, retry_count=0,
    )
    response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/999", "output_folder": "C:/downloads"}
    )
    body = response.json()
    assert body["entries"] == []
    assert body["needs_confirmation"] is True
    assert body["duplicate_urls"] == ["https://vimeo.com/999"]


def test_post_queue_queues_all_urls_when_duplicate_action_is_queue_all():
    client, _, _, history_store, _ = _make_client()
    history_store.record(
        entry_id="e0", batch_id=None, url="https://vimeo.com/999", title="Old",
        output_path="C:/out/Old.mp4", total_size="10MB", status="done",
        error_reason=None, retry_count=0,
    )
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/999",
            "output_folder": "C:/downloads",
            "duplicate_action": "queue_all",
        },
    )
    body = response.json()
    assert body["needs_confirmation"] is False
    assert len(body["entries"]) == 1
    assert body["entries"][0]["previously_downloaded"] is True


def test_post_queue_skips_only_duplicates_when_duplicate_action_is_skip_duplicates():
    client, _, _, history_store, _ = _make_client()
    history_store.record(
        entry_id="e0", batch_id=None, url="https://vimeo.com/999", title="Old",
        output_path="C:/out/Old.mp4", total_size="10MB", status="done",
        error_reason=None, retry_count=0,
    )
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/999\nhttps://vimeo.com/111",
            "output_folder": "C:/downloads",
            "duplicate_action": "skip_duplicates",
        },
    )
    body = response.json()
    assert body["needs_confirmation"] is False
    assert len(body["entries"]) == 1
    assert body["entries"][0]["url"] == "https://vimeo.com/111"
    assert body["skipped_duplicate_urls"] == ["https://vimeo.com/999"]


def test_post_queue_no_confirmation_needed_when_no_duplicates():
    client, _, _, _, _ = _make_client()
    response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/111", "output_folder": "C:/downloads"}
    )
    body = response.json()
    assert body["needs_confirmation"] is False
    assert len(body["entries"]) == 1


def test_post_queue_skips_duplicates_when_setting_enabled():
    client, _, _, history_store, settings_store = _make_client()
    settings_store.update(skip_duplicates=True)
    history_store.record(
        entry_id="e0", batch_id=None, url="https://vimeo.com/999", title="Old",
        output_path="C:/out/Old.mp4", total_size="10MB", status="done",
        error_reason=None, retry_count=0,
    )
    response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/999", "output_folder": "C:/downloads"}
    )
    body = response.json()
    assert body["entries"] == []
    assert body["skipped_duplicate_urls"] == ["https://vimeo.com/999"]
    assert body["needs_confirmation"] is False


def test_post_queue_skips_urls_already_active_in_the_live_queue():
    client, _, _, _, _ = _make_client()
    client.post(
        "/queue", json={"urls_text": "https://vimeo.com/999", "output_folder": "C:/downloads"}
    )
    response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/999", "output_folder": "C:/downloads"}
    )
    body = response.json()
    assert body["entries"] == []
    assert body["skipped_inflight_urls"] == ["https://vimeo.com/999"]


def test_post_queue_generates_shared_batch_id_for_all_entries_in_one_request():
    client, _, _, _, _ = _make_client()
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/1\nhttps://vimeo.com/2",
            "output_folder": "C:/downloads",
        },
    )
    entries = response.json()["entries"]
    assert entries[0]["batch_id"] == entries[1]["batch_id"]
    assert entries[0]["batch_id"] is not None


def test_post_queue_creates_subfolder_when_subfolder_name_provided(tmp_path):
    client, _, _, _, _ = _make_client()
    base_folder = tmp_path / "downloads"
    base_folder.mkdir()
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/1",
            "output_folder": str(base_folder),
            "subfolder": "My Course",
        },
    )
    entries = response.json()["entries"]
    expected_folder = str(base_folder / "My Course")
    assert entries[0]["output_folder"] == expected_folder
    assert (base_folder / "My Course").is_dir()


def test_post_queue_flat_folder_when_subfolder_omitted(tmp_path):
    client, _, _, _, _ = _make_client()
    base_folder = tmp_path / "downloads"
    base_folder.mkdir()
    response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/1", "output_folder": str(base_folder)}
    )
    entries = response.json()["entries"]
    assert entries[0]["output_folder"] == str(base_folder)


def test_retry_entry_uses_entrys_own_output_folder_not_global_state():
    client, queue_manager, orchestrator, _, _ = _make_client()

    # Track the output_folder argument passed to download_all during retry
    calls = []

    async def recording_download_all(entry_ids, output_folder, referer=None):
        calls.append(output_folder)

    # Swap the recording fake onto this test's orchestrator instance
    orchestrator.download_all = recording_download_all

    # Create first entry with folder-a and put it in error state
    first = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/1", "output_folder": "C:/folder-a"}
    )
    entry_id = first.json()["entries"][0]["id"]
    queue_manager.set_error(entry_id, "boom")

    # Create a second entry with folder-b (to test that we use the entry's own folder, not global state)
    client.post(
        "/queue", json={"urls_text": "https://vimeo.com/2", "output_folder": "C:/folder-b"}
    )

    # Retry the first entry
    response = client.post(f"/queue/{entry_id}/retry", json={})
    assert response.status_code == 202

    # Assert that the output_folder argument captured from the retry's download_all call
    # matches the entry's original folder, not the global state (folder-b)
    assert len(calls) > 0, "download_all was not called during retry"
    assert calls[-1] == "C:/folder-a", f"Expected 'C:/folder-a' but got '{calls[-1]}'"
