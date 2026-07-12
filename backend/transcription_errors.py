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
        provider_name = "OpenAI" if exc.provider == "openai" else "Anthropic"
        if exc.status_code == 401 or exc.status_code == 403:
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
