import base64
import json
from pathlib import Path

import httpx

GEMINI_GENERATE_CONTENT_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"

# Model IDs and API version headers move faster than this codebase does --
# verify these against ai.google.dev/gemini-api/docs and docs.anthropic.com
# at implementation/update time rather than trusting them indefinitely.
# Flash is the deliberately cheap-but-current tier, not a stripped-down
# "lite" variant.
GEMINI_TRANSCRIPTION_MODEL = "gemini-3.5-flash"
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

GEMINI_TRANSCRIPTION_PROMPT = (
    "Transcribe this audio completely and accurately. Break the transcript "
    "into natural segments (roughly a sentence each). For every segment, "
    "report its start time in seconds measured from the beginning of this "
    "audio file, and the spoken text. Also report the audio's total "
    "duration in seconds. Respond with only the JSON described by the "
    "response schema -- no extra commentary."
)

# Requesting numeric seconds (not a formatted timestamp string) keeps this
# directly usable by transcription.py's stitching math without a parsing
# step. Unlike a dedicated ASR model, these timestamps come from the model
# being asked to report them, not from acoustic alignment -- fine for a
# human-skimmable transcript, not frame-accurate subtitle sync.
GEMINI_TRANSCRIPTION_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "duration_seconds": {"type": "NUMBER"},
        "segments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start_seconds": {"type": "NUMBER"},
                    "text": {"type": "STRING"},
                },
                "required": ["start_seconds", "text"],
            },
        },
    },
    "required": ["duration_seconds", "segments"],
}


class AIClientError(Exception):
    """Raised for any non-2xx response or network failure talking to the
    Gemini/Anthropic APIs. status_code is None for network-level failures
    (no response was ever received), letting callers distinguish "the
    service rejected this" from "we couldn't reach it at all"."""

    def __init__(self, message: str, *, provider: str, status_code: "int | None" = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class GeminiTranscriptionClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def transcribe_chunk(self, chunk_path: Path, response_format: str = "verbose_json") -> dict:
        """Blocking -- must run in a thread executor. Gemini has no
        dedicated transcription endpoint; this sends the audio inline to
        generateContent with a JSON response schema, then reshapes the
        result into the same {"duration": float, "segments": [{"start":
        float, "text": str}]} shape the previous Whisper-based client
        returned, so transcription.py's stitching logic needs no changes
        regardless of which provider is configured. response_format is
        accepted for interface compatibility but unused -- Gemini's
        structured-output schema is what actually shapes the response."""
        chunk_path = Path(chunk_path)
        encoded_audio = base64.b64encode(chunk_path.read_bytes()).decode("ascii")
        url = GEMINI_GENERATE_CONTENT_URL_TEMPLATE.format(model=GEMINI_TRANSCRIPTION_MODEL)
        try:
            response = httpx.post(
                url,
                headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
                json={
                    "contents": [
                        {
                            "parts": [
                                {"text": GEMINI_TRANSCRIPTION_PROMPT},
                                {"inline_data": {"mime_type": "audio/mpeg", "data": encoded_audio}},
                            ]
                        }
                    ],
                    "generationConfig": {
                        "response_mime_type": "application/json",
                        "response_schema": GEMINI_TRANSCRIPTION_RESPONSE_SCHEMA,
                    },
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise AIClientError(f"Network error reaching Gemini: {exc}", provider="gemini") from exc

        if response.status_code >= 400:
            raise AIClientError(
                f"Gemini transcription request failed ({response.status_code}): {response.text[:500]}",
                provider="gemini",
                status_code=response.status_code,
            )

        body = response.json()
        try:
            raw_text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(raw_text)
        except (KeyError, IndexError, ValueError) as exc:
            raise AIClientError(
                f"Gemini returned an unexpected response shape: {exc}", provider="gemini"
            ) from exc

        segments = [
            {"start": segment.get("start_seconds", 0.0), "text": segment.get("text", "")}
            for segment in parsed.get("segments", [])
        ]
        return {"duration": parsed.get("duration_seconds", 0.0), "segments": segments}


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
