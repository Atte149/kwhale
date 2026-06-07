"""Subsonic protocol proxy.

The Flutter client (Musly fork) speaks OpenSubsonic for everything except
auth, which means search / cover art / scrobble / playlists all hit
`/rest/<endpoint>?...` against whatever base URL the user configured.

When the user configures `serverUrl = https://music.dueattendant149.org`
the request is routed by Caddy to Navidrome directly. But when the user
configures a LAN address like `http://192.168.1.119:19000` (the API
container), there is no subsonic handler there — so the client gets
empty/404 responses and search appears broken.

This router makes the kwhale API itself speak Subsonic on the same path
(`/rest/*`) so the client can use ANY base URL. Audio bytes are
deliberately NOT proxied here: the existing `/api/stream/{id}` 302
already short-circuits to the public Navidrome URL (where Caddy serves
audio directly), and streaming through the API process would waste
bandwidth. Endpoints that return audio bodies are excluded from this
router.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response

from ..config import settings

router = APIRouter(prefix="/rest", tags=["subsonic"])

# Audio-body endpoints: never proxy through us. The client should follow
# the 302 from /api/stream/ instead. Returning a 400 here surfaces the
# misconfiguration immediately rather than silently dropping the request.
STREAMING_ENDPOINTS = frozenset({
    "stream.view",
    "download.view",
    "scrobble.view",  # clients POST huge play-count updates; let Caddy handle
})


@router.api_route("/{endpoint:path}", methods=["GET", "POST"])
async def proxy_subsonic(endpoint: str, request: Request) -> Response:
    """Forward any /rest/<endpoint> to Navidrome and relay the response.

    Query string and form body are passed through verbatim so the
    Subsonic auth parameters (u, t, s, v) reach Navidrome untouched.
    Streaming endpoints are rejected with 400 so the client gets a clear
    "use /api/stream/" hint instead of a hung connection.
    """
    if endpoint in STREAMING_ENDPOINTS:
        return Response(
            content=(
                f"Use /api/stream/{{id}} for audio. The '{endpoint}' endpoint "
                "is not proxied through the kwhale API."
            ),
            status_code=400,
            media_type="text/plain",
        )

    # Build the upstream URL preserving all original query params.
    upstream = f"{settings.navidrome_url}/rest/{endpoint}"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"

    # Read body — works with both FastAPI Request (has .body()) and a
    # raw httpx Request (has .content). Tests use httpx.Request; the
    # production FastAPI handler passes a starlette Request.
    if hasattr(request, "body") and callable(request.body) and not isinstance(request, httpx.Request):
        body = await request.body()
    else:
        body = getattr(request, "content", b"") or b""

    # Forward headers except Host (httpx will set its own) and the
    # Content-Length we just consumed (httpx recomputes it).
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in {"host", "content-length"}
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.request(
                method=request.method,
                url=upstream,
                headers=fwd_headers,
                content=body if body else None,
            )
        except httpx.RequestError as e:
            return Response(
                content=f"Upstream Navidrome error: {e}",
                status_code=502,
                media_type="text/plain",
            )

    # Relay the upstream response verbatim. We deliberately do NOT add
    # CORS headers here — the global CORSMiddleware already allows
    # everything for the kwhale API. Navidrome's own CORS is irrelevant
    # because the response is now from our origin.
    return Response(
        content=r.content,
        status_code=r.status_code,
        headers={
            k: v for k, v in r.headers.items()
            if k.lower() not in {"transfer-encoding", "content-encoding", "content-length"}
        },
        media_type=r.headers.get("content-type"),
    )
