from queue_manager import QueueManager


def test_add_entries_creates_queued_entries_with_unique_ids():
    manager = QueueManager()
    entries = manager.add_entries(["https://vimeo.com/111", "https://vimeo.com/222"])
    assert len(entries) == 2
    assert entries[0].status == "queued"
    assert entries[1].status == "queued"
    assert entries[0].id != entries[1].id
    assert entries[0].url == "https://vimeo.com/111"


def test_get_returns_entry_by_id():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    assert manager.get(entry.id) is entry


def test_set_status_updates_entry_status():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.set_status(entry.id, "downloading")
    assert manager.get(entry.id).status == "downloading"


def test_set_title_updates_entry_title():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.set_title(entry.id, "Lesson 1 - Intro")
    assert manager.get(entry.id).title == "Lesson 1 - Intro"


def test_update_progress_sets_percent_speed_eta():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.update_progress(entry.id, 42.5, "1.2MiB/s", 30)
    updated = manager.get(entry.id)
    assert updated.percent == 42.5
    assert updated.speed == "1.2MiB/s"
    assert updated.eta == 30


def test_update_progress_sets_downloaded_and_total_size():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.update_progress(entry.id, 42.5, "1.2MiB/s", 30, "45.2MB", "120.4MB")
    updated = manager.get(entry.id)
    assert updated.downloaded_size == "45.2MB"
    assert updated.total_size == "120.4MB"


def test_update_progress_defaults_size_fields_to_none_when_omitted():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.update_progress(entry.id, 42.5, "1.2MiB/s", 30)
    updated = manager.get(entry.id)
    assert updated.downloaded_size is None
    assert updated.total_size is None


def test_set_error_sets_status_and_reason():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.set_error(entry.id, "Blocked — referer required")
    updated = manager.get(entry.id)
    assert updated.status == "error"
    assert updated.error_reason == "Blocked — referer required"


def test_reset_for_retry_clears_progress_and_increments_retry_count():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.update_progress(entry.id, 50.0, "1MiB/s", 10, "50MB", "100MB")
    manager.set_error(entry.id, "some error")
    manager.reset_for_retry(entry.id)
    updated = manager.get(entry.id)
    assert updated.status == "queued"
    assert updated.percent == 0.0
    assert updated.speed is None
    assert updated.eta is None
    assert updated.downloaded_size is None
    assert updated.total_size is None
    assert updated.error_reason is None
    assert updated.retry_count == 1


def test_to_list_returns_serializable_dicts():
    manager = QueueManager()
    manager.add_entries(["https://vimeo.com/111"])
    result = manager.to_list()
    assert isinstance(result, list)
    assert result[0]["url"] == "https://vimeo.com/111"
    assert result[0]["status"] == "queued"


def test_on_update_callback_fires_on_mutation():
    received = []
    manager = QueueManager(on_update=lambda entry_dict: received.append(entry_dict))
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.set_status(entry.id, "downloading")
    assert len(received) == 2
    assert received[-1]["status"] == "downloading"


def test_add_entries_stamps_batch_id_output_folder_on_all_created_entries():
    manager = QueueManager()
    entries = manager.add_entries(
        ["https://vimeo.com/1", "https://vimeo.com/2"],
        batch_id="batch-1",
        output_folder="C:/downloads",
    )
    assert all(e.batch_id == "batch-1" for e in entries)
    assert all(e.output_folder == "C:/downloads" for e in entries)


def test_add_entries_marks_previously_downloaded_urls():
    manager = QueueManager()
    entries = manager.add_entries(
        ["https://vimeo.com/1", "https://vimeo.com/2"],
        previously_downloaded_urls={"https://vimeo.com/1"},
    )
    assert entries[0].previously_downloaded is True
    assert entries[1].previously_downloaded is False


def test_add_entries_defaults_batch_folder_and_previously_downloaded():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/1"])
    assert entry.batch_id is None
    assert entry.output_folder is None
    assert entry.previously_downloaded is False


def test_is_batch_complete_false_while_any_entry_pending():
    manager = QueueManager()
    entries = manager.add_entries(["https://vimeo.com/1", "https://vimeo.com/2"], batch_id="b1")
    manager.set_status(entries[0].id, "done")
    assert manager.is_batch_complete("b1") is False


def test_is_batch_complete_true_when_all_terminal():
    manager = QueueManager()
    entries = manager.add_entries(["https://vimeo.com/1", "https://vimeo.com/2"], batch_id="b1")
    manager.set_status(entries[0].id, "done")
    manager.set_error(entries[1].id, "some error")
    assert manager.is_batch_complete("b1") is True


def test_is_batch_complete_false_for_unknown_batch_id():
    manager = QueueManager()
    assert manager.is_batch_complete("nonexistent") is False


def test_is_batch_complete_false_for_none_batch_id():
    manager = QueueManager()
    assert manager.is_batch_complete(None) is False


def test_batch_summary_counts_done_and_error():
    manager = QueueManager()
    entries = manager.add_entries(
        ["https://vimeo.com/1", "https://vimeo.com/2", "https://vimeo.com/3"], batch_id="b1"
    )
    manager.set_status(entries[0].id, "done")
    manager.set_status(entries[1].id, "done")
    manager.set_error(entries[2].id, "failed")
    assert manager.batch_summary("b1") == {"done": 2, "error": 1}
