from pathlib import Path

import httpx

OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"

# Model IDs and API version headers move faster than this codebase does --
# verify these against platform.openai.com/docs and docs.anthropic.com at
# implementation/update time rather than trusting them indefinitely.
OPENAI_WHISPER_MODEL = "whisper-1"
ANTHROPIC_MODEL = "claude-sonnet-4-5"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_MAX_SUMMARY_TOKENS = 1024

REQUEST_TIMEOUT_SECONDS = 120.0

SUMMARY_PROMPT_TEMPLATE = (
    "Summarize the following video transcript. Focus on the key points, "
    "structure it with short paragraphs or bullet points where that helps "
    "readability, and keep it concise relative to the transcript's length. "
    "Don't editorialize or add information that isn't in the transcript.\n\n"
    "Transcript:\n{transcript}"
)


class AIClientError(Exception):
    """Raised for any non-2xx response or network failure talking to the
    OpenAI/Anthropic APIs. status_code is None for network-level failures
    (no response was ever received), letting callers distinguish "the
    service rejected this" from "we couldn't reach it at all"."""

    def __init__(self, message: str, *, provider: str, status_code: "int | None" = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class OpenAIWhisperClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def transcribe_chunk(self, chunk_path: Path, response_format: str = "verbose_json") -> dict:
        """Blocking -- must run in a thread executor. Returns Whisper's
        parsed JSON response, which (in verbose_json mode) includes a
        top-level "duration" and a "segments" list with per-segment
        start/end timestamps relative to this chunk's own start at 0."""
        chunk_path = Path(chunk_path)
        try:
            with open(chunk_path, "rb") as f:
                response = httpx.post(
                    OPENAI_TRANSCRIPTION_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data={"model": OPENAI_WHISPER_MODEL, "response_format": response_format},
                    files={"file": (chunk_path.name, f, "audio/mpeg")},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
        except httpx.HTTPError as exc:
            raise AIClientError(f"Network error reaching OpenAI: {exc}", provider="openai") from exc

        if response.status_code >= 400:
            raise AIClientError(
                f"OpenAI transcription request failed ({response.status_code}): {response.text[:500]}",
                provider="openai",
                status_code=response.status_code,
            )
        return response.json()


class AnthropicClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def summarize(self, transcript_text: str) -> str:
        """Blocking -- must run in a thread executor. Claude's context
        window comfortably fits even hour-long transcripts in one call, so
        unlike Whisper this never needs chunking."""
        try:
            response = httpx.post(
                ANTHROPIC_MESSAGES_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": ANTHROPIC_API_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": ANTHROPIC_MAX_SUMMARY_TOKENS,
                    "messages": [
                        {
                            "role": "user",
                            "content": SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript_text),
                        }
                    ],
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise AIClientError(
                f"Network error reaching Anthropic: {exc}", provider="anthropic"
            ) from exc

        if response.status_code >= 400:
            raise AIClientError(
                f"Anthropic summarization request failed ({response.status_code}): {response.text[:500]}",
                provider="anthropic",
                status_code=response.status_code,
            )
        body = response.json()
        return "".join(block.get("text", "") for block in body.get("content", []))
