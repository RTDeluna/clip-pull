from ai_clients import AIClientError
from audio_extraction import AudioExtractionError

# Status codes worth retrying automatically before giving up and surfacing
# an error -- transient (rate limit, server-side hiccups, or a request that
# never got a response at all).
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def is_retryable(exc: AIClientError) -> bool:
    return exc.status_code is None or exc.status_code in RETRYABLE_STATUS_CODES


def humanize_transcription_error(exc: Exception) -> str:
    """Mirrors downloader.py's humanize_error_reason -- translates a raw
    exception into a short, actionable message the UI can show directly.
    Unlike yt-dlp's free-text errors, AIClientError already carries a
    structured provider/status_code, so this matches on that instead of
    regex-parsing text."""
    if isinstance(exc, AudioExtractionError):
        return str(exc)  # audio_extraction.py's own messages are already user-facing

    if isinstance(exc, AIClientError):
        provider_name = "Gemini" if exc.provider == "gemini" else "Anthropic"
        # Most providers use 401/403 for a bad key -- Gemini instead reports
        # it as a plain 400 INVALID_ARGUMENT ("API key not valid"), so that
        # case needs matching on the response text (already embedded in the
        # exception message by ai_clients.py) rather than status code alone.
        looks_like_bad_key = exc.status_code in (401, 403) or (
            exc.provider == "gemini"
            and exc.status_code == 400
            and "api key" in str(exc).lower()
        )
        if looks_like_bad_key:
            return (
                f"{provider_name} rejected the API key in Settings — "
                "check that it's correct and still active."
            )
        if exc.status_code == 429:
            return f"{provider_name} rate-limited this request. Wait a bit and hit Retry."
        if exc.status_code == 413:
            return "This audio was too large for the transcription service, even after compression."
        if exc.status_code is not None and 500 <= exc.status_code < 600:
            return f"{provider_name} had a temporary problem. Wait a moment and hit Retry."
        if exc.status_code is None:
            return f"Couldn't reach {provider_name} — check your internet connection and hit Retry."
        return f"{provider_name} rejected the request ({exc.status_code})."

    return "Something went wrong during transcription. Please try again."
