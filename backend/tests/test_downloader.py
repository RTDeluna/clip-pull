import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import patch

from queue_manager import QueueManager
from downloader import (
    CONCURRENT_FRAGMENT_DOWNLOADS,
    REFERER_BLOCKED_MESSAGE,
    DownloadOrchestrator,
    build_ydl_opts,
    check_aria2c_available,
    check_ffmpeg_available,
    format_bytes,
    format_speed,
    humanize_error_reason,
    is_referer_blocked_error,
    probe_total_bytes,
    resolve_output_folder,
    resolve_use_aria2c,
    sanitize_filename,
    select_format,
    stream_stage,
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


def test_build_ydl_opts_registers_progress_hook_as_postprocessor_hook_too():
    def hook(d):
        pass

    opts = build_ydl_opts("out/%(title)s.%(ext)s", None, hook)
    assert opts["postprocessor_hooks"] == [hook]


def test_is_referer_blocked_error_detects_403():
    assert is_referer_blocked_error(Exception("HTTP Error 403: Forbidden")) is True


def test_is_referer_blocked_error_ignores_other_errors():
    assert is_referer_blocked_error(Exception("Video unavailable")) is False


def test_humanize_error_reason_prioritizes_referer_block_over_other_rules():
    assert humanize_error_reason(Exception("HTTP Error 403: Forbidden")) == REFERER_BLOCKED_MESSAGE


def test_humanize_error_reason_rewrites_file_lock_errors():
    exc = Exception(
        "Unable to rename file: [WinError 32] The process cannot access the "
        "file because it is being used by another process: 'a.part' -> 'a.mp4'"
    )
    reason = humanize_error_reason(exc)
    assert "WinError" not in reason
    assert "locked by another process" in reason


def test_humanize_error_reason_rewrites_missing_fragment_errors():
    exc = Exception(
        "Unable to download video: [Errno 2] No such file or directory: "
        "'a.mp4.part-Frag6'"
    )
    reason = humanize_error_reason(exc)
    assert "Errno" not in reason
    assert "interrupted before it finished writing" in reason


def test_humanize_error_reason_rewrites_404_errors():
    exc = Exception("ERROR: [vimeo] 000000000: HTTP Error 404: Not Found")
    reason = humanize_error_reason(exc)
    assert "404" not in reason
    assert "couldn't be found" in reason


def test_humanize_error_reason_rewrites_network_errors():
    exc = Exception("<urlopen error [Errno 11001] getaddrinfo failed>")
    reason = humanize_error_reason(exc)
    assert "getaddrinfo" not in reason
    assert "Couldn't reach the video server" in reason


def test_humanize_error_reason_strips_ansi_codes_for_unmapped_errors():
    exc = Exception("\x1b[0;31mERROR:\x1b[0m Something unexpected happened")
    reason = humanize_error_reason(exc)
    assert "\x1b" not in reason
    assert reason == "ERROR: Something unexpected happened"


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
    with patch("shutil.which", return_value="C:/ffmpeg/ffmpeg.exe"):
        without_aria2c = build_ydl_opts("out/%(title)s.%(ext)s", None, lambda d: None)
        with_aria2c = build_ydl_opts(
            "out/%(title)s.%(ext)s", None, lambda d: None, use_aria2c=True
        )
    assert without_aria2c["format"] == "bestvideo+bestaudio/best"
    assert with_aria2c["format"] == "bestvideo+bestaudio/best"


def test_select_format_prefers_merge_format_when_ffmpeg_available():
    with patch("shutil.which", return_value="C:/ffmpeg/ffmpeg.exe"):
        assert select_format() == "bestvideo+bestaudio/best"


def test_select_format_falls_back_to_single_file_when_ffmpeg_missing():
    with patch("shutil.which", return_value=None):
        assert select_format() == "best"


def test_build_ydl_opts_uses_merge_format_when_ffmpeg_available():
    with patch("shutil.which", return_value="C:/ffmpeg/ffmpeg.exe"):
        opts = build_ydl_opts("out/%(title)s.%(ext)s", None, lambda d: None)
    assert opts["format"] == "bestvideo+bestaudio/best"


def test_build_ydl_opts_falls_back_to_single_file_format_when_ffmpeg_missing():
    with patch("shutil.which", return_value=None):
        opts = build_ydl_opts("out/%(title)s.%(ext)s", None, lambda d: None)
    assert opts["format"] == "best"


def test_probe_total_bytes_uses_merge_format_when_ffmpeg_available():
    captured_opts = {}

    class FakeYdl:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            return {"filesize": 10}

    with patch("shutil.which", return_value="C:/ffmpeg/ffmpeg.exe"):
        with patch("yt_dlp.YoutubeDL", FakeYdl):
            probe_total_bytes("https://vimeo.com/111", None)

    assert captured_opts["format"] == "bestvideo+bestaudio/best"


def test_probe_total_bytes_falls_back_to_single_file_format_when_ffmpeg_missing():
    captured_opts = {}

    class FakeYdl:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            return {"filesize": 10}

    with patch("shutil.which", return_value=None):
        with patch("yt_dlp.YoutubeDL", FakeYdl):
            probe_total_bytes("https://vimeo.com/111", None)

    assert captured_opts["format"] == "best"


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


def test_stream_stage_returns_video_for_video_only_format():
    assert stream_stage({"vcodec": "avc1", "acodec": "none"}) == "video"


def test_stream_stage_returns_audio_for_audio_only_format():
    assert stream_stage({"vcodec": "none", "acodec": "mp4a"}) == "audio"


def test_stream_stage_returns_none_for_progressive_format():
    assert stream_stage({"vcodec": "avc1", "acodec": "mp4a"}) is None


def test_stream_stage_returns_none_when_codecs_missing():
    assert stream_stage({}) is None


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
        entry_id, percent, speed, eta, downloaded_size=None, total_size=None, speed_bytes=None
    ):
        calls.append((percent, speed, eta, downloaded_size, total_size, speed_bytes))
        original_update_progress(
            entry_id, percent, speed, eta, downloaded_size, total_size, speed_bytes
        )

    manager.update_progress = capturing_update_progress

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
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

    _, first_speed, first_eta, first_downloaded_size, first_total_size, first_speed_bytes = calls[0]
    assert first_speed == "1001.4KiB/s"
    assert "\x1b" not in first_speed
    assert first_eta == 17
    assert isinstance(first_eta, int)
    assert first_downloaded_size == "50B"
    assert first_total_size == "100B"
    assert first_speed_bytes == 1025453.0


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
        entry_id, percent, speed, eta, downloaded_size=None, total_size=None, speed_bytes=None
    ):
        calls.append((percent, speed, eta, downloaded_size, total_size, speed_bytes))
        original_update_progress(
            entry_id, percent, speed, eta, downloaded_size, total_size, speed_bytes
        )

    manager.update_progress = capturing_update_progress

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        progress_hook(
            {"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100}
        )
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    final_calls = [c for c in calls if c[0] == 100.0]
    assert len(final_calls) == 1
    _, _, _, downloaded_size, total_size, speed_bytes = final_calls[0]
    assert downloaded_size is None
    assert total_size is None
    assert speed_bytes is None
    assert manager.get(entry.id).status == "done"


def test_download_entry_marks_done_and_sets_title_on_success():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
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

    def failing_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
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
        entry_id, percent, speed, eta, downloaded_size=None, total_size=None, speed_bytes=None
    ):
        call_count["n"] += 1
        original_update_progress(
            entry_id, percent, speed, eta, downloaded_size, total_size, speed_bytes
        )

    manager.update_progress = counting_update_progress

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
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


def test_probe_total_bytes_sums_filesize_across_requested_formats():
    class FakeYdl:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            assert download is False
            return {"requested_formats": [{"filesize": 1000}, {"filesize": 500}]}

    with patch("yt_dlp.YoutubeDL", FakeYdl):
        assert probe_total_bytes("https://vimeo.com/111", None) == 1500


def test_probe_total_bytes_falls_back_to_filesize_approx():
    class FakeYdl:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            return {"requested_formats": [{"filesize": None, "filesize_approx": 2000}]}

    with patch("yt_dlp.YoutubeDL", FakeYdl):
        assert probe_total_bytes("https://vimeo.com/111", None) == 2000


def test_probe_total_bytes_handles_single_format_without_requested_formats():
    class FakeYdl:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            return {"filesize": 42}

    with patch("yt_dlp.YoutubeDL", FakeYdl):
        assert probe_total_bytes("https://vimeo.com/111", None) == 42


def test_probe_total_bytes_returns_none_when_any_format_size_unknown():
    class FakeYdl:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            return {
                "requested_formats": [
                    {"filesize": 1000},
                    {"filesize": None, "filesize_approx": None},
                ]
            }

    with patch("yt_dlp.YoutubeDL", FakeYdl):
        assert probe_total_bytes("https://vimeo.com/111", None) is None


def test_probe_total_bytes_returns_none_on_extraction_failure():
    class FakeYdl:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            raise Exception("network error")

    with patch("yt_dlp.YoutubeDL", FakeYdl):
        assert probe_total_bytes("https://vimeo.com/111", None) is None


def test_probe_total_bytes_includes_referer_header_when_provided():
    captured_opts = {}

    class FakeYdl:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            return {"filesize": 10}

    with patch("yt_dlp.YoutubeDL", FakeYdl):
        probe_total_bytes("https://vimeo.com/111", "https://school.com")

    assert captured_opts["http_headers"] == {"Referer": "https://school.com"}


def test_progress_hook_computes_weighted_percent_across_two_streams_when_probe_available():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    calls = []
    original_update_progress = manager.update_progress

    def capturing_update_progress(
        entry_id, percent, speed, eta, downloaded_size=None, total_size=None, speed_bytes=None
    ):
        calls.append((percent, downloaded_size, total_size))
        original_update_progress(
            entry_id, percent, speed, eta, downloaded_size, total_size, speed_bytes
        )

    manager.update_progress = capturing_update_progress

    video_info = {"vcodec": "avc1", "acodec": "none"}
    audio_info = {"vcodec": "none", "acodec": "mp4a"}

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        # Video stream: 400 of 800, then completes at 800 of 800.
        progress_hook({"status": "downloading", "downloaded_bytes": 400, "total_bytes": 800, "info_dict": video_info})
        time.sleep(0.26)  # clear the throttle window deterministically
        progress_hook({"status": "downloading", "downloaded_bytes": 800, "total_bytes": 800, "info_dict": video_info})
        time.sleep(0.26)
        # Audio stream starts — total_bytes drops to 200, downloaded resets low.
        progress_hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 200, "info_dict": audio_info})
        return {"title": "Lesson 1"}

    # Probe reports the true combined total across both streams (800 + 200).
    orchestrator = DownloadOrchestrator(
        manager, download_fn=fake_download, probe_fn=lambda url, referer: 1000
    )
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    # Exclude the orchestrator's own unconditional 100%-complete call
    # (downloaded_size=None) issued after fake_download returns — it isn't
    # from progress_hook, and its ordering after the hook's own calls is
    # guaranteed by the explicit `await asyncio.sleep(0)` in download_entry.
    hook_calls = [c for c in calls if c[1] is not None]
    percents = [c[0] for c in hook_calls]
    # Never drops below a previously-shown percent, unlike the old per-stream
    # math (which would have gone 50% -> 100% -> 50% again for the audio pass).
    assert percents == [40.0, 80.0, 90.0]
    assert hook_calls[-1][1] == "900B"  # cumulative downloaded across both streams
    assert hook_calls[-1][2] == "1000B"  # stays at the probed grand total throughout


def test_progress_hook_tracks_cumulative_percent_and_size_across_streams_when_probe_unavailable():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    calls = []
    original_update_progress = manager.update_progress

    def capturing_update_progress(
        entry_id, percent, speed, eta, downloaded_size=None, total_size=None, speed_bytes=None
    ):
        calls.append((percent, downloaded_size, total_size))
        original_update_progress(
            entry_id, percent, speed, eta, downloaded_size, total_size, speed_bytes
        )

    manager.update_progress = capturing_update_progress

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        # Video stream completes at 800 of 800.
        progress_hook({
            "status": "downloading", "downloaded_bytes": 800, "total_bytes": 800,
            "info_dict": {"vcodec": "avc1", "acodec": "none"},
        })
        time.sleep(0.26)  # clear the throttle window deterministically
        # Audio stream starts — its own total (200) is much smaller than the
        # video stream's. No probe_fn is injected, so there's no upfront
        # grand total; this exercises the fallback path.
        progress_hook({
            "status": "downloading", "downloaded_bytes": 100, "total_bytes": 200,
            "info_dict": {"vcodec": "none", "acodec": "mp4a"},
        })
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    hook_calls = [c for c in calls if c[1] is not None]
    # Both downloaded and total climb across the stream transition — neither
    # ever drops, unlike the old per-stream math (which reported 50.0% of
    # "200B" total right after reporting 100.0% of "800B").
    assert hook_calls == [
        (100.0, "800B", "800B"),
        (90.0, "900B", "1000B"),
    ]


def test_progress_hook_does_not_inflate_total_when_estimate_fluctuates_within_one_stream():
    # Regression test for a real bug: fragmented/HLS formats continuously
    # *refine* total_bytes_estimate over the course of a single stream's
    # download (it's an evolving estimate, not a fixed value). The same
    # format (info_dict never changes) reports a jittery total across
    # several ticks here — this must never be mistaken for a stream
    # transition, or prior_streams_bytes balloons far past the real size
    # (observed in production: a 16.7MB file reported as 595MB).
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    calls = []
    original_update_progress = manager.update_progress

    def capturing_update_progress(
        entry_id, percent, speed, eta, downloaded_size=None, total_size=None, speed_bytes=None
    ):
        calls.append((downloaded_size, total_size))
        original_update_progress(
            entry_id, percent, speed, eta, downloaded_size, total_size, speed_bytes
        )

    manager.update_progress = capturing_update_progress

    video_info = {"vcodec": "avc1", "acodec": "none"}

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        # Same stream throughout (info_dict unchanged) — only the estimate
        # jitters tick to tick, as yt-dlp's fragment downloader refines it.
        progress_hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes_estimate": 500, "info_dict": video_info})
        time.sleep(0.26)
        progress_hook({"status": "downloading", "downloaded_bytes": 200, "total_bytes_estimate": 480, "info_dict": video_info})
        time.sleep(0.26)
        progress_hook({"status": "downloading", "downloaded_bytes": 300, "total_bytes_estimate": 510, "info_dict": video_info})
        time.sleep(0.26)
        progress_hook({"status": "downloading", "downloaded_bytes": 400, "total_bytes_estimate": 495, "info_dict": video_info})
        time.sleep(0.26)
        progress_hook({"status": "downloading", "downloaded_bytes": 500, "total_bytes": 500, "info_dict": video_info})
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    hook_calls = [c for c in calls if c[0] is not None]
    # total_size tracks each tick's own (jittery) estimate directly — it
    # never accumulates a "prior stream" on top, because there never was
    # one. Before the fix, each fluctuation wrongly added another ~500B
    # chunk, so five ticks would have inflated this well past 2000B.
    assert hook_calls == [
        ("100B", "500B"),
        ("200B", "480B"),
        ("300B", "510B"),
        ("400B", "495B"),
        ("500B", "500B"),
    ]


def test_download_entry_records_correct_combined_size_in_history_across_streams():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    recorded = []

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        # Video stream completes, then a much-smaller audio stream starts —
        # no probe_fn, so History must still record the combined size, not
        # just the last (audio) stream's own total.
        progress_hook({
            "status": "downloading", "downloaded_bytes": 800, "total_bytes": 800,
            "info_dict": {"vcodec": "avc1", "acodec": "none"},
        })
        time.sleep(0.26)
        progress_hook({
            "status": "downloading", "downloaded_bytes": 200, "total_bytes": 200,
            "info_dict": {"vcodec": "none", "acodec": "mp4a"},
        })
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(
        manager,
        download_fn=fake_download,
        record_history=lambda **kwargs: recorded.append(kwargs),
    )
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    assert recorded[0]["total_size"] == "1000B"


def test_progress_hook_falls_back_when_probe_fn_returns_none():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    calls = []
    original_update_progress = manager.update_progress

    def capturing_update_progress(
        entry_id, percent, speed, eta, downloaded_size=None, total_size=None, speed_bytes=None
    ):
        calls.append(percent)
        original_update_progress(
            entry_id, percent, speed, eta, downloaded_size, total_size, speed_bytes
        )

    manager.update_progress = capturing_update_progress

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        progress_hook({"status": "downloading", "downloaded_bytes": 25, "total_bytes": 100})
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(
        manager, download_fn=fake_download, probe_fn=lambda url, referer: None
    )
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    # Last entry is the orchestrator's own unconditional 100%-complete call.
    assert calls == [25.0, 100.0]


def test_progress_hook_reports_video_then_audio_stage_across_stream_transition():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    stages = []
    original_set_stage = manager.set_stage

    def capturing_set_stage(entry_id, stage):
        stages.append(stage)
        original_set_stage(entry_id, stage)

    manager.set_stage = capturing_set_stage

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        progress_hook({
            "status": "downloading",
            "downloaded_bytes": 400,
            "total_bytes": 800,
            "info_dict": {"vcodec": "avc1", "acodec": "none"},
        })
        time.sleep(0.26)  # clear the throttle window deterministically
        progress_hook({
            "status": "downloading",
            "downloaded_bytes": 100,
            "total_bytes": 200,
            "info_dict": {"vcodec": "none", "acodec": "mp4a"},
        })
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    # video -> audio -> cleared on completion.
    assert stages == ["video", "audio", None]


def test_progress_hook_reports_merging_stage_on_postprocessor_start():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    stages = []
    original_set_stage = manager.set_stage

    def capturing_set_stage(entry_id, stage):
        stages.append(stage)
        original_set_stage(entry_id, stage)

    manager.set_stage = capturing_set_stage

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        progress_hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 100})
        progress_hook({"status": "started", "postprocessor": "Merger"})
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    assert stages == ["merging", None]


def test_download_entry_invokes_probe_fn_with_url_and_referer():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    captured = {}

    def probe_fn(url, referer):
        captured["url"] = url
        captured["referer"] = referer
        return None

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download, probe_fn=probe_fn)
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out", referer="https://school.com"))

    assert captured == {"url": "https://vimeo.com/111", "referer": "https://school.com"}


def test_download_all_never_exceeds_max_concurrency():
    manager = QueueManager()
    entries = manager.add_entries([f"https://vimeo.com/{i}" for i in range(6)])
    counter_lock = threading.Lock()
    counters = {"active": 0, "peak": 0}

    def slow_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
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


def test_resolve_use_aria2c_true_when_enabled_and_available():
    with patch("shutil.which", return_value="C:/aria2/aria2c.exe"):
        assert resolve_use_aria2c(True) is True


def test_resolve_use_aria2c_false_when_disabled_even_if_available():
    with patch("shutil.which", return_value="C:/aria2/aria2c.exe"):
        assert resolve_use_aria2c(False) is False


def test_resolve_use_aria2c_false_when_enabled_but_not_available():
    with patch("shutil.which", return_value=None):
        assert resolve_use_aria2c(True) is False


def test_build_ydl_opts_uses_injected_fragment_concurrency_value():
    opts = build_ydl_opts(
        "out/%(title)s.%(ext)s", None, lambda d: None, concurrent_fragment_downloads=16
    )
    assert opts["concurrent_fragment_downloads"] == 16


def test_build_ydl_opts_defaults_fragment_concurrency_to_module_constant():
    opts = build_ydl_opts("out/%(title)s.%(ext)s", None, lambda d: None)
    assert opts["concurrent_fragment_downloads"] == CONCURRENT_FRAGMENT_DOWNLOADS


def test_download_all_resolves_max_concurrent_from_callable_once_per_call():
    manager = QueueManager()
    entries = manager.add_entries([f"https://vimeo.com/{i}" for i in range(4)])
    counter_lock = threading.Lock()
    counters = {"active": 0, "peak": 0}

    def slow_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        with counter_lock:
            counters["active"] += 1
            counters["peak"] = max(counters["peak"], counters["active"])
        time.sleep(0.05)
        with counter_lock:
            counters["active"] -= 1
        return {"title": "x"}

    call_count = {"n": 0}

    def get_max_concurrent():
        call_count["n"] += 1
        return 1

    orchestrator = DownloadOrchestrator(
        manager, download_fn=slow_download, get_max_concurrent=get_max_concurrent
    )
    asyncio.run(orchestrator.download_all([e.id for e in entries], "/tmp/out"))

    assert counters["peak"] == 1
    assert call_count["n"] == 1


def test_download_entry_records_history_on_success():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"], batch_id="b1")
    recorded = []

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        progress_hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 100})
        return {
            "title": "Lesson 1",
            "requested_downloads": [{"filepath": "C:/out/Lesson 1.mp4"}],
        }

    orchestrator = DownloadOrchestrator(
        manager,
        download_fn=fake_download,
        record_history=lambda **kwargs: recorded.append(kwargs),
    )
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    assert len(recorded) == 1
    assert recorded[0]["status"] == "done"
    assert recorded[0]["url"] == "https://vimeo.com/111"
    assert recorded[0]["output_path"] == "C:/out/Lesson 1.mp4"
    assert recorded[0]["total_size"] == "100B"
    assert recorded[0]["batch_id"] == "b1"


def test_download_entry_records_history_on_error():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    recorded = []

    def failing_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        raise Exception("HTTP Error 403: Forbidden")

    orchestrator = DownloadOrchestrator(
        manager,
        download_fn=failing_download,
        record_history=lambda **kwargs: recorded.append(kwargs),
    )
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    assert len(recorded) == 1
    assert recorded[0]["status"] == "error"
    assert "referer" in recorded[0]["error_reason"].lower()


def test_download_entry_status_not_corrupted_when_record_history_raises():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        return {"title": "Lesson 1"}

    def raising_record_history(**kwargs):
        raise RuntimeError("simulated history write failure")

    orchestrator = DownloadOrchestrator(
        manager, download_fn=fake_download, record_history=raising_record_history
    )
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    assert manager.get(entry.id).status == "done"


def test_download_all_fires_on_batch_complete_exactly_once_for_shared_batch_id():
    manager = QueueManager()
    entries = manager.add_entries(["https://vimeo.com/1", "https://vimeo.com/2"], batch_id="b1")
    completions = []

    def mixed_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        if url.endswith("/2"):
            raise Exception("failed")
        return {"title": "ok"}

    orchestrator = DownloadOrchestrator(
        manager,
        download_fn=mixed_download,
        on_batch_complete=lambda batch_id, summary: completions.append((batch_id, summary)),
    )
    asyncio.run(orchestrator.download_all([e.id for e in entries], "/tmp/out"))

    assert len(completions) == 1
    assert completions[0][0] == "b1"
    assert completions[0][1] == {"done": 1, "error": 1}


def test_entries_without_batch_id_never_trigger_on_batch_complete():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/1"])
    completions = []

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        return {"title": "ok"}

    orchestrator = DownloadOrchestrator(
        manager,
        download_fn=fake_download,
        on_batch_complete=lambda batch_id, summary: completions.append((batch_id, summary)),
    )
    asyncio.run(orchestrator.download_entry(entry.id, "/tmp/out"))

    assert completions == []


def test_finished_entry_is_auto_removed_from_queue_once_delay_elapses():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        return {"title": "Lesson 1"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download, auto_remove_delay=0)

    async def run():
        await orchestrator.download_entry(entry.id, "/tmp/out")
        # Let the scheduled auto-remove task get its turn on the event loop.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())

    assert manager.get_all() == []


def test_failed_entry_is_also_auto_removed_from_queue():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    def failing_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        raise Exception("boom")

    orchestrator = DownloadOrchestrator(manager, download_fn=failing_download, auto_remove_delay=0)

    async def run():
        await orchestrator.download_entry(entry.id, "/tmp/out")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())

    assert manager.get_all() == []


def test_finished_entry_stays_in_queue_until_delay_elapses():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        return {"title": "Lesson 1"}

    # A long delay that won't elapse during this test's brief run.
    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download, auto_remove_delay=10)

    async def run():
        await orchestrator.download_entry(entry.id, "/tmp/out")
        await asyncio.sleep(0)

    asyncio.run(run())

    assert manager.get(entry.id).status == "done"


def test_auto_removal_does_not_yank_an_entry_retried_during_the_delay_window():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    def failing_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        raise Exception("boom")

    orchestrator = DownloadOrchestrator(manager, download_fn=failing_download, auto_remove_delay=0)

    async def run():
        await orchestrator.download_entry(entry.id, "/tmp/out")
        # Simulate the user hitting Retry before the auto-remove task runs.
        manager.reset_for_retry(entry.id)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())

    assert manager.get(entry.id).status == "queued"


def test_request_pause_returns_false_when_entry_not_currently_downloading():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    orchestrator = DownloadOrchestrator(manager)
    assert orchestrator.request_pause(entry.id) is False
    assert orchestrator.request_pause("no-such-entry") is False


def test_pause_mid_download_stops_the_download_and_marks_entry_paused():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    hook_called = threading.Event()
    pause_requested = threading.Event()
    ran_to_completion = threading.Event()

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        progress_hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})
        hook_called.set()
        # Block until the test has actually called request_pause() — the
        # next progress_hook call is then guaranteed to observe the pause
        # flag and raise, unwinding this "blocking" call deterministically
        # rather than racing a real download's own timing.
        assert pause_requested.wait(timeout=5)
        progress_hook({"status": "downloading", "downloaded_bytes": 20, "total_bytes": 100})
        ran_to_completion.set()
        return {"title": "should not finish"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download)

    async def run():
        task = asyncio.create_task(orchestrator.download_entry(entry.id, "/tmp/out"))
        while not hook_called.is_set():
            await asyncio.sleep(0.01)
        assert orchestrator.request_pause(entry.id) is True
        pause_requested.set()
        await task

    asyncio.run(run())

    assert manager.get(entry.id).status == "paused"
    assert not ran_to_completion.is_set()


def test_paused_entry_keeps_its_progress_and_is_not_auto_removed():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])

    hook_called = threading.Event()
    pause_requested = threading.Event()

    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
        progress_hook({"status": "downloading", "downloaded_bytes": 40, "total_bytes": 100})
        hook_called.set()
        assert pause_requested.wait(timeout=5)
        progress_hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
        return {"title": "should not finish"}

    orchestrator = DownloadOrchestrator(manager, download_fn=fake_download, auto_remove_delay=0)

    async def run():
        task = asyncio.create_task(orchestrator.download_entry(entry.id, "/tmp/out"))
        while not hook_called.is_set():
            await asyncio.sleep(0.01)
        orchestrator.request_pause(entry.id)
        pause_requested.set()
        await task
        # Let the scheduled auto-remove task get its turn on the event loop.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())

    updated = manager.get(entry.id)
    assert updated.status == "paused"
    assert updated.percent == 40.0
    assert manager.get_all() != []


def test_resolve_output_folder_appends_sanitized_subfolder_when_given():
    result = resolve_output_folder("C:/downloads", "My Course: Part 1")
    assert result == str(Path("C:/downloads") / "My Course_ Part 1")


def test_resolve_output_folder_returns_base_unchanged_when_subfolder_none():
    assert resolve_output_folder("C:/downloads", None) == "C:/downloads"


def test_resolve_output_folder_returns_base_unchanged_when_subfolder_blank():
    assert resolve_output_folder("C:/downloads", "   ") == "C:/downloads"
