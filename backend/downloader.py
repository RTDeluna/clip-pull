import asyncio
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from queue_manager import QueueManager

MAX_CONCURRENT_DOWNLOADS = 3
REFERER_BLOCKED_MESSAGE = "Blocked — this video may require the course site as referer"
PROGRESS_THROTTLE_SECONDS = 0.25
CONCURRENT_FRAGMENT_DOWNLOADS = 8


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name)
    cleaned = cleaned.strip().strip(".")
    return cleaned or "untitled"


def check_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def check_aria2c_available() -> bool:
    return shutil.which("aria2c") is not None


def resolve_use_aria2c(enabled: bool) -> bool:
    return enabled and check_aria2c_available()


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


def is_referer_blocked_error(exc: Exception) -> bool:
    return "403" in str(exc)


def build_ydl_opts(
    output_template: str,
    referer: Optional[str],
    progress_hook: Callable[[dict], None],
    concurrent_fragment_downloads: int = CONCURRENT_FRAGMENT_DOWNLOADS,
    use_aria2c: bool = False,
) -> dict:
    opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": output_template,
        "concurrent_fragment_downloads": concurrent_fragment_downloads,
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
    }
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

    output_template = str(Path(output_folder) / "%(title)s [%(id)s].%(ext)s")
    use_aria2c = resolve_use_aria2c(aria2c_enabled)
    opts = build_ydl_opts(
        output_template, referer, progress_hook, concurrent_fragment_downloads, use_aria2c
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)


class DownloadOrchestrator:
    def __init__(
        self,
        queue_manager: QueueManager,
        max_concurrent: int = MAX_CONCURRENT_DOWNLOADS,
        download_fn: Callable = run_download,
        get_max_concurrent: Optional[Callable[[], int]] = None,
        get_fragment_concurrency: Optional[Callable[[], int]] = None,
        get_aria2c_enabled: Optional[Callable[[], bool]] = None,
        record_history: Optional[Callable[..., None]] = None,
        on_batch_complete: Optional[Callable[[str, dict], None]] = None,
    ):
        self.queue_manager = queue_manager
        self.download_fn = download_fn
        self.get_max_concurrent = get_max_concurrent or (lambda: max_concurrent)
        self.get_fragment_concurrency = get_fragment_concurrency or (
            lambda: CONCURRENT_FRAGMENT_DOWNLOADS
        )
        self.get_aria2c_enabled = get_aria2c_enabled or (lambda: True)
        self.record_history = record_history or (lambda **_: None)
        self.on_batch_complete = on_batch_complete

    async def download_entry(
        self,
        entry_id: str,
        output_folder: str,
        referer: Optional[str] = None,
        semaphore: Optional[asyncio.Semaphore] = None,
    ) -> None:
        sem = semaphore if semaphore is not None else asyncio.Semaphore(self.get_max_concurrent())
        async with sem:
            self.queue_manager.set_status(entry_id, "downloading")
            loop = asyncio.get_running_loop()
            entry = self.queue_manager.get(entry_id)
            url = entry.url
            last_progress_time = 0.0

            def progress_hook(d: dict) -> None:
                nonlocal last_progress_time
                if d.get("status") != "downloading":
                    return
                now = time.monotonic()
                if now - last_progress_time < PROGRESS_THROTTLE_SECONDS:
                    return
                last_progress_time = now
                downloaded = d.get("downloaded_bytes")
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                percent = (
                    round((downloaded / total) * 100, 1)
                    if downloaded is not None and total
                    else 0.0
                )
                speed = format_speed(d.get("speed"))
                eta_raw = d.get("eta")
                eta = int(eta_raw) if eta_raw is not None else None
                downloaded_size = format_bytes(downloaded)
                total_size = format_bytes(total)
                loop.call_soon_threadsafe(
                    self.queue_manager.update_progress,
                    entry_id, percent, speed, eta, downloaded_size, total_size,
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
                    output_path = None

                if title:
                    self.queue_manager.set_title(entry_id, title)
                self.queue_manager.update_progress(entry_id, 100.0, None, 0)
                self.queue_manager.set_status(entry_id, "done")
                # History recording is a side-channel (e.g. SQLite write) that
                # must never affect the download's already-determined "done"
                # status, so a failure here must not fall through to `except`.
                try:
                    self.record_history(
                        entry_id=entry_id,
                        batch_id=entry.batch_id,
                        url=url,
                        title=title,
                        output_path=output_path,
                        total_size=final_total_size,
                        status="done",
                        error_reason=None,
                        retry_count=entry.retry_count,
                    )
                except Exception:
                    pass
            except Exception as exc:
                reason = (
                    REFERER_BLOCKED_MESSAGE
                    if is_referer_blocked_error(exc)
                    else str(exc)
                )
                self.queue_manager.set_error(entry_id, reason)
                # As above: history recording must not raise out of the error
                # path and mask/replace the error status already set.
                try:
                    self.record_history(
                        entry_id=entry_id,
                        batch_id=entry.batch_id,
                        url=url,
                        title=entry.title,
                        output_path=None,
                        total_size=None,
                        status="error",
                        error_reason=reason,
                        retry_count=entry.retry_count,
                    )
                except Exception:
                    pass
            finally:
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
                    pass

    async def download_all(
        self, entry_ids: list[str], output_folder: str, referer: Optional[str] = None
    ) -> None:
        semaphore = asyncio.Semaphore(self.get_max_concurrent())
        await asyncio.gather(
            *(
                self.download_entry(entry_id, output_folder, referer, semaphore)
                for entry_id in entry_ids
            )
        )
