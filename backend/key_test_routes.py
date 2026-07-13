import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ai_clients import (
    ANTHROPIC_API_VERSION,
    PROVIDER_API_KEY_SETTINGS,
    PROVIDER_DISPLAY_NAMES,
)

logger = logging.getLogger("clippull")

# A key-validation check just confirms auth works, so it should be fast --
# a much shorter timeout than ai_clients.REQUEST_TIMEOUT_SECONDS, which
# covers real transcription/summarization work.
KEY_TEST_TIMEOUT_SECONDS = 10.0

# Standard "list available models" endpoints for each provider -- cheap,
# free, read-only, and consume no tokens, so they're ideal for confirming a
# key authenticates without doing any real work.
#
# openrouter is the one exception: /api/v1/models is fully public (returns
# 200 even with no Authorization header at all), so it can't distinguish a
# real key from a bogus one -- every key would show "valid". /api/v1/key is
# openrouter's own auth-gated "who am I" endpoint (401s on a bad key), so
# that's used for openrouter instead of the shared /models pattern.
KEY_TEST_MODEL_LIST_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "openai": "https://api.openai.com/v1/models",
    "groq": "https://api.groq.com/openai/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/key",
}

# Which HTTP status codes mean "the provider rejected this key" for each
# provider, as opposed to some other unexpected response. 401/403 are the
# conventional codes and cover every provider here -- except Gemini, which
# responds to an invalid key with 400 (a malformed-request code everywhere
# else), so it needs its own explicit rejection set to avoid reporting a bad
# key as a vague "unexpected response" that reads like a transient glitch.
REJECTED_KEY_STATUS_CODES = {
    "gemini": {400, 401, 403},
    "anthropic": {401, 403},
    "openai": {401, 403},
    "groq": {401, 403},
    "openrouter": {401, 403},
}


class TestKeyRequest(BaseModel):
    provider: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)


def _auth_headers(provider: str, api_key: str) -> dict:
    # Mirrors each provider's real client class in ai_clients.py exactly, so a
    # key that passes this check authenticates the same way real jobs do.
    if provider == "gemini":
        return {"x-goog-api-key": api_key}
    if provider == "anthropic":
        return {"x-api-key": api_key, "anthropic-version": ANTHROPIC_API_VERSION}
    # openai, groq, and openrouter all use OpenAI-style bearer auth.
    return {"Authorization": f"Bearer {api_key}"}


def build_key_test_router() -> APIRouter:
    # No store dependency -- this is a stateless passthrough check that neither
    # reads nor writes settings, so nothing needs to be injected.
    router = APIRouter()

    @router.post("/settings/test-key")
    def test_key(request: TestKeyRequest) -> dict:
        provider = request.provider
        if provider not in PROVIDER_API_KEY_SETTINGS:
            raise HTTPException(status_code=400, detail="Unknown provider.")

        display_name = PROVIDER_DISPLAY_NAMES[provider]
        url = KEY_TEST_MODEL_LIST_URLS[provider]

        # The submitted api_key lives only in the outbound request headers for
        # this one call -- it is never logged or persisted anywhere. Any log
        # line below names the provider and status code only, never the key.
        try:
            response = httpx.get(
                url,
                headers=_auth_headers(provider, request.api_key),
                timeout=KEY_TEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            # We couldn't even attempt the check (DNS/timeout/connection
            # refused) -- distinct from "we asked and the key was rejected".
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Couldn't reach {display_name} to test this key. "
                    "Check your connection and try again."
                ),
            )
        except Exception:
            # Belt-and-suspenders: no single provider's request should ever
            # crash the route. Worst case, report an unverifiable result.
            logger.warning("Unexpected error testing a %s API key.", provider)
            return {
                "valid": False,
                "detail": f"Couldn't verify the key right now (unexpected response from {display_name}).",
            }

        status_code = response.status_code
        if 200 <= status_code < 300:
            return {"valid": True}
        if status_code in REJECTED_KEY_STATUS_CODES[provider]:
            # A normal, expected outcome the frontend needs to display -- not an
            # HTTP error on our own route.
            logger.info("%s rejected a test key (status %s).", provider, status_code)
            return {
                "valid": False,
                "detail": f"That key was rejected by {display_name}. Double-check it's correct.",
            }
        logger.info("Unexpected status %s from %s while testing a key.", status_code, provider)
        return {
            "valid": False,
            "detail": f"Couldn't verify the key right now (unexpected response from {display_name}).",
        }

    return router
