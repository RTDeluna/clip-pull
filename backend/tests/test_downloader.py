import asyncio
import threading
import time
from unittest.mock import patch

from queue_manager import QueueManager
from downloader import (
    CONCURRENT_FRAGMENT_DOWNLOADS,
    DownloadOrchestrator,
    build_ydl_opts,
    check_aria2c_available,
    check_ffmpeg_available,
    format_bytes,
    format_speed,
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


def test_concurrent_fragment_downloads_increased_beyond_original_default():
    assert CONCURRENT_FRAGMENT_DOWNLOADS > 5


def test_build_ydl_opts_uses_named_fragment_concurrency_constant():
    opts = build_ydl_opts("out/%(title)s.%(ext)s", None, lambda d: None)
    assert opts["concurrent_fragment_downloads"] == CONCURRENT_FRAGMENT_DOWNLOADS


def test_check_aria2c_available_returns_true_when_on_path():
    with patch("shutil.which", return_value="C:/aria2/aria2c.exe"):
        assert check_aria2c_available() is True


def test_check_aria2c_available_returns_false_when_missing():
    with patch("shutil.which", return_value=None):
        assert check_aria2c_available() is False


def test_build_ydl_opts_configures_aria2c_when_enabled():
    opts = build_ydl_opts(
        "out/%(title)s.%(ext)s", None, lambda d: None, use_aria2c=True
    )
    assert opts["external_downloader"] == "aria2c"


def test_build_ydl_opts_omits_aria2c_when_disabled():
    opts = build_ydl_opts("out/%(title)s.%(ext)s", None, lambda d: None)
    assert "external_downloader" not in opts


def test_build_ydl_opts_format_unchanged_regardless_of_aria2c():
    without_aria2c = build_ydl_opts("out/%(title)s.%(ext)s", None, lambda d: None)
    with_aria2c = build_ydl_opts(
        "out/%(title)s.%(ext)s", None, lambda d: None, use_aria2c=True
    )
    assert without_aria2c["format"] == "bestvideo+bestaudio/best"
    assert with_aria2c["format"] == "bestvideo+bestaudio/best"


def test_check_ffmpeg_available_returns_true_when_on_path():
    with patch("shutil.which", return_value="C:/ffmpeg/ffmpeg.exe"):
        assert check_ffmpeg_available() is True


def test_check_ffmpeg_available_returns_false_when_missing():
    with patch("shutil.which", return_value=None):
        assert check_ffmpeg_available() is False


def test_format_speed_formats_bytes_per_second_human_readable():
    assert format_speed(500) == "500.0B/s"
    assert format_speed(1024) == "1.0KiB/s"
    assert format_speed(1025453.0) == "1001.4KiB/s"


def test_format_speed_returns_none_for_missing_or_zero_speed():
    assert format_speed(None) is None
    assert format_speed(0) is None


def test_format_bytes_formats_human_readable():
    assert format_bytes(500) == "500B"
    assert format_bytes(1024) == "1.0KB"
    assert format_bytes(1048576) == "1.0MB"


def test_format_bytes_returns_zero_string_for_zero_bytes():
    # Unlike format_speed(0), which is None (no speed reading yet), 0 of N
    # bytes downloaded is a legitimate, meaningful progress state.
    assert format_bytes(0) == "0B"


def test_format_bytes_returns_none_for_missing_value():
    assert format_bytes(None) is None


def test_progress_hook_produces_clean_speed_and_integer_eta():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    # Capture every update_progress call — the orchestrator's own
    # unconditional 100%-complete call (speed=None, eta=0) fires after
    # fake_download returns, so we must check the value progress_hook
    # itself reported, not the entry's final state.
    calls = []
    original_update_progress = manager.update_progress

    def capturing_update_progress(
        entry_id, percent, speed, eta, downloaded_size=None, total_size=None
    ):
        calls.append((percent, speed, eta, downloaded_size, total_size))
        original_update_progress(
            entry_id, percent, speed, eta, downloaded_size, total_size
        )

    manager.update_progress = capturing_update_progress

    def fake_download(url, output_folder, referer, progress_hook):
        # yt-dlp's real progress dict includes both a raw numeric "speed"
        # and a pre-colorized "_speed_str" meant for terminal display (with
        # ANSI escape codes) — we must use the former, not the latter, and
        # "eta" can arrive as a float that needs to become a clean integer.
        progress_hook(
            {
                "status": "downloading",
                "downloaded_bytes": 50,
                "total_bytes": 100,
                "_speed_str": "\x1b[0;32m1001.42KiB/s\x1b[0m",
                "speed": 1025453.0,
                "eta": 17.08478471174968,
            }
        )
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    _, first_speed, first_eta, first_downloaded_size, first_total_size = calls[0]
    assert first_speed == "1001.4KiB/s"
    assert "\x1b" not in first_speed
    assert first_eta == 17
    assert isinstance(first_eta, int)
    assert first_downloaded_size == "50B"
    assert first_total_size == "100B"


def test_download_entry_final_update_clears_size_fields_like_speed_and_eta():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    # The hook's update is scheduled via call_soon_threadsafe, so its exact
    # ordering relative to the orchestrator's own post-success call isn't
    # guaranteed when download_fn returns instantly (a real yt-dlp download
    # always has real elapsed time between hook ticks and completion, so
    # this ordering is never actually racy in production). Capture every
    # call and check the post-success call's own arguments directly, rather
    # than the entry's final field values, which depend on that ordering.
    calls = []
    original_update_progress = manager.update_progress

    def capturing_update_progress(
        entry_id, percent, speed, eta, downloaded_size=None, total_size=None
    ):
        calls.append((percent, speed, eta, downloaded_size, total_size))
        original_update_progress(
            entry_id, percent, speed, eta, downloaded_size, total_size
        )

    manager.update_progress = capturing_update_progress

    def fake_download(url, output_folder, referer, progress_hook):
        progress_hook(
            {"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100}
        )
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    final_calls = [c for c in calls if c[0] == 100.0]
    assert len(final_calls) == 1
    _, _, _, downloaded_size, total_size = final_calls[0]
    assert downloaded_size is None
    assert total_size is None
    assert manager.get(entry.id).status == "done"


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

    def counting_update_progress(
        entry_id, percent, speed, eta, downloaded_size=None, total_size=None
    ):
        call_count["n"] += 1
        original_update_progress(
            entry_id, percent, speed, eta, downloaded_size, total_size
        )

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
