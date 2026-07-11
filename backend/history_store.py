import threading
from pathlib import Path
from typing import Optional, Union

from db import get_connection


class HistoryStore:
    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        self._conn = get_connection(db_path)
        # This one connection is shared across FastAPI's sync-route
        # threadpool (get_history, delete_history_entry, clear_history) and
        # the event-loop thread (record() called from download completion
        # callbacks) -- SQLite's own thread-safety for a single connection
        # accessed concurrently from multiple threads isn't guaranteed by
        # the sqlite3 module's docs, so serialize explicitly rather than
        # relying on it.
        self._lock = threading.Lock()

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
        with self._lock:
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
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM history {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, entry_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM history WHERE id = ?", (entry_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def clear(self, query: Optional[str] = None, status: Optional[str] = None) -> int:
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
        with self._lock:
            cursor = self._conn.execute(f"DELETE FROM history {where}", params)
            self._conn.commit()
            return cursor.rowcount

    def was_previously_downloaded(self, urls: list[str]) -> set[str]:
        if not urls:
            return set()
        placeholders = ",".join("?" for _ in urls)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT DISTINCT url FROM history WHERE status = 'done' AND url IN ({placeholders})",
                urls,
            ).fetchall()
        return {row["url"] for row in rows}
