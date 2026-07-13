from ai_clients import AIClientError
from audio_extraction import AudioExtractionError
from transcription_errors import humanize_transcription_error, is_retryable


def test_humanize_transcription_error_passes_through_audio_extraction_messages():
    exc = AudioExtractionError("ffmpeg isn't available, so audio can't be extracted for transcription.")
    assert humanize_transcription_error(exc) == str(exc)


def test_humanize_transcription_error_rewrites_anthropic_401():
    exc = AIClientError("unauthorized", provider="anthropic", status_code=401)
    reason = humanize_transcription_error(exc)
    assert "Anthropic" in reason
    assert "rejected the API key" in reason


def test_humanize_transcription_error_rewrites_gemini_400_api_key_invalid():
    # Gemini reports a bad key as a plain 400 INVALID_ARGUMENT, not 401 --
    # confirmed against the real API during manual verification. Must still
    # be recognized as a key problem, not shown as a generic "rejected (400)".
    exc = AIClientError(
        'Gemini transcription request failed (400): {"error": {"code": 400, '
        '"message": "API key not valid. Please pass a valid API key.", '
        '"status": "INVALID_ARGUMENT"}}',
        provider="gemini",
        status_code=400,
    )
    reason = humanize_transcription_error(exc)
    assert "Gemini" in reason
    assert "rejected the API key" in reason


def test_humanize_transcription_error_does_not_treat_every_gemini_400_as_a_bad_key():
    exc = AIClientError(
        'Gemini transcription request failed (400): {"error": {"message": "Invalid JSON payload"}}',
        provider="gemini",
        status_code=400,
    )
    reason = humanize_transcription_error(exc)
    assert "rejected the API key" not in reason
    assert "Gemini" in reason


def test_humanize_transcription_error_rewrites_429():
    exc = AIClientError("rate limited", provider="gemini", status_code=429)
    reason = humanize_transcription_error(exc)
    assert "rate-limited" in reason


def test_humanize_transcription_error_rewrites_413():
    exc = AIClientError("payload too large", provider="gemini", status_code=413)
    reason = humanize_transcription_error(exc)
    assert "too large" in reason


def test_humanize_transcription_error_rewrites_5xx():
    exc = AIClientError("server error", provider="anthropic", status_code=503)
    reason = humanize_transcription_error(exc)
    assert "temporary problem" in reason


def test_humanize_transcription_error_rewrites_network_failure():
    exc = AIClientError("boom", provider="gemini", status_code=None)
    reason = humanize_transcription_error(exc)
    assert "Couldn't reach Gemini" in reason


def test_humanize_transcription_error_names_new_providers_correctly():
    for provider, expected_name in [("openai", "OpenAI"), ("groq", "Groq"), ("openrouter", "OpenRouter")]:
        exc = AIClientError("unauthorized", provider=provider, status_code=401)
        reason = humanize_transcription_error(exc)
        assert expected_name in reason
        assert "rejected the API key" in reason


def test_humanize_transcription_error_falls_back_for_unmapped_status():
    exc = AIClientError("teapot", provider="anthropic", status_code=418)
    reason = humanize_transcription_error(exc)
    assert "Anthropic" in reason
    assert "418" in reason


def test_humanize_transcription_error_has_generic_fallback_for_unknown_exceptions():
    assert humanize_transcription_error(RuntimeError("something else")) == (
        "Something went wrong during transcription. Please try again."
    )


def test_is_retryable_true_for_network_failure():
    assert is_retryable(AIClientError("boom", provider="gemini", status_code=None)) is True


def test_is_retryable_true_for_rate_limit_and_5xx():
    assert is_retryable(AIClientError("x", provider="gemini", status_code=429)) is True
    assert is_retryable(AIClientError("x", provider="gemini", status_code=503)) is True


def test_is_retryable_false_for_bad_key():
    assert is_retryable(AIClientError("x", provider="gemini", status_code=400)) is False
    assert is_retryable(AIClientError("x", provider="anthropic", status_code=401)) is False
