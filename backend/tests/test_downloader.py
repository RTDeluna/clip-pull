import asyncio
import threading
import time
from unittest.mock import patch

from queue_manager import QueueManager
from downloader import (
    DownloadOrchestrator,
    build_ydl_opts,
    check_ffmpeg_available,
    is_referer_blocked_error,
    sanitize_filename,
)


def test_sanitize_filename_replaces_illegal_characters():
    assert sanitize_filename('Lesson 1: "Intro"?') == "Lesson 1_ _Intro__"


def test_sanitize_filename_falls_back_when_empty():
    assert sanitize_filename("   ") == "untitled"


def test_build_ydl_opts_includes_referer_header_when_provided():
    opts = build_ydl_opts("out/%(title)s.%(ext)s", "https://school.com", lambda d: None)
    assert opts["http_headers"] == {"Referer": "https://school.com"}


def test_build_ydl_opts_omits_referer_header_when_not_provided():
    opts = build_ydl_opts("out/%(title)s.%(ext)s", None, lambda d: None)
    assert "http_headers" not in opts


def test_is_referer_blocked_error_detects_403():
    assert is_referer_blocked_error(Exception("HTTP Error 403: Forbidden")) is True


def test_is_referer_blocked_error_ignores_other_errors():
    assert is_referer_blocked_error(Exception("Video unavailable")) is False


def test_check_ffmpeg_available_returns_true_when_on_path():
    with patch("shutil.which", return_value="C:/ffmpeg/ffmpeg.exe"):
        assert check_ffmpeg_available() is True


def test_check_ffmpeg_available_returns_false_when_missing():
    with patch("shutil.which", return_value=None):
        assert check_ffmpeg_available() is False


def test_download_entry_marks_done_and_sets_title_on_success():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    def fake_download(url, output_folder, referer, progress_hook):
        progress_hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    updated = manager.get(entry.id)
    assert updated.status == "done"
    assert updated.title == "Lesson 1"
    assert updated.percent == 100.0


def test_download_entry_sets_referer_blocked_message_on_403():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    def failing_download(url, output_folder, referer, progress_hook):
        raise Exception("HTTP Error 403: Forbidden")

    orchestrator = DownloadOrchestrator(manager, download_fn=failing_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    updated = manager.get(entry.id)
    assert updated.status == "error"
    assert "referer" in updated.error_reason.lower()


def test_progress_hook_throttles_rapid_updates():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    call_count = {"n": 0}
    original_update_progress = manager.update_progress

    def counting_update_progress(entry_id, percent, speed, eta):
        call_count["n"] += 1
        original_update_progress(entry_id, percent, speed, eta)

    manager.update_progress = counting_update_progress

    def fake_download(url, output_folder, referer, progress_hook):
        # Simulate yt-dlp firing many rapid progress ticks with no delay between
        # them (as it can several times per second). All of these fall within a
        # single 0.25s throttle window, so this is deterministic: no time.sleep,
        # no flakiness.
        for i in range(50):
            progress_hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": i,
                    "total_bytes": 50,
                }
            )
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    # Only the first hook call within the throttle window should get through,
    # plus the guaranteed final 100% update issued after the executor call
    # completes (unconditional, outside progress_hook) — so far fewer than 50.
    assert call_count["n"] < 5
    updated = manager.get(entry.id)
    assert updated.status == "done"
    assert updated.percent == 100.0


def test_download_all_never_exceeds_max_concurrency():
    manager = QueueManager()
    entries = manager.add_entries([f"https://vimeo.com/{i}" for i in range(6)])
    counter_lock = threading.Lock()
    counters = {"active": 0, "peak": 0}

    def slow_download(url, output_folder, referer, progress_hook):
        with counter_lock:
            counters["active"] += 1
            counters["peak"] = max(counters["peak"], counters["active"])
        time.sleep(0.05)
        with counter_lock:
            counters["active"] -= 1
        return {"title": "x"}

    orchestrator = DownloadOrchestrator(manager, max_concurrent=2, download_fn=slow_download)
    asyncio.run(orchestrator.download_all([e.id for e in entries], "/tmp/out"))

    assert counters["peak"] == 2
    assert all(manager.get(e.id).status == "done" for e in entries)
