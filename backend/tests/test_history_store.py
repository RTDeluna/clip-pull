from history_store import HistoryStore


def _record(store, url="https://vimeo.com/1", status="done", **overrides):
    defaults = dict(
        entry_id="e1",
        batch_id="b1",
        url=url,
        title="Test Video",
        output_path="C:/downloads/Test Video [1].mp4",
        total_size="45.2MB",
        status=status,
        error_reason=None,
        retry_count=0,
    )
    defaults.update(overrides)
    return store.record(**defaults)


def test_record_inserts_row_and_returns_dict():
    store = HistoryStore()
    result = _record(store)
    assert result["url"] == "https://vimeo.com/1"
    assert result["status"] == "done"
    assert result["finished_at"] is not None


def test_record_logs_both_done_and_error_outcomes():
    store = HistoryStore()
    _record(store, status="done")
    _record(store, status="error", error_reason="Blocked", output_path=None)
    all_entries = store.search()
    assert len(all_entries) == 2
    statuses = {entry["status"] for entry in all_entries}
    assert statuses == {"done", "error"}


def test_search_returns_all_when_no_filters():
    store = HistoryStore()
    _record(store, url="https://vimeo.com/1")
    _record(store, url="https://vimeo.com/2")
    assert len(store.search()) == 2


def test_search_filters_by_status():
    store = HistoryStore()
    _record(store, status="done")
    _record(store, status="error")
    done_only = store.search(status="done")
    assert len(done_only) == 1
    assert done_only[0]["status"] == "done"


def test_search_filters_by_query_matching_url_title_or_output_path():
    store = HistoryStore()
    _record(store, url="https://vimeo.com/1", title="Intro to Marketing")
    _record(store, url="https://www.loom.com/share/abc", title="Q&A Session")
    results = store.search(query="Marketing")
    assert len(results) == 1
    assert results[0]["title"] == "Intro to Marketing"


def test_search_respects_limit_and_offset():
    store = HistoryStore()
    for i in range(5):
        _record(store, url=f"https://vimeo.com/{i}")
    page = store.search(limit=2, offset=1)
    assert len(page) == 2


def test_was_previously_downloaded_only_matches_done_status():
    store = HistoryStore()
    _record(store, url="https://vimeo.com/1", status="done")
    _record(store, url="https://vimeo.com/2", status="error")
    matched = store.was_previously_downloaded(
        ["https://vimeo.com/1", "https://vimeo.com/2", "https://vimeo.com/3"]
    )
    assert matched == {"https://vimeo.com/1"}


def test_was_previously_downloaded_returns_empty_set_for_empty_input():
    store = HistoryStore()
    assert store.was_previously_downloaded([]) == set()


def test_delete_removes_entry_and_returns_true():
    store = HistoryStore()
    result = _record(store)
    assert store.delete(result["id"]) is True
    assert store.search() == []


def test_delete_returns_false_when_entry_does_not_exist():
    store = HistoryStore()
    assert store.delete(999) is False


def test_clear_removes_all_entries_when_no_filters():
    store = HistoryStore()
    _record(store, url="https://vimeo.com/1")
    _record(store, url="https://vimeo.com/2")
    assert store.clear() == 2
    assert store.search() == []


def test_clear_respects_status_filter():
    store = HistoryStore()
    _record(store, status="done")
    _record(store, status="error")
    assert store.clear(status="error") == 1
    remaining = store.search()
    assert len(remaining) == 1
    assert remaining[0]["status"] == "done"


def test_record_with_update_id_updates_existing_row_in_place():
    store = HistoryStore()
    first = _record(store, status="error", error_reason="Blocked", output_path=None)
    second = _record(
        store,
        status="done",
        error_reason=None,
        output_path="C:/downloads/Test Video [1].mp4",
        retry_count=1,
        update_id=first["id"],
    )
    assert second["id"] == first["id"]
    all_entries = store.search()
    assert len(all_entries) == 1
    assert all_entries[0]["status"] == "done"
    assert all_entries[0]["retry_count"] == 1


def test_record_with_update_id_falls_back_to_insert_when_row_is_gone():
    store = HistoryStore()
    first = _record(store, status="error")
    store.delete(first["id"])
    second = _record(store, status="done", update_id=first["id"])
    assert second["id"] != first["id"]
    assert store.search() == [second]


def test_record_without_update_id_always_inserts_a_new_row():
    store = HistoryStore()
    _record(store, status="error")
    _record(store, status="done")
    assert len(store.search()) == 2


def test_get_returns_the_matching_row():
    store = HistoryStore()
    created = _record(store)
    fetched = store.get(created["id"])
    assert fetched == created


def test_get_returns_none_for_unknown_id():
    store = HistoryStore()
    assert store.get(999) is None


def test_new_history_rows_default_to_no_transcript():
    store = HistoryStore()
    row = _record(store)
    assert row["transcript_status"] == "none"
    assert row["transcript"] is None
    assert row["summary"] is None


def test_update_transcript_sets_fields_and_returns_updated_row():
    store = HistoryStore()
    created = _record(store)
    updated = store.update_transcript(created["id"], status="done", transcript="Hello world.")
    assert updated["transcript_status"] == "done"
    assert updated["transcript"] == "Hello world."
    assert updated["transcribed_at"] is not None
    # Transcribing never touches the independent summary state.
    assert updated["summary_status"] == "none"
    assert updated["summary"] is None


def test_update_transcript_records_error_state():
    store = HistoryStore()
    created = _record(store)
    updated = store.update_transcript(created["id"], status="error", error="Invalid API key")
    assert updated["transcript_status"] == "error"
    assert updated["transcript_error"] == "Invalid API key"
    assert updated["transcript"] is None


def test_update_transcript_returns_none_for_unknown_id():
    store = HistoryStore()
    assert store.update_transcript(999, status="done") is None


def test_update_summary_sets_fields_independently_of_transcript_state():
    store = HistoryStore()
    created = _record(store)
    store.update_transcript(created["id"], status="done", transcript="Hello world.")

    updated = store.update_summary(created["id"], status="done", summary="A greeting.")

    assert updated["summary_status"] == "done"
    assert updated["summary"] == "A greeting."
    assert updated["summarized_at"] is not None
    # Summarizing never touches the already-set transcript state.
    assert updated["transcript_status"] == "done"
    assert updated["transcript"] == "Hello world."


def test_update_summary_records_error_state():
    store = HistoryStore()
    created = _record(store)
    updated = store.update_summary(created["id"], status="error", error="Invalid API key")
    assert updated["summary_status"] == "error"
    assert updated["summary_error"] == "Invalid API key"
    assert updated["summary"] is None


def test_update_summary_returns_none_for_unknown_id():
    store = HistoryStore()
    assert store.update_summary(999, status="done") is None


def test_reset_stuck_transcriptions_resets_running_transcript_to_error():
    store = HistoryStore()
    created = _record(store)
    store.update_transcript(created["id"], status="running")

    count = store.reset_stuck_transcriptions()

    assert count == 1
    updated = store.get(created["id"])
    assert updated["transcript_status"] == "error"
    assert "interrupted" in updated["transcript_error"]


def test_reset_stuck_transcriptions_resets_running_summary_to_error():
    store = HistoryStore()
    created = _record(store)
    store.update_transcript(created["id"], status="done", transcript="hi")
    store.update_summary(created["id"], status="running")

    count = store.reset_stuck_transcriptions()

    assert count == 1
    updated = store.get(created["id"])
    assert updated["transcript_status"] == "done"  # untouched
    assert updated["summary_status"] == "error"
    assert "interrupted" in updated["summary_error"]


def test_reset_stuck_transcriptions_resets_both_independently_in_one_pass():
    store = HistoryStore()
    created = _record(store)
    store.update_transcript(created["id"], status="running")
    store.update_summary(created["id"], status="running")

    count = store.reset_stuck_transcriptions()

    assert count == 1  # one row touched, both columns reset
    updated = store.get(created["id"])
    assert updated["transcript_status"] == "error"
    assert updated["summary_status"] == "error"


def test_reset_stuck_transcriptions_leaves_other_statuses_alone():
    store = HistoryStore()
    created = _record(store)
    store.update_transcript(created["id"], status="done", transcript="hi")

    count = store.reset_stuck_transcriptions()

    assert count == 0
    assert store.get(created["id"])["transcript_status"] == "done"


def test_history_persists_across_store_instances_pointing_at_same_file(tmp_path):
    db_path = tmp_path / "history.db"
    store1 = HistoryStore(db_path)
    _record(store1, url="https://vimeo.com/999")

    store2 = HistoryStore(db_path)
    assert len(store2.search()) == 1
    assert store2.search()[0]["url"] == "https://vimeo.com/999"
