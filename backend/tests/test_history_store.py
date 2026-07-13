import json

from history_store import HistoryStore, redact_pro_summary_fields


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


def test_find_transcribable_returns_only_done_downloads_without_a_transcript():
    store = HistoryStore()
    none_entry = _record(store, url="https://vimeo.com/1")  # transcript_status "none"
    errored_transcript = _record(store, url="https://vimeo.com/2")
    store.update_transcript(errored_transcript["id"], status="error", error="boom")
    done_transcript = _record(store, url="https://vimeo.com/3")
    store.update_transcript(done_transcript["id"], status="done", transcript="hi")
    running_transcript = _record(store, url="https://vimeo.com/4")
    store.update_transcript(running_transcript["id"], status="running")
    _record(store, url="https://vimeo.com/5", status="error", output_path=None)  # failed download

    eligible_ids = {row["id"] for row in store.find_transcribable()}

    # 'none' and 'error' transcript states on finished downloads are retryable;
    # 'done'/'running' transcripts and failed downloads are excluded.
    assert eligible_ids == {none_entry["id"], errored_transcript["id"]}


def test_find_transcribable_respects_the_limit():
    store = HistoryStore()
    for i in range(5):
        _record(store, url=f"https://vimeo.com/{i}")
    assert len(store.find_transcribable(limit=3)) == 3


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


def test_search_matches_transcript_text():
    store = HistoryStore()
    a = _record(store, url="https://vimeo.com/1", title="Video A")
    store.update_transcript(a["id"], status="done", transcript="[00:00:00] photosynthesis explained")
    _record(store, url="https://vimeo.com/2", title="Video B")

    results = store.search(query="photosynthesis")

    assert len(results) == 1
    assert results[0]["id"] == a["id"]


def test_search_matches_summary_text():
    store = HistoryStore()
    a = _record(store, url="https://vimeo.com/1", title="Video A")
    store.update_summary(
        a["id"], status="done",
        summary='{"tldr": "all about mitochondria", "key_points": [], "chapters": []}',
    )
    _record(store, url="https://vimeo.com/2", title="Video B")

    results = store.search(query="mitochondria")

    assert len(results) == 1
    assert results[0]["id"] == a["id"]


def test_clear_matches_transcript_text():
    store = HistoryStore()
    a = _record(store, url="https://vimeo.com/1")
    store.update_transcript(a["id"], status="done", transcript="[00:00:00] a special keyword here")
    _record(store, url="https://vimeo.com/2")

    removed = store.clear(query="special keyword")

    assert removed == 1
    remaining = store.search()
    assert len(remaining) == 1
    assert remaining[0]["url"] == "https://vimeo.com/2"


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


def test_record_stores_output_folder_even_on_a_failed_download():
    # output_path is only known once a file actually gets written (None on
    # failure), but output_folder is known upfront and must survive a
    # failure too -- it's what lets a History-tab retry reuse the same
    # destination instead of falling back to the current default/prompting
    # the user again.
    store = HistoryStore()
    result = _record(
        store, status="error", error_reason="Blocked", output_path=None,
        output_folder="C:/downloads/course-a",
    )
    assert result["output_folder"] == "C:/downloads/course-a"


def test_record_with_update_id_preserves_output_folder_across_a_retry():
    store = HistoryStore()
    first = _record(
        store, status="error", error_reason="Blocked", output_path=None,
        output_folder="C:/downloads/course-a",
    )
    second = _record(
        store,
        status="done",
        error_reason=None,
        output_path="C:/downloads/course-a/Test Video [1].mp4",
        output_folder="C:/downloads/course-a",
        update_id=first["id"],
    )
    assert second["output_folder"] == "C:/downloads/course-a"


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


# --------------------------------------------------------------------------
# redact_pro_summary_fields
# --------------------------------------------------------------------------

def _entry_with_summary(**overrides):
    summary = {"tldr": "A short summary.", "key_points": [{"seconds": 5, "text": "point"}], "chapters": [{"seconds": 0, "title": "Intro"}]}
    summary.update(overrides.pop("summary_overrides", {}))
    entry = {"id": 1, "status": "done", "summary": json.dumps(summary)}
    entry.update(overrides)
    return entry


def test_redact_pro_summary_fields_strips_key_points_and_chapters_when_not_pro():
    entry = _entry_with_summary()
    redacted = redact_pro_summary_fields(entry, is_pro=False)
    stored = json.loads(redacted["summary"])
    assert stored["tldr"] == "A short summary."
    assert stored["key_points"] == []
    assert stored["chapters"] == []


def test_redact_pro_summary_fields_leaves_pro_entries_untouched():
    entry = _entry_with_summary()
    redacted = redact_pro_summary_fields(entry, is_pro=True)
    assert redacted is entry
    stored = json.loads(redacted["summary"])
    assert len(stored["key_points"]) == 1
    assert len(stored["chapters"]) == 1


def test_redact_pro_summary_fields_is_a_noop_when_no_summary_yet():
    entry = {"id": 1, "status": "done", "summary": None}
    redacted = redact_pro_summary_fields(entry, is_pro=False)
    assert redacted is entry


def test_redact_pro_summary_fields_does_not_mutate_the_original_entry():
    entry = _entry_with_summary()
    original_summary = entry["summary"]
    redact_pro_summary_fields(entry, is_pro=False)
    assert entry["summary"] == original_summary
