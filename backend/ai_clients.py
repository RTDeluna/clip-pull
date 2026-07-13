import base64
import json
from pathlib import Path
from typing import Optional

import httpx

GEMINI_GENERATE_CONTENT_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"

# Model IDs and API version headers move faster than this codebase does --
# verify these against each provider's live docs at implementation/update
# time rather than trusting them indefinitely. Every "cheap" pick below is
# a deliberately current, non-deprecated tier, not a stripped-down variant.
GEMINI_MODEL = "gemini-3.5-flash"
ANTHROPIC_MODEL = "claude-sonnet-4-5"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_MAX_SUMMARY_TOKENS = 1024
# whisper-1 specifically (not gpt-4o-transcribe/gpt-4o-mini-transcribe) --
# the newer OpenAI transcribe models don't support response_format=
# verbose_json with per-segment timestamps, which stitch_transcript()
# depends on to build one continuous timeline across chunks.
OPENAI_TRANSCRIPTION_MODEL = "whisper-1"
OPENAI_SUMMARY_MODEL = "gpt-5-mini"
# Groq's OpenAI-compatible endpoint serves real Whisper models directly, so
# verbose_json + segments works the same way as OpenAI's own whisper-1.
# turbo is Groq's fastest/cheapest tier at only a small quality tradeoff.
GROQ_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"
# OpenRouter model IDs are "provider/model" -- this one's been stable and
# widely available since GPT-4o mini's release.
OPENROUTER_SUMMARY_MODEL = "openai/gpt-4o-mini"

REQUEST_TIMEOUT_SECONDS = 120.0

# Asks the model for structured "Lesson Notes" as a JSON object rather than
# prose. The transcript passed in already carries per-line [HH:MM:SS] prefixes
# (see stitch_transcript/format_timestamp in transcription.py), so the model is
# told to convert those into integer `seconds` for each key point / chapter.
# The expected shape is:
#   {"tldr": "1-3 sentence summary",
#    "key_points": [{"seconds": 125, "text": "..."}],
#    "chapters": [{"seconds": 0, "title": "..."}]}
# transcription.py's parse_structured_notes() parses/normalizes the response and
# falls back gracefully if the model doesn't return valid JSON, so this prompt is
# a best-effort request, not a hard contract. All four summarization clients relay
# this same template verbatim, keeping the change provider-agnostic.
SUMMARY_PROMPT_TEMPLATE = (
    "You are creating structured lesson notes from a video transcript. "
    "Respond with ONLY a single raw JSON object (no markdown code fences, no "
    "commentary before or after) matching exactly this shape:\n"
    # Literal braces are doubled so str.format(transcript=...) leaves them
    # intact and only substitutes the {transcript} placeholder at the end.
    '{{"tldr": "a 1-3 sentence summary of the whole video", '
    '"key_points": [{{"seconds": 125, "text": "a concise key point"}}], '
    '"chapters": [{{"seconds": 0, "title": "a short chapter title"}}]}}\n\n'
    "Rules:\n"
    "- Each line of the transcript is prefixed with a [HH:MM:SS] timestamp. "
    "Derive every `seconds` value by converting the relevant line's [HH:MM:SS] "
    "timestamp to a total number of seconds (e.g. [00:02:05] -> 125).\n"
    "- Keep `key_points` to a reasonable number (roughly 5-12, using your "
    "judgment based on the video's length and density).\n"
    "- `chapters` are optional: include them only if the content has clear "
    "topic breaks, otherwise return an empty list for `chapters`.\n"
    "- Don't editorialize or add information that isn't in the transcript.\n\n"
    "Transcript:\n{transcript}"
)

# System prompt for "Chat with your lesson": grounds the model strictly in the
# supplied transcript. The transcript is substituted once via .format(); braces
# inside the transcript value itself are left untouched by str.format (only the
# template's own {transcript} placeholder is filled). All four chat() methods
# relay this same prompt so the grounding rules stay provider-agnostic.
CHAT_SYSTEM_PROMPT = (
    "You are answering questions about a specific video using ONLY the transcript "
    "provided below. The transcript is your single source of truth.\n"
    "- Base every answer solely on what the transcript says; do not rely on outside "
    "knowledge or invent details.\n"
    "- If the transcript doesn't contain enough information to answer, say so clearly "
    "instead of guessing.\n"
    "- Keep answers concise and directly focused on the question.\n\n"
    "Transcript:\n{transcript}"
)


def _build_chat_turns(question: str, history: Optional[list[dict]] = None) -> list[dict]:
    """Builds the alternating conversation turns for a chat request: the prior
    `history` turns (each {"role": "user"|"assistant", "content": str}) followed
    by the new `question` as a trailing user turn. Malformed history entries
    (missing/unknown role, non-string content) are dropped defensively -- the
    route layer validates shape, this is a second provider-agnostic guard.
    Shared by all four chat() methods so the turn-building logic lives in one
    place; each provider then maps these {"role","content"} dicts into its own
    wire format (OpenAI/Anthropic use them as-is; Gemini renames "assistant" ->
    "model" and wraps content in a parts array)."""
    turns: list[dict] = []
    for turn in history or []:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            turns.append({"role": role, "content": content})
    turns.append({"role": "user", "content": question})
    return turns


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
    service rejected this" from "we couldn't reach it at all". timed_out
    further splits that second case: a connection failure never reached the
    provider at all (safe to retry), but a timeout is ambiguous -- the
    provider may have already received the request and started (or even
    finished) processing/billing it before the client gave up waiting. See
    is_retryable in transcription_errors.py, which treats the two
    differently to avoid auto-retrying a request that might double-bill the
    user's own API key."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: "int | None" = None,
        timed_out: bool = False,
    ):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.timed_out = timed_out


def _network_error(exc: Exception, *, provider: str) -> AIClientError:
    """Builds the AIClientError for a request that never got a response
    back at all. Shared by every provider client below so the
    connect-vs-timeout distinction (see AIClientError's docstring) is made
    consistently in one place instead of per call site."""
    timed_out = isinstance(exc, httpx.TimeoutException)
    verb = "Timed out reaching" if timed_out else "Network error reaching"
    return AIClientError(f"{verb} {provider}: {exc}", provider=provider, timed_out=timed_out)


class GeminiTranscriptionClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.last_usage = None

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
        url = GEMINI_GENERATE_CONTENT_URL_TEMPLATE.format(model=GEMINI_MODEL)
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
            raise _network_error(exc, provider="gemini") from exc

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
        usage = body.get("usageMetadata", {})
        self.last_usage = {
            "provider": "gemini",
            "model": GEMINI_MODEL,
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
            "total_tokens": usage.get("totalTokenCount"),
            "audio_seconds": None,
        }
        return {"duration": parsed.get("duration_seconds", 0.0), "segments": segments}


class AnthropicClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.last_usage = None

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
            raise _network_error(exc, provider="anthropic") from exc

        if response.status_code >= 400:
            raise AIClientError(
                f"Anthropic summarization request failed ({response.status_code}): {response.text[:500]}",
                provider="anthropic",
                status_code=response.status_code,
            )
        body = response.json()
        usage = body.get("usage", {})
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        self.last_usage = {
            "provider": "anthropic",
            "model": ANTHROPIC_MODEL,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": (input_tokens + output_tokens)
            if input_tokens is not None and output_tokens is not None
            else None,
            "audio_seconds": None,
        }
        return "".join(block.get("text", "") for block in body.get("content", []))

    def chat(
        self, transcript_text: str, question: str, history: Optional[list[dict]] = None
    ) -> str:
        """Blocking -- must run in a thread executor. Answers `question` about
        the video grounded in `transcript_text`. The transcript rides in the
        top-level `system` field (Anthropic's convention for context that
        isn't a conversation turn), leaving `messages` as the clean alternating
        user/assistant history plus the new question."""
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
                    "system": CHAT_SYSTEM_PROMPT.format(transcript=transcript_text),
                    "messages": _build_chat_turns(question, history),
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise _network_error(exc, provider="anthropic") from exc

        if response.status_code >= 400:
            raise AIClientError(
                f"Anthropic chat request failed ({response.status_code}): {response.text[:500]}",
                provider="anthropic",
                status_code=response.status_code,
            )
        body = response.json()
        usage = body.get("usage", {})
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        self.last_usage = {
            "provider": "anthropic",
            "model": ANTHROPIC_MODEL,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": (input_tokens + output_tokens)
            if input_tokens is not None and output_tokens is not None
            else None,
            "audio_seconds": None,
        }
        return "".join(block.get("text", "") for block in body.get("content", []))


def _transcribe_via_openai_compatible_endpoint(
    url: str, api_key: str, model: str, chunk_path: Path, provider: str
) -> tuple[dict, dict]:
    """Shared by OpenAIWhisperClient and GroqWhisperClient -- both expose
    the same multipart /audio/transcriptions shape (Groq's endpoint is
    explicitly OpenAI-compatible), differing only in URL/model/provider
    label. verbose_json's segments already carry start times in seconds,
    so no reshaping math is needed beyond picking the fields out. Returns
    a (result, usage) tuple -- Whisper billing is by audio duration, not
    tokens, so usage carries audio_seconds and leaves the token fields
    None; each client sets its own last_usage from the usage half."""
    chunk_path = Path(chunk_path)
    try:
        with open(chunk_path, "rb") as audio_file:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": model, "response_format": "verbose_json"},
                files={"file": (chunk_path.name, audio_file, "audio/mpeg")},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
    except httpx.HTTPError as exc:
        raise _network_error(exc, provider=provider) from exc

    if response.status_code >= 400:
        raise AIClientError(
            f"{provider} transcription request failed ({response.status_code}): {response.text[:500]}",
            provider=provider,
            status_code=response.status_code,
        )

    body = response.json()
    segments = [
        {"start": segment.get("start", 0.0), "text": segment.get("text", "")}
        for segment in body.get("segments", [])
    ]
    result = {"duration": body.get("duration", 0.0), "segments": segments}
    usage = {
        "provider": provider,
        "model": model,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "audio_seconds": body.get("duration"),
    }
    return result, usage


class OpenAIWhisperClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.last_usage = None

    def transcribe_chunk(self, chunk_path: Path, response_format: str = "verbose_json") -> dict:
        """Blocking -- must run in a thread executor. response_format is
        accepted for interface compatibility with the other transcription
        clients but always sent as verbose_json, since that's what carries
        the per-segment start times stitch_transcript() needs."""
        result, usage = _transcribe_via_openai_compatible_endpoint(
            OPENAI_TRANSCRIPTIONS_URL, self.api_key, OPENAI_TRANSCRIPTION_MODEL, chunk_path, "openai"
        )
        self.last_usage = usage
        return result


class GroqWhisperClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.last_usage = None

    def transcribe_chunk(self, chunk_path: Path, response_format: str = "verbose_json") -> dict:
        """Blocking -- must run in a thread executor."""
        result, usage = _transcribe_via_openai_compatible_endpoint(
            GROQ_TRANSCRIPTIONS_URL, self.api_key, GROQ_TRANSCRIPTION_MODEL, chunk_path, "groq"
        )
        self.last_usage = usage
        return result


class OpenAISummaryClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.last_usage = None

    def summarize(self, transcript_text: str) -> str:
        """Blocking -- must run in a thread executor."""
        try:
            response = httpx.post(
                OPENAI_CHAT_COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
                json={
                    "model": OPENAI_SUMMARY_MODEL,
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
            raise _network_error(exc, provider="openai") from exc

        if response.status_code >= 400:
            raise AIClientError(
                f"OpenAI summarization request failed ({response.status_code}): {response.text[:500]}",
                provider="openai",
                status_code=response.status_code,
            )
        body = response.json()
        usage = body.get("usage", {})
        self.last_usage = {
            "provider": "openai",
            "model": OPENAI_SUMMARY_MODEL,
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "audio_seconds": None,
        }
        return body["choices"][0]["message"]["content"]

    def chat(
        self, transcript_text: str, question: str, history: Optional[list[dict]] = None
    ) -> str:
        """Blocking -- must run in a thread executor. Same chat-completions
        endpoint summarize() uses; the transcript rides in a leading system
        message, followed by the prior turns and the new question."""
        messages = [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT.format(transcript=transcript_text)}
        ]
        messages.extend(_build_chat_turns(question, history))
        try:
            response = httpx.post(
                OPENAI_CHAT_COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
                json={"model": OPENAI_SUMMARY_MODEL, "messages": messages},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise _network_error(exc, provider="openai") from exc

        if response.status_code >= 400:
            raise AIClientError(
                f"OpenAI chat request failed ({response.status_code}): {response.text[:500]}",
                provider="openai",
                status_code=response.status_code,
            )
        body = response.json()
        usage = body.get("usage", {})
        self.last_usage = {
            "provider": "openai",
            "model": OPENAI_SUMMARY_MODEL,
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "audio_seconds": None,
        }
        return body["choices"][0]["message"]["content"]


class GeminiSummaryClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.last_usage = None

    def summarize(self, transcript_text: str) -> str:
        """Blocking -- must run in a thread executor. Same generateContent
        endpoint GeminiTranscriptionClient uses, minus the inline audio
        part -- a plain text-only prompt."""
        url = GEMINI_GENERATE_CONTENT_URL_TEMPLATE.format(model=GEMINI_MODEL)
        try:
            response = httpx.post(
                url,
                headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
                json={
                    "contents": [
                        {"parts": [{"text": SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript_text)}]}
                    ]
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise _network_error(exc, provider="gemini") from exc

        if response.status_code >= 400:
            raise AIClientError(
                f"Gemini summarization request failed ({response.status_code}): {response.text[:500]}",
                provider="gemini",
                status_code=response.status_code,
            )
        body = response.json()
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise AIClientError(
                f"Gemini returned an unexpected response shape: {exc}", provider="gemini"
            ) from exc
        usage = body.get("usageMetadata", {})
        self.last_usage = {
            "provider": "gemini",
            "model": GEMINI_MODEL,
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
            "total_tokens": usage.get("totalTokenCount"),
            "audio_seconds": None,
        }
        return text

    def chat(
        self, transcript_text: str, question: str, history: Optional[list[dict]] = None
    ) -> str:
        """Blocking -- must run in a thread executor. Gemini's generateContent
        takes a `contents` array whose turns use role "user"/"model" (NOT
        "assistant"), so history's "assistant" turns are renamed to "model"
        here. The transcript grounding rides in `systemInstruction`."""
        url = GEMINI_GENERATE_CONTENT_URL_TEMPLATE.format(model=GEMINI_MODEL)
        contents = [
            {
                "role": "model" if turn["role"] == "assistant" else "user",
                "parts": [{"text": turn["content"]}],
            }
            for turn in _build_chat_turns(question, history)
        ]
        try:
            response = httpx.post(
                url,
                headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
                json={
                    "systemInstruction": {
                        "parts": [{"text": CHAT_SYSTEM_PROMPT.format(transcript=transcript_text)}]
                    },
                    "contents": contents,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise _network_error(exc, provider="gemini") from exc

        if response.status_code >= 400:
            raise AIClientError(
                f"Gemini chat request failed ({response.status_code}): {response.text[:500]}",
                provider="gemini",
                status_code=response.status_code,
            )
        body = response.json()
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise AIClientError(
                f"Gemini returned an unexpected response shape: {exc}", provider="gemini"
            ) from exc
        usage = body.get("usageMetadata", {})
        self.last_usage = {
            "provider": "gemini",
            "model": GEMINI_MODEL,
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
            "total_tokens": usage.get("totalTokenCount"),
            "audio_seconds": None,
        }
        return text


class OpenRouterSummaryClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.last_usage = None

    def summarize(self, transcript_text: str) -> str:
        """Blocking -- must run in a thread executor. OpenRouter's chat
        completions endpoint is OpenAI-compatible."""
        try:
            response = httpx.post(
                OPENROUTER_CHAT_COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
                json={
                    "model": OPENROUTER_SUMMARY_MODEL,
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
            raise _network_error(exc, provider="openrouter") from exc

        if response.status_code >= 400:
            raise AIClientError(
                f"OpenRouter summarization request failed ({response.status_code}): {response.text[:500]}",
                provider="openrouter",
                status_code=response.status_code,
            )
        body = response.json()
        usage = body.get("usage", {})
        self.last_usage = {
            "provider": "openrouter",
            "model": OPENROUTER_SUMMARY_MODEL,
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "audio_seconds": None,
        }
        return body["choices"][0]["message"]["content"]

    def chat(
        self, transcript_text: str, question: str, history: Optional[list[dict]] = None
    ) -> str:
        """Blocking -- must run in a thread executor. OpenRouter's chat
        completions endpoint is OpenAI-compatible, so the message shape matches
        OpenAISummaryClient.chat exactly."""
        messages = [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT.format(transcript=transcript_text)}
        ]
        messages.extend(_build_chat_turns(question, history))
        try:
            response = httpx.post(
                OPENROUTER_CHAT_COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
                json={"model": OPENROUTER_SUMMARY_MODEL, "messages": messages},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise _network_error(exc, provider="openrouter") from exc

        if response.status_code >= 400:
            raise AIClientError(
                f"OpenRouter chat request failed ({response.status_code}): {response.text[:500]}",
                provider="openrouter",
                status_code=response.status_code,
            )
        body = response.json()
        usage = body.get("usage", {})
        self.last_usage = {
            "provider": "openrouter",
            "model": OPENROUTER_SUMMARY_MODEL,
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "audio_seconds": None,
        }
        return body["choices"][0]["message"]["content"]


# Provider -> client class, shared by transcription.py (to instantiate the
# configured provider) and settings validation (to know which provider IDs
# are valid). Keys match the *_api_key settings columns' provider prefix.
TRANSCRIPTION_CLIENTS = {
    "gemini": GeminiTranscriptionClient,
    "openai": OpenAIWhisperClient,
    "groq": GroqWhisperClient,
}
SUMMARIZATION_CLIENTS = {
    "anthropic": AnthropicClient,
    "openai": OpenAISummaryClient,
    "gemini": GeminiSummaryClient,
    "openrouter": OpenRouterSummaryClient,
}

# Which settings-store column holds each provider's key, and the
# human-readable name to use in status/error messages. Both transcription.py
# (to look up the configured provider's key) and transcription_errors.py (to
# name the provider in a friendly error) share this single source of truth.
PROVIDER_API_KEY_SETTINGS = {
    "gemini": "gemini_api_key",
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "groq": "groq_api_key",
    "openrouter": "openrouter_api_key",
}
PROVIDER_DISPLAY_NAMES = {
    "gemini": "Gemini",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "groq": "Groq",
    "openrouter": "OpenRouter",
}
# "a Gemini API key" vs "an Anthropic API key" -- keyed explicitly rather
# than sniffing the first letter, since that broke before (a sed-based
# rename once silently turned "an OpenRouter key" into "an Gemini key").
PROVIDER_INDEFINITE_ARTICLES = {
    "gemini": "a",
    "anthropic": "an",
    "openai": "an",
    "groq": "a",
    "openrouter": "an",
}
