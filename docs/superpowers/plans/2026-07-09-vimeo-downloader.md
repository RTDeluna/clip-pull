# Vimeo Course Downloader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Electron + Python desktop app that lets the user paste Vimeo links, choose an output folder, and download them all in parallel with live per-video progress bars.

**Architecture:** An Electron desktop app (renderer: HTML/CSS/vanilla JS) spawns a local Python FastAPI backend as a child process. The backend downloads videos via yt-dlp (up to 3 concurrent, 5 concurrent fragments each) and pushes live progress over a WebSocket; the renderer updates progress bars in place as events arrive.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, yt-dlp, pytest, httpx (test client) — Node.js 18+, Electron ^33.

## Global Constraints

- Backend runs on fixed local port 8934 (`http://127.0.0.1:8934`).
- Max concurrent video downloads: 3, fixed (not user-configurable this iteration).
- `concurrent_fragment_downloads` set to 5 for intra-video parallelism.
- No installer/packaging — app runs via `npm start` (spawns Electron, which spawns the Python backend).
- No queue persistence across backend restarts — queue is in-memory only.
- No login/cookie auth for private Vimeo accounts — only unlisted/embed links, with an optional Referer header for referer-protected embeds.
- File names are sanitized video titles (illegal filesystem characters replaced with `_`).

---

## File Structure

```
Vimeo Downloader/
├── pytest.ini
├── package.json
├── main.js
├── preload.js
├── .gitignore
├── backend/
│   ├── requirements.txt
│   ├── main.py
│   ├── queue_manager.py
│   ├── url_validation.py
│   ├── downloader.py
│   └── tests/
│       ├── test_url_validation.py
│       ├── test_queue_manager.py
│       ├── test_downloader.py
│       └── test_main.py
└── frontend/
    ├── index.html
    ├── styles.css
    ├── renderer.js
    └── ws-client.js
```

---

### Task 1: Project scaffold + Vimeo URL validation

**Files:**
- Create: `backend/requirements.txt`
- Create: `pytest.ini`
- Create: `.gitignore`
- Create: `backend/url_validation.py`
- Test: `backend/tests/test_url_validation.py`

**Interfaces:**
- Produces: `is_vimeo_url(url: str) -> bool`, `parse_url_list(text: str) -> tuple[list[str], list[str]]` (returns `(valid_urls, invalid_lines)`) — used by Task 4's `/queue` endpoint.

- [ ] **Step 1: Create backend dependency and pytest config files**

Create `backend/requirements.txt`:

```
fastapi>=0.115
uvicorn[standard]>=0.34
yt-dlp>=2024.12.0
pytest>=8.3
httpx>=0.28
```

Create `pytest.ini` at the project root:

```ini
[pytest]
pythonpath = backend
testpaths = backend/tests
```

Create `.gitignore` at the project root:

```
node_modules/
__pycache__/
*.pyc
.venv/
venv/
*.egg-info/
dist/
build/
.pytest_cache/
```

- [ ] **Step 2: Install backend dependencies**

Run: `pip install -r backend/requirements.txt`
Expected: packages install without error (this may take a minute for `yt-dlp`).

- [ ] **Step 3: Write the failing tests**

Create `backend/tests/test_url_validation.py`:

```python
from url_validation import is_vimeo_url, parse_url_list


def test_is_vimeo_url_accepts_standard_link():
    assert is_vimeo_url("https://vimeo.com/123456789") is True


def test_is_vimeo_url_accepts_link_with_hash_param():
    assert is_vimeo_url("https://vimeo.com/123456789?h=abcdef1234") is True


def test_is_vimeo_url_accepts_player_embed_link():
    assert is_vimeo_url("https://player.vimeo.com/video/123456789") is True


def test_is_vimeo_url_rejects_non_vimeo_link():
    assert is_vimeo_url("https://youtube.com/watch?v=abc123") is False


def test_is_vimeo_url_rejects_empty_string():
    assert is_vimeo_url("") is False


def test_is_vimeo_url_rejects_garbage_text():
    assert is_vimeo_url("not a url at all") is False


def test_parse_url_list_splits_valid_and_invalid_lines():
    text = "https://vimeo.com/111\nnot a url\nhttps://vimeo.com/222?h=abc\n\n"
    valid, invalid = parse_url_list(text)
    assert valid == ["https://vimeo.com/111", "https://vimeo.com/222?h=abc"]
    assert invalid == ["not a url"]


def test_parse_url_list_ignores_blank_lines():
    valid, invalid = parse_url_list("\n\n   \n")
    assert valid == []
    assert invalid == []


def test_parse_url_list_strips_whitespace_around_urls():
    valid, invalid = parse_url_list("  https://vimeo.com/333  \n")
    assert valid == ["https://vimeo.com/333"]
    assert invalid == []
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_url_validation.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'url_validation'`

- [ ] **Step 5: Implement `url_validation.py`**

Create `backend/url_validation.py`:

```python
import re

VIMEO_URL_PATTERN = re.compile(
    r"^https?://(www\.)?(player\.)?vimeo\.com/(video/)?\d+(\?.*)?$"
)


def is_vimeo_url(url: str) -> bool:
    if not url:
        return False
    return bool(VIMEO_URL_PATTERN.match(url.strip()))


def parse_url_list(text: str) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    invalid: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if is_vimeo_url(line):
            valid.append(line)
        else:
            invalid.append(line)
    return valid, invalid
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_url_validation.py -v`
Expected: all 8 tests PASS

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt pytest.ini .gitignore backend/url_validation.py backend/tests/test_url_validation.py
git commit -m "feat: add Vimeo URL validation with tests"
```

---

### Task 2: Queue manager (in-memory download queue state)

**Files:**
- Create: `backend/queue_manager.py`
- Test: `backend/tests/test_queue_manager.py`

**Interfaces:**
- Produces: `QueueEntry` dataclass (`id, url, title, status, percent, speed, eta, error_reason, retry_count`), `QueueManager` class with `add_entries`, `get`, `get_all`, `set_status`, `set_title`, `update_progress`, `set_error`, `reset_for_retry`, `to_dict`, `to_list`, and an `on_update` callback fired on every mutation with the entry's dict — used by Task 3 (`DownloadOrchestrator`) and Task 4 (`main.py`, wiring `on_update` to WebSocket broadcast).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_queue_manager.py`:

```python
from queue_manager import QueueManager


def test_add_entries_creates_queued_entries_with_unique_ids():
    manager = QueueManager()
    entries = manager.add_entries(["https://vimeo.com/111", "https://vimeo.com/222"])
    assert len(entries) == 2
    assert entries[0].status == "queued"
    assert entries[1].status == "queued"
    assert entries[0].id != entries[1].id
    assert entries[0].url == "https://vimeo.com/111"


def test_get_returns_entry_by_id():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    assert manager.get(entry.id) is entry


def test_set_status_updates_entry_status():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.set_status(entry.id, "downloading")
    assert manager.get(entry.id).status == "downloading"


def test_set_title_updates_entry_title():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.set_title(entry.id, "Lesson 1 - Intro")
    assert manager.get(entry.id).title == "Lesson 1 - Intro"


def test_update_progress_sets_percent_speed_eta():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.update_progress(entry.id, 42.5, "1.2MiB/s", 30)
    updated = manager.get(entry.id)
    assert updated.percent == 42.5
    assert updated.speed == "1.2MiB/s"
    assert updated.eta == 30


def test_set_error_sets_status_and_reason():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.set_error(entry.id, "Blocked — referer required")
    updated = manager.get(entry.id)
    assert updated.status == "error"
    assert updated.error_reason == "Blocked — referer required"


def test_reset_for_retry_clears_progress_and_increments_retry_count():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.update_progress(entry.id, 50.0, "1MiB/s", 10)
    manager.set_error(entry.id, "some error")
    manager.reset_for_retry(entry.id)
    updated = manager.get(entry.id)
    assert updated.status == "queued"
    assert updated.percent == 0.0
    assert updated.speed is None
    assert updated.eta is None
    assert updated.error_reason is None
    assert updated.retry_count == 1


def test_to_list_returns_serializable_dicts():
    manager = QueueManager()
    manager.add_entries(["https://vimeo.com/111"])
    result = manager.to_list()
    assert isinstance(result, list)
    assert result[0]["url"] == "https://vimeo.com/111"
    assert result[0]["status"] == "queued"


def test_on_update_callback_fires_on_mutation():
    received = []
    manager = QueueManager(on_update=lambda entry_dict: received.append(entry_dict))
    [entry] = manager.add_entries(["https://vimeo.com/111"])
    manager.set_status(entry.id, "downloading")
    assert len(received) == 2
    assert received[-1]["status"] == "downloading"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_queue_manager.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'queue_manager'`

- [ ] **Step 3: Implement `queue_manager.py`**

Create `backend/queue_manager.py`:

```python
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
    ) -> None:
        entry = self._entries[entry_id]
        entry.percent = percent
        entry.speed = speed
        entry.eta = eta
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
        entry.error_reason = None
        entry.retry_count += 1
        self._notify(entry)

    def to_dict(self, entry_id: str) -> dict:
        return self._entries[entry_id].to_dict()

    def to_list(self) -> list[dict]:
        return [self._entries[eid].to_dict() for eid in self._order]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_queue_manager.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/queue_manager.py backend/tests/test_queue_manager.py
git commit -m "feat: add in-memory queue manager with update notifications"
```

---

### Task 3: Downloader (yt-dlp wrapper + concurrency orchestrator)

**Files:**
- Create: `backend/downloader.py`
- Test: `backend/tests/test_downloader.py`

**Interfaces:**
- Consumes: `QueueManager` from Task 2 (`get`, `set_status`, `set_title`, `update_progress`, `set_error`).
- Produces: `sanitize_filename(name: str) -> str`, `is_referer_blocked_error(exc: Exception) -> bool`, `build_ydl_opts(output_template, referer, progress_hook) -> dict`, `run_download(url, output_folder, referer, progress_hook) -> dict` (real yt-dlp call), `DownloadOrchestrator` class with `download_entry(entry_id, output_folder, referer=None)` and `download_all(entry_ids, output_folder, referer=None)` — used by Task 4's `/queue` and `/queue/{id}/retry` endpoints.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_downloader.py`:

```python
import asyncio
import threading
import time

from queue_manager import QueueManager
from downloader import (
    DownloadOrchestrator,
    build_ydl_opts,
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

    assert counters["peak"] <= 2
    assert all(manager.get(e.id).status == "done" for e in entries)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_downloader.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'downloader'`

- [ ] **Step 3: Implement `downloader.py`**

Create `backend/downloader.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_downloader.py -v`
Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/downloader.py backend/tests/test_downloader.py
git commit -m "feat: add yt-dlp download orchestrator with concurrency limit"
```

---

### Task 4: FastAPI app (queue endpoints + WebSocket broadcast)

**Files:**
- Create: `backend/main.py`
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: `parse_url_list` (Task 1), `QueueManager` (Task 2), `DownloadOrchestrator` (Task 3).
- Produces: HTTP API — `GET /health`, `GET /queue`, `POST /queue` (body `{urls_text, output_folder, referer?}` → `{entries, invalid_lines}`), `POST /queue/{entry_id}/retry` (body `{referer?}` → `{entry}`), `WS /ws` (sends `{type: "sync", entries}` on connect, then `{type: "update", entry}` per change) — used by Task 7 (`renderer.js`, `ws-client.js`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_main.py`:

```python
import main as main_module
from fastapi.testclient import TestClient


async def fake_download_all(entry_ids, output_folder, referer=None):
    return None


def setup_function():
    main_module.queue_manager._entries.clear()
    main_module.queue_manager._order.clear()
    main_module.orchestrator.download_all = fake_download_all


client = TestClient(main_module.app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_post_queue_creates_entries_for_valid_urls():
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/111\nhttps://vimeo.com/222",
            "output_folder": "C:/downloads",
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert len(body["entries"]) == 2
    assert body["invalid_lines"] == []
    assert body["entries"][0]["status"] == "queued"


def test_post_queue_reports_invalid_lines_without_blocking_valid_ones():
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/111\nnot a url",
            "output_folder": "C:/downloads",
        },
    )
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["invalid_lines"] == ["not a url"]


def test_get_queue_returns_current_entries():
    client.post(
        "/queue",
        json={"urls_text": "https://vimeo.com/333", "output_folder": "C:/downloads"},
    )
    response = client.get("/queue")
    urls = [entry["url"] for entry in response.json()["entries"]]
    assert "https://vimeo.com/333" in urls


def test_retry_entry_resets_status_to_queued():
    post_response = client.post(
        "/queue",
        json={"urls_text": "https://vimeo.com/444", "output_folder": "C:/downloads"},
    )
    entry_id = post_response.json()["entries"][0]["id"]
    main_module.queue_manager.set_error(entry_id, "some error")

    response = client.post(f"/queue/{entry_id}/retry", json={})
    assert response.status_code == 202
    assert response.json()["entry"]["status"] == "queued"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_main.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Implement `main.py`**

Create `backend/main.py`:

```python
import asyncio
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from downloader import DownloadOrchestrator
from queue_manager import QueueManager
from url_validation import parse_url_list


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        stale = []
        for connection in self.active:
            try:
                await connection.send_json(message)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


class AppState:
    def __init__(self):
        self.output_folder: Optional[str] = None
        self.referer: Optional[str] = None


app = FastAPI()
connection_manager = ConnectionManager()
state = AppState()


def _notify_websocket_clients(entry_dict: dict) -> None:
    try:
        asyncio.create_task(
            connection_manager.broadcast({"type": "update", "entry": entry_dict})
        )
    except RuntimeError:
        pass  # no running event loop (e.g. called outside a request, such as in tests)


queue_manager = QueueManager(on_update=_notify_websocket_clients)
orchestrator = DownloadOrchestrator(queue_manager)


class QueueRequest(BaseModel):
    urls_text: str
    output_folder: str
    referer: Optional[str] = None


class RetryRequest(BaseModel):
    referer: Optional[str] = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/queue")
def get_queue() -> dict:
    return {"entries": queue_manager.to_list()}


@app.post("/queue", status_code=202)
async def post_queue(request: QueueRequest) -> dict:
    valid_urls, invalid_lines = parse_url_list(request.urls_text)
    state.output_folder = request.output_folder
    state.referer = request.referer
    entries = queue_manager.add_entries(valid_urls)
    if entries:
        asyncio.create_task(
            orchestrator.download_all(
                [entry.id for entry in entries],
                request.output_folder,
                request.referer,
            )
        )
    return {
        "entries": [entry.to_dict() for entry in entries],
        "invalid_lines": invalid_lines,
    }


@app.post("/queue/{entry_id}/retry", status_code=202)
async def retry_entry(entry_id: str, request: RetryRequest) -> dict:
    queue_manager.reset_for_retry(entry_id)
    referer = request.referer or state.referer
    asyncio.create_task(
        orchestrator.download_all([entry_id], state.output_folder, referer)
    )
    return {"entry": queue_manager.to_dict(entry_id)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await connection_manager.connect(websocket)
    await websocket.send_json({"type": "sync", "entries": queue_manager.to_list()})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8934)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_main.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Run the full backend test suite**

Run: `python -m pytest backend/tests -v`
Expected: all tests across all four test files PASS (30 tests total)

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_main.py
git commit -m "feat: add FastAPI queue endpoints and WebSocket progress broadcast"
```

---

### Task 5: Electron main process (window, backend spawn, native folder picker)

**Files:**
- Create: `package.json`
- Create: `main.js`
- Create: `preload.js`

**Interfaces:**
- Consumes: `backend/main.py` (Task 4) as a spawned child process exposing `GET /health` on port 8934.
- Produces: `window.api.chooseFolder(): Promise<string | null>` and `window.api.backendPort: number`, exposed to the renderer — used by Task 7 (`renderer.js`).

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "vimeo-course-downloader",
  "version": "1.0.0",
  "description": "Paste Vimeo links, download them in parallel with live progress bars.",
  "main": "main.js",
  "scripts": {
    "start": "electron ."
  },
  "devDependencies": {
    "electron": "^33.2.0"
  }
}
```

- [ ] **Step 2: Create `preload.js`**

```javascript
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  chooseFolder: () => ipcRenderer.invoke("choose-folder"),
  backendPort: 8934,
});
```

- [ ] **Step 3: Create `main.js`**

```javascript
const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const http = require("http");

const BACKEND_PORT = 8934;
const BACKEND_HEALTH_URL = `http://127.0.0.1:${BACKEND_PORT}/health`;

let backendProcess = null;
let mainWindow = null;

function spawnBackend() {
  backendProcess = spawn("python", ["main.py"], {
    cwd: path.join(__dirname, "backend"),
    stdio: "inherit",
  });
  backendProcess.on("error", (err) => {
    console.error("Failed to start backend:", err);
  });
}

function waitForBackend(retriesLeft, onReady) {
  if (retriesLeft <= 0) {
    console.error("Backend did not become ready in time.");
    onReady();
    return;
  }
  http
    .get(BACKEND_HEALTH_URL, (res) => {
      if (res.statusCode === 200) {
        onReady();
      } else {
        setTimeout(() => waitForBackend(retriesLeft - 1, onReady), 300);
      }
    })
    .on("error", () => {
      setTimeout(() => waitForBackend(retriesLeft - 1, onReady), 300);
    });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 900,
    height: 700,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile(path.join(__dirname, "frontend", "index.html"));
}

ipcMain.handle("choose-folder", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openDirectory"],
  });
  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }
  return result.filePaths[0];
});

app.whenReady().then(() => {
  spawnBackend();
  waitForBackend(20, createWindow);
});

app.on("window-all-closed", () => {
  if (backendProcess) {
    backendProcess.kill();
  }
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  if (backendProcess) {
    backendProcess.kill();
  }
});
```

- [ ] **Step 4: Install Node dependencies**

Run: `npm install`
Expected: `node_modules/` created, `electron` installed, no errors.

- [ ] **Step 5: Verify the backend spawns correctly (frontend files don't exist yet, so skip full launch)**

Run this directly to confirm the backend half of `main.js` works before the window-loading half exists:

```bash
python backend/main.py &
sleep 1
curl http://127.0.0.1:8934/health
kill %1
```

Expected: `{"status":"ok"}` printed, then the background process is killed.

- [ ] **Step 6: Commit**

```bash
git add package.json main.js preload.js
git commit -m "feat: add Electron shell that spawns the Python backend"
```

---

### Task 6: Frontend markup and styling

**Files:**
- Create: `frontend/index.html`
- Create: `frontend/styles.css`

**Interfaces:**
- Produces: DOM elements consumed by Task 7's `renderer.js` — ids `urls`, `invalid-lines`, `output-folder`, `browse-btn`, `referer`, `start-btn`, `queue-list`, `queue-summary`.

- [ ] **Step 1: Create `frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Vimeo Course Downloader</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <div class="app">
    <header class="app__header">
      <h1 class="app__title">VIMEO<span class="app__title-accent">.PULL</span></h1>
      <p class="app__subtitle">Paste links. Pick a folder. Walk away.</p>
    </header>

    <section class="panel">
      <label class="field-label" for="urls">Vimeo links (one per line)</label>
      <textarea
        id="urls"
        class="urls-input"
        rows="6"
        placeholder="https://vimeo.com/123456789&#10;https://vimeo.com/987654321?h=abc123"
      ></textarea>
      <div id="invalid-lines" class="invalid-lines" hidden></div>

      <div class="field-row">
        <div class="field-group field-group--grow">
          <label class="field-label" for="output-folder">Save to</label>
          <div class="folder-picker">
            <input
              id="output-folder"
              class="folder-input"
              type="text"
              placeholder="Choose a folder…"
              readonly
            />
            <button id="browse-btn" class="btn btn--ghost" type="button">Browse…</button>
          </div>
        </div>
      </div>

      <div class="field-row">
        <div class="field-group field-group--grow">
          <label class="field-label" for="referer">Referer domain (optional)</label>
          <input
            id="referer"
            class="text-input"
            type="text"
            placeholder="e.g. https://your-course-site.com"
          />
        </div>
      </div>

      <button id="start-btn" class="btn btn--primary" type="button">Start Download</button>
    </section>

    <section class="panel">
      <div class="queue-header">
        <h2 class="queue-title">Queue</h2>
        <span id="queue-summary" class="queue-summary"></span>
      </div>
      <ul id="queue-list" class="queue-list"></ul>
    </section>
  </div>

  <script type="module" src="ws-client.js"></script>
  <script type="module" src="renderer.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `frontend/styles.css`**

```css
:root {
  --bg: #0b0d10;
  --panel: #14171c;
  --panel-border: #262b33;
  --text: #e7e9ec;
  --text-dim: #8b93a1;
  --accent: #ffb020;
  --accent-dim: #7a5a1a;
  --error: #ff5c5c;
  --success: #35d488;
  --font-display: "Bahnschrift", "Segoe UI", sans-serif;
  --font-mono: "Cascadia Mono", "Consolas", monospace;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-display);
}

.app {
  max-width: 720px;
  margin: 0 auto;
  padding: 32px 24px 64px;
}

.app__header {
  margin-bottom: 28px;
}

.app__title {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: 2px;
  margin: 0;
  text-transform: uppercase;
}

.app__title-accent {
  color: var(--accent);
}

.app__subtitle {
  color: var(--text-dim);
  margin: 4px 0 0;
  font-size: 14px;
}

.panel {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 20px;
}

.field-label {
  display: block;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text-dim);
  margin-bottom: 6px;
}

.urls-input {
  width: 100%;
  background: #0e1013;
  color: var(--text);
  border: 1px solid var(--panel-border);
  border-radius: 6px;
  padding: 12px;
  font-family: var(--font-mono);
  font-size: 13px;
  resize: vertical;
}

.invalid-lines {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  background: rgba(255, 92, 92, 0.1);
  border: 1px solid rgba(255, 92, 92, 0.3);
  color: var(--error);
  font-family: var(--font-mono);
  font-size: 12px;
  white-space: pre-line;
}

.field-row {
  margin-top: 14px;
}

.field-group--grow {
  flex-grow: 1;
}

.folder-picker {
  display: flex;
  gap: 8px;
}

.folder-input,
.text-input {
  flex: 1;
  background: #0e1013;
  color: var(--text);
  border: 1px solid var(--panel-border);
  border-radius: 6px;
  padding: 10px 12px;
  font-family: var(--font-mono);
  font-size: 13px;
}

.btn {
  border: none;
  border-radius: 6px;
  padding: 10px 16px;
  font-family: var(--font-display);
  font-weight: 600;
  letter-spacing: 0.5px;
  cursor: pointer;
}

.btn--ghost {
  background: transparent;
  border: 1px solid var(--panel-border);
  color: var(--text);
}

.btn--primary {
  background: var(--accent);
  color: #1a1300;
  margin-top: 18px;
  width: 100%;
  font-size: 14px;
  text-transform: uppercase;
}

.btn--primary:disabled {
  background: var(--accent-dim);
  cursor: not-allowed;
}

.queue-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 12px;
}

.queue-title {
  font-size: 16px;
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 1px;
}

.queue-summary {
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 12px;
}

.queue-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.queue-row {
  border: 1px solid var(--panel-border);
  border-radius: 6px;
  padding: 12px 14px;
}

.queue-row__top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 8px;
}

.queue-row__title {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queue-row__status {
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text-dim);
  flex-shrink: 0;
}

.queue-row__status--done {
  color: var(--success);
}

.queue-row__status--error {
  color: var(--error);
}

.progress-track {
  background: #0e1013;
  border-radius: 4px;
  height: 8px;
  overflow: hidden;
}

.progress-fill {
  background: var(--accent);
  height: 100%;
  width: 0%;
  transition: width 0.2s ease-out;
}

.queue-row__meta {
  display: flex;
  justify-content: space-between;
  margin-top: 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-dim);
}

.queue-row__error {
  margin-top: 8px;
  color: var(--error);
  font-size: 12px;
}

.retry-btn {
  margin-top: 8px;
  background: transparent;
  border: 1px solid var(--error);
  color: var(--error);
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 11px;
  cursor: pointer;
}
```

- [ ] **Step 3: Verify markup opens standalone**

Run: `start frontend/index.html` (Windows — opens the file directly in the default browser)
Expected: dark-themed layout renders — title, paste textarea, folder/referer fields, empty queue panel. Browser console will show 404s for `ws-client.js`/`renderer.js` — expected until Task 7.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/styles.css
git commit -m "feat: add frontend markup and dark technical theme"
```

---

### Task 7: Frontend logic (WebSocket client + queue rendering)

**Files:**
- Create: `frontend/ws-client.js`
- Create: `frontend/renderer.js`

**Interfaces:**
- Consumes: backend API from Task 4 (`POST /queue`, `POST /queue/{id}/retry`, `WS /ws`), `window.api` from Task 5's `preload.js`, DOM structure from Task 6.

- [ ] **Step 1: Create `frontend/ws-client.js`**

```javascript
const BACKEND_PORT = window.api?.backendPort ?? 8934;
const WS_URL = `ws://127.0.0.1:${BACKEND_PORT}/ws`;

export function connectQueueSocket(onEvent) {
  const socket = new WebSocket(WS_URL);

  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    onEvent(data);
  });

  socket.addEventListener("close", () => {
    setTimeout(() => connectQueueSocket(onEvent), 1000);
  });

  return socket;
}
```

- [ ] **Step 2: Create `frontend/renderer.js`**

```javascript
import { connectQueueSocket } from "./ws-client.js";

const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

const urlsInput = document.getElementById("urls");
const invalidLinesEl = document.getElementById("invalid-lines");
const outputFolderInput = document.getElementById("output-folder");
const browseBtn = document.getElementById("browse-btn");
const refererInput = document.getElementById("referer");
const startBtn = document.getElementById("start-btn");
const queueList = document.getElementById("queue-list");
const queueSummary = document.getElementById("queue-summary");

const rows = new Map();

function statusLabel(entry) {
  if (entry.status === "error") return "Failed";
  if (entry.status === "done") return "Done";
  if (entry.status === "downloading") return "Downloading";
  return "Queued";
}

function formatSpeed(speed) {
  return speed ? speed : "--";
}

function formatEta(eta) {
  if (eta === null || eta === undefined) return "--";
  const minutes = Math.floor(eta / 60);
  const seconds = eta % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function renderRow(entry) {
  let row = rows.get(entry.id);
  if (!row) {
    row = document.createElement("li");
    row.className = "queue-row";
    row.innerHTML = `
      <div class="queue-row__top">
        <span class="queue-row__title"></span>
        <span class="queue-row__status"></span>
      </div>
      <div class="progress-track"><div class="progress-fill"></div></div>
      <div class="queue-row__meta">
        <span class="queue-row__speed"></span>
        <span class="queue-row__eta"></span>
      </div>
      <div class="queue-row__error"></div>
      <button class="retry-btn" hidden>Retry</button>
    `;
    queueList.appendChild(row);
    rows.set(entry.id, row);

    row.querySelector(".retry-btn").addEventListener("click", () => {
      retryEntry(entry.id);
    });
  }

  row.querySelector(".queue-row__title").textContent = entry.title || entry.url;
  const statusEl = row.querySelector(".queue-row__status");
  statusEl.textContent = statusLabel(entry);
  statusEl.className = "queue-row__status";
  if (entry.status === "done") statusEl.classList.add("queue-row__status--done");
  if (entry.status === "error") statusEl.classList.add("queue-row__status--error");

  row.querySelector(".progress-fill").style.width = `${entry.percent}%`;
  row.querySelector(".queue-row__speed").textContent = formatSpeed(entry.speed);
  row.querySelector(".queue-row__eta").textContent = formatEta(entry.eta);

  const errorEl = row.querySelector(".queue-row__error");
  const retryBtn = row.querySelector(".retry-btn");
  if (entry.status === "error") {
    errorEl.textContent = entry.error_reason || "Unknown error";
    retryBtn.hidden = false;
  } else {
    errorEl.textContent = "";
    retryBtn.hidden = true;
  }

  updateSummary();
}

function updateSummary() {
  const entries = Array.from(rows.keys()).length;
  const done = Array.from(queueList.querySelectorAll(".queue-row__status--done")).length;
  const failed = Array.from(queueList.querySelectorAll(".queue-row__status--error")).length;
  queueSummary.textContent = entries
    ? `${done}/${entries} downloaded${failed ? `, ${failed} failed` : ""}`
    : "";
}

async function retryEntry(entryId) {
  await fetch(`${API_BASE}/queue/${entryId}/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ referer: refererInput.value || null }),
  });
}

browseBtn.addEventListener("click", async () => {
  const folder = await window.api.chooseFolder();
  if (folder) {
    outputFolderInput.value = folder;
  }
});

startBtn.addEventListener("click", async () => {
  if (!outputFolderInput.value) {
    alert("Choose an output folder first.");
    return;
  }
  startBtn.disabled = true;
  invalidLinesEl.hidden = true;

  const response = await fetch(`${API_BASE}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      urls_text: urlsInput.value,
      output_folder: outputFolderInput.value,
      referer: refererInput.value || null,
    }),
  });
  const body = await response.json();

  if (body.invalid_lines && body.invalid_lines.length > 0) {
    invalidLinesEl.hidden = false;
    invalidLinesEl.textContent = `Skipped invalid lines:\n${body.invalid_lines.join("\n")}`;
  }

  body.entries.forEach(renderRow);
  urlsInput.value = "";
  startBtn.disabled = false;
});

connectQueueSocket((event) => {
  if (event.type === "sync") {
    event.entries.forEach(renderRow);
  } else if (event.type === "update") {
    renderRow(event.entry);
  }
});
```

- [ ] **Step 3: Commit**

```bash
git add frontend/ws-client.js frontend/renderer.js
git commit -m "feat: wire frontend to backend via REST + WebSocket for live progress"
```

---

### Task 8: Manual end-to-end verification

This step requires a human at a real display — a subagent cannot visually confirm an Electron window or responsibly trigger a real multi-hundred-MB video download on your behalf. Do not skip it.

**Files:** none (verification only)

- [ ] **Step 1: Install everything and launch**

```bash
pip install -r backend/requirements.txt
npm install
npm start
```

Expected: an Electron window opens showing the dark "VIMEO.PULL" UI. No console errors other than expected WebSocket-not-yet-connected warnings before the backend finishes booting (it retries automatically).

- [ ] **Step 2: Verify the folder picker**

Click **Browse…**. Expected: the native Windows folder picker opens; selecting a folder fills in the "Save to" field.

- [ ] **Step 3: Verify a real download**

Paste 1–2 Vimeo links you know are downloadable (a public `vimeo.com/<id>` link works; for a course link, use one of your actual lesson URLs). Click **Start Download**.
Expected: a row appears per link immediately, status moves `Queued → Downloading → Done`, the progress bar fills smoothly with live percent/speed/ETA, and the finished file appears in the chosen folder named after the video's title.

- [ ] **Step 4: Verify invalid-line handling**

Paste one valid Vimeo link and one garbage line (e.g. `not a url`) together, click **Start Download**.
Expected: the valid link downloads normally; the invalid line appears in a red "Skipped invalid lines" box and does not create a queue row.

- [ ] **Step 5: Verify per-video error isolation (only if you have a referer-protected course link)**

If step 3 produced a "Blocked — this video may require the course site as referer" error on a course link, fill in the **Referer domain** field with your course site's URL and click that row's **Retry**.
Expected: the row resets to "Queued" and retries with the referer header; other rows are unaffected throughout.

- [ ] **Step 6: Close the app and confirm clean shutdown**

Close the Electron window.
Expected: the Python backend process exits with it (check Task Manager / `tasklist | findstr python` — no orphaned `python main.py` process left running).
