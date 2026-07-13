import threading
from pathlib import Path
from typing import Optional, Union

from db import get_connection


class UsageStore:
    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        self._conn = get_connection(db_path)
        # Same reasoning as HistoryStore._lock: this one connection is read
        # from usage_routes' sync handler (FastAPI threadpool) and written
        # from the transcription orchestrator's per-chunk/per-summary
        # recording (asyncio event-loop thread) -- serialize explicitly
        # rather than relying on undocumented sqlite3 connection thread-safety
        # under concurrent access.
        self._lock = threading.Lock()

    def record(
        self,
        *,
        provider: str,
        operation: str,
        model: Optional[str] = None,
        history_id: Optional[int] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        audio_seconds: Optional[float] = None,
    ) -> None:
        """Appends one AI-call usage row. Best-effort telemetry only -- the
        caller wraps this so a failure here never interrupts the real
        transcription/summarization job. created_at defaults in SQL."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO ai_usage
                  (provider, model, operation, history_id,
                   input_tokens, output_tokens, total_tokens, audio_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider, model, operation, history_id,
                    input_tokens, output_tokens, total_tokens, audio_seconds,
                ),
            )
            self._conn.commit()

    def summary(self) -> dict:
        """Aggregates all recorded usage grouped by provider. SQL SUM ignores
        NULLs, so a provider that only ever billed by duration (Whisper) sums
        to 0 tokens rather than NULL, and vice versa. Providers with no rows
        are simply absent from the result -- the frontend never has to filter
        zero-usage rows itself."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT provider,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(audio_seconds), 0.0) AS audio_seconds,
                       COUNT(*) AS calls
                FROM ai_usage
                GROUP BY provider
                """
            ).fetchall()
        providers = {}
        total_calls = 0
        for row in rows:
            providers[row["provider"]] = {
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "total_tokens": row["total_tokens"],
                "audio_seconds": row["audio_seconds"],
                "calls": row["calls"],
            }
            total_calls += row["calls"]
        return {"providers": providers, "total_calls": total_calls}
