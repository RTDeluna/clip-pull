import json
from unittest.mock import patch

import httpx
import pytest

from ai_clients import (
    AIClientError,
    AnthropicClient,
    GEMINI_TRANSCRIPTION_MODEL,
    GeminiTranscriptionClient,
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
    assert call.args[0] == f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TRANSCRIPTION_MODEL}:generateContent"
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
