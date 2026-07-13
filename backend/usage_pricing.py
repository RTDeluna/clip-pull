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


# Below this line: a real dollar figure has to clear this bar before the
# Insights dashboard recommends switching providers over it -- otherwise a
# fraction-of-a-cent "savings" would nag the user for no real benefit.
MIN_RECOMMENDATION_SAVINGS_USD = 0.01


def cheapest_provider_for(
    current_cost: float,
    total_volume: dict,
    candidates: list,
    *,
    current_provider: Optional[str] = None,
) -> Optional[dict]:
    """Given the actual cost billed for some operation's total recorded
    usage (`current_cost`, already folded across whichever provider(s) it
    actually ran on), checks whether any `candidates` (provider, model)
    pair would have been cheaper for that SAME total volume
    (`total_volume`: {"input_tokens", "output_tokens", "audio_seconds"}) --
    simulating "what if all of this had run through provider X instead."
    `current_provider` is a plain pass-through label for display (e.g. the
    dominant provider by volume when more than one contributed to
    `current_cost`) -- this function doesn't need to know how it was
    derived, it just relays it in the result.

    Deliberately has no dependency on ai_clients -- the caller is
    responsible for only passing candidates actually capable of the
    operation in question (e.g. via ai_clients.TRANSCRIPTION_CLIENTS /
    SUMMARIZATION_CLIENTS), keeping this module's only concern pricing math.

    Returns None if no candidate has a pricing entry, no candidate is
    billed in a comparable unit to the recorded volume (see below), or the
    cheapest candidate doesn't clear MIN_RECOMMENDATION_SAVINGS_USD."""
    # Some providers doing the "same" operation are billed in genuinely
    # different units -- e.g. for transcription, Whisper-style providers
    # (openai, groq) bill per minute of audio, while Gemini's multimodal
    # transcription bills per token. A pure-audio workload (audio_seconds
    # set, no tokens recorded) can't be meaningfully priced against a
    # token-only candidate: it would compute using 0 tokens and misreport
    # that provider as nearly free, since this app has no way to know how
    # many tokens that audio would have consumed on a different provider.
    # Restrict comparison to candidates billed in the same unit as the
    # volume actually available; if both units are present (or neither),
    # no restriction applies.
    has_audio = (total_volume.get("audio_seconds") or 0) > 0
    has_tokens = (total_volume.get("input_tokens") or 0) > 0 or (total_volume.get("output_tokens") or 0) > 0

    best = None
    for provider, model in candidates:
        entry = PRICING.get((provider, model))
        if entry is None:
            continue
        if has_audio and not has_tokens and "per_minute" not in entry:
            continue
        if has_tokens and not has_audio and "per_minute" in entry and "input_per_million" not in entry:
            continue
        cost = estimate_cost_usd(
            provider,
            model,
            input_tokens=total_volume.get("input_tokens"),
            output_tokens=total_volume.get("output_tokens"),
            audio_seconds=total_volume.get("audio_seconds"),
        )
        if cost is None:
            continue
        if best is None or cost < best[2]:
            best = (provider, model, cost)

    if best is None:
        return None
    cheaper_provider, cheaper_model, cheaper_cost = best
    savings_usd = current_cost - cheaper_cost
    if savings_usd <= MIN_RECOMMENDATION_SAVINGS_USD:
        return None
    return {
        "current_provider": current_provider,
        "current_cost_usd": round(current_cost, 6),
        "cheaper_provider": cheaper_provider,
        "cheaper_model": cheaper_model,
        "cheaper_cost_usd": round(cheaper_cost, 6),
        "savings_usd": round(savings_usd, 6),
        "savings_pct": round((savings_usd / current_cost) * 100, 1) if current_cost > 0 else None,
    }
