import csv
import io
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Response

import ai_clients
from license_store import LicenseStore
from usage_pricing import PRICING, cheapest_provider_for, estimate_cost_usd
from usage_store import UsageStore

DB_BUSY_MESSAGE = "The app's local database is busy — try again in a moment."
USAGE_PRO_MESSAGE = (
    "AI usage insights are a CLIP.PULL Pro feature. "
    "Upgrade to unlock trends, per-video costs, and export."
)

_RANGE_PRESET_DAYS = {"7d": 7, "30d": 30, "90d": 90}

# Which providers are even comparable for a given operation, and which
# model each would use -- mirrors ai_clients.TRANSCRIPTION_CLIENTS /
# SUMMARIZATION_CLIENTS exactly (same provider sets), but maps to the
# model constant instead of the client class, since the cost simulation
# needs (provider, model) pairs to look up in PRICING.
_TRANSCRIPTION_PROVIDER_MODELS = {
    "gemini": ai_clients.GEMINI_MODEL,
    "openai": ai_clients.OPENAI_TRANSCRIPTION_MODEL,
    "groq": ai_clients.GROQ_TRANSCRIPTION_MODEL,
}
_SUMMARIZATION_PROVIDER_MODELS = {
    "anthropic": ai_clients.ANTHROPIC_MODEL,
    "openai": ai_clients.OPENAI_SUMMARY_MODEL,
    "gemini": ai_clients.GEMINI_MODEL,
    "openrouter": ai_clients.OPENROUTER_SUMMARY_MODEL,
}
_SUMMARIZATION_OPERATIONS = {"summarize", "chat", "course_chat", "course_digest"}

_OPERATION_LABELS = {
    "transcribe_chunk": "transcription",
    "summarize": "summaries",
    "chat": "lesson chat",
    "course_chat": "course chat",
    "course_digest": "study guides",
}


def _friendly_operation_label(operation: str) -> str:
    return _OPERATION_LABELS.get(operation, operation)


def _candidates_for_operation(operation: str) -> list:
    """Which (provider, model) pairs are actually capable of this
    operation -- so the cost-savings recommendation never suggests, say,
    Anthropic for transcription (Anthropic has no transcription client)."""
    if operation == "transcribe_chunk":
        return list(_TRANSCRIPTION_PROVIDER_MODELS.items())
    if operation in _SUMMARIZATION_OPERATIONS:
        return list(_SUMMARIZATION_PROVIDER_MODELS.items())
    return []


def _estimated_cost_for_provider(provider: str, stats: dict):
    """The usage summary groups by provider, but pricing is keyed by
    (provider, model) -- and one provider (openai) bills both by tokens
    (summaries) and by audio duration (Whisper transcription). Sum the
    estimate across every model registered for the provider: token-priced
    entries pick up the token totals and duration-priced entries pick up
    audio_seconds, so their contributions stay disjoint rather than
    double-counting. Returns None when the provider has no pricing entry at
    all (unknown -- don't guess)."""
    models = [model for (prov, model) in PRICING if prov == provider]
    if not models:
        return None
    total = 0.0
    for model in models:
        cost = estimate_cost_usd(
            provider,
            model,
            input_tokens=stats.get("input_tokens"),
            output_tokens=stats.get("output_tokens"),
            audio_seconds=stats.get("audio_seconds"),
        )
        if cost is not None:
            total += cost
    return round(total, 6)


def _fold_cost(rows: list) -> Optional[float]:
    """Sums estimate_cost_usd across a list of {provider, model,
    input_tokens, output_tokens, audio_seconds} rows -- the shape returned
    by UsageStore's daily_breakdown/operation_breakdown/per_video_breakdown.
    Returns None only when EVERY row's (provider, model) pair is unpriced,
    matching _estimated_cost_for_provider's "don't guess, but don't let one
    unknown model blank out an otherwise-known total" rule."""
    total = 0.0
    any_known = False
    for row in rows:
        cost = estimate_cost_usd(
            row["provider"],
            row.get("model"),
            input_tokens=row.get("input_tokens"),
            output_tokens=row.get("output_tokens"),
            audio_seconds=row.get("audio_seconds"),
        )
        if cost is not None:
            total += cost
            any_known = True
    return round(total, 6) if any_known else None


def _has_unresolvable_cost(rows: list) -> bool:
    """True if any row has real usage but its (provider, model) pair isn't
    in PRICING -- i.e. exactly the pricing/ai_clients drift condition that
    silently breaks cost transparency. Drives the dashboard's warning
    banner instead of letting unresolvable costs vanish silently."""
    return any(
        estimate_cost_usd(
            row["provider"],
            row.get("model"),
            input_tokens=row.get("input_tokens"),
            output_tokens=row.get("output_tokens"),
            audio_seconds=row.get("audio_seconds"),
        )
        is None
        for row in rows
    )


def _fold_grouped(rows: list, key_fields: tuple, passthrough_fields: tuple = ()) -> list:
    """Groups usage rows (any of daily_breakdown/operation_breakdown/
    per_video_breakdown's shapes, which all come back at a finer
    (x, provider, model) granularity) up to one entry per `key_fields`,
    summing the numeric usage columns and folding cost per group. Shared by
    the daily/operations/videos sections of the dashboard response, which
    all need this same "collapse the provider/model split, keep a folded
    cost" shape."""
    groups: dict = {}
    order: list = []
    for row in rows:
        key = tuple(row[f] for f in key_fields)
        if key not in groups:
            entry = {f: row[f] for f in key_fields}
            for f in passthrough_fields:
                entry[f] = row.get(f)
            entry.update(
                input_tokens=0, output_tokens=0, total_tokens=0, audio_seconds=0.0, calls=0
            )
            groups[key] = (entry, [])
            order.append(key)
        entry, group_rows = groups[key]
        entry["input_tokens"] += row.get("input_tokens") or 0
        entry["output_tokens"] += row.get("output_tokens") or 0
        entry["total_tokens"] += row.get("total_tokens") or 0
        entry["audio_seconds"] += row.get("audio_seconds") or 0
        entry["calls"] += row.get("calls") or 0
        group_rows.append(row)

    result = []
    for key in order:
        entry, group_rows = groups[key]
        entry["estimated_cost_usd"] = _fold_cost(group_rows)
        result.append(entry)
    return result


def _compute_provider_recommendation(operation_rows: list) -> Optional[dict]:
    """operation_rows is UsageStore.operation_breakdown()'s result -- one
    row per (operation, provider, model). Groups by operation, and for each
    checks whether a comparable provider would have been cheaper for that
    operation's total recorded volume (simulating "what if all of this had
    run through provider X instead"). Keeps whichever operation has the
    single largest dollar savings across the whole range. Returns None if
    no operation has both a resolvable current cost and a cheaper
    alternative clearing the minimum-savings threshold."""
    by_operation: dict = {}
    for row in operation_rows:
        by_operation.setdefault(row["operation"], []).append(row)

    best = None
    for operation, rows in by_operation.items():
        candidates = _candidates_for_operation(operation)
        if not candidates:
            continue
        current_cost = _fold_cost(rows)
        if current_cost is None:
            continue
        total_volume = {
            "input_tokens": sum(r.get("input_tokens") or 0 for r in rows),
            "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
            "audio_seconds": sum(r.get("audio_seconds") or 0 for r in rows),
        }
        # Usually exactly one provider handled this operation across the
        # whole range (Settings only lets one be configured at a time), but
        # a user can switch mid-range -- label with whichever contributed
        # the most calls, rather than a plain sum of provider names.
        calls_by_provider: dict = {}
        for r in rows:
            calls_by_provider[r["provider"]] = calls_by_provider.get(r["provider"], 0) + (r.get("calls") or 0)
        dominant_provider = max(calls_by_provider, key=calls_by_provider.get) if calls_by_provider else None

        result = cheapest_provider_for(
            current_cost, total_volume, candidates, current_provider=dominant_provider
        )
        if result is None:
            continue
        candidate = {"operation": operation, **result}
        if best is None or candidate["savings_usd"] > best["savings_usd"]:
            best = candidate
    return best


def _sparkline_shape(trend_rows: list, key_field: str = "date") -> list:
    """A relative (0..1, peak-normalized), value-free version of the usage
    trend -- enough to render a blurred trend-chart preview for non-Pro
    users without revealing their actual usage numbers. Uses raw volume
    (tokens + audio seconds combined) rather than cost, since a shape-only
    preview doesn't need pricing at all. key_field is "date" or "hour"
    depending on which granularity the caller resolved (see
    get_usage_dashboard's trend_rows/trend_key)."""
    by_bucket: dict = {}
    for row in trend_rows:
        volume = (row.get("total_tokens") or 0) + (row.get("audio_seconds") or 0)
        by_bucket[row[key_field]] = by_bucket.get(row[key_field], 0) + volume
    if not by_bucket:
        return []
    values = [by_bucket[bucket] for bucket in sorted(by_bucket)]
    peak = max(values) or 1
    return [round(v / peak, 3) for v in values]


def _build_video_list(usage_store: UsageStore, since: Optional[str], until: Optional[str], limit: int) -> list:
    """Shared by GET /usage/dashboard (top 10, for display) and GET
    /usage/export.csv (a much higher limit, since an export shouldn't
    silently truncate the way the in-app list does): fetches, folds, and
    cost-sorts the per-video breakdown."""
    video_rows = usage_store.per_video_breakdown(since=since, until=until, limit=limit)
    videos = _fold_grouped(video_rows, ("history_id",), passthrough_fields=("title", "url"))
    videos.sort(key=lambda row: (row["estimated_cost_usd"] is None, -(row["estimated_cost_usd"] or 0)))
    return videos


def _resolve_range(range_: str, since: Optional[str], until: Optional[str]) -> tuple:
    """Resolves the range/since/until query params into concrete bounds for
    the store queries. created_at is UTC (SQL datetime('now')), so preset
    ranges are computed in UTC too, rather than local time, to keep day
    boundaries consistent with how rows are actually stamped."""
    if since or until:
        return since, until, "custom"
    if range_ == "all":
        return None, None, "all"
    days = _RANGE_PRESET_DAYS.get(range_)
    if days is None:
        raise HTTPException(
            status_code=400, detail='range must be one of "7d", "30d", "90d", "all".'
        )
    since_str = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    return since_str, None, range_


def build_usage_router(usage_store: UsageStore, license_store: LicenseStore) -> APIRouter:
    router = APIRouter()

    @router.get("/usage")
    def get_usage() -> dict:
        # Ungated / free for everyone -- cost transparency benefits every user,
        # it's not a Pro feature. Deliberately unchanged by the Insights
        # dashboard work below: same query, same shape, same tests.
        try:
            summary = usage_store.summary()
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        for provider, stats in summary["providers"].items():
            stats["estimated_cost_usd"] = _estimated_cost_for_provider(provider, stats)
        return summary

    @router.get("/usage/dashboard")
    def get_usage_dashboard(range: str = "30d", since: Optional[str] = None, until: Optional[str] = None) -> dict:
        resolved_since, resolved_until, preset = _resolve_range(range, since, until)

        try:
            summary = usage_store.summary(since=resolved_since, until=resolved_until)
            daily_rows = usage_store.daily_breakdown(since=resolved_since, until=resolved_until)
            operation_rows = usage_store.operation_breakdown(since=resolved_since, until=resolved_until)
            videos = _build_video_list(usage_store, resolved_since, resolved_until, limit=100)
            videos_processed = usage_store.distinct_video_count(since=resolved_since, until=resolved_until)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)

        provider_recommendation = _compute_provider_recommendation(operation_rows)
        cost_data_incomplete = _has_unresolvable_cost(operation_rows)

        # A brand-new user's usage often all falls on one calendar day, which
        # would otherwise always collapse to a single daily-breakdown point --
        # no trend line possible no matter how many calls they've made today.
        # Fall back to hour-level buckets (still within the same resolved
        # range) so a real trend can still show up within a single day.
        # Resolved once, shared by both the free-tier sparkline preview below
        # and the paid dashboard's trend section further down.
        trend_rows, trend_key = daily_rows, "date"
        if len({row["date"] for row in daily_rows}) < 2:
            try:
                hourly_rows = usage_store.hourly_breakdown(since=resolved_since, until=resolved_until)
            except sqlite3.OperationalError:
                raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
            if len({row["hour"] for row in hourly_rows}) >= 2:
                trend_rows, trend_key = hourly_rows, "hour"

        # Pro-gate AFTER running the (local, cheap) queries above -- unlike
        # export/chat/batch/course, this route deliberately computes a
        # coarse, value-free preview for non-Pro callers (see
        # _sparkline_shape's docstring), so it can't gate before reading
        # data the way those routes do. The preview intentionally carries
        # no real numbers, only a normalized shape and a generic teaser.
        if not license_store.is_pro():
            preview = {
                "trend_sparkline_shape": _sparkline_shape(trend_rows, key_field=trend_key),
                "provider_recommendation_teaser": (
                    f"Switching providers could save you money on "
                    f"{_friendly_operation_label(provider_recommendation['operation'])}."
                    if provider_recommendation
                    else None
                ),
            }
            raise HTTPException(status_code=402, detail={"message": USAGE_PRO_MESSAGE, "preview": preview})

        daily = _fold_grouped(trend_rows, (trend_key,))
        daily.sort(key=lambda row: row[trend_key])

        operations = _fold_grouped(operation_rows, ("operation",))
        operations.sort(key=lambda row: (row["estimated_cost_usd"] is None, -(row["estimated_cost_usd"] or 0)))

        videos = videos[:10]

        estimated_cost_usd = None
        provider_stats = {}
        for provider, stats in summary["providers"].items():
            cost = _estimated_cost_for_provider(provider, stats)
            provider_stats[provider] = {**stats, "estimated_cost_usd": cost}
            if cost is not None:
                estimated_cost_usd = (estimated_cost_usd or 0.0) + cost

        audio_seconds = sum(stats["audio_seconds"] for stats in summary["providers"].values())

        return {
            "range": {"since": resolved_since, "until": resolved_until, "preset": preset},
            "kpis": {
                # Value-first: hours/videos are the headline numbers, cost
                # comes last -- people don't renew to see what they spent,
                # they renew to see what they got.
                "hours_processed": round(audio_seconds / 3600, 2),
                "videos_processed": videos_processed,
                "total_calls": summary["total_calls"],
                "total_tokens": sum(stats["total_tokens"] for stats in summary["providers"].values()),
                "audio_seconds": audio_seconds,
                "estimated_cost_usd": estimated_cost_usd,
            },
            "daily": daily,
            "providers": provider_stats,
            "operations": operations,
            "videos": videos,
            "provider_recommendation": provider_recommendation,
            "cost_data_incomplete": cost_data_incomplete,
        }

    @router.get("/usage/export.csv")
    def get_usage_export_csv(range: str = "30d", since: Optional[str] = None, until: Optional[str] = None):
        # Pure Pro gate, checked before any DB read -- unlike /usage/dashboard,
        # there's no free-tier preview concept for an export; this matches the
        # export/chat/batch/course precedent in transcription_routes.py.
        if not license_store.is_pro():
            raise HTTPException(status_code=402, detail=USAGE_PRO_MESSAGE)

        resolved_since, resolved_until, preset = _resolve_range(range, since, until)
        try:
            # A generous limit -- an export shouldn't silently truncate the
            # way the in-app "top 10" display does.
            videos = _build_video_list(usage_store, resolved_since, resolved_until, limit=1000)
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["title", "url", "total_tokens", "audio_minutes", "calls", "estimated_cost_usd"])
        for video in videos:
            writer.writerow([
                video["title"] or "Removed from history",
                video["url"] or "",
                video["total_tokens"],
                round((video["audio_seconds"] or 0) / 60, 2),
                video["calls"],
                video["estimated_cost_usd"] if video["estimated_cost_usd"] is not None else "",
            ])

        filename = f"clip-pull-usage-{preset}.csv"
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router
