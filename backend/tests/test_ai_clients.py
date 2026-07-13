import json
from unittest.mock import patch

import httpx
import pytest

from ai_clients import (
    ANTHROPIC_MODEL,
    GEMINI_MODEL,
    GROQ_TRANSCRIPTION_MODEL,
    OPENAI_SUMMARY_MODEL,
    OPENAI_TRANSCRIPTION_MODEL,
    OPENROUTER_SUMMARY_MODEL,
    AIClientError,
    AnthropicClient,
    GeminiSummaryClient,
    GeminiTranscriptionClient,
    GroqWhisperClient,
    OpenAISummaryClient,
    OpenAIWhisperClient,
    OpenRouterSummaryClient,
)


class FakeResponse:
    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = text or str(json_body)

    def json(self):
        return self._json_body


def _fake_gemini_response(status_code=200, duration_seconds=12.5, segments=None):
    payload = {"duration_seconds": duration_seconds, "segments": segments or []}
    return FakeResponse(
        status_code,
        {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]},
    )


def test_transcribe_chunk_returns_reshaped_result_on_success(tmp_path):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")
    fake_response = _fake_gemini_response(
        duration_seconds=12.5, segments=[{"start_seconds": 1.5, "text": "hello world"}]
    )

    with patch("ai_clients.httpx.post", return_value=fake_response) as mock_post:
        client = GeminiTranscriptionClient(api_key="sk-gemini-test")
        result = client.transcribe_chunk(chunk)

    assert result["duration"] == 12.5
    assert result["segments"] == [{"start": 1.5, "text": "hello world"}]
    assert mock_post.call_args.kwargs["headers"]["x-goog-api-key"] == "sk-gemini-test"


def test_transcribe_chunk_hits_the_gemini_endpoint_for_the_configured_model_with_base64_audio(tmp_path):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")
    fake_response = _fake_gemini_response()

    with patch("ai_clients.httpx.post", return_value=fake_response) as mock_post:
        client = GeminiTranscriptionClient(api_key="sk-gemini-test")
        client.transcribe_chunk(chunk)

    call = mock_post.call_args
    assert call.args[0] == f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    parts = call.kwargs["json"]["contents"][0]["parts"]
    inline_data = next(p["inline_data"] for p in parts if "inline_data" in p)
    assert inline_data["data"]  # base64 payload present
    assert inline_data["mime_type"] == "audio/mpeg"


def test_transcribe_chunk_raises_ai_client_error_with_status_on_401(tmp_path):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")
    fake_response = FakeResponse(401, text="invalid_api_key")

    with patch("ai_clients.httpx.post", return_value=fake_response):
        client = GeminiTranscriptionClient(api_key="bad-key")
        with pytest.raises(AIClientError) as exc_info:
            client.transcribe_chunk(chunk)

    assert exc_info.value.provider == "gemini"
    assert exc_info.value.status_code == 401


def test_transcribe_chunk_raises_ai_client_error_on_network_failure(tmp_path):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")

    with patch("ai_clients.httpx.post", side_effect=httpx.ConnectError("boom")):
        client = GeminiTranscriptionClient(api_key="sk-gemini-test")
        with pytest.raises(AIClientError) as exc_info:
            client.transcribe_chunk(chunk)

    assert exc_info.value.provider == "gemini"
    assert exc_info.value.status_code is None


def test_transcribe_chunk_raises_ai_client_error_on_malformed_response_shape(tmp_path):
    # Unlike a dedicated transcription endpoint, Gemini's structured output
    # is nested two layers deep (candidates -> content -> parts -> text,
    # itself a JSON string) -- a genuinely new failure mode worth covering.
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")
    fake_response = FakeResponse(200, {"candidates": []})

    with patch("ai_clients.httpx.post", return_value=fake_response):
        client = GeminiTranscriptionClient(api_key="sk-gemini-test")
        with pytest.raises(AIClientError) as exc_info:
            client.transcribe_chunk(chunk)

    assert exc_info.value.provider == "gemini"


def test_transcribe_chunk_raises_ai_client_error_when_inner_text_is_not_valid_json(tmp_path):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")
    fake_response = FakeResponse(
        200, {"candidates": [{"content": {"parts": [{"text": "not valid json"}]}}]}
    )

    with patch("ai_clients.httpx.post", return_value=fake_response):
        client = GeminiTranscriptionClient(api_key="sk-gemini-test")
        with pytest.raises(AIClientError) as exc_info:
            client.transcribe_chunk(chunk)

    assert exc_info.value.provider == "gemini"


def test_summarize_returns_concatenated_text_blocks():
    fake_response = FakeResponse(
        200,
        {"content": [{"type": "text", "text": "Part one. "}, {"type": "text", "text": "Part two."}]},
    )

    with patch("ai_clients.httpx.post", return_value=fake_response) as mock_post:
        client = AnthropicClient(api_key="sk-ant-test")
        result = client.summarize("some transcript text")

    assert result == "Part one. Part two."
    assert mock_post.call_args.kwargs["headers"]["x-api-key"] == "sk-ant-test"


def test_summarize_raises_ai_client_error_with_status_on_429():
    fake_response = FakeResponse(429, text="rate limited")

    with patch("ai_clients.httpx.post", return_value=fake_response):
        client = AnthropicClient(api_key="sk-ant-test")
        with pytest.raises(AIClientError) as exc_info:
            client.summarize("some transcript text")

    assert exc_info.value.provider == "anthropic"
    assert exc_info.value.status_code == 429


def test_summarize_raises_ai_client_error_on_network_failure():
    with patch("ai_clients.httpx.post", side_effect=httpx.ConnectError("boom")):
        client = AnthropicClient(api_key="sk-ant-test")
        with pytest.raises(AIClientError) as exc_info:
            client.summarize("some transcript text")

    assert exc_info.value.provider == "anthropic"
    assert exc_info.value.status_code is None


# -- OpenAI Whisper / Groq (shared OpenAI-compatible multipart shape) ------


def _fake_whisper_verbose_json_response(duration=12.5, segments=None):
    return FakeResponse(
        200,
        {"duration": duration, "segments": segments or [], "text": "combined text"},
    )


@pytest.mark.parametrize(
    "client_cls, provider",
    [(OpenAIWhisperClient, "openai"), (GroqWhisperClient, "groq")],
)
def test_whisper_compatible_transcribe_chunk_returns_reshaped_result(tmp_path, client_cls, provider):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")
    fake_response = _fake_whisper_verbose_json_response(
        duration=12.5, segments=[{"start": 1.5, "text": "hello world"}]
    )

    with patch("ai_clients.httpx.post", return_value=fake_response) as mock_post:
        client = client_cls(api_key="sk-test")
        result = client.transcribe_chunk(chunk)

    assert result["duration"] == 12.5
    assert result["segments"] == [{"start": 1.5, "text": "hello world"}]
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert mock_post.call_args.kwargs["data"]["response_format"] == "verbose_json"


@pytest.mark.parametrize(
    "client_cls, provider",
    [(OpenAIWhisperClient, "openai"), (GroqWhisperClient, "groq")],
)
def test_whisper_compatible_transcribe_chunk_raises_ai_client_error_on_401(tmp_path, client_cls, provider):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")

    with patch("ai_clients.httpx.post", return_value=FakeResponse(401, text="invalid key")):
        client = client_cls(api_key="bad-key")
        with pytest.raises(AIClientError) as exc_info:
            client.transcribe_chunk(chunk)

    assert exc_info.value.provider == provider
    assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    "client_cls, provider",
    [(OpenAIWhisperClient, "openai"), (GroqWhisperClient, "groq")],
)
def test_whisper_compatible_transcribe_chunk_raises_ai_client_error_on_network_failure(
    tmp_path, client_cls, provider
):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")

    with patch("ai_clients.httpx.post", side_effect=httpx.ConnectError("boom")):
        client = client_cls(api_key="sk-test")
        with pytest.raises(AIClientError) as exc_info:
            client.transcribe_chunk(chunk)

    assert exc_info.value.provider == provider
    assert exc_info.value.status_code is None


# -- OpenAI / OpenRouter summarization (shared chat-completions shape) ----


def _fake_chat_completion_response(text="a summary"):
    return FakeResponse(200, {"choices": [{"message": {"content": text}}]})


@pytest.mark.parametrize(
    "client_cls, provider",
    [(OpenAISummaryClient, "openai"), (OpenRouterSummaryClient, "openrouter")],
)
def test_chat_completion_summarize_returns_message_content(client_cls, provider):
    fake_response = _fake_chat_completion_response("a concise summary")

    with patch("ai_clients.httpx.post", return_value=fake_response) as mock_post:
        client = client_cls(api_key="sk-test")
        result = client.summarize("some transcript text")

    assert result == "a concise summary"
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"


@pytest.mark.parametrize(
    "client_cls, provider",
    [(OpenAISummaryClient, "openai"), (OpenRouterSummaryClient, "openrouter")],
)
def test_chat_completion_summarize_raises_ai_client_error_with_status_on_429(client_cls, provider):
    with patch("ai_clients.httpx.post", return_value=FakeResponse(429, text="rate limited")):
        client = client_cls(api_key="sk-test")
        with pytest.raises(AIClientError) as exc_info:
            client.summarize("some transcript text")

    assert exc_info.value.provider == provider
    assert exc_info.value.status_code == 429


@pytest.mark.parametrize(
    "client_cls, provider",
    [(OpenAISummaryClient, "openai"), (OpenRouterSummaryClient, "openrouter")],
)
def test_chat_completion_summarize_raises_ai_client_error_on_network_failure(client_cls, provider):
    with patch("ai_clients.httpx.post", side_effect=httpx.ConnectError("boom")):
        client = client_cls(api_key="sk-test")
        with pytest.raises(AIClientError) as exc_info:
            client.summarize("some transcript text")

    assert exc_info.value.provider == provider
    assert exc_info.value.status_code is None


# -- Gemini summarization (text-only generateContent) ----------------------


def test_gemini_summarize_returns_text_from_first_candidate():
    fake_response = FakeResponse(
        200, {"candidates": [{"content": {"parts": [{"text": "a gemini summary"}]}}]}
    )

    with patch("ai_clients.httpx.post", return_value=fake_response) as mock_post:
        client = GeminiSummaryClient(api_key="sk-gemini-test")
        result = client.summarize("some transcript text")

    assert result == "a gemini summary"
    assert mock_post.call_args.kwargs["headers"]["x-goog-api-key"] == "sk-gemini-test"
    # Text-only prompt -- no inline_data part, unlike transcription.
    parts = mock_post.call_args.kwargs["json"]["contents"][0]["parts"]
    assert all("inline_data" not in p for p in parts)


def test_gemini_summarize_raises_ai_client_error_on_malformed_response_shape():
    with patch("ai_clients.httpx.post", return_value=FakeResponse(200, {"candidates": []})):
        client = GeminiSummaryClient(api_key="sk-gemini-test")
        with pytest.raises(AIClientError) as exc_info:
            client.summarize("some transcript text")

    assert exc_info.value.provider == "gemini"


def test_gemini_summarize_raises_ai_client_error_on_network_failure():
    with patch("ai_clients.httpx.post", side_effect=httpx.ConnectError("boom")):
        client = GeminiSummaryClient(api_key="sk-gemini-test")
        with pytest.raises(AIClientError) as exc_info:
            client.summarize("some transcript text")

    assert exc_info.value.provider == "gemini"
    assert exc_info.value.status_code is None


# -- last_usage capture (token- and duration-based representatives) ---------


def test_last_usage_starts_none_before_any_call():
    assert GeminiTranscriptionClient(api_key="x").last_usage is None
    assert AnthropicClient(api_key="x").last_usage is None
    assert OpenAIWhisperClient(api_key="x").last_usage is None
    assert GroqWhisperClient(api_key="x").last_usage is None
    assert OpenAISummaryClient(api_key="x").last_usage is None
    assert GeminiSummaryClient(api_key="x").last_usage is None
    assert OpenRouterSummaryClient(api_key="x").last_usage is None


def test_gemini_transcribe_sets_last_usage_from_usage_metadata(tmp_path):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")
    payload = {"duration_seconds": 12.5, "segments": [{"start_seconds": 0.0, "text": "hi"}]}
    fake_response = FakeResponse(
        200,
        {
            "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}],
            "usageMetadata": {
                "promptTokenCount": 111,
                "candidatesTokenCount": 22,
                "totalTokenCount": 133,
            },
        },
    )

    with patch("ai_clients.httpx.post", return_value=fake_response):
        client = GeminiTranscriptionClient(api_key="sk-gemini-test")
        client.transcribe_chunk(chunk)

    assert client.last_usage == {
        "provider": "gemini",
        "model": GEMINI_MODEL,
        "input_tokens": 111,
        "output_tokens": 22,
        "total_tokens": 133,
        "audio_seconds": None,
    }


def test_anthropic_summarize_sets_last_usage_from_usage_block():
    fake_response = FakeResponse(
        200,
        {
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 300, "output_tokens": 40},
        },
    )

    with patch("ai_clients.httpx.post", return_value=fake_response):
        client = AnthropicClient(api_key="sk-ant-test")
        client.summarize("some transcript text")

    assert client.last_usage == {
        "provider": "anthropic",
        "model": ANTHROPIC_MODEL,
        "input_tokens": 300,
        "output_tokens": 40,
        # total is derived from the sum since Anthropic reports no total field.
        "total_tokens": 340,
        "audio_seconds": None,
    }


def test_openai_summarize_sets_last_usage_from_usage_block():
    fake_response = FakeResponse(
        200,
        {
            "choices": [{"message": {"content": "a summary"}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 60, "total_tokens": 560},
        },
    )

    with patch("ai_clients.httpx.post", return_value=fake_response):
        client = OpenAISummaryClient(api_key="sk-test")
        client.summarize("some transcript text")

    assert client.last_usage == {
        "provider": "openai",
        "model": OPENAI_SUMMARY_MODEL,
        "input_tokens": 500,
        "output_tokens": 60,
        "total_tokens": 560,
        "audio_seconds": None,
    }


@pytest.mark.parametrize(
    "client_cls, provider, expected_model",
    [
        (OpenAIWhisperClient, "openai", OPENAI_TRANSCRIPTION_MODEL),
        (GroqWhisperClient, "groq", GROQ_TRANSCRIPTION_MODEL),
    ],
)
def test_whisper_transcribe_sets_last_usage_with_audio_seconds_only(
    tmp_path, client_cls, provider, expected_model
):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")
    fake_response = _fake_whisper_verbose_json_response(
        duration=42.0, segments=[{"start": 0.0, "text": "hi"}]
    )

    with patch("ai_clients.httpx.post", return_value=fake_response):
        client = client_cls(api_key="sk-test")
        client.transcribe_chunk(chunk)

    # Whisper billing is by audio duration, not tokens -- token fields stay None.
    assert client.last_usage == {
        "provider": provider,
        "model": expected_model,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "audio_seconds": 42.0,
    }


def test_gemini_transcribe_last_usage_all_none_when_metadata_absent(tmp_path):
    # A response without usageMetadata must still set last_usage (with None
    # counts) rather than leaving it stale/unset.
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")
    fake_response = _fake_gemini_response(
        duration_seconds=5.0, segments=[{"start_seconds": 0.0, "text": "hi"}]
    )

    with patch("ai_clients.httpx.post", return_value=fake_response):
        client = GeminiTranscriptionClient(api_key="sk-gemini-test")
        client.transcribe_chunk(chunk)

    assert client.last_usage == {
        "provider": "gemini",
        "model": GEMINI_MODEL,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "audio_seconds": None,
    }


# -- Chat (grounded Q&A over a transcript) ---------------------------------


def test_anthropic_chat_puts_transcript_in_system_and_history_in_messages():
    fake_response = FakeResponse(
        200,
        {
            "content": [{"type": "text", "text": "The answer is 42."}],
            "usage": {"input_tokens": 120, "output_tokens": 8},
        },
    )
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]

    with patch("ai_clients.httpx.post", return_value=fake_response) as mock_post:
        client = AnthropicClient(api_key="sk-ant-test")
        result = client.chat("the transcript text", "new question", history)

    assert result == "The answer is 42."
    body = mock_post.call_args.kwargs["json"]
    # Transcript rides in the top-level system field, not a conversation turn.
    assert "the transcript text" in body["system"]
    assert body["messages"] == [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "new question"},
    ]
    assert mock_post.call_args.kwargs["headers"]["x-api-key"] == "sk-ant-test"
    assert client.last_usage == {
        "provider": "anthropic",
        "model": ANTHROPIC_MODEL,
        "input_tokens": 120,
        "output_tokens": 8,
        "total_tokens": 128,
        "audio_seconds": None,
    }


def test_chat_with_no_history_sends_only_the_new_question():
    fake_response = FakeResponse(200, {"content": [{"type": "text", "text": "ok"}], "usage": {}})

    with patch("ai_clients.httpx.post", return_value=fake_response) as mock_post:
        client = AnthropicClient(api_key="sk-ant-test")
        client.chat("the transcript text", "just this question")

    assert mock_post.call_args.kwargs["json"]["messages"] == [
        {"role": "user", "content": "just this question"}
    ]


def test_anthropic_chat_raises_ai_client_error_on_500():
    with patch("ai_clients.httpx.post", return_value=FakeResponse(500, text="server error")):
        client = AnthropicClient(api_key="sk-ant-test")
        with pytest.raises(AIClientError) as exc_info:
            client.chat("transcript", "question")

    assert exc_info.value.provider == "anthropic"
    assert exc_info.value.status_code == 500


def test_anthropic_chat_raises_ai_client_error_on_network_failure():
    with patch("ai_clients.httpx.post", side_effect=httpx.ConnectError("boom")):
        client = AnthropicClient(api_key="sk-ant-test")
        with pytest.raises(AIClientError) as exc_info:
            client.chat("transcript", "question")

    assert exc_info.value.provider == "anthropic"
    assert exc_info.value.status_code is None


@pytest.mark.parametrize(
    "client_cls, provider, model",
    [
        (OpenAISummaryClient, "openai", OPENAI_SUMMARY_MODEL),
        (OpenRouterSummaryClient, "openrouter", OPENROUTER_SUMMARY_MODEL),
    ],
)
def test_chat_completion_chat_builds_system_plus_history_messages(client_cls, provider, model):
    fake_response = FakeResponse(
        200,
        {
            "choices": [{"message": {"content": "A grounded answer."}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 10, "total_tokens": 210},
        },
    )
    history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]

    with patch("ai_clients.httpx.post", return_value=fake_response) as mock_post:
        client = client_cls(api_key="sk-test")
        result = client.chat("the transcript text", "q2", history)

    assert result == "A grounded answer."
    messages = mock_post.call_args.kwargs["json"]["messages"]
    assert messages[0]["role"] == "system"
    assert "the transcript text" in messages[0]["content"]
    assert messages[1:] == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert client.last_usage == {
        "provider": provider,
        "model": model,
        "input_tokens": 200,
        "output_tokens": 10,
        "total_tokens": 210,
        "audio_seconds": None,
    }


def test_gemini_chat_maps_assistant_role_to_model_and_uses_system_instruction():
    fake_response = FakeResponse(
        200,
        {
            "candidates": [{"content": {"parts": [{"text": "A gemini answer."}]}}],
            "usageMetadata": {
                "promptTokenCount": 90,
                "candidatesTokenCount": 6,
                "totalTokenCount": 96,
            },
        },
    )
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]

    with patch("ai_clients.httpx.post", return_value=fake_response) as mock_post:
        client = GeminiSummaryClient(api_key="sk-gemini-test")
        result = client.chat("the transcript text", "q2", history)

    assert result == "A gemini answer."
    body = mock_post.call_args.kwargs["json"]
    # Transcript grounding rides in systemInstruction, not a content turn.
    assert "the transcript text" in body["systemInstruction"]["parts"][0]["text"]
    # Gemini's assistant role is literally "model", not "assistant".
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["contents"][0]["parts"][0]["text"] == "q1"
    assert body["contents"][1]["parts"][0]["text"] == "a1"
    assert body["contents"][2]["parts"][0]["text"] == "q2"
    assert mock_post.call_args.kwargs["headers"]["x-goog-api-key"] == "sk-gemini-test"
    assert client.last_usage == {
        "provider": "gemini",
        "model": GEMINI_MODEL,
        "input_tokens": 90,
        "output_tokens": 6,
        "total_tokens": 96,
        "audio_seconds": None,
    }


def test_gemini_chat_raises_ai_client_error_on_malformed_response_shape():
    with patch("ai_clients.httpx.post", return_value=FakeResponse(200, {"candidates": []})):
        client = GeminiSummaryClient(api_key="sk-gemini-test")
        with pytest.raises(AIClientError) as exc_info:
            client.chat("transcript", "question")

    assert exc_info.value.provider == "gemini"


def test_chat_last_usage_starts_none_before_any_call():
    assert AnthropicClient(api_key="x").last_usage is None
    assert OpenAISummaryClient(api_key="x").last_usage is None
    assert GeminiSummaryClient(api_key="x").last_usage is None
    assert OpenRouterSummaryClient(api_key="x").last_usage is None
