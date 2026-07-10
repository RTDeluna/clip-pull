import uuid
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class QueueEntry:
    id: str
    url: str
    title: Optional[str] = None
    status: str = "queued"
    percent: float = 0.0
    speed: Optional[str] = None
    speed_bytes: Optional[float] = None
    eta: Optional[int] = None
    downloaded_size: Optional[str] = None
    total_size: Optional[str] = None
    error_reason: Optional[str] = None
    retry_count: int = 0
    batch_id: Optional[str] = None
    output_folder: Optional[str] = None
    previously_downloaded: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "status": self.status,
            "percent": self.percent,
            "speed": self.speed,
            "speed_bytes": self.speed_bytes,
            "eta": self.eta,
            "downloaded_size": self.downloaded_size,
            "total_size": self.total_size,
            "error_reason": self.error_reason,
            "retry_count": self.retry_count,
            "batch_id": self.batch_id,
            "output_folder": self.output_folder,
            "previously_downloaded": self.previously_downloaded,
        }


class QueueManager:
    def __init__(
        self,
        on_update: Optional[Callable[[dict], None]] = None,
        on_remove: Optional[Callable[[str], None]] = None,
    ):
        self._entries: dict[str, QueueEntry] = {}
        self._order: list[str] = []
        self.on_update = on_update
        self.on_remove = on_remove

    def _notify(self, entry: QueueEntry) -> None:
        if self.on_update:
            self.on_update(entry.to_dict())

    def add_entries(
        self,
        urls: list[str],
        batch_id: Optional[str] = None,
        output_folder: Optional[str] = None,
        previously_downloaded_urls: Optional[set[str]] = None,
    ) -> list[QueueEntry]:
        previously_downloaded_urls = previously_downloaded_urls or set()
        # Skip URLs that already have an active (not yet done/error) entry —
        # protects against duplicate rows from double-submits, whether from
        # the desktop UI or an external caller like the browser extension
        # re-sending the same link.
        active_urls = {e.url for e in self._entries.values() if e.status not in ("done", "error")}
        created = []
        for url in urls:
            if url in active_urls:
                continue
            entry = QueueEntry(
                id=uuid.uuid4().hex,
                url=url,
                batch_id=batch_id,
                output_folder=output_folder,
                previously_downloaded=url in previously_downloaded_urls,
            )
            self._entries[entry.id] = entry
            self._order.append(entry.id)
            created.append(entry)
            active_urls.add(url)
            self._notify(entry)
        return created

    def get(self, entry_id: str) -> QueueEntry:
        return self._entries[entry_id]

    def get_all(self) -> list[QueueEntry]:
        return [self._entries[eid] for eid in self._order]

    def is_batch_complete(self, batch_id: Optional[str]) -> bool:
        if batch_id is None:
            return False
        batch_entries = [e for e in self._entries.values() if e.batch_id == batch_id]
        if not batch_entries:
            return False
        return all(e.status in ("done", "error") for e in batch_entries)

    def batch_summary(self, batch_id: Optional[str]) -> dict:
        batch_entries = [e for e in self._entries.values() if e.batch_id == batch_id]
        return {
            "done": sum(1 for e in batch_entries if e.status == "done"),
            "error": sum(1 for e in batch_entries if e.status == "error"),
        }

    def set_status(self, entry_id: str, status: str) -> None:
        entry = self._entries[entry_id]
        entry.status = status
        self._notify(entry)

    def set_title(self, entry_id: str, title: str) -> None:
        entry = self._entries[entry_id]
        entry.title = title
        self._notify(entry)

    def update_progress(
        self,
        entry_id: str,
        percent: float,
        speed: Optional[str],
        eta: Optional[int],
        downloaded_size: Optional[str] = None,
        total_size: Optional[str] = None,
        speed_bytes: Optional[float] = None,
    ) -> None:
        entry = self._entries[entry_id]
        entry.percent = percent
        entry.speed = speed
        entry.speed_bytes = speed_bytes
        entry.eta = eta
        entry.downloaded_size = downloaded_size
        entry.total_size = total_size
        self._notify(entry)

    def set_error(self, entry_id: str, reason: str) -> None:
        entry = self._entries[entry_id]
        entry.status = "error"
        entry.error_reason = reason
        self._notify(entry)

    def mark_paused(self, entry_id: str) -> None:
        """Sets status to "paused" without touching percent/downloaded/total
        size — unlike reset_for_retry, a paused download should resume from
        where it left off, not restart at 0%."""
        entry = self._entries[entry_id]
        entry.status = "paused"
        entry.speed = None
        entry.speed_bytes = None
        entry.eta = None
        self._notify(entry)

    def reset_for_retry(self, entry_id: str) -> None:
        entry = self._entries[entry_id]
        entry.status = "queued"
        entry.percent = 0.0
        entry.speed = None
        entry.speed_bytes = None
        entry.eta = None
        entry.downloaded_size = None
        entry.total_size = None
        entry.error_reason = None
        entry.retry_count += 1
        self._notify(entry)

    def to_dict(self, entry_id: str) -> dict:
        return self._entries[entry_id].to_dict()

    def to_list(self) -> list[dict]:
        return [self._entries[eid].to_dict() for eid in self._order]

    def remove(self, entry_id: str) -> None:
        if entry_id in self._entries:
            del self._entries[entry_id]
        if entry_id in self._order:
            self._order.remove(entry_id)
        if self.on_remove:
            self.on_remove(entry_id)
