from unittest.mock import patch

import httpx
import pytest

from ai_clients import AIClientError, AnthropicClient, OpenAIWhisperClient


class FakeResponse:
    def __init__(self, status_code, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = text or str(json_body)

    def json(self):
        return self._json_body


def test_transcribe_chunk_returns_parsed_json_on_success(tmp_path):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")
    fake_response = FakeResponse(200, {"text": "hello world", "duration": 12.5, "segments": []})

    with patch("ai_clients.httpx.post", return_value=fake_response) as mock_post:
        client = OpenAIWhisperClient(api_key="sk-test")
        result = client.transcribe_chunk(chunk)

    assert result["text"] == "hello world"
    assert result["duration"] == 12.5
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"


def test_transcribe_chunk_raises_ai_client_error_with_status_on_401(tmp_path):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")
    fake_response = FakeResponse(401, text="invalid_api_key")

    with patch("ai_clients.httpx.post", return_value=fake_response):
        client = OpenAIWhisperClient(api_key="bad-key")
        with pytest.raises(AIClientError) as exc_info:
            client.transcribe_chunk(chunk)

    assert exc_info.value.provider == "openai"
    assert exc_info.value.status_code == 401


def test_transcribe_chunk_raises_ai_client_error_on_network_failure(tmp_path):
    chunk = tmp_path / "chunk_0000.mp3"
    chunk.write_bytes(b"fake audio bytes")

    with patch("ai_clients.httpx.post", side_effect=httpx.ConnectError("boom")):
        client = OpenAIWhisperClient(api_key="sk-test")
        with pytest.raises(AIClientError) as exc_info:
            client.transcribe_chunk(chunk)

    assert exc_info.value.provider == "openai"
    assert exc_info.value.status_code is None


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
