"""Tests for the shared LLM client: retry on transient errors, fail-fast on
client errors, and correct message extraction.

Uses respx to mock the HTTP layer; backoff sleeps are patched to no-ops so the
suite stays fast.

Run: pytest worker/tests/test_llm_client.py -v
"""
import sys
from pathlib import Path

import httpx
import pytest
import respx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm_client
from app.llm_client import chat_completion, LLMError

_URL = f"{llm_client.OPENAI_API_BASE}/chat/completions"

_OK_BODY = {"choices": [{"message": {"content": "hello", "role": "assistant"}}]}


@pytest.fixture(autouse=True)
def _fast_and_keyed(monkeypatch):
    # No real sleeping; ensure a key is present and reset the shared client.
    monkeypatch.setattr(llm_client.time, "sleep", lambda *_: None)
    monkeypatch.setattr(llm_client, "OPENAI_API_KEY", "test-key")
    llm_client.close_client()
    yield
    llm_client.close_client()


@respx.mock
def test_success_returns_message():
    respx.post(_URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    msg = chat_completion([{"role": "user", "content": "hi"}], model="m")
    assert msg["content"] == "hello"


@respx.mock
def test_retries_on_429_then_succeeds():
    route = respx.post(_URL)
    route.side_effect = [
        httpx.Response(429, text="rate limited"),
        httpx.Response(200, json=_OK_BODY),
    ]
    msg = chat_completion([{"role": "user", "content": "hi"}], model="m")
    assert msg["content"] == "hello"
    assert route.call_count == 2


@respx.mock
def test_retries_on_500_then_succeeds():
    route = respx.post(_URL)
    route.side_effect = [
        httpx.Response(500, text="boom"),
        httpx.Response(503, text="still boom"),
        httpx.Response(200, json=_OK_BODY),
    ]
    msg = chat_completion([{"role": "user", "content": "hi"}], model="m")
    assert msg["content"] == "hello"
    assert route.call_count == 3


@respx.mock
def test_fails_fast_on_400():
    route = respx.post(_URL).mock(return_value=httpx.Response(400, text="bad request"))
    with pytest.raises(LLMError) as exc:
        chat_completion([{"role": "user", "content": "hi"}], model="m")
    assert "400" in str(exc.value)
    # No retry on a non-retryable client error.
    assert route.call_count == 1


@respx.mock
def test_exhausts_retries_and_raises():
    respx.post(_URL).mock(return_value=httpx.Response(503, text="down"))
    with pytest.raises(LLMError):
        chat_completion([{"role": "user", "content": "hi"}], model="m")


@respx.mock
def test_retries_on_timeout():
    route = respx.post(_URL)
    route.side_effect = [
        httpx.ConnectTimeout("timeout"),
        httpx.Response(200, json=_OK_BODY),
    ]
    msg = chat_completion([{"role": "user", "content": "hi"}], model="m")
    assert msg["content"] == "hello"
    assert route.call_count == 2


@respx.mock
def test_unexpected_shape_raises():
    respx.post(_URL).mock(return_value=httpx.Response(200, json={"nope": 1}))
    with pytest.raises(LLMError):
        chat_completion([{"role": "user", "content": "hi"}], model="m")


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(llm_client, "OPENAI_API_KEY", "")
    with pytest.raises(LLMError):
        chat_completion([{"role": "user", "content": "hi"}], model="m")


@respx.mock
def test_tools_payload_included():
    captured = {}

    def _handler(request):
        import json as _json
        captured.update(_json.loads(request.content))
        return httpx.Response(200, json=_OK_BODY)

    respx.post(_URL).mock(side_effect=_handler)
    chat_completion(
        [{"role": "user", "content": "hi"}],
        model="m",
        tools=[{"type": "function", "function": {"name": "f"}}],
    )
    assert "tools" in captured
    assert captured["tool_choice"] == "auto"
