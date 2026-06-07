"""Shared LLM client for the worker — chat completions with retries.

Centralises every OpenAI-compatible call the worker makes (vibe tags, the
multimodal spectrogram description) behind one function that:

  * reuses a module-level httpx.Client (no per-call socket churn);
  * retries transient failures (429, 5xx, timeouts, connection resets) with
    exponential backoff + jitter;
  * raises on non-retryable client errors (4xx other than 429) so bugs surface;
  * returns the assistant message content as a string.

It deliberately stays OpenAI-wire-format so it works against the project's
OPENAI_API_BASE (which may be a self-hosted gateway) without an SDK dependency.
"""
from __future__ import annotations

import os
import time
import random
import threading
from typing import Any

import httpx

OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
USER_AGENT = "melorise/1.0"

# Retry policy
_MAX_ATTEMPTS = int(os.environ.get("LLM_MAX_ATTEMPTS", "4"))
_BASE_DELAY = float(os.environ.get("LLM_RETRY_BASE_DELAY", "1.0"))
_MAX_DELAY = float(os.environ.get("LLM_RETRY_MAX_DELAY", "20.0"))
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# A single shared client. httpx.Client is thread-safe for issuing requests, so
# Celery's prefork/threaded workers can share it. Guarded for lazy init.
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _get_client(timeout: float) -> httpx.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=httpx.Timeout(timeout, connect=10.0),
                    headers={"User-Agent": USER_AGENT},
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                )
    return _client


def close_client() -> None:
    """Close the shared client (call on worker shutdown)."""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


def _sleep_for_attempt(attempt: int, retry_after: float | None = None) -> None:
    """Exponential backoff with full jitter, honouring Retry-After if given."""
    if retry_after is not None:
        time.sleep(min(retry_after, _MAX_DELAY))
        return
    delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
    time.sleep(random.uniform(0, delay))


class LLMError(Exception):
    """Raised when an LLM call fails after exhausting retries, or on a
    non-retryable client error."""


def chat_completion(
    messages: list[dict],
    model: str,
    *,
    max_tokens: int = 512,
    temperature: float | None = None,
    response_format: dict | None = None,
    tools: list[dict] | None = None,
    tool_choice: Any | None = None,
    timeout: float = 60.0,
) -> dict:
    """Call the chat/completions endpoint and return the assistant message dict.

    Returns the raw `choices[0].message` object so callers can read `content`
    and/or `tool_calls`. Raises LLMError if the key is missing, the response
    shape is unexpected, or all retries are exhausted.
    """
    if not OPENAI_API_KEY:
        raise LLMError("OPENAI_API_KEY is not set")

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if response_format is not None:
        payload["response_format"] = response_format
    if tools is not None:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"

    client = _get_client(timeout)
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    url = f"{OPENAI_API_BASE}/chat/completions"

    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            r = client.post(url, headers=headers, json=payload, timeout=timeout)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_exc = e
            if attempt < _MAX_ATTEMPTS - 1:
                _sleep_for_attempt(attempt)
                continue
            raise LLMError(f"transport error after {_MAX_ATTEMPTS} attempts: {e}") from e

        if r.status_code in _RETRYABLE_STATUS:
            last_exc = LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
            if attempt < _MAX_ATTEMPTS - 1:
                retry_after = _parse_retry_after(r.headers.get("Retry-After"))
                _sleep_for_attempt(attempt, retry_after)
                continue
            raise last_exc

        if r.status_code >= 400:
            # Non-retryable client error — surface immediately with context.
            raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")

        try:
            data = r.json()
            return data["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"unexpected response shape: {e}; body={r.text[:300]}") from e

    raise LLMError(f"exhausted retries: {last_exc}")


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
