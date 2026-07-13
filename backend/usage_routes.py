import sqlite3

from fastapi import APIRouter, HTTPException

from usage_pricing import PRICING, estimate_cost_usd
from usage_store import UsageStore

DB_BUSY_MESSAGE = "The app's local database is busy — try again in a moment."


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


def build_usage_router(usage_store: UsageStore) -> APIRouter:
    router = APIRouter()

    @router.get("/usage")
    def get_usage() -> dict:
        # Ungated / free for everyone -- cost transparency benefits every user,
        # it's not a Pro feature.
        try:
            summary = usage_store.summary()
        except sqlite3.OperationalError:
            raise HTTPException(status_code=503, detail=DB_BUSY_MESSAGE)
        for provider, stats in summary["providers"].items():
            stats["estimated_cost_usd"] = _estimated_cost_for_provider(provider, stats)
        return summary

    return router
