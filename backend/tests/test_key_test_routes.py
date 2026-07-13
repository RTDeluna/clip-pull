from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_clients import ANTHROPIC_API_VERSION
from key_test_routes import build_key_test_router

# A key the tests submit -- asserted to never leak into any response body.
SECRET_KEY = "sk-super-secret-value-12345"


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def _make_client():
    app = FastAPI()
    app.include_router(build_key_test_router())
    return TestClient(app)


def test_unknown_provider_returns_400():
    client = _make_client()
    response = client.post("/settings/test-key", json={"provider": "notreal", "api_key": SECRET_KEY})
    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown provider."


def test_empty_fields_are_rejected_with_422():
    client = _make_client()
    assert client.post("/settings/test-key", json={"provider": "", "api_key": SECRET_KEY}).status_code == 422
    assert client.post("/settings/test-key", json={"provider": "openai", "api_key": ""}).status_code == 422


@pytest.mark.parametrize(
    "provider",
    ["gemini", "anthropic", "openai", "groq", "openrouter"],
)
def test_valid_key_returns_valid_true(provider):
    client = _make_client()
    with patch("key_test_routes.httpx.get", return_value=FakeResponse(200)) as mock_get:
        response = client.post("/settings/test-key", json={"provider": provider, "api_key": SECRET_KEY})

    assert response.status_code == 200
    assert response.json() == {"valid": True}
    # Exactly one cheap, read-only GET was made to test the key.
    assert mock_get.call_count == 1


@pytest.mark.parametrize(
    "provider, expected_header, expected_value",
    [
        ("gemini", "x-goog-api-key", SECRET_KEY),
        ("anthropic", "x-api-key", SECRET_KEY),
        ("openai", "Authorization", f"Bearer {SECRET_KEY}"),
        ("groq", "Authorization", f"Bearer {SECRET_KEY}"),
        ("openrouter", "Authorization", f"Bearer {SECRET_KEY}"),
    ],
)
def test_uses_each_providers_real_auth_header_style(provider, expected_header, expected_value):
    client = _make_client()
    with patch("key_test_routes.httpx.get", return_value=FakeResponse(200)) as mock_get:
        client.post("/settings/test-key", json={"provider": provider, "api_key": SECRET_KEY})

    headers = mock_get.call_args.kwargs["headers"]
    assert headers[expected_header] == expected_value
    if provider == "anthropic":
        assert headers["anthropic-version"] == ANTHROPIC_API_VERSION


@pytest.mark.parametrize("status_code", [401, 403])
def test_rejected_key_returns_valid_false_with_200(status_code):
    client = _make_client()
    with patch("key_test_routes.httpx.get", return_value=FakeResponse(status_code)):
        response = client.post("/settings/test-key", json={"provider": "openai", "api_key": SECRET_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert "rejected" in body["detail"].lower()
    assert "OpenAI" in body["detail"]
    # The submitted key must never be echoed back to the caller.
    assert SECRET_KEY not in response.text


def test_gemini_400_is_treated_as_a_rejected_key():
    # Gemini uniquely responds to an invalid key with 400 (everywhere else
    # 400 means "malformed request") -- verified live against the real API.
    # Without special-casing this, a bad Gemini key fell into the generic
    # "unexpected response" bucket and read like a transient glitch instead
    # of "this key is wrong".
    client = _make_client()
    with patch("key_test_routes.httpx.get", return_value=FakeResponse(400)):
        response = client.post("/settings/test-key", json={"provider": "gemini", "api_key": SECRET_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert "rejected" in body["detail"].lower()
    assert "Gemini" in body["detail"]


def test_openai_400_is_not_treated_as_a_rejected_key():
    # Confirms the Gemini-specific 400 handling didn't leak into the other
    # providers, where 400 really does mean "unexpected response".
    client = _make_client()
    with patch("key_test_routes.httpx.get", return_value=FakeResponse(400)):
        response = client.post("/settings/test-key", json={"provider": "openai", "api_key": SECRET_KEY})

    body = response.json()
    assert body["valid"] is False
    assert "couldn't verify" in body["detail"].lower()


def test_openrouter_tests_against_the_auth_gated_key_endpoint():
    # openrouter's /models endpoint is fully public (200s even with no auth
    # at all -- verified live), so it can never tell a real key from a bogus
    # one. /key is openrouter's auth-gated endpoint and must be used instead.
    client = _make_client()
    with patch("key_test_routes.httpx.get", return_value=FakeResponse(200)) as mock_get:
        client.post("/settings/test-key", json={"provider": "openrouter", "api_key": SECRET_KEY})

    called_url = mock_get.call_args.args[0]
    assert called_url.endswith("/key")
    assert not called_url.endswith("/models")


def test_unexpected_status_returns_valid_false_without_crashing():
    client = _make_client()
    with patch("key_test_routes.httpx.get", return_value=FakeResponse(500)):
        response = client.post("/settings/test-key", json={"provider": "groq", "api_key": SECRET_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert "couldn't verify" in body["detail"].lower()
    assert SECRET_KEY not in response.text


def test_network_failure_returns_503():
    client = _make_client()
    with patch("key_test_routes.httpx.get", side_effect=httpx.ConnectError("boom")):
        response = client.post("/settings/test-key", json={"provider": "gemini", "api_key": SECRET_KEY})

    assert response.status_code == 503
    body = response.json()
    assert "couldn't reach" in body["detail"].lower()
    assert "Gemini" in body["detail"]
    # A network error must never leak the submitted key (e.g. via an exception
    # message getting reflected into the response).
    assert SECRET_KEY not in response.text
