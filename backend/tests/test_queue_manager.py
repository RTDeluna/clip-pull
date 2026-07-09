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
    manager.update_progress(entry.id, 50.0, "1MiB/s", 10)
    manager.set_error(entry.id, "some error")
    manager.reset_for_retry(entry.id)
    updated = manager.get(entry.id)
    assert updated.status == "queued"
    assert updated.percent == 0.0
    assert updated.speed is None
    assert updated.eta is None
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
