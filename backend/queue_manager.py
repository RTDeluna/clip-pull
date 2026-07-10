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
    eta: Optional[int] = None
    downloaded_size: Optional[str] = None
    total_size: Optional[str] = None
    error_reason: Optional[str] = None
    retry_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "status": self.status,
            "percent": self.percent,
            "speed": self.speed,
            "eta": self.eta,
            "downloaded_size": self.downloaded_size,
            "total_size": self.total_size,
            "error_reason": self.error_reason,
            "retry_count": self.retry_count,
        }


class QueueManager:
    def __init__(self, on_update: Optional[Callable[[dict], None]] = None):
        self._entries: dict[str, QueueEntry] = {}
        self._order: list[str] = []
        self.on_update = on_update

    def _notify(self, entry: QueueEntry) -> None:
        if self.on_update:
            self.on_update(entry.to_dict())

    def add_entries(self, urls: list[str]) -> list[QueueEntry]:
        created = []
        for url in urls:
            entry = QueueEntry(id=uuid.uuid4().hex, url=url)
            self._entries[entry.id] = entry
            self._order.append(entry.id)
            created.append(entry)
            self._notify(entry)
        return created

    def get(self, entry_id: str) -> QueueEntry:
        return self._entries[entry_id]

    def get_all(self) -> list[QueueEntry]:
        return [self._entries[eid] for eid in self._order]

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
    ) -> None:
        entry = self._entries[entry_id]
        entry.percent = percent
        entry.speed = speed
        entry.eta = eta
        entry.downloaded_size = downloaded_size
        entry.total_size = total_size
        self._notify(entry)

    def set_error(self, entry_id: str, reason: str) -> None:
        entry = self._entries[entry_id]
        entry.status = "error"
        entry.error_reason = reason
        self._notify(entry)

    def reset_for_retry(self, entry_id: str) -> None:
        entry = self._entries[entry_id]
        entry.status = "queued"
        entry.percent = 0.0
        entry.speed = None
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
