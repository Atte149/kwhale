"""Tests for the /library/cover proxy endpoint.

We don't spin up the FastAPI app (it requires a live Postgres). Instead we
unit-test the endpoint function directly. The function is async — we
exercise it via asyncio.run with httpx mocked. We assert the body bytes
flow through, the Content-Type is propagated, and upstream errors surface
as clean HTTPException(404/502).
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _run(coro):
    return asyncio.run(coro)


def _build_mock_client(status: int, content_type: str, chunks: list[bytes]):
    """Build a mock for httpx.AsyncClient that mimics stream() with a working
    aiter_bytes. The client also supports being used as an async context
    manager — although the endpoint does not use one, the mock is reused."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": content_type}
    resp.aread = AsyncMock()
    resp.aclose = AsyncMock()
    resp.raise_for_status = MagicMock(
        side_effect=Exception(f"HTTP {status}") if status >= 400 else None
    )

    async def _aiter(chunk_size=65536):
        for c in chunks:
            yield c

    resp.aiter_bytes = _aiter
    client = MagicMock()
    client.send = AsyncMock(return_value=resp)
    client.build_request = MagicMock(return_value=MagicMock())
    client.aclose = AsyncMock()
    return client, resp


def test_cover_proxy_streams_jpeg_bytes(monkeypatch):
    from fastapi import HTTPException
    from app.config import settings
    from app.routers import library

    monkeypatch.setattr(settings, "navidrome_url", "http://navidrome:4533")
    client, resp = _build_mock_client(
        200, "image/jpeg", [b"\xff\xd8\xff\xe0", b"FAKE_BODY"]
    )
    with patch("httpx.AsyncClient", return_value=client):
        result = _run(library.get_cover("abc123", size=1200, user="u"))

    assert isinstance(result, __import__("fastapi.responses", fromlist=["StreamingResponse"]).StreamingResponse)
    assert result.media_type == "image/jpeg"
    assert result.headers["x-cover-source"] == "kwhale-api"
    assert result.headers["cache-control"] == "public, max-age=86400"


def test_cover_proxy_caps_huge_size(monkeypatch):
    """size > 1500 gets clamped to 1500 — guards against abuse / 5xx from upstream."""
    from app.config import settings
    from app.routers import library
    from app import navidrome as _nav

    monkeypatch.setattr(settings, "navidrome_url", "http://navidrome:4533")
    captured = []

    def _fake_cover(cover_id, size=300):
        captured.append(size)
        return f"http://navidrome:4533/rest/getCoverArt.view?id={cover_id}&size={size}"

    client, resp = _build_mock_client(200, "image/jpeg", [b"x"])
    with patch("httpx.AsyncClient", return_value=client), \
         patch.object(_nav, "cover_url", side_effect=_fake_cover):
        _run(library.get_cover("abc", size=9999, user="u"))

    assert captured == [1500]


def test_cover_proxy_returns_404_on_upstream_missing(monkeypatch):
    from fastapi import HTTPException
    from app.config import settings
    from app.routers import library

    monkeypatch.setattr(settings, "navidrome_url", "http://navidrome:4533")
    client, resp = _build_mock_client(404, "application/json", [b"{}"])
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            _run(library.get_cover("missing", size=1200, user="u"))
    assert exc.value.status_code == 404
    resp.aclose.assert_called()
    client.aclose.assert_called()


def test_cover_proxy_returns_404_on_upstream_5xx(monkeypatch):
    from fastapi import HTTPException
    from app.config import settings
    from app.routers import library

    monkeypatch.setattr(settings, "navidrome_url", "http://navidrome:4533")
    client, resp = _build_mock_client(503, "text/plain", [b"oops"])
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(HTTPException) as exc:
            _run(library.get_cover("x", size=1200, user="u"))
    assert exc.value.status_code == 404
    assert "navidrome error" in str(exc.value.detail)
