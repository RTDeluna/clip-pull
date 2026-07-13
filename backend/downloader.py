import asyncio
import logging
import os
import random
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from background_tasks import track_task
from queue_manager import QueueManager

logger = logging.getLogger("clippull")

MAX_CONCURRENT_DOWNLOADS = 3
REFERER_BLOCKED_MESSAGE = "Blocked — this video may require the course site as referer"
PROGRESS_THROTTLE_SECONDS = 0.25
CONCURRENT_FRAGMENT_DOWNLOADS = 8
AUTO_REMOVE_DELAY_SECONDS = 4.0
# The first attempt plus up to 2 automatic retries for transient failures
# (see is_auto_retryable_error) before falling through to a normal error the
# user retries manually. Exponential backoff with jitter between attempts,
# per standard retry-resilience guidance (AWS Builders' Library: "Timeouts,
# retries, and backoff with jitter").
AUTO_RETRY_MAX_ATTEMPTS = 3
AUTO_RETRY_BASE_DELAY_SECONDS = 2.0
AUTO_RETRY_MAX_DELAY_SECONDS = 20.0

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi_codes(text: str) -> str:
    """yt-dlp's own exception/log messages sometimes embed ANSI color codes
    meant for terminal display (e.g. a colorized 'ERROR:' prefix) — left
    in, these render as mojibake in a browser/Electron UI."""
    return ANSI_ESCAPE_RE.sub("", text)


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name)
    cleaned = cleaned.strip().strip(".")
    return cleaned or "untitled"


def resolve_output_folder(base_folder: str, subfolder: Optional[str]) -> str:
    if not subfolder or not subfolder.strip():
        return base_folder
    return str(Path(base_folder) / sanitize_filename(subfolder))


def _dangerous_output_roots() -> list[Path]:
    """Directories a video downloader has no legitimate reason to write
    into, regardless of how output_folder was sourced (the native folder
    picker, a History retry, or a raw API caller). Startup folders are a
    persistence primitive (anything dropped there runs on next login);
    system/program directories are just never a real destination. Read from
    the environment rather than hardcoded drive letters so this still works
    on a non-C: install."""
    roots: list[Path] = []
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        roots.append(Path(windir))
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"):
        value = os.environ.get(var)
        if value:
            roots.append(Path(value))
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup")
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        roots.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "StartUp")
    # macOS/Linux equivalents -- inert on Windows, real coverage if this app
    # is ever run elsewhere.
    unix_roots = (
        "/System", "/Library/LaunchAgents", "/Library/LaunchDaemons",
        "/etc", "/bin", "/sbin", "/usr",
    )
    for unix_root in unix_roots:
        roots.append(Path(unix_root))
    roots.append(Path.home() / "Library" / "LaunchAgents")
    return roots


def is_dangerous_output_folder(folder: str) -> bool:
    """True if `folder` is (or is inside) one of the directories above.
    Resolves symlinks/`..` segments first so those can't be used to alias
    around the check. Any path that fails to resolve at all is treated as
    dangerous too -- fail closed rather than silently letting a malformed
    path through."""
    try:
        resolved = Path(folder).resolve()
    except OSError:
        return True
    for root in _dangerous_output_roots():
        try:
            root_resolved = root.resolve()
        except OSError:
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            return True
    return False


def get_bundled_ffmpeg_path() -> Optional[str]:
    """Path to the ffmpeg binary bundled with the packaged app, if main.js
    found one alongside the backend and passed it down via env var. None in
    dev runs or older installs without a bundled copy, where ffmpeg has to
    come from the system PATH instead (see check_ffmpeg_available)."""
    path = os.environ.get("CLIP_PULL_FFMPEG_PATH")
    return path if path and os.path.isfile(path) else None


def check_ffmpeg_available() -> bool:
    return get_bundled_ffmpeg_path() is not None or shutil.which("ffmpeg") is not None


def check_aria2c_available() -> bool:
    return shutil.which("aria2c") is not None


def resolve_use_aria2c(enabled: bool) -> bool:
    return enabled and check_aria2c_available()


def select_format() -> str:
    """Vimeo/Loom's highest-quality formats are separate video+audio streams
    that need ffmpeg to merge. Without ffmpeg, requesting that combination
    doesn't gracefully degrade on its own: yt-dlp still selects it (the "/"
    fallback only applies at format-selection time, not merge time) and then
    hard-fails once the merge step actually runs. Checking ffmpeg's presence
    upfront and requesting a single pre-muxed format instead avoids ever
    hitting that failure, at the cost of a possibly lower max quality on
    machines without ffmpeg."""
    return "bestvideo+bestaudio/best" if check_ffmpeg_available() else "best"


def format_speed(speed_bytes_per_sec: Optional[float]) -> Optional[str]:
    """Human-readable speed string computed from yt-dlp's numeric `speed`
    field. Deliberately does not use yt-dlp's own `_speed_str`, which embeds
    ANSI terminal color codes meant for console output and renders as
    mojibake in a browser/Electron UI."""
    if not speed_bytes_per_sec or speed_bytes_per_sec <= 0:
        return None
    value = float(speed_bytes_per_sec)
    for unit in ("B/s", "KiB/s", "MiB/s", "GiB/s"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TiB/s"


def format_bytes(num_bytes: Optional[float]) -> Optional[str]:
    """Human-readable byte-count string (e.g. '45.2MB'). Unlike format_speed,
    0 is a legitimate value (a fresh download starts at 0 of N bytes) and
    formats as '0B' — only a genuinely missing reading (None) returns None."""
    if num_bytes is None or num_bytes < 0:
        return None
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{int(value)}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def stream_stage(info_dict: dict) -> Optional[str]:
    """Labels which half of a split video+audio download is currently in
    progress, from yt-dlp's per-format info_dict passed into progress_hook.
    None for an ordinary single-stream (progressive) format, where there's
    nothing to label."""
    vcodec = info_dict.get("vcodec")
    acodec = info_dict.get("acodec")
    has_video = bool(vcodec) and vcodec != "none"
    has_audio = bool(acodec) and acodec != "none"
    if has_video and not has_audio:
        return "video"
    if has_audio and not has_video:
        return "audio"
    return None


def is_referer_blocked_error(exc: Exception) -> bool:
    return "403" in str(exc)


class _YtDlpLogger:
    """yt-dlp's own report_error/report_warning write straight to stderr via
    to_stderr — completely ignoring the quiet/no_warnings opts — before the
    matching exception is raised and caught below, so without this, every
    failed download prints raw, ANSI-coded, un-humanized text straight to
    the console in addition to (and before) showing up as a proper UI error.
    Passing this as the `logger` opt is yt-dlp's supported way to redirect
    that output; downgrading it to DEBUG keeps it out of the console by
    default while still leaving a trace if debug logging is ever enabled."""

    def debug(self, msg: str) -> None:
        logger.debug(msg)

    def warning(self, msg: str) -> None:
        logger.debug(msg)

    def error(self, msg: str) -> None:
        logger.debug(msg)


_YTDLP_LOGGER = _YtDlpLogger()


FRIENDLY_ERROR_RULES: list[tuple["re.Pattern[str]", str]] = [
    (
        re.compile(r"WinError 32|being used by another process", re.IGNORECASE),
        "This file is locked by another process — it may already be downloading "
        "in another queue entry, or a different app has it open. Wait a moment "
        "and hit Retry.",
    ),
    (
        # Checked before the generic Errno-2 rule below: a too-long path can
        # otherwise get misdiagnosed as "interrupted, hit Retry" -- retrying
        # would fail identically every time, since the real cause (path
        # length) never changes. WinError 206 and ENAMETOOLONG are
        # unambiguous signals for this specific condition (unlike bare
        # WinError 3, which can also mean an unrelated missing/deleted path).
        re.compile(r"WinError 206|File name too long|\[Errno 36\]", re.IGNORECASE),
        "This video's title makes the destination file path too long for "
        "Windows. Try a shorter Course/batch folder name, or choose an "
        "output folder closer to your drive's root (e.g. C:\\Downloads).",
    ),
    (
        re.compile(r"WinError 112|\[Errno 28\]|No space left on device", re.IGNORECASE),
        "Not enough disk space on the destination drive. Free up space and hit Retry.",
    ),
    (
        # WinError 223 (ERROR_FILE_TOO_LARGE) and Errno 27 (EFBIG) are what
        # Windows/Python report when a single file would exceed FAT32's
        # 4GB-per-file limit -- distinct from actually running out of disk
        # space, so it needs its own message pointing at the real fix
        # (reformat/choose an NTFS or exFAT drive) rather than "free up space".
        re.compile(r"WinError 223|\[Errno 27\]|File too large", re.IGNORECASE),
        "This video is larger than 4GB, which exceeds the FAT32 file size limit "
        "on the destination drive. Choose an output folder on an NTFS or exFAT "
        "drive instead (FAT32 USB drives and SD cards are commonly formatted "
        "this way).",
    ),
    (
        re.compile(r"\[Errno 2\]|No such file or directory", re.IGNORECASE),
        "The download was interrupted before it finished writing to disk. "
        "Hit Retry to start it again.",
    ),
    (
        re.compile(r"HTTP Error 404|404: Not Found", re.IGNORECASE),
        "This video couldn't be found — the link may be private, deleted, or mistyped.",
    ),
    (
        re.compile(
            r"HTTP Error 5\d\d|Service Unavailable|Internal Server Error|"
            r"Bad Gateway|Gateway Timeout",
            re.IGNORECASE,
        ),
        "The video host's server had a temporary problem. Wait a moment and hit Retry.",
    ),
    (
        re.compile(
            r"No video formats found|Requested format is not available|"
            r"DRM protected|requires payment|members-only|"
            r"only available for registered users",
            re.IGNORECASE,
        ),
        "This video isn't downloadable — it may be DRM-protected, private, "
        "members-only, or otherwise restricted at the source.",
    ),
    (
        re.compile(
            r"getaddrinfo failed|Failed to establish a new connection|"
            r"Name or service not known|Network is unreachable|"
            r"ConnectionError|Connection refused",
            re.IGNORECASE,
        ),
        "Couldn't reach the video server. Check your internet connection and try again.",
    ),
    (
        re.compile(r"Read timed out|Connection timed out|timed out", re.IGNORECASE),
        "The connection to the video server timed out. Check your internet connection and hit Retry.",
    ),
    (
        re.compile(r"HTTP Error 429|Too Many Requests", re.IGNORECASE),
        "The video host is temporarily rate-limiting downloads. Wait a few minutes and hit Retry.",
    ),
    # No standalone 403 rule here -- is_referer_blocked_error (checked before
    # this rule list even runs) already catches any "403" and maps it to
    # REFERER_BLOCKED_MESSAGE, so a second 403 rule in this list would be
    # unreachable dead code.
    (
        re.compile(
            r"Video unavailable|This video is (unavailable|private)|"
            r"Private video|has been removed",
            re.IGNORECASE,
        ),
        "This video is unavailable — it may have been removed, made private, or deleted by its owner.",
    ),
    (
        re.compile(
            r"not available in your country|blocked it (on|in) copyright grounds|"
            r"not available on this app|geo.?restrict",
            re.IGNORECASE,
        ),
        "This video isn't available in your region.",
    ),
    (
        re.compile(r"Sign in to confirm your age|age.?restrict", re.IGNORECASE),
        "This video is age-restricted and requires signing in on the source site to view.",
    ),
    (
        re.compile(r"This live event will begin|Premieres in|live event", re.IGNORECASE),
        "This is an upcoming live stream that hasn't started yet — it can't be downloaded until it airs.",
    ),
    (
        re.compile(r"Unsupported URL", re.IGNORECASE),
        "This link isn't a supported video page — double-check the URL.",
    ),
    (
        re.compile(
            r"Unable to extract|Failed to parse|Unable to download webpage",
            re.IGNORECASE,
        ),
        "This site's page couldn't be read — it may have changed its layout, or this isn't a "
        "direct video link.",
    ),
]

# Shown for any download failure that doesn't match a rule above, instead of
# the raw exception text (which is often full of yt-dlp/OS internals a user
# can't act on -- WinError codes, stack fragments, ANSI artifacts). The raw
# detail isn't lost: it's still captured in full by the logger.exception(...)
# call at the download_entry catch site, just not surfaced in the UI.
GENERIC_ERROR_MESSAGE = (
    "This download failed unexpectedly. Hit Retry, or check the app log if it keeps happening."
)


def humanize_error_reason(exc: Exception) -> str:
    """Rewrites a download failure into a short, actionable message. End
    users should never see raw WinError/Errno codes, yt-dlp/Python exception
    internals, file paths, or the ANSI color codes yt-dlp's own log lines
    embed for terminal display -- every path through this function returns
    either a specific rule match or the generic fallback, never raw
    yt-dlp/OS exception text. The one exception (heh) is our own
    InsufficientDiskSpaceError, whose str() is already a friendly,
    purpose-written message -- referencing that class here (defined later
    in this module) is fine since it's only resolved when this function is
    actually called, not when it's defined."""
    if is_referer_blocked_error(exc):
        return REFERER_BLOCKED_MESSAGE
    if isinstance(exc, InsufficientDiskSpaceError):
        return str(exc)
    raw = strip_ansi_codes(str(exc))
    for pattern, friendly in FRIENDLY_ERROR_RULES:
        if pattern.search(raw):
            return friendly
    return GENERIC_ERROR_MESSAGE


def build_ydl_opts(
    output_template: str,
    referer: Optional[str],
    progress_hook: Callable[[dict], None],
    concurrent_fragment_downloads: int = CONCURRENT_FRAGMENT_DOWNLOADS,
    use_aria2c: bool = False,
) -> dict:
    opts = {
        "format": select_format(),
        "outtmpl": output_template,
        "concurrent_fragment_downloads": concurrent_fragment_downloads,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
        "logger": _YTDLP_LOGGER,
        # yt-dlp used as a library (not via its own CLI) gets none of the
        # CLI's default retry counts -- leaving these unset means 0 retries,
        # so a single transient blip anywhere in a long download aborts the
        # whole thing. These match yt-dlp's own CLI defaults.
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 10,
        "socket_timeout": 30,
        # Keeps generated filenames Windows-safe and bounded well under the
        # 260-char MAX_PATH, so a long video title can't silently produce an
        # unwritable path (see FRIENDLY_ERROR_RULES for the residual case
        # where the destination folder itself is already deeply nested).
        "windowsfilenames": True,
        "trim_filenames": 150,
    }
    bundled_ffmpeg = get_bundled_ffmpeg_path()
    if bundled_ffmpeg:
        # Without this, yt-dlp falls back to searching PATH on its own --
        # pointless on a packaged install where ffmpeg was never added to
        # PATH in the first place, only unpacked alongside the backend exe.
        opts["ffmpeg_location"] = bundled_ffmpeg
    if referer:
        opts["http_headers"] = {"Referer": referer}
    if use_aria2c:
        # yt-dlp's own Aria2cFD already applies well-tuned parallelism
        # defaults (-x16 -j16 -s16); no need to override external_downloader_args.
        opts["external_downloader"] = "aria2c"
    return opts


def run_download(
    url: str,
    output_folder: str,
    referer: Optional[str],
    progress_hook: Callable[[dict], None],
    concurrent_fragment_downloads: int = CONCURRENT_FRAGMENT_DOWNLOADS,
    aria2c_enabled: bool = True,
) -> dict:
    """Blocking — must run in a thread executor. Real yt-dlp integration;
    verified manually against live Vimeo links (see design spec Testing section)."""
    import yt_dlp

    # A URL starting with "-" would be parsed as a flag rather than a
    # positional argument by aria2c (yt-dlp's external_downloader, when
    # enabled). is_supported_url's http(s):// scheme check already rejects
    # this incidentally today -- no "-..." string has a valid scheme -- but
    # that's not a guarantee that check can't loosen in the future. This is
    # the actual point aria2c gets invoked, so it's the right place for an
    # explicit, intentional guard rather than relying on an unrelated check
    # elsewhere staying strict.
    if url.startswith("-"):
        raise ValueError("Refusing to download a URL that starts with '-'.")

    output_template = str(Path(output_folder) / "%(title)s [%(id)s].%(ext)s")
    use_aria2c = resolve_use_aria2c(aria2c_enabled)
    opts = build_ydl_opts(
        output_template, referer, progress_hook, concurrent_fragment_downloads, use_aria2c
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)


def probe_total_bytes(url: str, referer: Optional[str]) -> Optional[int]:
    """Best-effort lookahead so the progress bar can show one continuously
    climbing percentage across every stream in a download (e.g. yt-dlp
    downloading separate video and audio streams for ffmpeg to merge) instead
    of restarting at 0% each time it begins the next stream. Runs a
    metadata-only extraction (no download) to read the selected formats'
    advertised filesize; returns None whenever that isn't available (or
    extraction fails for any other reason) — the caller then falls back to
    reporting each stream's own percentage, exactly as before this existed."""
    import yt_dlp

    opts = {
        # Must match build_ydl_opts' own format selection exactly, or this
        # probe's size lookahead would describe a different set of streams
        # than what actually gets downloaded (e.g. reporting the video+audio
        # combo's size while the real download falls back to a single file),
        # throwing the progress bar's percentage off.
        "format": select_format(),
        "quiet": True,
        "no_warnings": True,
        "logger": _YTDLP_LOGGER,
        "retries": 10,
        "socket_timeout": 30,
    }
    bundled_ffmpeg = get_bundled_ffmpeg_path()
    if bundled_ffmpeg:
        opts["ffmpeg_location"] = bundled_ffmpeg
    if referer:
        opts["http_headers"] = {"Referer": referer}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None
    if not isinstance(info, dict):
        return None
    formats = info.get("requested_formats") or [info]
    sizes = [f.get("filesize") or f.get("filesize_approx") for f in formats]
    if not sizes or any(size is None for size in sizes):
        return None
    return sum(sizes)


class DownloadPaused(Exception):
    """Raised from progress_hook — which yt-dlp invokes on the download's
    worker thread — to unwind an in-flight ydl.extract_info() call the
    moment a pause is requested. asyncio.Task.cancel() alone can't
    interrupt code already running inside a ThreadPoolExecutor thread, so
    this cooperative check-and-raise is what actually stops the download."""


class InsufficientDiskSpaceError(Exception):
    """Raised before attempting a download when the destination drive
    doesn't have enough free space for the video's known size — reported
    immediately with an actionable message, instead of after wasting time
    and bandwidth partway through, or as a confusing raw OS error. Its own
    str() is already the friendly message, since it can't match any of
    FRIENDLY_ERROR_RULES (those are for real yt-dlp/OS error text)."""


# Patterns worth an automatic retry with no user action needed: transient
# network blips, a temporarily unreachable/overloaded server, or a file lock
# likely to clear on its own. Deliberately narrower than FRIENDLY_ERROR_RULES
# above -- anything not matched here (disk full, path too long, DRM, a
# missing video, a referer block, or any error this app doesn't specifically
# recognize) is left for the user's own manual Retry, since auto-retrying
# those with identical inputs can't succeed.
AUTO_RETRYABLE_ERROR_RE = re.compile(
    r"WinError 32|being used by another process|"
    r"HTTP Error 5\d\d|Service Unavailable|Internal Server Error|Bad Gateway|Gateway Timeout|"
    r"getaddrinfo failed|Failed to establish a new connection|"
    r"Name or service not known|Network is unreachable|ConnectionError|Connection refused|"
    r"Read timed out|Connection timed out|timed out|"
    r"\[Errno 2\]|No such file or directory",
    re.IGNORECASE,
)


def is_auto_retryable_error(exc: Exception) -> bool:
    """See AUTO_RETRYABLE_ERROR_RE. A referer block and insufficient disk
    space are excluded even if their text happened to match: retrying either
    with identical inputs seconds later can't succeed -- the user has to
    supply a different referer or free up space first."""
    if is_referer_blocked_error(exc) or isinstance(exc, InsufficientDiskSpaceError):
        return False
    return bool(AUTO_RETRYABLE_ERROR_RE.search(strip_ansi_codes(str(exc))))


def auto_retry_delay_seconds(attempt: int, base_delay: float) -> float:
    """Exponential backoff with jitter before auto-retry attempt `attempt`
    (1-based, counting the retry itself): base_delay, 2x, 4x..., capped at
    AUTO_RETRY_MAX_DELAY_SECONDS, plus up to 30% random jitter so several
    queue entries hitting the same transient failure at once don't all
    retry in lockstep."""
    base = min(base_delay * (2 ** (attempt - 1)), AUTO_RETRY_MAX_DELAY_SECONDS)
    return base + random.uniform(0, base * 0.3)


class DownloadOrchestrator:
    def __init__(
        self,
        queue_manager: QueueManager,
        max_concurrent: int = MAX_CONCURRENT_DOWNLOADS,
        download_fn: Callable = run_download,
        probe_fn: Optional[Callable[[str, Optional[str]], Optional[int]]] = None,
        get_max_concurrent: Optional[Callable[[], int]] = None,
        get_fragment_concurrency: Optional[Callable[[], int]] = None,
        get_aria2c_enabled: Optional[Callable[[], bool]] = None,
        record_history: Optional[Callable[..., None]] = None,
        on_batch_complete: Optional[Callable[[str, dict], None]] = None,
        auto_remove_delay: float = AUTO_REMOVE_DELAY_SECONDS,
        auto_retry_max_attempts: int = AUTO_RETRY_MAX_ATTEMPTS,
        auto_retry_base_delay: float = AUTO_RETRY_BASE_DELAY_SECONDS,
    ):
        self.queue_manager = queue_manager
        self.download_fn = download_fn
        self.probe_fn = probe_fn
        self.get_max_concurrent = get_max_concurrent or (lambda: max_concurrent)
        self.get_fragment_concurrency = get_fragment_concurrency or (
            lambda: CONCURRENT_FRAGMENT_DOWNLOADS
        )
        self.get_aria2c_enabled = get_aria2c_enabled or (lambda: True)
        self.auto_retry_max_attempts = auto_retry_max_attempts
        self.auto_retry_base_delay = auto_retry_base_delay
        self.record_history = record_history or (lambda **_: None)
        self.on_batch_complete = on_batch_complete
        self.auto_remove_delay = auto_remove_delay
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._pause_requested: set[str] = set()
        self._shared_semaphore: Optional[asyncio.Semaphore] = None
        self._shared_semaphore_limit: Optional[int] = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        """One Semaphore shared by every download entry point (batch,
        retry, resume) so max_concurrent_downloads is actually a global cap
        instead of a per-call one. Previously each call to download_all
        built its own fresh Semaphore -- retrying or resuming entries while
        a batch already held its full allotment let real concurrency
        silently exceed the configured limit, since each call's semaphore
        only knew about its own downloads. Rebuilt only when the live
        setting value actually changes; downloads already holding a permit
        from a since-replaced semaphore simply keep running under the old
        cap until they finish -- a one-time, self-correcting transition,
        not an ongoing bypass."""
        limit = self.get_max_concurrent()
        if self._shared_semaphore is None or self._shared_semaphore_limit != limit:
            self._shared_semaphore = asyncio.Semaphore(limit)
            self._shared_semaphore_limit = limit
        return self._shared_semaphore

    def request_pause(self, entry_id: str) -> bool:
        """Flags the in-flight download for entry_id to stop, if one is
        currently running. Deliberately does not call asyncio.Task.cancel()
        here: cancelling a task that's awaiting run_in_executor() detaches
        it from the future immediately, racing ahead of (and outliving) the
        worker thread that's still actually running yt-dlp — the thread
        would keep downloading, unsupervised, with no reliable way to know
        when it's actually done. Instead, progress_hook (invoked from that
        worker thread) checks this flag itself and raises DownloadPaused,
        so the thread's own next tick is what cleanly unwinds it. Returns
        True if a download was actually running, False otherwise."""
        task = self._active_tasks.get(entry_id)
        if task is None or task.done():
            return False
        self._pause_requested.add(entry_id)
        return True

    async def _remove_from_queue_after_delay(self, entry_id: str) -> None:
        if self.auto_remove_delay > 0:
            await asyncio.sleep(self.auto_remove_delay)
        try:
            entry = self.queue_manager.get(entry_id)
        except KeyError:
            return  # already removed (e.g. a manual "clear completed")
        # Only remove if it's still in the same terminal state — a retry
        # started during the delay window resets it to "queued" and must
        # not be yanked out from under an in-flight re-download.
        if entry.status in ("done", "error"):
            self.queue_manager.remove(entry_id)

    async def download_entry(
        self,
        entry_id: str,
        output_folder: str,
        referer: Optional[str] = None,
        semaphore: Optional[asyncio.Semaphore] = None,
    ) -> None:
        sem = semaphore if semaphore is not None else self._get_semaphore()
        async with sem:
            self._active_tasks[entry_id] = asyncio.current_task()
            self.queue_manager.set_status(entry_id, "downloading")
            loop = asyncio.get_running_loop()
            entry = self.queue_manager.get(entry_id)
            url = entry.url

            # Best-effort lookahead so a download with separate video+audio
            # streams shows one continuously-climbing percentage across both,
            # instead of the bar completing at 100% then restarting at 0% when
            # the second stream begins. probe_fn is None by default (tests and
            # any caller that doesn't opt in keep the old per-stream behavior
            # unchanged); the real app wires in probe_total_bytes.
            expected_total_bytes: Optional[int] = None
            if self.probe_fn is not None:
                expected_total_bytes = await loop.run_in_executor(None, self.probe_fn, url, referer)

            try:
                # Only checkable when the probe knew the video's size ahead
                # of time; if it didn't, this is skipped and a mid-download
                # "disk full" still gets a friendly message via
                # FRIENDLY_ERROR_RULES instead of a raw OS error.
                if expected_total_bytes:
                    try:
                        free_bytes = shutil.disk_usage(output_folder).free
                    except OSError:
                        free_bytes = None
                    if free_bytes is not None and free_bytes < expected_total_bytes:
                        raise InsufficientDiskSpaceError(
                            f"Not enough disk space — this download needs about "
                            f"{format_bytes(expected_total_bytes)} but only "
                            f"{format_bytes(free_bytes)} is free on that drive."
                        )

                info = None
                for attempt in range(1, self.auto_retry_max_attempts + 1):
                    # Fresh per-attempt progress-tracking state -- a retried
                    # attempt restarts yt-dlp's own extract_info (which
                    # resumes from its own .part files where possible), so
                    # the running totals this closure accumulates must reset
                    # too, or a retry would double-count bytes left over from
                    # the abandoned attempt.
                    last_progress_time = 0.0
                    smoothed_speed: Optional[float] = None
                    prior_streams_bytes = 0
                    last_stream_key: Optional[str] = None
                    last_stream_total: Optional[int] = None
                    last_stage: Optional[str] = None

                    def progress_hook(d: dict) -> None:
                        nonlocal last_progress_time, smoothed_speed, prior_streams_bytes
                        nonlocal last_stream_key, last_stream_total, last_stage
                        if entry_id in self._pause_requested:
                            raise DownloadPaused()

                        # Registered as both a progress_hooks and postprocessor_hooks
                        # callback (see build_ydl_opts) — this branch handles the
                        # latter, which fires with a completely different dict shape
                        # (no downloaded_bytes/total_bytes) when ffmpeg starts
                        # merging the finished video+audio streams.
                        if d.get("postprocessor") == "Merger" and d.get("status") == "started":
                            if last_stage != "merging":
                                last_stage = "merging"
                                loop.call_soon_threadsafe(self.queue_manager.set_stage, entry_id, "merging")
                            return

                        if d.get("status") != "downloading":
                            return
                        now = time.monotonic()
                        if now - last_progress_time < PROGRESS_THROTTLE_SECONDS:
                            return
                        last_progress_time = now
                        downloaded = d.get("downloaded_bytes")
                        total = d.get("total_bytes") or d.get("total_bytes_estimate")
                        info_dict = d.get("info_dict") or {}
                        stage = stream_stage(info_dict)

                        # Identifies which underlying stream/format is currently
                        # downloading. Deliberately NOT based on `total`: for
                        # fragmented/HLS/DASH formats, total_bytes_estimate is
                        # continuously *refined* over the course of a single
                        # stream's download (it's an evolving estimate, not a fixed
                        # value) — comparing raw totals here previously mistook
                        # ordinary estimate fluctuations for "a new stream started,"
                        # which kept folding the same stream's size into
                        # prior_streams_bytes over and over, inflating the reported
                        # total far beyond the real combined file size.
                        stream_key = info_dict.get("format_id") or stage

                        # A genuine change in which stream is downloading — fold the
                        # just-finished stream's last known size into the running
                        # total so overall progress keeps climbing instead of
                        # resetting. Only fires once per real transition, since
                        # last_stream_key is updated to match immediately below.
                        if (
                            stream_key is not None
                            and last_stream_key is not None
                            and stream_key != last_stream_key
                            and last_stream_total is not None
                        ):
                            prior_streams_bytes += last_stream_total
                        if stream_key is not None:
                            last_stream_key = stream_key
                        if total is not None:
                            last_stream_total = total

                        # downloaded/total are always folded onto prior_streams_bytes,
                        # whether or not the upfront probe knew the true grand total —
                        # this keeps both numbers climbing across a stream transition
                        # instead of snapping down to just the new (much smaller)
                        # stream's own numbers, which used to read as a "reset."
                        overall_downloaded = (
                            prior_streams_bytes + downloaded if downloaded is not None else None
                        )
                        if expected_total_bytes:
                            overall_total = expected_total_bytes
                        elif total is not None:
                            overall_total = prior_streams_bytes + total
                        else:
                            overall_total = None

                        if overall_downloaded is not None and overall_total:
                            percent = round(min(overall_downloaded / overall_total, 1.0) * 100, 1)
                        else:
                            percent = 0.0
                        # Real bytes are flowing but yt-dlp never learned (or hasn't
                        # yet learned) this stream's total size -- percent above is
                        # stuck at 0 in that case, so flag it separately rather than
                        # let the frontend mistake "unknowable" for "no progress."
                        size_unknown = overall_total is None and overall_downloaded is not None
                        downloaded_size = format_bytes(overall_downloaded)
                        total_size = format_bytes(overall_total)

                        if stage != last_stage:
                            last_stage = stage
                            loop.call_soon_threadsafe(self.queue_manager.set_stage, entry_id, stage)

                        # yt-dlp's raw per-chunk speed swings wildly between calls
                        # (fragment boundaries, brief network hiccups) even when the
                        # real throughput is steady — an exponential moving average
                        # reads as fast and stable instead of erratic, matching how
                        # browsers/other download managers smooth their speed readout.
                        raw_speed = d.get("speed")
                        if raw_speed:
                            smoothed_speed = (
                                raw_speed
                                if smoothed_speed is None
                                else 0.3 * raw_speed + 0.7 * smoothed_speed
                            )
                        speed = format_speed(smoothed_speed)
                        eta_raw = d.get("eta")
                        eta = int(eta_raw) if eta_raw is not None else None
                        loop.call_soon_threadsafe(
                            self.queue_manager.update_progress,
                            entry_id, percent, speed, eta, downloaded_size, total_size, smoothed_speed,
                            size_unknown,
                        )

                    try:
                        info = await loop.run_in_executor(
                            None,
                            self.download_fn,
                            url,
                            output_folder,
                            referer,
                            progress_hook,
                            self.get_fragment_concurrency(),
                            self.get_aria2c_enabled(),
                        )
                        break  # success -- fall through to the completion handling below
                    except (DownloadPaused, asyncio.CancelledError):
                        # User-initiated, never a transient failure to smooth
                        # over -- always propagate immediately, never retried.
                        raise
                    except Exception as exc:
                        if attempt >= self.auto_retry_max_attempts or not is_auto_retryable_error(exc):
                            raise
                        delay = auto_retry_delay_seconds(attempt, self.auto_retry_base_delay)
                        logger.info(
                            "Auto-retrying %s after a transient error (attempt %d/%d in %.1fs): %s",
                            url, attempt + 1, self.auto_retry_max_attempts, delay, exc,
                        )
                        self.queue_manager.set_stage(entry_id, "retrying")
                        await asyncio.sleep(delay)
                        # loop continues to the next attempt

                # progress_hook's last update is scheduled via
                # call_soon_threadsafe from the worker thread and isn't
                # guaranteed to be processed before this coroutine resumes.
                # Yielding once lets any already-scheduled callback run first,
                # so the completion state below is always authoritative.
                await asyncio.sleep(0)

                final_total_size = self.queue_manager.get(entry_id).total_size
                title = info.get("title") if isinstance(info, dict) else None
                output_path = None
                try:
                    if isinstance(info, dict):
                        downloads = info.get("requested_downloads") or []
                        if downloads:
                            output_path = downloads[-1].get("filepath")
                except Exception:
                    logger.exception("Failed to read output_path from yt-dlp's result for %s", url)
                    output_path = None

                if title:
                    self.queue_manager.set_title(entry_id, title)
                self.queue_manager.set_stage(entry_id, None)
                self.queue_manager.update_progress(entry_id, 100.0, None, 0)
                self.queue_manager.set_status(entry_id, "done")
                # History recording is a side-channel (e.g. SQLite write) that
                # must never affect the download's already-determined "done"
                # status, so a failure here must not fall through to `except`.
                try:
                    history_row = self.record_history(
                        entry_id=entry_id,
                        batch_id=entry.batch_id,
                        url=url,
                        title=title,
                        output_path=output_path,
                        total_size=final_total_size,
                        status="done",
                        error_reason=None,
                        retry_count=entry.retry_count,
                        update_id=entry.history_id,
                        output_folder=entry.output_folder,
                    )
                    if history_row:
                        self.queue_manager.set_history_id(entry_id, history_row["id"])
                except Exception:
                    logger.exception("Failed to record history (done) for %s", url)
            except (DownloadPaused, asyncio.CancelledError):
                self.queue_manager.mark_paused(entry_id)
            except Exception as exc:
                reason = humanize_error_reason(exc)
                # The UI only ever shows the humanized/friendly message, not
                # the original technical detail -- log the raw exception
                # (with traceback) so it's still recoverable for debugging.
                logger.exception("Download failed for %s", url)
                self.queue_manager.set_error(entry_id, reason)
                # As above: history recording must not raise out of the error
                # path and mask/replace the error status already set.
                try:
                    history_row = self.record_history(
                        entry_id=entry_id,
                        batch_id=entry.batch_id,
                        url=url,
                        title=entry.title,
                        output_path=None,
                        total_size=None,
                        status="error",
                        error_reason=reason,
                        retry_count=entry.retry_count,
                        update_id=entry.history_id,
                        output_folder=entry.output_folder,
                    )
                    if history_row:
                        self.queue_manager.set_history_id(entry_id, history_row["id"])
                except Exception:
                    logger.exception("Failed to record history (error) for %s", url)
            finally:
                self._active_tasks.pop(entry_id, None)
                self._pause_requested.discard(entry_id)
                # A raising on_batch_complete (or is_batch_complete/batch_summary)
                # must not escape download_entry/download_all as an unhandled
                # exception in asyncio.gather, which would leave sibling
                # in-flight downloads in the same batch running unsupervised.
                try:
                    if entry.batch_id and self.on_batch_complete:
                        if self.queue_manager.is_batch_complete(entry.batch_id):
                            summary = self.queue_manager.batch_summary(entry.batch_id)
                            self.on_batch_complete(entry.batch_id, summary)
                except Exception:
                    logger.exception("on_batch_complete failed for batch %s", entry.batch_id)
                # A finished entry (done or error) is already recorded in
                # history, so it's cleared out of the live queue shortly
                # after — the user briefly sees the final state, then it's
                # gone from Queue and only lives on in History.
                track_task(asyncio.create_task(self._remove_from_queue_after_delay(entry_id)))

    async def download_all(
        self, entry_ids: list[str], output_folder: str, referer: Optional[str] = None
    ) -> None:
        semaphore = self._get_semaphore()
        await asyncio.gather(
            *(
                self.download_entry(entry_id, output_folder, referer, semaphore)
                for entry_id in entry_ids
            )
        )
