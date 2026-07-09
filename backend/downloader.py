import asyncio
import re
from pathlib import Path
from typing import Callable, Optional

from queue_manager import QueueManager

MAX_CONCURRENT_DOWNLOADS = 3
REFERER_BLOCKED_MESSAGE = "Blocked — this video may require the course site as referer"


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name)
    cleaned = cleaned.strip().strip(".")
    return cleaned or "untitled"


def is_referer_blocked_error(exc: Exception) -> bool:
    return "403" in str(exc)


def build_ydl_opts(
    output_template: str,
    referer: Optional[str],
    progress_hook: Callable[[dict], None],
) -> dict:
    opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": output_template,
        "concurrent_fragment_downloads": 5,
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
    }
    if referer:
        opts["http_headers"] = {"Referer": referer}
    return opts


def run_download(
    url: str,
    output_folder: str,
    referer: Optional[str],
    progress_hook: Callable[[dict], None],
) -> dict:
    """Blocking — must run in a thread executor. Real yt-dlp integration;
    verified manually against live Vimeo links (see design spec Testing section)."""
    import yt_dlp

    output_template = str(Path(output_folder) / "%(title)s.%(ext)s")
    opts = build_ydl_opts(output_template, referer, progress_hook)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)


class DownloadOrchestrator:
    def __init__(
        self,
        queue_manager: QueueManager,
        max_concurrent: int = MAX_CONCURRENT_DOWNLOADS,
        download_fn: Callable[[str, str, Optional[str], Callable], dict] = run_download,
    ):
        self.queue_manager = queue_manager
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.download_fn = download_fn

    async def download_entry(
        self, entry_id: str, output_folder: str, referer: Optional[str] = None
    ) -> None:
        async with self.semaphore:
            self.queue_manager.set_status(entry_id, "downloading")
            loop = asyncio.get_running_loop()
            url = self.queue_manager.get(entry_id).url

            def progress_hook(d: dict) -> None:
                if d.get("status") != "downloading":
                    return
                downloaded = d.get("downloaded_bytes")
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                percent = (
                    round((downloaded / total) * 100, 1)
                    if downloaded is not None and total
                    else 0.0
                )
                speed = d.get("_speed_str")
                eta = d.get("eta")
                loop.call_soon_threadsafe(
                    self.queue_manager.update_progress, entry_id, percent, speed, eta
                )

            try:
                info = await loop.run_in_executor(
                    None, self.download_fn, url, output_folder, referer, progress_hook
                )
                title = info.get("title") if isinstance(info, dict) else None
                if title:
                    self.queue_manager.set_title(entry_id, title)
                self.queue_manager.update_progress(entry_id, 100.0, None, 0)
                self.queue_manager.set_status(entry_id, "done")
            except Exception as exc:
                reason = (
                    REFERER_BLOCKED_MESSAGE
                    if is_referer_blocked_error(exc)
                    else str(exc)
                )
                self.queue_manager.set_error(entry_id, reason)

    async def download_all(
        self, entry_ids: list[str], output_folder: str, referer: Optional[str] = None
    ) -> None:
        await asyncio.gather(
            *(
                self.download_entry(entry_id, output_folder, referer)
                for entry_id in entry_ids
            )
        )
