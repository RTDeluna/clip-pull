import pytest

import ai_clients
from usage_pricing import PRICING, cheapest_provider_for, estimate_cost_usd


def test_estimate_cost_for_token_based_model():
    # gemini-3.5-flash: 0.075/M input, 0.30/M output.
    cost = estimate_cost_usd(
        "gemini", "gemini-3.5-flash", input_tokens=2_000_000, output_tokens=1_000_000
    )
    assert cost == pytest.approx(2 * 0.075 + 1 * 0.30)


def test_estimate_cost_for_duration_based_model():
    # whisper-1: 0.006/minute. 120s = 2 minutes.
    cost = estimate_cost_usd("openai", "whisper-1", audio_seconds=120)
    assert cost == pytest.approx(2 * 0.006)


def test_estimate_cost_unknown_provider_model_returns_none():
    assert estimate_cost_usd("nobody", "no-such-model", input_tokens=1000) is None


def test_estimate_cost_known_provider_unknown_model_returns_none():
    # A known provider but a model not in the table must not be guessed.
    assert estimate_cost_usd("openai", "some-future-model", input_tokens=1000) is None


def test_estimate_cost_missing_usage_fields_contribute_zero_not_none():
    # Entry exists, but only output tokens were supplied -- input contributes
    # 0 via the `or 0` pattern rather than making the whole estimate None.
    cost = estimate_cost_usd("gemini", "gemini-3.5-flash", output_tokens=1_000_000)
    assert cost == pytest.approx(0.30)


def test_estimate_cost_with_no_usage_at_all_is_zero_when_entry_exists():
    # An entry exists, so we return a real number (0.0), not None.
    assert estimate_cost_usd("anthropic", "claude-sonnet-4-5") == 0.0


# Drift guard: PRICING is keyed by the exact (provider, model) strings
# ai_clients.py actually records in each client's last_usage. If a model
# constant ever moves without a matching PRICING update, cost silently
# starts reading "unknown" for every new call on that provider -- which
# quietly breaks the "full cost transparency" promise the Insights dashboard
# is built around (see the CLIP.PULL Insights plan's "Cost transparency
# integrity" note). Every (provider, model) pair actually used by a client
# below must have a PRICING entry.
def test_pricing_covers_every_model_ai_clients_actually_records():
    used_pairs = {
        ("gemini", ai_clients.GEMINI_MODEL),
        ("anthropic", ai_clients.ANTHROPIC_MODEL),
        ("openai", ai_clients.OPENAI_TRANSCRIPTION_MODEL),
        ("openai", ai_clients.OPENAI_SUMMARY_MODEL),
        ("groq", ai_clients.GROQ_TRANSCRIPTION_MODEL),
        ("openrouter", ai_clients.OPENROUTER_SUMMARY_MODEL),
    }
    missing = used_pairs - set(PRICING)
    assert not missing, f"PRICING is missing entries recorded by ai_clients.py: {missing}"


# -- cheapest_provider_for ---------------------------------------------------


def test_cheapest_provider_for_finds_cheaper_duration_priced_alternative():
    # 10 minutes on openai's whisper-1 (0.006/min) = $0.06.
    current_cost = 10 * 0.006
    result = cheapest_provider_for(
        current_cost,
        {"audio_seconds": 600.0},
        [("openai", "whisper-1"), ("groq", "whisper-large-v3-turbo")],
        current_provider="openai",
    )
    assert result is not None
    assert result["cheaper_provider"] == "groq"
    assert result["current_provider"] == "openai"
    assert result["savings_usd"] > 0.01


def test_cheapest_provider_for_excludes_token_priced_candidate_from_pure_audio_workload():
    # gemini-3.5-flash is token-priced (no per_minute entry). A pure-audio
    # workload (no recorded tokens) must not be compared against it -- doing
    # so would compute 0 tokens * rate = "free," which is meaningless, not
    # a real cheaper option.
    result = cheapest_provider_for(
        0.06,
        {"audio_seconds": 600.0},
        [("gemini", "gemini-3.5-flash"), ("groq", "whisper-large-v3-turbo")],
    )
    assert result is not None
    assert result["cheaper_provider"] == "groq"


def test_cheapest_provider_for_returns_none_when_no_candidate_is_cheaper():
    # groq is already the cheapest transcription option -- comparing it
    # against itself/openai should surface nothing to switch to.
    current_cost = 10 * 0.000667
    result = cheapest_provider_for(
        current_cost,
        {"audio_seconds": 600.0},
        [("openai", "whisper-1"), ("groq", "whisper-large-v3-turbo")],
    )
    assert result is None


def test_cheapest_provider_for_returns_none_below_minimum_savings_threshold():
    # A trivially small workload -- any savings round to well under $0.01.
    result = cheapest_provider_for(
        0.0000006,
        {"audio_seconds": 0.1},
        [("openai", "whisper-1"), ("groq", "whisper-large-v3-turbo")],
    )
    assert result is None


def test_cheapest_provider_for_returns_none_when_no_candidate_has_pricing():
    result = cheapest_provider_for(1.0, {"input_tokens": 1000}, [("nobody", "no-such-model")])
    assert result is None
