import pytest

from usage_pricing import estimate_cost_usd


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
