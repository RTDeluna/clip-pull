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
        created_at: Optional[str] = None,
    ) -> None:
        """Appends one AI-call usage row. Best-effort telemetry only -- the
        caller wraps this so a failure here never interrupts the real
        transcription/summarization job. created_at defaults in SQL unless
        explicitly supplied -- real callers never pass it; it exists so
        tests can seed deterministic timestamps for range/day-bucket
        queries instead of racing the clock."""
        with self._lock:
            if created_at is None:
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
            else:
                self._conn.execute(
                    """
                    INSERT INTO ai_usage
                      (provider, model, operation, history_id,
                       input_tokens, output_tokens, total_tokens, audio_seconds,
                       created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        provider, model, operation, history_id,
                        input_tokens, output_tokens, total_tokens, audio_seconds,
                        created_at,
                    ),
                )
            self._conn.commit()

    @staticmethod
    def _range_where(since: Optional[str], until: Optional[str]) -> tuple:
        """Builds an optional half-open [since, until) WHERE clause on
        created_at, shared by the simple (non-joined) range-scoped query
        methods below. Returns ("", ()) when neither bound is given, so the
        no-arg call sites (summary(), the free /usage endpoint) produce the
        exact same unfiltered SQL as before this method existed."""
        clauses = []
        params: list = []
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("created_at < ?")
            params.append(until)
        if not clauses:
            return "", ()
        return "WHERE " + " AND ".join(clauses), tuple(params)

    def summary(self, *, since: Optional[str] = None, until: Optional[str] = None) -> dict:
        """Aggregates recorded usage grouped by provider. SQL SUM ignores
        NULLs, so a provider that only ever billed by duration (Whisper) sums
        to 0 tokens rather than NULL, and vice versa. Providers with no rows
        are simply absent from the result -- the frontend never has to filter
        zero-usage rows itself. With no since/until, this is the all-time
        lifetime total -- unchanged from before, since the free /usage
        endpoint and its existing tests depend on that exact no-arg shape."""
        where_sql, params = self._range_where(since, until)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT provider,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(audio_seconds), 0.0) AS audio_seconds,
                       COUNT(*) AS calls
                FROM ai_usage
                {where_sql}
                GROUP BY provider
                """,
                params,
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

    def daily_breakdown(self, *, since: Optional[str] = None, until: Optional[str] = None) -> list:
        """Usage grouped by (day, provider, model) for the Insights trend
        chart. Kept broken out by provider/model rather than folded to one
        per-day total, so the route layer can fold cost correctly per day
        using the same disjoint token-vs-duration logic
        _estimated_cost_for_provider already relies on."""
        where_sql, params = self._range_where(since, until)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT date(created_at) AS date, provider, model,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(audio_seconds), 0.0) AS audio_seconds,
                       COUNT(*) AS calls
                FROM ai_usage
                {where_sql}
                GROUP BY date(created_at), provider, model
                ORDER BY date ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def hourly_breakdown(self, *, since: Optional[str] = None, until: Optional[str] = None) -> list:
        """Usage grouped by (hour, provider, model) -- same shape as
        daily_breakdown, one level finer. A brand-new user's whole usage
        history often falls on a single calendar day, which would otherwise
        always collapse to exactly one daily-breakdown point (no trend line
        possible) no matter how many calls they've made that day. The route
        layer falls back to this when daily_breakdown collapses to under 2
        points, so a real trend can still show up within a single day."""
        where_sql, params = self._range_where(since, until)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT strftime('%Y-%m-%d %H:00:00', created_at) AS hour, provider, model,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(audio_seconds), 0.0) AS audio_seconds,
                       COUNT(*) AS calls
                FROM ai_usage
                {where_sql}
                GROUP BY hour, provider, model
                ORDER BY hour ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def operation_breakdown(self, *, since: Optional[str] = None, until: Optional[str] = None) -> list:
        """Usage grouped by (operation, provider, model). Serves two
        purposes: the Insights "per-operation" UI breakdown, and the input
        to the provider-cost-recommendation calculation (which needs to know
        how much volume each operation ran on each provider to simulate what
        that same volume would have cost elsewhere)."""
        where_sql, params = self._range_where(since, until)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT operation, provider, model,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(audio_seconds), 0.0) AS audio_seconds,
                       COUNT(*) AS calls
                FROM ai_usage
                {where_sql}
                GROUP BY operation, provider, model
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def per_video_breakdown(
        self, *, since: Optional[str] = None, until: Optional[str] = None, limit: int = 50
    ) -> list:
        """Usage grouped by (history_id, provider, model), joined to the
        video's title/url for display, ordered by usage volume descending,
        top `limit`. Course-scoped calls (history_id IS NULL, from
        course_chat/course_digest) are excluded -- they don't belong to one
        video. Uses a LEFT JOIN (not INNER), not because a null title is
        expected often, but because history_id has no ON DELETE constraint
        -- a History row can be deleted while its ai_usage rows remain, and
        those should still surface (with title/url as None, so the caller
        can show a "Removed from history" fallback) rather than silently
        disappearing from the report."""
        clauses = ["u.history_id IS NOT NULL"]
        params: list = []
        if since is not None:
            clauses.append("u.created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("u.created_at < ?")
            params.append(until)
        where_sql = "WHERE " + " AND ".join(clauses)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT u.history_id AS history_id, h.title AS title, h.url AS url,
                       u.provider AS provider, u.model AS model,
                       COALESCE(SUM(u.input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(u.output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(u.total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(u.audio_seconds), 0.0) AS audio_seconds,
                       COUNT(*) AS calls
                FROM ai_usage u
                LEFT JOIN history h ON h.id = u.history_id
                {where_sql}
                GROUP BY u.history_id, u.provider, u.model
                ORDER BY (COALESCE(SUM(u.total_tokens), 0) + COALESCE(SUM(u.audio_seconds), 0)) DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def distinct_video_count(self, *, since: Optional[str] = None, until: Optional[str] = None) -> int:
        """Count of distinct videos with at least one AI call in range --
        feeds the free "videos processed" KPI. Course-scoped calls
        (history_id IS NULL) don't count as a video."""
        clauses = ["history_id IS NOT NULL"]
        params: list = []
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("created_at < ?")
            params.append(until)
        where_sql = "WHERE " + " AND ".join(clauses)
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(DISTINCT history_id) AS count FROM ai_usage {where_sql}",
                params,
            ).fetchone()
        return row["count"]
