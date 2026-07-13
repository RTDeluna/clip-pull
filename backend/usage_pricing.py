from typing import Optional

# Rough, approximate USD pricing for display purposes only -- these are NOT
# guaranteed current and MUST be verified against each provider's live
# pricing page before being trusted for real budgeting. Model IDs move faster
# than this table does (same caveat as GEMINI_MODEL/ANTHROPIC_MODEL etc. in
# ai_clients.py). Keyed by (provider, model) so the model strings recorded in
# ai_usage line up 1:1 with these keys. Token-based entries are USD per
# 1,000,000 tokens; duration-based entries (Whisper-style transcription,
# billed by audio length, not tokens) are USD per minute.
PRICING = {
    ("gemini", "gemini-3.5-flash"): {"input_per_million": 0.075, "output_per_million": 0.30},
    ("anthropic", "claude-sonnet-4-5"): {"input_per_million": 3.00, "output_per_million": 15.00},
    ("openai", "gpt-5-mini"): {"input_per_million": 0.25, "output_per_million": 2.00},
    ("openai", "whisper-1"): {"per_minute": 0.006},
    ("groq", "whisper-large-v3-turbo"): {"per_minute": 0.000667},  # ~$0.04/hour, verify
    ("openrouter", "openai/gpt-4o-mini"): {"input_per_million": 0.15, "output_per_million": 0.60},
}


def estimate_cost_usd(
    provider: str,
    model: Optional[str],
    *,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    audio_seconds: Optional[float] = None,
) -> Optional[float]:
    """Best-effort USD cost estimate for one (provider, model) pair's usage.
    Returns None only when there's no pricing entry for the pair (unknown
    model -- don't guess). When an entry exists, missing individual usage
    fields simply contribute 0 via the `or 0` pattern rather than making the
    whole estimate None."""
    entry = PRICING.get((provider, model))
    if entry is None:
        return None
    cost = 0.0
    if "per_minute" in entry:
        cost += (audio_seconds or 0) / 60 * entry["per_minute"]
    if "input_per_million" in entry:
        cost += (input_tokens or 0) / 1_000_000 * entry["input_per_million"]
    if "output_per_million" in entry:
        cost += (output_tokens or 0) / 1_000_000 * entry["output_per_million"]
    return cost
