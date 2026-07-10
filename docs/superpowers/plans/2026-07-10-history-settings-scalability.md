# History, Duplicate Detection, Scalability, Settings, Notifications, Subfolders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent SQLite-backed download history with a History view, duplicate-download detection, a Settings panel for runtime-configurable performance knobs, batch-completion OS notifications, optional per-course output subfolders, and scalability fixes for large batches — while splitting `backend/main.py` into focused modules to keep it maintainable.

**Architecture:** Two new SQLite-backed store classes (`HistoryStore`, `SettingsStore`) sharing one on-disk DB file via a shared `db.py` connection/migration helper. `backend/main.py` splits into a thin composition root plus `background_tasks.py`, `ws_manager.py`, `queue_routes.py`, `history_routes.py`, `settings_routes.py`. `DownloadOrchestrator` gains injected callables (matching its existing `on_update`/`download_fn` DI style) for settings-driven concurrency, history recording, and batch-completion detection.

**Tech Stack:** Python's stdlib `sqlite3` (no new pip dependency), FastAPI `APIRouter` factory functions, vanilla JS frontend (no framework/build step, matching the existing app).

## Global Constraints

- No new pip dependencies — `sqlite3` is stdlib.
- One shared SQLite file: `backend/data/clip_pull.db` (path overridable via `CLIP_PULL_DB_PATH` env var — tests set this to `:memory:` via `backend/tests/conftest.py` so they never touch a real file on disk).
- Backend port stays 8934. Existing WS message types (`sync`, `update`) are unchanged; `update_batch` and `batch_complete` are additive new types.
- All 58 currently-passing backend tests must keep passing. Several existing test fakes in `test_downloader.py` require signature updates (adding 2 new trailing params) — this is expected, explicit churn, not a regression, called out per-task below.
- Frontend: no new npm dependencies. `node --check` on every new/modified `.js` file. No build step — plain `<script type="module">` files.
- Follow this project's existing dependency-injection-over-mocking test convention throughout (real temp SQLite files / `:memory:`, injected callables, fakes — never mock internals).

---

## File Structure

```
backend/
├── db.py                     [NEW] shared SQLite connection + migrations
├── settings_store.py         [NEW] SettingsStore
├── history_store.py          [NEW] HistoryStore
├── background_tasks.py       [NEW] extracted from main.py, unchanged logic
├── ws_manager.py             [NEW] ConnectionManager (moved) + QueueBroadcaster
├── queue_routes.py           [NEW] /queue GET/POST, /queue/{id}/retry
├── history_routes.py         [NEW] GET /history
├── settings_routes.py        [NEW] GET/PATCH /settings
├── main.py                   [REWRITE] thin composition root
├── queue_manager.py          [MODIFY] new QueueEntry fields + methods
├── downloader.py             [MODIFY] settings-driven orchestrator, history recording, batch completion, subfolder resolution
├── .gitignore                [MODIFY] add backend/data/
└── tests/
    ├── conftest.py           [NEW] forces CLIP_PULL_DB_PATH=:memory: for all tests
    ├── test_db.py            [NEW]
    ├── test_settings_store.py [NEW]
    ├── test_history_store.py [NEW]
    ├── test_background_tasks.py [NEW]
    ├── test_ws_manager.py     [NEW] (absorbs the broadcast-serialization test moved from test_main.py)
    ├── test_queue_routes.py  [NEW] (absorbs queue-related tests moved from test_main.py, then extended)
    ├── test_history_routes.py [NEW]
    ├── test_settings_routes.py [NEW]
    ├── test_main.py          [SHRINK] just /health + CORS smoke tests
    ├── test_queue_manager.py [MODIFY] new field/method tests
    └── test_downloader.py    [MODIFY] new tests + ~6 existing fake signatures updated

frontend/
├── index.html                [MODIFY] tabs, Settings/History views, course-folder field, duplicate badge
├── styles.css                [MODIFY] tabs, history-controls, duplicate-badge styles
├── renderer.js               [MODIFY] update_batch/batch_complete handling, incremental summary counters, subfolder field wiring, duplicate badge
├── tabs.js                   [NEW]
├── settings-view.js          [NEW]
└── history-view.js           [NEW]

main.js                       [MODIFY] shell.showItemInFolder IPC handler
preload.js                    [MODIFY] expose revealFile
```

---

### Task 1: SQLite connection helper + migrations

**Files:**
- Create: `backend/db.py`
- Test: `backend/tests/conftest.py`, `backend/tests/test_db.py`

**Interfaces:**
- Produces: `get_connection(db_path) -> sqlite3.Connection` (WAL mode for real files, `row_factory=sqlite3.Row`, migrations applied), `run_migrations(conn) -> None` — used by Tasks 2 and 3.

- [ ] **Step 1: Create the test conftest that isolates all tests from the real DB file**

Create `backend/tests/conftest.py`:

```python
import os

os.environ.setdefault("CLIP_PULL_DB_PATH", ":memory:")
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_db.py`:

```python
from db import get_connection, run_migrations


def test_get_connection_creates_history_and_settings_tables():
    conn = get_connection(":memory:")
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "history" in tables
    assert "settings" in tables


def test_get_connection_row_factory_allows_column_access_by_name():
    conn = get_connection(":memory:")
    conn.execute("INSERT INTO settings (id, max_concurrent_downloads) VALUES (1, 5)")
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    assert row["max_concurrent_downloads"] == 5


def test_get_connection_persists_to_real_file(tmp_path):
    db_path = tmp_path / "test.db"
    conn1 = get_connection(db_path)
    conn1.execute("INSERT INTO settings (id, max_concurrent_downloads) VALUES (1, 7)")
    conn1.commit()
    conn1.close()

    conn2 = get_connection(db_path)
    row = conn2.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    assert row["max_concurrent_downloads"] == 7


def test_run_migrations_idempotent_when_called_twice():
    conn = get_connection(":memory:")
    run_migrations(conn)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = [row["name"] for row in tables]
    assert table_names.count("history") == 1
    assert table_names.count("settings") == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_db.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 4: Implement `db.py`**

Create `backend/db.py`:

```python
import sqlite3
from pathlib import Path
from typing import Union

MIGRATIONS = [
    (1, """
        CREATE TABLE IF NOT EXISTS history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entry_id TEXT,
          batch_id TEXT,
          url TEXT NOT NULL,
          title TEXT,
          output_path TEXT,
          total_size TEXT,
          status TEXT NOT NULL CHECK (status IN ('done', 'error')),
          error_reason TEXT,
          retry_count INTEGER NOT NULL DEFAULT 0,
          started_at TEXT,
          finished_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_history_url ON history(url);
        CREATE INDEX IF NOT EXISTS idx_history_status_url ON history(status, url);
    """),
    (2, """
        CREATE TABLE IF NOT EXISTS settings (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          max_concurrent_downloads INTEGER NOT NULL DEFAULT 3,
          concurrent_fragment_downloads INTEGER NOT NULL DEFAULT 8,
          aria2c_enabled INTEGER NOT NULL DEFAULT 1,
          skip_duplicates INTEGER NOT NULL DEFAULT 0,
          default_output_folder TEXT
        );
    """),
]


def get_connection(db_path: Union[str, Path]) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    run_migrations(conn)
    return conn


def run_migrations(conn: sqlite3.Connection) -> None:
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, sql in MIGRATIONS:
        if version > current_version:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_db.py -v`
Expected: all 4 tests PASS

- [ ] **Step 6: Add `backend/data/` to `.gitignore` and commit**

Append to `backend/.gitignore` (create the file if it doesn't exist) — actually this project's `.gitignore` is at the repo root, so instead append this line to the root `.gitignore`:

```
backend/data/
```

```bash
git add backend/db.py backend/tests/conftest.py backend/tests/test_db.py .gitignore
git commit -m "feat: add shared SQLite connection helper with migrations"
```

---

### Task 2: SettingsStore

**Files:**
- Create: `backend/settings_store.py`
- Test: `backend/tests/test_settings_store.py`

**Interfaces:**
- Consumes: `get_connection` from Task 1.
- Produces: `SettingsStore(db_path=":memory:")` with `.get() -> dict` (keys: `max_concurrent_downloads`, `concurrent_fragment_downloads`, `aria2c_enabled`, `skip_duplicates`, `default_output_folder`) and `.update(**changes) -> dict` — used by Task 9 (orchestrator wiring), Task 11 (`settings_routes.py`, `queue_routes.py`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_settings_store.py`:

```python
from settings_store import SettingsStore


def test_get_returns_defaults_when_no_row_exists_yet():
    store = SettingsStore()
    settings = store.get()
    assert settings["max_concurrent_downloads"] == 3
    assert settings["concurrent_fragment_downloads"] == 8
    assert settings["aria2c_enabled"] is True
    assert settings["skip_duplicates"] is False
    assert settings["default_output_folder"] is None


def test_update_persists_partial_changes():
    store = SettingsStore()
    updated = store.update(max_concurrent_downloads=5)
    assert updated["max_concurrent_downloads"] == 5
    assert updated["concurrent_fragment_downloads"] == 8


def test_update_only_changes_provided_fields():
    store = SettingsStore()
    store.update(aria2c_enabled=False)
    settings = store.get()
    assert settings["aria2c_enabled"] is False
    assert settings["max_concurrent_downloads"] == 3


def test_update_ignores_none_values():
    store = SettingsStore()
    store.update(max_concurrent_downloads=5)
    store.update(max_concurrent_downloads=None)
    assert store.get()["max_concurrent_downloads"] == 5


def test_settings_persist_across_store_instances_pointing_at_same_file(tmp_path):
    db_path = tmp_path / "settings.db"
    store1 = SettingsStore(db_path)
    store1.update(max_concurrent_downloads=9)

    store2 = SettingsStore(db_path)
    assert store2.get()["max_concurrent_downloads"] == 9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_settings_store.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'settings_store'`

- [ ] **Step 3: Implement `settings_store.py`**

Create `backend/settings_store.py`:

```python
from pathlib import Path
from typing import Union

from db import get_connection

DEFAULT_SETTINGS = {
    "max_concurrent_downloads": 3,
    "concurrent_fragment_downloads": 8,
    "aria2c_enabled": True,
    "skip_duplicates": False,
    "default_output_folder": None,
}


class SettingsStore:
    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        self._conn = get_connection(db_path)
        self._conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
        self._conn.commit()

    def get(self) -> dict:
        row = self._conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        return {
            "max_concurrent_downloads": row["max_concurrent_downloads"],
            "concurrent_fragment_downloads": row["concurrent_fragment_downloads"],
            "aria2c_enabled": bool(row["aria2c_enabled"]),
            "skip_duplicates": bool(row["skip_duplicates"]),
            "default_output_folder": row["default_output_folder"],
        }

    def update(self, **changes) -> dict:
        # None means "not provided" (partial update semantics), not "clear this field."
        fields_to_update = {
            key: value
            for key, value in changes.items()
            if key in DEFAULT_SETTINGS and value is not None
        }
        if not fields_to_update:
            return self.get()
        set_clause = ", ".join(f"{key} = ?" for key in fields_to_update)
        values = [
            int(value) if isinstance(value, bool) else value
            for value in fields_to_update.values()
        ]
        self._conn.execute(f"UPDATE settings SET {set_clause} WHERE id = 1", values)
        self._conn.commit()
        return self.get()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_settings_store.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/settings_store.py backend/tests/test_settings_store.py
git commit -m "feat: add SettingsStore for persisted, runtime-configurable settings"
```

---

### Task 3: HistoryStore

**Files:**
- Create: `backend/history_store.py`
- Test: `backend/tests/test_history_store.py`

**Interfaces:**
- Consumes: `get_connection` from Task 1.
- Produces: `HistoryStore(db_path=":memory:")` with `.record(*, entry_id, batch_id, url, title, output_path, total_size, status, error_reason, retry_count) -> dict`, `.search(query=None, status=None, limit=200, offset=0) -> list[dict]`, `.was_previously_downloaded(urls: list[str]) -> set[str]` — used by Task 9 (orchestrator), Task 11 (`history_routes.py`, `queue_routes.py`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_history_store.py`:

```python
from history_store import HistoryStore


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


def test_history_persists_across_store_instances_pointing_at_same_file(tmp_path):
    db_path = tmp_path / "history.db"
    store1 = HistoryStore(db_path)
    _record(store1, url="https://vimeo.com/999")

    store2 = HistoryStore(db_path)
    assert len(store2.search()) == 1
    assert store2.search()[0]["url"] == "https://vimeo.com/999"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_history_store.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'history_store'`

- [ ] **Step 3: Implement `history_store.py`**

Create `backend/history_store.py`:

```python
from pathlib import Path
from typing import Optional, Union

from db import get_connection


class HistoryStore:
    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        self._conn = get_connection(db_path)

    def record(
        self,
        *,
        entry_id: Optional[str],
        batch_id: Optional[str],
        url: str,
        title: Optional[str],
        output_path: Optional[str],
        total_size: Optional[str],
        status: str,
        error_reason: Optional[str],
        retry_count: int,
    ) -> dict:
        cursor = self._conn.execute(
            """
            INSERT INTO history
              (entry_id, batch_id, url, title, output_path, total_size,
               status, error_reason, retry_count, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                entry_id, batch_id, url, title, output_path, total_size,
                status, error_reason, retry_count,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM history WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return dict(row)

    def search(
        self,
        query: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        conditions = []
        params: list = []
        if query:
            conditions.append("(url LIKE ? OR title LIKE ? OR output_path LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])
        rows = self._conn.execute(
            f"SELECT * FROM history {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def was_previously_downloaded(self, urls: list[str]) -> set[str]:
        if not urls:
            return set()
        placeholders = ",".join("?" for _ in urls)
        rows = self._conn.execute(
            f"SELECT DISTINCT url FROM history WHERE status = 'done' AND url IN ({placeholders})",
            urls,
        ).fetchall()
        return {row["url"] for row in rows}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_history_store.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/history_store.py backend/tests/test_history_store.py
git commit -m "feat: add HistoryStore for persistent download history"
```

---

### Task 4: QueueEntry/QueueManager extensions (batch_id, output_folder, previously_downloaded, batch completion)

**Files:**
- Modify: `backend/queue_manager.py`
- Modify: `backend/tests/test_queue_manager.py`

**Interfaces:**
- Produces: `QueueEntry` gains `batch_id: Optional[str] = None`, `output_folder: Optional[str] = None`, `previously_downloaded: bool = False` (all in `to_dict()`); `QueueManager.add_entries(urls, batch_id=None, output_folder=None, previously_downloaded_urls=None)`; `QueueManager.is_batch_complete(batch_id) -> bool`; `QueueManager.batch_summary(batch_id) -> dict` — used by Task 9 (`downloader.py`), Task 11 (`queue_routes.py`).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_queue_manager.py` (append; keep all existing tests):

```python
def test_add_entries_stamps_batch_id_output_folder_on_all_created_entries():
    manager = QueueManager()
    entries = manager.add_entries(
        ["https://vimeo.com/1", "https://vimeo.com/2"],
        batch_id="batch-1",
        output_folder="C:/downloads",
    )
    assert all(e.batch_id == "batch-1" for e in entries)
    assert all(e.output_folder == "C:/downloads" for e in entries)


def test_add_entries_marks_previously_downloaded_urls():
    manager = QueueManager()
    entries = manager.add_entries(
        ["https://vimeo.com/1", "https://vimeo.com/2"],
        previously_downloaded_urls={"https://vimeo.com/1"},
    )
    assert entries[0].previously_downloaded is True
    assert entries[1].previously_downloaded is False


def test_add_entries_defaults_batch_folder_and_previously_downloaded():
    manager = QueueManager()
    [entry] = manager.add_entries(["https://vimeo.com/1"])
    assert entry.batch_id is None
    assert entry.output_folder is None
    assert entry.previously_downloaded is False


def test_is_batch_complete_false_while_any_entry_pending():
    manager = QueueManager()
    entries = manager.add_entries(["https://vimeo.com/1", "https://vimeo.com/2"], batch_id="b1")
    manager.set_status(entries[0].id, "done")
    assert manager.is_batch_complete("b1") is False


def test_is_batch_complete_true_when_all_terminal():
    manager = QueueManager()
    entries = manager.add_entries(["https://vimeo.com/1", "https://vimeo.com/2"], batch_id="b1")
    manager.set_status(entries[0].id, "done")
    manager.set_error(entries[1].id, "some error")
    assert manager.is_batch_complete("b1") is True


def test_is_batch_complete_false_for_unknown_batch_id():
    manager = QueueManager()
    assert manager.is_batch_complete("nonexistent") is False


def test_is_batch_complete_false_for_none_batch_id():
    manager = QueueManager()
    assert manager.is_batch_complete(None) is False


def test_batch_summary_counts_done_and_error():
    manager = QueueManager()
    entries = manager.add_entries(
        ["https://vimeo.com/1", "https://vimeo.com/2", "https://vimeo.com/3"], batch_id="b1"
    )
    manager.set_status(entries[0].id, "done")
    manager.set_status(entries[1].id, "done")
    manager.set_error(entries[2].id, "failed")
    assert manager.batch_summary("b1") == {"done": 2, "error": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_queue_manager.py -v`
Expected: FAIL on the 8 new tests (`AttributeError`/`TypeError` — fields/methods/params don't exist yet)

- [ ] **Step 3: Implement the changes in `queue_manager.py`**

In `backend/queue_manager.py`, update `QueueEntry`:

```python
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
            "eta": self.eta,
            "downloaded_size": self.downloaded_size,
            "total_size": self.total_size,
            "error_reason": self.error_reason,
            "retry_count": self.retry_count,
            "batch_id": self.batch_id,
            "output_folder": self.output_folder,
            "previously_downloaded": self.previously_downloaded,
        }
```

Update `add_entries`:

```python
def add_entries(
    self,
    urls: list[str],
    batch_id: Optional[str] = None,
    output_folder: Optional[str] = None,
    previously_downloaded_urls: Optional[set[str]] = None,
) -> list[QueueEntry]:
    previously_downloaded_urls = previously_downloaded_urls or set()
    created = []
    for url in urls:
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
        self._notify(entry)
    return created
```

Add two new methods (anywhere after `get_all`, e.g. right before `to_dict`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_queue_manager.py -v`
Expected: all tests (9 existing + 8 new = 17) PASS

- [ ] **Step 5: Commit**

```bash
git add backend/queue_manager.py backend/tests/test_queue_manager.py
git commit -m "feat: add batch_id, output_folder, previously_downloaded to QueueEntry"
```

---

### Task 5: background_tasks.py + ws_manager.py (ConnectionManager moved, QueueBroadcaster added)

**Files:**
- Create: `backend/background_tasks.py`
- Create: `backend/ws_manager.py`
- Test: `backend/tests/test_background_tasks.py`, `backend/tests/test_ws_manager.py`

**Interfaces:**
- Produces: `track_task(task: asyncio.Task) -> None`; `ConnectionManager` (connect/disconnect/broadcast, same as current `backend/main.py`); `QueueBroadcaster(connection_manager, flush_interval=0.05)` with `.notify(entry_dict: dict) -> None` — used by Task 8 (`main.py` composition root).

- [ ] **Step 1: Write the failing test for background_tasks.py**

Create `backend/tests/test_background_tasks.py`:

```python
import asyncio

from background_tasks import track_task, _background_tasks


def test_track_task_holds_reference_until_done():
    async def run():
        async def noop():
            await asyncio.sleep(0.05)

        task = asyncio.create_task(noop())
        track_task(task)
        assert task in _background_tasks
        await task
        assert task not in _background_tasks

    asyncio.run(run())
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest backend/tests/test_background_tasks.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'background_tasks'`

- [ ] **Step 3: Implement `background_tasks.py`**

Create `backend/background_tasks.py`:

```python
import asyncio

_background_tasks: set[asyncio.Task] = set()


def track_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest backend/tests/test_background_tasks.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing tests for ws_manager.py**

Create `backend/tests/test_ws_manager.py`:

```python
import asyncio

from ws_manager import ConnectionManager, QueueBroadcaster


class _FakeSocket:
    def __init__(self):
        self.received = []

    async def send_json(self, message: dict) -> None:
        self.received.append(message)


class _RecordingSocket:
    """Fake WebSocket that records how many send_json calls are in-flight
    concurrently, so we can prove ConnectionManager.broadcast serializes them."""

    def __init__(self, delay: float = 0.01):
        self.delay = delay
        self.received = []
        self._active = 0
        self.max_concurrent_sends = 0

    async def send_json(self, message: dict) -> None:
        self._active += 1
        self.max_concurrent_sends = max(self.max_concurrent_sends, self._active)
        await asyncio.sleep(self.delay)
        self.received.append(message)
        self._active -= 1


def test_connection_manager_broadcast_serializes_concurrent_sends():
    manager = ConnectionManager()
    socket = _RecordingSocket()
    manager.active.append(socket)

    async def run():
        await asyncio.gather(
            manager.broadcast({"n": 1}),
            manager.broadcast({"n": 2}),
        )

    asyncio.run(run())

    assert socket.max_concurrent_sends == 1
    assert len(socket.received) == 2


def test_queue_broadcaster_coalesces_rapid_updates_into_single_batch_message():
    manager = ConnectionManager()
    socket = _FakeSocket()
    manager.active.append(socket)
    broadcaster = QueueBroadcaster(manager, flush_interval=0.02)

    async def run():
        broadcaster.notify({"id": "e1", "status": "downloading", "percent": 10})
        broadcaster.notify({"id": "e1", "status": "downloading", "percent": 20})
        broadcaster.notify({"id": "e2", "status": "queued", "percent": 0})
        await asyncio.sleep(0.05)

    asyncio.run(run())

    assert len(socket.received) == 1
    batch_message = socket.received[0]
    assert batch_message["type"] == "update_batch"
    entries_by_id = {e["id"]: e for e in batch_message["entries"]}
    assert entries_by_id["e1"]["percent"] == 20
    assert "e2" in entries_by_id


def test_queue_broadcaster_does_nothing_with_no_running_loop():
    manager = ConnectionManager()
    broadcaster = QueueBroadcaster(manager)
    broadcaster.notify({"id": "e1", "status": "queued"})  # must not raise
```

- [ ] **Step 6: Run to verify they fail**

Run: `python -m pytest backend/tests/test_ws_manager.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'ws_manager'`

- [ ] **Step 7: Implement `ws_manager.py`**

Create `backend/ws_manager.py`:

```python
import asyncio
from typing import Optional

from fastapi import WebSocket

from background_tasks import track_task

FLUSH_INTERVAL_SECONDS = 0.05


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []
        self._send_lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        async with self._send_lock:
            stale = []
            for connection in self.active:
                try:
                    await connection.send_json(message)
                except Exception:
                    stale.append(connection)
            for connection in stale:
                self.disconnect(connection)


class QueueBroadcaster:
    """Coalesces rapid per-entry notify() calls into a single WS message,
    flushed on a short interval — prevents a big batch (100 pasted links)
    from producing one WS frame per entry mutation."""

    def __init__(
        self,
        connection_manager: ConnectionManager,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
    ):
        self.connection_manager = connection_manager
        self.flush_interval = flush_interval
        self._pending: dict[str, dict] = {}
        self._flush_scheduled = False

    def notify(self, entry_dict: dict) -> None:
        self._pending[entry_dict["id"]] = entry_dict
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no running event loop (e.g. called directly in tests)
        if not self._flush_scheduled:
            self._flush_scheduled = True
            track_task(asyncio.create_task(self._flush_after_delay()))

    async def _flush_after_delay(self) -> None:
        await asyncio.sleep(self.flush_interval)
        entries = list(self._pending.values())
        self._pending.clear()
        self._flush_scheduled = False
        if entries:
            await self.connection_manager.broadcast(
                {"type": "update_batch", "entries": entries}
            )
```

- [ ] **Step 8: Run to verify they pass**

Run: `python -m pytest backend/tests/test_ws_manager.py -v`
Expected: all 3 tests PASS

- [ ] **Step 9: Commit**

```bash
git add backend/background_tasks.py backend/ws_manager.py backend/tests/test_background_tasks.py backend/tests/test_ws_manager.py
git commit -m "feat: extract background task tracking and add coalescing WS broadcaster"
```

---

### Task 6: queue_routes.py (faithful extraction, no new behavior yet)

**Files:**
- Create: `backend/queue_routes.py`
- Create: `backend/tests/test_queue_routes.py`

**Interfaces:**
- Consumes: `QueueManager`, `DownloadOrchestrator` (existing, unchanged in this task), `parse_url_list`, `track_task` from Task 5.
- Produces: `build_queue_router(queue_manager, orchestrator, state) -> APIRouter`, `AppState` (holds `referer` only — `output_folder` is dropped from here in Task 11), `QueueRequest`, `RetryRequest` — used by Task 8 (`main.py`).

This task is a pure move of the existing `/queue` GET/POST and `/queue/{id}/retry` logic out of `backend/main.py` into a router factory. Behavior must be identical to what's in `main.py` today.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_queue_routes.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from queue_manager import QueueManager
from downloader import DownloadOrchestrator
from queue_routes import build_queue_router, AppState


async def fake_download_all(entry_ids, output_folder, referer=None):
    return None


def _make_client():
    queue_manager = QueueManager()
    orchestrator = DownloadOrchestrator(queue_manager)
    orchestrator.download_all = fake_download_all
    state = AppState()
    app = FastAPI()
    app.include_router(build_queue_router(queue_manager, orchestrator, state))
    return TestClient(app), queue_manager


def test_post_queue_creates_entries_for_valid_urls():
    client, _ = _make_client()
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
    client, _ = _make_client()
    response = client.post(
        "/queue",
        json={"urls_text": "https://vimeo.com/111\nnot a url", "output_folder": "C:/downloads"},
    )
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["invalid_lines"] == ["not a url"]


def test_post_queue_accepts_non_vimeo_urls_like_loom():
    client, _ = _make_client()
    response = client.post(
        "/queue",
        json={"urls_text": "https://www.loom.com/share/abc123", "output_folder": "C:/downloads"},
    )
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["invalid_lines"] == []


def test_get_queue_returns_current_entries():
    client, _ = _make_client()
    client.post(
        "/queue", json={"urls_text": "https://vimeo.com/333", "output_folder": "C:/downloads"}
    )
    response = client.get("/queue")
    urls = [entry["url"] for entry in response.json()["entries"]]
    assert "https://vimeo.com/333" in urls


def test_retry_entry_resets_status_to_queued():
    client, queue_manager = _make_client()
    post_response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/444", "output_folder": "C:/downloads"}
    )
    entry_id = post_response.json()["entries"][0]["id"]
    queue_manager.set_error(entry_id, "some error")

    response = client.post(f"/queue/{entry_id}/retry", json={})
    assert response.status_code == 202
    assert response.json()["entry"]["status"] == "queued"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_queue_routes.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'queue_routes'`

- [ ] **Step 3: Implement `queue_routes.py`**

Create `backend/queue_routes.py`:

```python
import asyncio
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from background_tasks import track_task
from downloader import DownloadOrchestrator
from queue_manager import QueueManager
from url_validation import parse_url_list


class QueueRequest(BaseModel):
    urls_text: str
    output_folder: str
    referer: Optional[str] = None


class RetryRequest(BaseModel):
    referer: Optional[str] = None


class AppState:
    def __init__(self):
        self.output_folder: Optional[str] = None
        self.referer: Optional[str] = None


def build_queue_router(
    queue_manager: QueueManager,
    orchestrator: DownloadOrchestrator,
    state: AppState,
) -> APIRouter:
    router = APIRouter()

    @router.get("/queue")
    def get_queue() -> dict:
        return {"entries": queue_manager.to_list()}

    @router.post("/queue", status_code=202)
    async def post_queue(request: QueueRequest) -> dict:
        valid_urls, invalid_lines = parse_url_list(request.urls_text)
        state.output_folder = request.output_folder
        state.referer = request.referer
        entries = queue_manager.add_entries(valid_urls)
        if entries:
            track_task(
                asyncio.create_task(
                    orchestrator.download_all(
                        [entry.id for entry in entries],
                        request.output_folder,
                        request.referer,
                    )
                )
            )
        return {
            "entries": [entry.to_dict() for entry in entries],
            "invalid_lines": invalid_lines,
        }

    @router.post("/queue/{entry_id}/retry", status_code=202)
    async def retry_entry(entry_id: str, request: RetryRequest) -> dict:
        queue_manager.reset_for_retry(entry_id)
        referer = request.referer or state.referer
        track_task(
            asyncio.create_task(
                orchestrator.download_all([entry_id], state.output_folder, referer)
            )
        )
        return {"entry": queue_manager.to_dict(entry_id)}

    return router
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest backend/tests/test_queue_routes.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/queue_routes.py backend/tests/test_queue_routes.py
git commit -m "refactor: extract queue routes into queue_routes.py"
```

---

### Task 7: history_routes.py + settings_routes.py

**Files:**
- Create: `backend/history_routes.py`, `backend/tests/test_history_routes.py`
- Create: `backend/settings_routes.py`, `backend/tests/test_settings_routes.py`

**Interfaces:**
- Consumes: `HistoryStore` (Task 3), `SettingsStore` (Task 2), `check_aria2c_available` (existing, in `downloader.py`).
- Produces: `build_history_router(history_store) -> APIRouter`, `build_settings_router(settings_store) -> APIRouter` — used by Task 8 (`main.py`).

- [ ] **Step 1: Write the failing tests for history_routes.py**

Create `backend/tests/test_history_routes.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from history_store import HistoryStore
from history_routes import build_history_router


def _make_client():
    store = HistoryStore()
    app = FastAPI()
    app.include_router(build_history_router(store))
    return TestClient(app), store


def test_get_history_returns_recorded_entries():
    client, store = _make_client()
    store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="Video 1",
        output_path="C:/downloads/Video 1.mp4", total_size="10MB",
        status="done", error_reason=None, retry_count=0,
    )
    response = client.get("/history")
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["url"] == "https://vimeo.com/1"


def test_get_history_filters_by_query_param():
    client, store = _make_client()
    store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="Marketing Intro",
        output_path=None, total_size=None, status="done", error_reason=None, retry_count=0,
    )
    store.record(
        entry_id="e2", batch_id="b1", url="https://vimeo.com/2", title="Sales Pitch",
        output_path=None, total_size=None, status="done", error_reason=None, retry_count=0,
    )
    response = client.get("/history", params={"q": "Marketing"})
    entries = response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["title"] == "Marketing Intro"


def test_get_history_filters_by_status():
    client, store = _make_client()
    store.record(
        entry_id="e1", batch_id="b1", url="https://vimeo.com/1", title="V1",
        output_path=None, total_size=None, status="done", error_reason=None, retry_count=0,
    )
    store.record(
        entry_id="e2", batch_id="b1", url="https://vimeo.com/2", title="V2",
        output_path=None, total_size=None, status="error", error_reason="failed", retry_count=0,
    )
    response = client.get("/history", params={"status": "error"})
    entries = response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["status"] == "error"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_history_routes.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'history_routes'`

- [ ] **Step 3: Implement `history_routes.py`**

Create `backend/history_routes.py`:

```python
from typing import Optional

from fastapi import APIRouter

from history_store import HistoryStore


def build_history_router(history_store: HistoryStore) -> APIRouter:
    router = APIRouter()

    @router.get("/history")
    def get_history(
        q: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        entries = history_store.search(query=q, status=status, limit=limit, offset=offset)
        return {"entries": entries}

    return router
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest backend/tests/test_history_routes.py -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Write the failing tests for settings_routes.py**

Create `backend/tests/test_settings_routes.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from settings_store import SettingsStore
from settings_routes import build_settings_router


def _make_client():
    store = SettingsStore()
    app = FastAPI()
    app.include_router(build_settings_router(store))
    return TestClient(app), store


def test_get_settings_returns_current_values():
    client, _ = _make_client()
    response = client.get("/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["max_concurrent_downloads"] == 3
    assert "aria2c_detected" in body


def test_patch_settings_updates_and_returns_new_values():
    client, _ = _make_client()
    response = client.patch("/settings", json={"max_concurrent_downloads": 5})
    assert response.status_code == 200
    assert response.json()["max_concurrent_downloads"] == 5


def test_patch_settings_rejects_out_of_range_concurrency():
    client, _ = _make_client()
    response = client.patch("/settings", json={"max_concurrent_downloads": 100})
    assert response.status_code == 422


def test_patch_settings_only_updates_provided_fields():
    client, store = _make_client()
    client.patch("/settings", json={"aria2c_enabled": False})
    settings = store.get()
    assert settings["aria2c_enabled"] is False
    assert settings["max_concurrent_downloads"] == 3
```

- [ ] **Step 6: Run to verify they fail**

Run: `python -m pytest backend/tests/test_settings_routes.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'settings_routes'`

- [ ] **Step 7: Implement `settings_routes.py`**

Create `backend/settings_routes.py`:

```python
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from downloader import check_aria2c_available
from settings_store import SettingsStore


class SettingsUpdateRequest(BaseModel):
    max_concurrent_downloads: Optional[int] = Field(None, ge=1, le=10)
    concurrent_fragment_downloads: Optional[int] = Field(None, ge=1, le=32)
    aria2c_enabled: Optional[bool] = None
    skip_duplicates: Optional[bool] = None
    default_output_folder: Optional[str] = None


def build_settings_router(settings_store: SettingsStore) -> APIRouter:
    router = APIRouter()

    @router.get("/settings")
    def get_settings() -> dict:
        settings = settings_store.get()
        settings["aria2c_detected"] = check_aria2c_available()
        return settings

    @router.patch("/settings")
    def patch_settings(request: SettingsUpdateRequest) -> dict:
        updated = settings_store.update(**request.model_dump())
        updated["aria2c_detected"] = check_aria2c_available()
        return updated

    return router
```

- [ ] **Step 8: Run to verify they pass**

Run: `python -m pytest backend/tests/test_settings_routes.py -v`
Expected: all 4 tests PASS

- [ ] **Step 9: Commit**

```bash
git add backend/history_routes.py backend/settings_routes.py backend/tests/test_history_routes.py backend/tests/test_settings_routes.py
git commit -m "feat: add history and settings API routes"
```

---

### Task 8: main.py composition root rewrite

**Files:**
- Modify: `backend/main.py` (full rewrite)
- Modify: `backend/tests/test_main.py` (shrink to smoke tests only)

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: the same public HTTP surface as before (`/health`, `/queue`, `/queue/{id}/retry`, `/ws`) plus the new `/history`, `/settings` — used by the Electron frontend (unchanged contract) and by Task 9's orchestrator wiring (Task 9 will modify this file further).

- [ ] **Step 1: Rewrite `backend/main.py`**

Replace the full contents of `backend/main.py` with:

```python
import os
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from downloader import DownloadOrchestrator, check_ffmpeg_available
from history_routes import build_history_router
from history_store import HistoryStore
from queue_manager import QueueManager
from queue_routes import AppState, build_queue_router
from settings_routes import build_settings_router
from settings_store import SettingsStore
from ws_manager import ConnectionManager, QueueBroadcaster

DB_PATH = os.environ.get(
    "CLIP_PULL_DB_PATH", str(Path(__file__).parent / "data" / "clip_pull.db")
)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

connection_manager = ConnectionManager()
broadcaster = QueueBroadcaster(connection_manager)

history_store = HistoryStore(DB_PATH)
settings_store = SettingsStore(DB_PATH)

queue_manager = QueueManager(on_update=broadcaster.notify)
orchestrator = DownloadOrchestrator(queue_manager)

state = AppState()

app.include_router(build_queue_router(queue_manager, orchestrator, state))
app.include_router(build_history_router(history_store))
app.include_router(build_settings_router(settings_store))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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

    if not check_ffmpeg_available():
        print(
            "WARNING: ffmpeg not found on PATH. High-quality downloads "
            "require ffmpeg to merge video+audio streams; downloads may fail "
            "or fall back to lower quality without it.",
            file=sys.stderr,
        )

    uvicorn.run(app, host="127.0.0.1", port=8934)
```

- [ ] **Step 2: Shrink `backend/tests/test_main.py`**

Replace the full contents of `backend/tests/test_main.py` with:

```python
import main as main_module
from fastapi.testclient import TestClient


def test_health_returns_ok():
    client = TestClient(main_module.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_headers_present_for_cross_origin_request():
    client = TestClient(main_module.app)
    response = client.get("/health", headers={"Origin": "http://example.com"})
    assert response.headers.get("access-control-allow-origin") == "*"
```

(The rest of the old `test_main.py`'s coverage — queue behavior, the WS broadcast serialization test — now lives in `test_queue_routes.py` (Task 6) and `test_ws_manager.py` (Task 5) respectively. `conftest.py` from Task 1 guarantees `main_module`'s import doesn't touch a real DB file during tests.)

- [ ] **Step 3: Run the full backend suite**

Run: `python -m pytest backend/tests -v`
Expected: all tests pass. Count should be roughly: 4 (db) + 5 (settings_store) + 9 (history_store) + 17 (queue_manager) + 1 (background_tasks) + 3 (ws_manager) + 5 (queue_routes) + 3 (history_routes) + 4 (settings_routes) + 2 (main, shrunk) + existing `test_downloader.py`/`test_url_validation.py` counts (unchanged in this task) = should be green with no failures.

- [ ] **Step 4: Manually verify the app still boots**

Run: `python backend/main.py` in one terminal, then in another: `curl http://127.0.0.1:8934/health` and `curl http://127.0.0.1:8934/settings` — expect `{"status":"ok"}` and a settings JSON body respectively. Stop the server (Ctrl+C) when done.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_main.py
git commit -m "refactor: rewrite main.py as a thin composition root"
```

---

### Task 9: Orchestrator enhancement — settings-driven concurrency/fragments/aria2c, batch completion, history recording

**Files:**
- Modify: `backend/downloader.py`
- Modify: `backend/tests/test_downloader.py`

**Interfaces:**
- Consumes: `QueueEntry.batch_id`/`is_batch_complete`/`batch_summary` from Task 4.
- Produces: `resolve_use_aria2c(enabled: bool) -> bool`; `build_ydl_opts(..., concurrent_fragment_downloads=CONCURRENT_FRAGMENT_DOWNLOADS, use_aria2c=False)`; `run_download(..., concurrent_fragment_downloads=CONCURRENT_FRAGMENT_DOWNLOADS, aria2c_enabled=True)`; `DownloadOrchestrator.__init__` gains `get_max_concurrent`, `get_fragment_concurrency`, `get_aria2c_enabled`, `record_history`, `on_batch_complete` — used by Task 11 (`main.py` wiring).

**This is the most involved task in the plan — read it fully before starting.**

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_downloader.py`, first update the import block to add `resolve_use_aria2c` and `resolve_output_folder` is NOT here yet (that's Task 10) — just add `resolve_use_aria2c`:

```python
from downloader import (
    CONCURRENT_FRAGMENT_DOWNLOADS,
    DownloadOrchestrator,
    build_ydl_opts,
    check_aria2c_available,
    check_ffmpeg_available,
    format_bytes,
    format_speed,
    is_referer_blocked_error,
    resolve_use_aria2c,
    sanitize_filename,
)
```

Now update the **existing** test fakes' signatures (these currently take only 4 params; `download_fn` will now be called with 6 positional args). Find and update each of these 6 functions in the file, adding `, concurrent_fragment_downloads=8, aria2c_enabled=True` to their parameter list — do not change their bodies:

1. `fake_download` inside `test_progress_hook_produces_clean_speed_and_integer_eta`
2. `fake_download` inside `test_download_entry_final_update_clears_size_fields_like_speed_and_eta`
3. `fake_download` inside `test_download_entry_marks_done_and_sets_title_on_success`
4. `failing_download` inside `test_download_entry_sets_referer_blocked_message_on_403`
5. `fake_download` inside `test_progress_hook_throttles_rapid_updates`
6. `slow_download` inside `test_download_all_never_exceeds_max_concurrency`

For example, function 3 changes from:
```python
    def fake_download(url, output_folder, referer, progress_hook):
```
to:
```python
    def fake_download(url, output_folder, referer, progress_hook, concurrent_fragment_downloads=8, aria2c_enabled=True):
```
Apply the identical parameter-list change (only the parameter list, not the body) to all 6 functions listed above.

Now append these new tests at the end of the file:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_downloader.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_use_aria2c'` (and once that's fixed by Step 3, the new-behavior tests will fail until the orchestrator changes land — that's expected mid-step; the key checkpoint is Step 4's full green run)

- [ ] **Step 3: Implement the changes in `downloader.py`**

Add `resolve_use_aria2c` right after `check_aria2c_available`:

```python
def resolve_use_aria2c(enabled: bool) -> bool:
    return enabled and check_aria2c_available()
```

Replace `build_ydl_opts` with:

```python
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
```

Replace `run_download` with:

```python
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
```

Replace the whole `DownloadOrchestrator` class with:

```python
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
            except Exception as exc:
                reason = (
                    REFERER_BLOCKED_MESSAGE
                    if is_referer_blocked_error(exc)
                    else str(exc)
                )
                self.queue_manager.set_error(entry_id, reason)
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
            finally:
                if entry.batch_id and self.on_batch_complete:
                    if self.queue_manager.is_batch_complete(entry.batch_id):
                        summary = self.queue_manager.batch_summary(entry.batch_id)
                        self.on_batch_complete(entry.batch_id, summary)

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_downloader.py -v`
Expected: all tests PASS (9 pre-existing renamed/updated + ~13 new = ~22 tests in this file)

- [ ] **Step 5: Run the full suite to catch any cross-file regressions**

Run: `python -m pytest backend/tests -v`
Expected: all tests PASS. Run it 3 times in a row to check for flakiness (this task touches the same async-scheduling area that previously had a real race — see the `asyncio.sleep(0)` comment above, already fixed, but worth re-confirming under the new code paths).

- [ ] **Step 6: Commit**

```bash
git add backend/downloader.py backend/tests/test_downloader.py
git commit -m "feat: wire settings-driven concurrency, history recording, and batch completion into DownloadOrchestrator"
```

---

### Task 10: resolve_output_folder helper (subfolder resolution)

**Files:**
- Modify: `backend/downloader.py`
- Modify: `backend/tests/test_downloader.py`

**Interfaces:**
- Consumes: `sanitize_filename` (existing).
- Produces: `resolve_output_folder(base_folder: str, subfolder: Optional[str]) -> str` — used by Task 11 (`queue_routes.py`).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_downloader.py` (update the import line to add `resolve_output_folder`, then append the tests):

```python
from downloader import (
    CONCURRENT_FRAGMENT_DOWNLOADS,
    DownloadOrchestrator,
    build_ydl_opts,
    check_aria2c_available,
    check_ffmpeg_available,
    format_bytes,
    format_speed,
    is_referer_blocked_error,
    resolve_output_folder,
    resolve_use_aria2c,
    sanitize_filename,
)
```

```python
def test_resolve_output_folder_appends_sanitized_subfolder_when_given():
    result = resolve_output_folder("C:/downloads", "My Course: Part 1")
    assert result == str(Path("C:/downloads") / "My Course_ Part 1")


def test_resolve_output_folder_returns_base_unchanged_when_subfolder_none():
    assert resolve_output_folder("C:/downloads", None) == "C:/downloads"


def test_resolve_output_folder_returns_base_unchanged_when_subfolder_blank():
    assert resolve_output_folder("C:/downloads", "   ") == "C:/downloads"
```

This test file needs `Path` imported at the top if not already — check the existing imports in `test_downloader.py`; if `from pathlib import Path` is not present, add it.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/tests/test_downloader.py -v`
Expected: FAIL/ERROR — `ImportError: cannot import name 'resolve_output_folder'`

- [ ] **Step 3: Implement `resolve_output_folder` in `downloader.py`**

Add this function right after `sanitize_filename`:

```python
def resolve_output_folder(base_folder: str, subfolder: Optional[str]) -> str:
    if not subfolder or not subfolder.strip():
        return base_folder
    return str(Path(base_folder) / sanitize_filename(subfolder))
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest backend/tests/test_downloader.py -v`
Expected: all tests PASS (previous count + 3)

- [ ] **Step 5: Commit**

```bash
git add backend/downloader.py backend/tests/test_downloader.py
git commit -m "feat: add resolve_output_folder helper for per-course subfolders"
```

---

### Task 11: Wire duplicate detection, subfolders, batch IDs, and orchestrator callables into queue_routes.py + main.py

**Files:**
- Modify: `backend/queue_routes.py` (full rewrite)
- Modify: `backend/tests/test_queue_routes.py` (full rewrite)
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `HistoryStore.was_previously_downloaded`/`.record` (Task 3), `SettingsStore.get` (Task 2), `resolve_output_folder` (Task 10), `QueueManager.add_entries`'s new params (Task 4), `DownloadOrchestrator`'s new callables (Task 9).
- Produces: updated `POST /queue` response shape (`{entries, invalid_lines, skipped_duplicate_urls}`), `QueueRequest.subfolder` — used by Task 14 (frontend).

- [ ] **Step 1: Rewrite `backend/queue_routes.py`**

Replace the full contents of `backend/queue_routes.py` with:

```python
import asyncio
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from background_tasks import track_task
from downloader import DownloadOrchestrator, resolve_output_folder
from history_store import HistoryStore
from queue_manager import QueueManager
from settings_store import SettingsStore
from url_validation import parse_url_list


class QueueRequest(BaseModel):
    urls_text: str
    output_folder: str
    referer: Optional[str] = None
    subfolder: Optional[str] = None


class RetryRequest(BaseModel):
    referer: Optional[str] = None


class AppState:
    def __init__(self):
        self.referer: Optional[str] = None


def build_queue_router(
    queue_manager: QueueManager,
    orchestrator: DownloadOrchestrator,
    history_store: HistoryStore,
    settings_store: SettingsStore,
    state: AppState,
) -> APIRouter:
    router = APIRouter()

    @router.get("/queue")
    def get_queue() -> dict:
        return {"entries": queue_manager.to_list()}

    @router.post("/queue", status_code=202)
    async def post_queue(request: QueueRequest) -> dict:
        valid_urls, invalid_lines = parse_url_list(request.urls_text)
        state.referer = request.referer

        resolved_folder = resolve_output_folder(request.output_folder, request.subfolder)
        Path(resolved_folder).mkdir(parents=True, exist_ok=True)

        previously_downloaded_urls = history_store.was_previously_downloaded(valid_urls)

        skip_duplicates = settings_store.get()["skip_duplicates"]
        skipped_duplicate_urls: list[str] = []
        if skip_duplicates and previously_downloaded_urls:
            skipped_duplicate_urls = [u for u in valid_urls if u in previously_downloaded_urls]
            valid_urls = [u for u in valid_urls if u not in previously_downloaded_urls]
            previously_downloaded_urls = set()

        batch_id = uuid.uuid4().hex if valid_urls else None
        entries = queue_manager.add_entries(
            valid_urls,
            batch_id=batch_id,
            output_folder=resolved_folder,
            previously_downloaded_urls=previously_downloaded_urls,
        )
        if entries:
            track_task(
                asyncio.create_task(
                    orchestrator.download_all(
                        [entry.id for entry in entries],
                        resolved_folder,
                        request.referer,
                    )
                )
            )
        return {
            "entries": [entry.to_dict() for entry in entries],
            "invalid_lines": invalid_lines,
            "skipped_duplicate_urls": skipped_duplicate_urls,
        }

    @router.post("/queue/{entry_id}/retry", status_code=202)
    async def retry_entry(entry_id: str, request: RetryRequest) -> dict:
        entry = queue_manager.get(entry_id)
        queue_manager.reset_for_retry(entry_id)
        referer = request.referer or state.referer
        track_task(
            asyncio.create_task(
                orchestrator.download_all([entry_id], entry.output_folder, referer)
            )
        )
        return {"entry": queue_manager.to_dict(entry_id)}

    return router
```

- [ ] **Step 2: Rewrite `backend/tests/test_queue_routes.py`**

Replace the full contents of `backend/tests/test_queue_routes.py` with:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from queue_manager import QueueManager
from downloader import DownloadOrchestrator
from history_store import HistoryStore
from settings_store import SettingsStore
from queue_routes import build_queue_router, AppState


async def fake_download_all(entry_ids, output_folder, referer=None):
    return None


def _make_client():
    queue_manager = QueueManager()
    orchestrator = DownloadOrchestrator(queue_manager)
    orchestrator.download_all = fake_download_all
    history_store = HistoryStore()
    settings_store = SettingsStore()
    state = AppState()
    app = FastAPI()
    app.include_router(
        build_queue_router(queue_manager, orchestrator, history_store, settings_store, state)
    )
    return TestClient(app), queue_manager, history_store, settings_store


def test_post_queue_creates_entries_for_valid_urls():
    client, _, _, _ = _make_client()
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
    client, _, _, _ = _make_client()
    response = client.post(
        "/queue",
        json={"urls_text": "https://vimeo.com/111\nnot a url", "output_folder": "C:/downloads"},
    )
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["invalid_lines"] == ["not a url"]


def test_post_queue_accepts_non_vimeo_urls_like_loom():
    client, _, _, _ = _make_client()
    response = client.post(
        "/queue",
        json={"urls_text": "https://www.loom.com/share/abc123", "output_folder": "C:/downloads"},
    )
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["invalid_lines"] == []


def test_get_queue_returns_current_entries():
    client, _, _, _ = _make_client()
    client.post(
        "/queue", json={"urls_text": "https://vimeo.com/333", "output_folder": "C:/downloads"}
    )
    response = client.get("/queue")
    urls = [entry["url"] for entry in response.json()["entries"]]
    assert "https://vimeo.com/333" in urls


def test_retry_entry_resets_status_to_queued():
    client, queue_manager, _, _ = _make_client()
    post_response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/444", "output_folder": "C:/downloads"}
    )
    entry_id = post_response.json()["entries"][0]["id"]
    queue_manager.set_error(entry_id, "some error")

    response = client.post(f"/queue/{entry_id}/retry", json={})
    assert response.status_code == 202
    assert response.json()["entry"]["status"] == "queued"


def test_post_queue_flags_previously_downloaded_urls():
    client, _, history_store, _ = _make_client()
    history_store.record(
        entry_id="e0", batch_id=None, url="https://vimeo.com/999", title="Old",
        output_path="C:/out/Old.mp4", total_size="10MB", status="done",
        error_reason=None, retry_count=0,
    )
    response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/999", "output_folder": "C:/downloads"}
    )
    entries = response.json()["entries"]
    assert entries[0]["previously_downloaded"] is True


def test_post_queue_skips_duplicates_when_setting_enabled():
    client, _, history_store, settings_store = _make_client()
    settings_store.update(skip_duplicates=True)
    history_store.record(
        entry_id="e0", batch_id=None, url="https://vimeo.com/999", title="Old",
        output_path="C:/out/Old.mp4", total_size="10MB", status="done",
        error_reason=None, retry_count=0,
    )
    response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/999", "output_folder": "C:/downloads"}
    )
    body = response.json()
    assert body["entries"] == []
    assert body["skipped_duplicate_urls"] == ["https://vimeo.com/999"]


def test_post_queue_generates_shared_batch_id_for_all_entries_in_one_request():
    client, _, _, _ = _make_client()
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/1\nhttps://vimeo.com/2",
            "output_folder": "C:/downloads",
        },
    )
    entries = response.json()["entries"]
    assert entries[0]["batch_id"] == entries[1]["batch_id"]
    assert entries[0]["batch_id"] is not None


def test_post_queue_creates_subfolder_when_subfolder_name_provided(tmp_path):
    client, _, _, _ = _make_client()
    base_folder = tmp_path / "downloads"
    base_folder.mkdir()
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/1",
            "output_folder": str(base_folder),
            "subfolder": "My Course",
        },
    )
    entries = response.json()["entries"]
    expected_folder = str(base_folder / "My Course")
    assert entries[0]["output_folder"] == expected_folder
    assert (base_folder / "My Course").is_dir()


def test_post_queue_flat_folder_when_subfolder_omitted(tmp_path):
    client, _, _, _ = _make_client()
    base_folder = tmp_path / "downloads"
    base_folder.mkdir()
    response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/1", "output_folder": str(base_folder)}
    )
    entries = response.json()["entries"]
    assert entries[0]["output_folder"] == str(base_folder)


def test_retry_entry_uses_entrys_own_output_folder_not_global_state():
    client, queue_manager, _, _ = _make_client()
    first = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/1", "output_folder": "C:/folder-a"}
    )
    entry_id = first.json()["entries"][0]["id"]
    queue_manager.set_error(entry_id, "boom")

    client.post(
        "/queue", json={"urls_text": "https://vimeo.com/2", "output_folder": "C:/folder-b"}
    )

    response = client.post(f"/queue/{entry_id}/retry", json={})
    assert response.status_code == 202
    assert queue_manager.get(entry_id).output_folder == "C:/folder-a"
```

- [ ] **Step 3: Run to verify the new tests fail and old ones still compile**

Run: `python -m pytest backend/tests/test_queue_routes.py -v`
Expected: import/signature errors until Step 1's `queue_routes.py` rewrite is in place — since Steps 1 and 2 are both "write the implementation and its test together" for a refactor task, run this only after both steps are done, and expect a clean PASS (there's no meaningful separate RED state here since this is a coordinated rewrite of implementation + tests, not new-behavior-on-top-of-old — the "RED" checkpoint that matters is confirming the OLD test file failed to import before this task started touching it, which was already demonstrated back in Task 6).

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest backend/tests/test_queue_routes.py -v`
Expected: all 11 tests PASS

- [ ] **Step 5: Update `backend/main.py`'s orchestrator wiring**

In `backend/main.py`, add these imports near the top (alongside the existing ones):

```python
import asyncio

from background_tasks import track_task
```

Replace the two lines:
```python
app.include_router(build_queue_router(queue_manager, orchestrator, state))
```
and
```python
orchestrator = DownloadOrchestrator(queue_manager)
```
with, in this order (orchestrator now depends on `history_store`/`settings_store`, which are already constructed above it):

```python
orchestrator = DownloadOrchestrator(
    queue_manager,
    get_max_concurrent=lambda: settings_store.get()["max_concurrent_downloads"],
    get_fragment_concurrency=lambda: settings_store.get()["concurrent_fragment_downloads"],
    get_aria2c_enabled=lambda: settings_store.get()["aria2c_enabled"],
    record_history=lambda **kwargs: history_store.record(**kwargs),
    on_batch_complete=lambda batch_id, summary: track_task(
        asyncio.create_task(
            connection_manager.broadcast(
                {"type": "batch_complete", "batch_id": batch_id, "summary": summary}
            )
        )
    ),
)
```

and:

```python
app.include_router(
    build_queue_router(queue_manager, orchestrator, history_store, settings_store, state)
)
```

Double check the final file has `queue_manager`, `history_store`, `settings_store`, `connection_manager`, `broadcaster` all defined *before* `orchestrator = DownloadOrchestrator(...)`, since the lambdas close over them by name (they don't need to exist yet at lambda-definition time since lambdas are lazily evaluated, but keep the construction order matching the plan below for clarity):

```python
connection_manager = ConnectionManager()
broadcaster = QueueBroadcaster(connection_manager)

history_store = HistoryStore(DB_PATH)
settings_store = SettingsStore(DB_PATH)

queue_manager = QueueManager(on_update=broadcaster.notify)
orchestrator = DownloadOrchestrator(
    queue_manager,
    get_max_concurrent=lambda: settings_store.get()["max_concurrent_downloads"],
    get_fragment_concurrency=lambda: settings_store.get()["concurrent_fragment_downloads"],
    get_aria2c_enabled=lambda: settings_store.get()["aria2c_enabled"],
    record_history=lambda **kwargs: history_store.record(**kwargs),
    on_batch_complete=lambda batch_id, summary: track_task(
        asyncio.create_task(
            connection_manager.broadcast(
                {"type": "batch_complete", "batch_id": batch_id, "summary": summary}
            )
        )
    ),
)

state = AppState()

app.include_router(
    build_queue_router(queue_manager, orchestrator, history_store, settings_store, state)
)
app.include_router(build_history_router(history_store))
app.include_router(build_settings_router(settings_store))
```

- [ ] **Step 6: Run the full backend suite**

Run: `python -m pytest backend/tests -v`
Expected: all tests PASS, no failures.

- [ ] **Step 7: Manually verify the app boots and duplicate-detection round-trips**

```bash
python backend/main.py &
sleep 1.5
curl -s -X POST http://127.0.0.1:8934/queue -H "Content-Type: application/json" -d "{\"urls_text\": \"https://vimeo.com/123456789\", \"output_folder\": \"C:/temp\"}"
curl -s http://127.0.0.1:8934/history
kill %1
```
Expected: the POST returns a 202 with one entry; `/history` is initially empty (nothing has completed yet in this quick manual check — that's fine, this step only confirms the endpoints don't error).

- [ ] **Step 8: Commit**

```bash
git add backend/queue_routes.py backend/tests/test_queue_routes.py backend/main.py
git commit -m "feat: wire duplicate detection, subfolders, and batch IDs into queue routes"
```

---

### Task 12: Frontend — tabs + Settings view

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/styles.css`
- Create: `frontend/tabs.js`
- Create: `frontend/settings-view.js`

**Interfaces:**
- Consumes: `GET/PATCH /settings` from Task 7, `window.api.chooseFolder()` (existing).

- [ ] **Step 1: Restructure `frontend/index.html`**

Wrap the existing `<div class="app">...</div>` (everything currently inside `<body>`) in a new `<div id="view-queue" class="view">`, add a `<nav class="tabs">` right after the opening `<body>` tag, and add two new hidden view divs. The full new `<body>` becomes:

```html
<body>
  <nav class="tabs">
    <button class="tab-btn active" data-view="view-queue" type="button">Queue</button>
    <button class="tab-btn" data-view="view-history" type="button">History</button>
    <button class="tab-btn" data-view="view-settings" type="button">Settings</button>
  </nav>

  <div id="view-queue" class="view">
    <div class="app">
      <!-- existing header, paste panel, and queue panel content goes here, unchanged -->
    </div>
  </div>

  <div id="view-history" class="view" hidden>
    <div class="app">
      <h2 class="queue-title">History</h2>
      <div class="history-controls">
        <input id="history-search" class="text-input" type="text" placeholder="Search title, URL, or file path…" />
        <select id="history-status-filter" class="text-input">
          <option value="">All</option>
          <option value="done">Done</option>
          <option value="error">Failed</option>
        </select>
      </div>
      <ul id="history-list" class="queue-list"></ul>
    </div>
  </div>

  <div id="view-settings" class="view" hidden>
    <div class="app">
      <h2 class="queue-title">Settings</h2>
      <div class="panel">
        <label class="field-label" for="setting-max-concurrent">Max concurrent downloads</label>
        <input id="setting-max-concurrent" class="text-input" type="number" min="1" max="10" />

        <label class="field-label" for="setting-fragment-concurrency">Fragment concurrency</label>
        <input id="setting-fragment-concurrency" class="text-input" type="number" min="1" max="32" />

        <label class="field-label">
          <input id="setting-aria2c-enabled" type="checkbox" /> Use aria2c when available
          <span id="aria2c-detected-note" class="queue-summary"></span>
        </label>

        <label class="field-label">
          <input id="setting-skip-duplicates" type="checkbox" /> Skip already-downloaded links automatically
        </label>

        <label class="field-label" for="setting-default-folder">Default output folder</label>
        <div class="folder-picker">
          <input id="setting-default-folder" class="folder-input" type="text" readonly />
          <button id="setting-browse-btn" class="btn btn--ghost" type="button">Browse…</button>
        </div>

        <button id="settings-save-btn" class="btn btn--primary" type="button">Save Settings</button>
      </div>
    </div>
  </div>

  <script type="module" src="ws-client.js"></script>
  <script type="module" src="tabs.js"></script>
  <script type="module" src="settings-view.js"></script>
  <script type="module" src="history-view.js"></script>
  <script type="module" src="renderer.js"></script>
</body>
```

Keep every existing element inside `#view-queue .app` byte-for-byte identical to what's there today (paste textarea/gutter, output-folder field, referer field, Start button, queue list) — this task only adds the wrapping structure and the two new views, it does not change the Queue view's contents (the course-folder field and duplicate badge come in Task 14).

- [ ] **Step 2: Add styles**

Append to `frontend/styles.css`:

```css
.tabs {
  display: flex;
  gap: 4px;
  padding: 12px 24px 0;
  border-bottom: 1px solid var(--panel-border);
}

.tab-btn {
  background: transparent;
  border: none;
  color: var(--text-dim);
  padding: 10px 16px;
  font-family: var(--font-display);
  font-weight: 600;
  cursor: pointer;
  border-bottom: 2px solid transparent;
}

.tab-btn.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

.view[hidden] {
  display: none;
}

.history-controls {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
```

- [ ] **Step 3: Create `frontend/tabs.js`**

```javascript
const tabButtons = document.querySelectorAll(".tab-btn");
const views = document.querySelectorAll(".view");

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    tabButtons.forEach((b) => b.classList.remove("active"));
    views.forEach((v) => (v.hidden = true));
    btn.classList.add("active");
    document.getElementById(btn.dataset.view).hidden = false;
  });
});
```

- [ ] **Step 4: Create `frontend/settings-view.js`**

```javascript
const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

const maxConcurrentInput = document.getElementById("setting-max-concurrent");
const fragmentConcurrencyInput = document.getElementById("setting-fragment-concurrency");
const aria2cEnabledInput = document.getElementById("setting-aria2c-enabled");
const aria2cDetectedNote = document.getElementById("aria2c-detected-note");
const skipDuplicatesInput = document.getElementById("setting-skip-duplicates");
const defaultFolderInput = document.getElementById("setting-default-folder");
const browseBtn = document.getElementById("setting-browse-btn");
const saveBtn = document.getElementById("settings-save-btn");

function applySettings(settings) {
  maxConcurrentInput.value = settings.max_concurrent_downloads;
  fragmentConcurrencyInput.value = settings.concurrent_fragment_downloads;
  aria2cEnabledInput.checked = settings.aria2c_enabled;
  aria2cDetectedNote.textContent = settings.aria2c_detected
    ? "(detected on this machine)"
    : "(not detected on PATH)";
  skipDuplicatesInput.checked = settings.skip_duplicates;
  defaultFolderInput.value = settings.default_output_folder || "";
}

async function loadSettings() {
  const response = await fetch(`${API_BASE}/settings`);
  applySettings(await response.json());
}

browseBtn.addEventListener("click", async () => {
  const folder = await window.api.chooseFolder();
  if (folder) defaultFolderInput.value = folder;
});

saveBtn.addEventListener("click", async () => {
  const response = await fetch(`${API_BASE}/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      max_concurrent_downloads: Number(maxConcurrentInput.value),
      concurrent_fragment_downloads: Number(fragmentConcurrencyInput.value),
      aria2c_enabled: aria2cEnabledInput.checked,
      skip_duplicates: skipDuplicatesInput.checked,
      default_output_folder: defaultFolderInput.value || null,
    }),
  });
  applySettings(await response.json());
});

loadSettings();
```

- [ ] **Step 5: Syntax-check and manually verify**

Run: `node --check frontend/tabs.js && node --check frontend/settings-view.js`
Expected: no output (success).

Manual check (after Task 13 also lands, since `history-view.js` is referenced by `index.html`'s script tags added in Step 1 — if running this task in isolation before Task 13 exists, temporarily comment out the `history-view.js` script tag to smoke-test, then restore it): `npm start`, click the Settings tab, confirm current values load, change a value, click Save, reload the app and confirm it persisted.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/styles.css frontend/tabs.js frontend/settings-view.js
git commit -m "feat: add tabbed navigation and a Settings view"
```

---

### Task 13: Frontend — History view + Electron reveal-file IPC

**Files:**
- Create: `frontend/history-view.js`
- Modify: `main.js`
- Modify: `preload.js`

**Interfaces:**
- Consumes: `GET /history` from Task 7.
- Produces: `window.api.revealFile(path)` — used only within this task's own `history-view.js`.

- [ ] **Step 1: Add the reveal-file IPC handler to `main.js`**

In `main.js`, change the Electron import line from:
```javascript
const { app, BrowserWindow, dialog, ipcMain } = require("electron");
```
to:
```javascript
const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
```

Add this handler alongside the existing `ipcMain.handle("choose-folder", ...)`:

```javascript
ipcMain.handle("reveal-file", (_, filePath) => {
  shell.showItemInFolder(filePath);
});
```

- [ ] **Step 2: Expose it in `preload.js`**

Update the `contextBridge.exposeInMainWorld` call to add `revealFile`:

```javascript
contextBridge.exposeInMainWorld("api", {
  chooseFolder: () => ipcRenderer.invoke("choose-folder"),
  revealFile: (filePath) => ipcRenderer.invoke("reveal-file", filePath),
  backendPort: 8934,
});
```

- [ ] **Step 3: Create `frontend/history-view.js`**

```javascript
const BACKEND_PORT = window.api?.backendPort ?? 8934;
const API_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

const searchInput = document.getElementById("history-search");
const statusFilter = document.getElementById("history-status-filter");
const historyList = document.getElementById("history-list");

function renderHistoryRow(entry) {
  const row = document.createElement("li");
  row.className = "queue-row";
  row.innerHTML = `
    <div class="queue-row__top">
      <span class="queue-row__title"></span>
      <span class="queue-row__status"></span>
    </div>
    <div class="queue-row__size"></div>
    <button class="btn btn--ghost reveal-btn" type="button">Reveal</button>
  `;
  row.querySelector(".queue-row__title").textContent = entry.title || entry.url;
  const statusEl = row.querySelector(".queue-row__status");
  statusEl.textContent = entry.status === "done" ? "Done" : "Failed";
  statusEl.classList.add(
    entry.status === "done" ? "queue-row__status--done" : "queue-row__status--error"
  );
  row.querySelector(".queue-row__size").textContent =
    `${entry.total_size || "--"} · ${entry.finished_at}`;
  const revealBtn = row.querySelector(".reveal-btn");
  revealBtn.disabled = !entry.output_path;
  revealBtn.addEventListener("click", () => {
    if (entry.output_path) window.api.revealFile(entry.output_path);
  });
  return row;
}

async function loadHistory() {
  const params = new URLSearchParams();
  if (searchInput.value) params.set("q", searchInput.value);
  if (statusFilter.value) params.set("status", statusFilter.value);
  const response = await fetch(`${API_BASE}/history?${params.toString()}`);
  const body = await response.json();
  historyList.innerHTML = "";
  body.entries.forEach((entry) => historyList.appendChild(renderHistoryRow(entry)));
}

let debounceTimer;
searchInput.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(loadHistory, 250);
});
statusFilter.addEventListener("change", loadHistory);

loadHistory();
```

Note this uses only `textContent`/`classList` for entry-derived values (title, status, size, finished_at) — never `innerHTML` interpolation of untrusted data — matching this codebase's existing XSS-safety convention in `renderer.js`.

- [ ] **Step 4: Syntax-check**

Run: `node --check frontend/history-view.js`
Expected: no output (success).

- [ ] **Step 5: Manual verification**

`npm start`, click the History tab (should be empty initially), download a real video from the Queue tab, switch to History and confirm the completed entry appears with a working Reveal button that opens the file's containing folder.

- [ ] **Step 6: Commit**

```bash
git add frontend/history-view.js main.js preload.js
git commit -m "feat: add History view with reveal-in-folder support"
```

---

### Task 14: Frontend — batch notifications, update_batch handling, incremental summary counters, subfolder field, duplicate badge

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/styles.css`
- Modify: `frontend/renderer.js`

**Interfaces:**
- Consumes: `update_batch`/`batch_complete` WS message types from Tasks 5/9/11.

- [ ] **Step 1: Add the course-folder field to `frontend/index.html`**

Add this new field row right after the existing referer field's `<div class="field-row">...</div>` block, before the Start button:

```html
<div class="field-row">
  <div class="field-group field-group--grow">
    <label class="field-label" for="course-folder">Course/batch folder name (optional)</label>
    <input
      id="course-folder"
      class="text-input"
      type="text"
      placeholder="e.g. Advanced Marketing Course"
    />
  </div>
</div>
```

Add a duplicate-badge span into the queue row template inside `renderer.js`'s `renderRow` (see Step 3 below) — the corresponding CSS class is added in Step 2.

- [ ] **Step 2: Add styles**

Append to `frontend/styles.css`:

```css
.queue-row__duplicate-badge {
  font-size: 10px;
  color: var(--text-dim);
  background: rgba(255, 176, 32, 0.15);
  border: 1px solid rgba(255, 176, 32, 0.3);
  border-radius: 4px;
  padding: 2px 6px;
  text-transform: uppercase;
  margin-left: 6px;
}
```

- [ ] **Step 3: Update `frontend/renderer.js`**

Add a reference to the new field near the other `const ... = document.getElementById(...)` lines:

```javascript
const courseFolderInput = document.getElementById("course-folder");
```

Add a summary-counts tracker near `const rows = new Map();`:

```javascript
const summaryCounts = { done: 0, error: 0 };
```

In `renderRow`'s row-creation template, add the duplicate badge span next to the title:

```html
<div class="queue-row__top">
  <span class="queue-row__title"></span>
  <span class="queue-row__duplicate-badge" hidden>Already downloaded</span>
  <span class="queue-row__status"></span>
</div>
```

In `renderRow`, right after `const row = state.el;` and before the existing `maxPercent` logic, add the incremental summary-count bookkeeping:

```javascript
if (state.lastStatus === "done") summaryCounts.done -= 1;
if (state.lastStatus === "error") summaryCounts.error -= 1;
if (entry.status === "done") summaryCounts.done += 1;
if (entry.status === "error") summaryCounts.error += 1;
state.lastStatus = entry.status;
```

Update the row-creation `state = { el, maxPercent: 0 };` line to also initialize `lastStatus`:

```javascript
state = { el, maxPercent: 0, lastStatus: null };
```

After the existing title-setting line (`row.querySelector(".queue-row__title").textContent = entry.title || entry.url;`), add:

```javascript
row.querySelector(".queue-row__duplicate-badge").hidden = !entry.previously_downloaded;
```

Replace the whole `updateSummary` function body with the incremental version:

```javascript
function updateSummary() {
  const total = rows.size;
  queueSummary.textContent = total
    ? `${summaryCounts.done}/${total} downloaded${summaryCounts.error ? `, ${summaryCounts.error} failed` : ""}`
    : "";
}
```

In the `startBtn` click handler's POST body, add the `subfolder` field:

```javascript
body: JSON.stringify({
  urls_text: urlsInput.value,
  output_folder: outputFolderInput.value,
  referer: refererInput.value || null,
  subfolder: courseFolderInput.value || null,
}),
```

Replace the WebSocket handler at the bottom of the file with:

```javascript
connectQueueSocket((event) => {
  if (event.type === "sync") {
    event.entries.forEach(renderRow);
  } else if (event.type === "update") {
    renderRow(event.entry);
  } else if (event.type === "update_batch") {
    event.entries.forEach(renderRow);
  } else if (event.type === "batch_complete") {
    if (window.Notification && Notification.permission === "granted") {
      new Notification("Batch complete", {
        body: `${event.summary.done} done, ${event.summary.error} failed`,
      });
    } else if (window.Notification && Notification.permission !== "denied") {
      Notification.requestPermission();
    }
  }
});
```

- [ ] **Step 4: Syntax-check**

Run: `node --check frontend/renderer.js`
Expected: no output (success).

- [ ] **Step 5: Manual verification**

`npm start`. Paste a small batch (2-3 links) with a course-folder name filled in, confirm files land in the named subfolder. Paste a URL you already downloaded earlier in this session and confirm the "Already downloaded" badge appears on that row. Let a full batch complete and confirm an OS notification appears (you may need to grant notification permission the first time — check your OS notification settings if it doesn't appear). Confirm the summary counter (`N/M downloaded`) still updates correctly as before.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/styles.css frontend/renderer.js
git commit -m "feat: add batch notifications, subfolder field, duplicate badge, and incremental summary counters"
```
