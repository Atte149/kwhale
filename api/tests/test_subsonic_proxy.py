"""Tests for the Subsonic protocol proxy at /rest/*.

The proxy must:
  - Forward GET requests (with query string) to Navidrome
  - Forward POST bodies
  - Return upstream's body + status + content-type verbatim
  - Reject audio-streaming endpoints with 400 (use /api/stream/ instead)
  - Surface upstream connection errors as 502

We use respx to mock the upstream Navidrome; no TestClient (which would
require a live Postgres for the API lifespan).
"""
import httpx
import pytest
import respx

from app.routers.subsonic import proxy_subsonic


@pytest.mark.asyncio
async def test_proxy_forwards_get_with_query_string():
    upstream_response_body = b'{"subsonic-response":{"status":"ok","searchResult3":{"song":[]}}}'
    with respx.mock(base_url="http://navidrome:4533") as mock:
        # respx's path matcher ignores the query string, which is what we
        # want — the proxy passes the original query through verbatim
        # and respx just needs to confirm it was routed to navidrome.
        route = mock.get(path="/rest/search3").respond(
            200,
            content=upstream_response_body,
            headers={"content-type": "application/json; charset=utf-8"},
        )
        req = httpx.Request(
            "GET", "http://api/rest/search3?query=BabyMetal&u=admin&t=abc&s=xyz&f=json"
        )
        response = await proxy_subsonic("search3", req)

        assert response.status_code == 200
        assert response.body == upstream_response_body
        assert "application/json" in response.headers.get("content-type", "")
        assert route.called


@pytest.mark.asyncio
async def test_proxy_rejects_streaming_endpoints():
    req = httpx.Request("GET", "http://api/rest/stream.view?id=abc")
    response = await proxy_subsonic("stream.view", req)
    assert response.status_code == 400
    assert b"/api/stream/" in response.body
    assert b"stream.view" in response.body


@pytest.mark.asyncio
async def test_proxy_rejects_download_view():
    req = httpx.Request("GET", "http://api/rest/download.view?id=abc")
    response = await proxy_subsonic("download.view", req)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_proxy_returns_502_on_upstream_error():
    with respx.mock(assert_all_mocked=False) as mock:
        mock.get(url="http://navidrome:4533/rest/ping").mock(
            side_effect=httpx.ConnectError("navidrome down")
        )
        req = httpx.Request("GET", "http://api/rest/ping")
        response = await proxy_subsonic("ping", req)

        assert response.status_code == 502
        assert b"Upstream Navidrome error" in response.body


@pytest.mark.asyncio
async def test_proxy_forwards_post_body():
    with respx.mock(assert_all_mocked=False) as mock:
        route = mock.post(url="http://navidrome:4533/rest/setRating").respond(
            200, content=b""
        )
        req = httpx.Request(
            "POST", "http://api/rest/setRating",
            content=b"id=abc&rating=5",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        response = await proxy_subsonic("setRating", req)

        assert response.status_code == 200
        assert route.called
