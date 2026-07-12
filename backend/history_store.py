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
        update_id: Optional[int] = None,
    ) -> dict:
        """Writes a finished download's outcome. When update_id names an
        existing row (a retry of that same History entry, either re-queued
        from the History tab or re-tried while still in the live Queue),
        that row is updated in place instead of inserting a new one -- so a
        failed-then-retried-successfully download ends up as one History
        entry reflecting the latest outcome, not two. Falls back to a plain
        insert if update_id's row is gone (e.g. cleared mid-retry)."""
        with self._lock:
            if update_id is not None:
                cursor = self._conn.execute(
                    """
                    UPDATE history
                    SET entry_id = ?, batch_id = ?, url = ?, title = ?, output_path = ?,
                        total_size = ?, status = ?, error_reason = ?, retry_count = ?,
                        finished_at = datetime('now')
                    WHERE id = ?
                    """,
                    (
                        entry_id, batch_id, url, title, output_path, total_size,
                        status, error_reason, retry_count, update_id,
                    ),
                )
                self._conn.commit()
                if cursor.rowcount > 0:
                    row = self._conn.execute(
                        "SELECT * FROM history WHERE id = ?", (update_id,)
                    ).fetchone()
                    return dict(row)

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

    def get(self, entry_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM history WHERE id = ?", (entry_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_transcript(
        self,
        entry_id: int,
        *,
        status: str,
        transcript: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Optional[dict]:
        """Updates an existing row's transcription state in place -- always
        targets a row a normal download completion already created, so
        unlike record() there's no insert-fallback. Returns None if the row
        is gone (e.g. deleted from History mid-job). Transcription and
        summarization are independent jobs (see update_summary) -- this
        never touches the summary columns."""
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE history
                SET transcript_status = ?, transcript = ?,
                    transcript_error = ?, transcribed_at = datetime('now')
                WHERE id = ?
                """,
                (status, transcript, error, entry_id),
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                return None
            row = self._conn.execute(
                "SELECT * FROM history WHERE id = ?", (entry_id,)
            ).fetchone()
            return dict(row)

    def update_summary(
        self,
        entry_id: int,
        *,
        status: str,
        summary: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Optional[dict]:
        """Updates an existing row's summarization state in place. Mirrors
        update_transcript's shape exactly, but touches only the summary_*
        columns -- summarizing is a separate, optional, user-triggered job
        that runs after a transcript already exists, not a sub-step of
        transcription."""
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE history
                SET summary_status = ?, summary = ?,
                    summary_error = ?, summarized_at = datetime('now')
                WHERE id = ?
                """,
                (status, summary, error, entry_id),
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                return None
            row = self._conn.execute(
                "SELECT * FROM history WHERE id = ?", (entry_id,)
            ).fetchone()
            return dict(row)

    def reset_stuck_transcriptions(self) -> int:
        """A transcription/summarization job only lives in memory while
        running, so an app quit mid-job leaves its row stuck on "running"
        forever with nothing left to ever resume it. Called once at startup
        to sweep both independently back to a retryable error state.
        Returns how many rows were touched (either column)."""
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE history
                SET transcript_status = CASE WHEN transcript_status = 'running'
                        THEN 'error' ELSE transcript_status END,
                    transcript_error = CASE WHEN transcript_status = 'running'
                        THEN 'Transcription was interrupted — try again.' ELSE transcript_error END,
                    summary_status = CASE WHEN summary_status = 'running'
                        THEN 'error' ELSE summary_status END,
                    summary_error = CASE WHEN summary_status = 'running'
                        THEN 'Summarization was interrupted — try again.' ELSE summary_error END
                WHERE transcript_status = 'running' OR summary_status = 'running'
                """
            )
            self._conn.commit()
            return cursor.rowcount

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
