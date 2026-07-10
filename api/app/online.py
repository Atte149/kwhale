"""Async clients for online music catalogs (ICM, Yandex) used by the API process.

Search philosophy (see STABILIZATION_PLAN.md, search v2):
  * called directly from FastAPI (no Celery round-trip) with short timeouts;
  * never resolves stream URLs during search (that is lazy, on play/acquire);
  * results are cached in the source_cache table (metadata only, no signed URLs).

The worker keeps its own sync providers for downloading; this module is only
for browse/search/metadata, so the two don't share code on purpose — each is
small enough to read in one screen.
"""
import hashlib
import json
import os
from typing import Any

import httpx

from .config import settings
from .db import get_pool

ICM_BASE = os.environ.get("ICM_BASE_URL", "https://byicloud.online").rstrip("/")
YANDEX_TOKEN = os.environ.get("YANDEX_MUSIC_TOKEN", "")
SEARCH_CACHE_TTL_HOURS = 6

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0))
    return _client


# ── source_cache helpers ──────────────────────────────────────────────────────
async def _cache_get(key: str) -> Any | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT result_json FROM source_cache WHERE cache_key=$1 AND expires_at > NOW()",
        key,
    )
    if not row:
        return None
    val = row["result_json"]
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


async def _cache_put(key: str, provider: str, value: Any,
                     ttl_hours: int = SEARCH_CACHE_TTL_HOURS) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO source_cache (cache_key, provider, result_json, expires_at)
        VALUES ($1, $2, $3, NOW() + make_interval(hours => $4))
        ON CONFLICT (cache_key) DO UPDATE
        SET result_json = EXCLUDED.result_json, expires_at = EXCLUDED.expires_at
        """,
        key, provider, json.dumps(value), ttl_hours,
    )


def _key(*parts: str) -> str:
    return hashlib.sha256("::".join(parts).encode()).hexdigest()


# ── ICM (byicloud.online Partner API) ────────────────────────────────────────
def _icm_headers() -> dict:
    return {"X-Partner-Key": settings.icm_partner_key}


def icm_available() -> bool:
    return bool(settings.icm_partner_key)


async def icm_search(q: str, limit: int = 20) -> dict:
    """Search ICM. Returns {"tracks": [...], "albums": [...], "artists": [...]}.

    ICM mixes the three entity types in one `items` list, flagged with
    isArtist / isAlbum. source=all also folds VK results in.
    """
    empty = {"tracks": [], "albums": [], "artists": []}
    if not icm_available():
        return empty
    key = _key("icm.search", q, str(limit))
    cached = await _cache_get(key)
    if cached is not None:
        return cached

    r = await _http().get(
        f"{ICM_BASE}/api/partner/search",
        params={"q": q, "region": settings.icm_default_region, "limit": limit},
        headers=_icm_headers(),
        timeout=12.0,  # cold ICM searches can exceed the default client timeout
    )
    r.raise_for_status()
    items = r.json().get("items", [])

    out = {"tracks": [], "albums": [], "artists": []}
    for it in items:
        if it.get("isArtist"):
            out["artists"].append({
                "source": "icm",
                "artist_id": it.get("artistId") or it.get("id"),
                "name": it.get("artistName") or it.get("title"),
                "coverUrl": it.get("cover"),
            })
        elif it.get("isAlbum"):
            out["albums"].append({
                "source": "icm",
                "album_id": it.get("collectionId") or it.get("id"),
                "title": it.get("title"),
                "artist": it.get("artist"),
                "coverUrl": it.get("cover"),
            })
        else:
            out["tracks"].append({
                "source": "icm",
                "provider": "icm",
                "provider_id": it.get("id"),
                "title": it.get("title"),
                "artist": it.get("artist"),
                "album": it.get("album"),
                "album_id": it.get("collectionId"),
                "coverUrl": it.get("cover"),
                "previewUrl": it.get("preview"),
                "explicit": bool(it.get("is_explicit")),
            })
    await _cache_put(key, "icm", out)
    return out


async def icm_album(album_id: str) -> dict | None:
    """Album (or Apple editorial playlist `pl.*`) with full tracklist."""
    key = _key("icm.album", album_id)
    cached = await _cache_get(key)
    if cached is not None:
        return cached
    r = await _http().get(
        f"{ICM_BASE}/api/partner/album/{album_id}",
        params={"region": settings.icm_default_region},
        headers=_icm_headers(),
        timeout=20.0,  # cold album fetches can exceed the default search timeout
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    album = data.get("album", {})
    out = {
        "source": "icm",
        "album_id": album.get("id") or album_id,
        "title": album.get("title"),
        "artist": album.get("artist"),
        "artist_id": album.get("artistId"),
        "coverUrl": album.get("cover"),
        "year": album.get("year"),
        "type": album.get("type"),
        # NB: the real /album response has no trackNumber/duration fields —
        # track order in the list IS the album order (verified live 2026-06).
        "tracks": [
            {
                "source": "icm",
                "provider": "icm",
                "provider_id": t.get("id"),
                "title": t.get("title"),
                "artist": t.get("artist") or album.get("artist"),
                "album": album.get("title"),
                "trackNumber": i + 1,
                "explicit": bool(t.get("is_explicit")),
                "coverUrl": t.get("cover") or album.get("cover"),
            }
            for i, t in enumerate(data.get("tracks", []))
        ],
    }
    await _cache_put(key, "icm", out, ttl_hours=24)
    return out


async def icm_artist(artist_id: str) -> dict | None:
    """Artist page: top tracks, albums, similar artists."""
    key = _key("icm.artist", artist_id)
    cached = await _cache_get(key)
    if cached is not None:
        return cached
    r = await _http().get(
        f"{ICM_BASE}/api/partner/artist/{artist_id}",
        params={"region": settings.icm_default_region},
        headers=_icm_headers(),
        timeout=20.0,  # cold artist fetches can exceed the default search timeout
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()

    def _tracks(lst):
        return [{
            "source": "icm", "provider": "icm",
            "provider_id": t.get("id"),
            "title": t.get("title"), "artist": t.get("artist"),
            "album": t.get("album"), "coverUrl": t.get("cover"),
        } for t in (lst or [])]

    def _albums(lst):
        return [{
            "source": "icm",
            "album_id": a.get("collectionId") or a.get("id"),
            "title": a.get("title"), "artist": a.get("artist"),
            "coverUrl": a.get("cover"), "year": a.get("year"),
        } for a in (lst or [])]

    out = {
        "source": "icm",
        "artist_id": artist_id,
        "name": d.get("name") or d.get("artistName"),
        "coverUrl": d.get("cover"),
        "topTracks": _tracks(d.get("topTracks") or d.get("topSongs")),
        "albums": _albums(d.get("albums")),
        "similarArtists": [
            {"source": "icm", "artist_id": s.get("id") or s.get("artistId"),
             "name": s.get("name") or s.get("title"), "coverUrl": s.get("cover")}
            for s in (d.get("similarArtists") or [])
        ],
    }
    await _cache_put(key, "icm", out, ttl_hours=24)
    return out


async def icm_resolve(track_id: str, quality: str = "256K") -> str | None:
    """Resolve a playable signed stream URL for an ICM track (valid ~10 min).

    Used for listen-before-download. Never cached (signed URLs expire).
    Cold tracks can take a while server-side, hence the generous timeout.

    Handles ICM edge-cases that the simple one-shot call missed:
      * 451 region_unavailable → follow required_region redirect (once)
      * 502 track_download_failed → retry with fallback region
    Both mirror the logic in worker/app/providers/icm.py::_resolve_track.
    """
    tried: set[str] = set()
    regions = [settings.icm_default_region, settings.icm_fallback_region]
    for region in regions:
        if region in tried:
            continue
        tried.add(region)
        try:
            r = await _http().post(
                f"{ICM_BASE}/api/partner/track",
                json={"trackId": track_id,
                      "region": region,
                      "quality": quality},
                headers=_icm_headers(),
                timeout=30.0,
            )
        except httpx.TimeoutException:
            continue
        if r.status_code == 200:
            return r.json().get("url")
        if r.status_code == 451:
            req = (r.json() or {}).get("required_region") or (r.json() or {}).get("detail", {}).get("required_region")
            if req and req not in tried:
                regions.append(req)
            continue
    return None


async def icm_lyrics(track_id: str) -> str | None:
    """Synced LRC lyrics for an ICM track id, or None."""
    r = await _http().get(
        f"{ICM_BASE}/api/partner/track/{track_id}/lyrics",
        params={"region": settings.icm_default_region},
        headers=_icm_headers(),
    )
    if r.status_code != 200:
        return None
    body = r.json() if "json" in (r.headers.get("content-type") or "") else None
    if isinstance(body, dict):
        return body.get("lyrics") or body.get("lrc") or body.get("text")
    return r.text or None


# ── Yandex Music (raw HTTP, token from env) ───────────────────────────────────
def yandex_available() -> bool:
    return bool(YANDEX_TOKEN)


def _ya_cover(uri: str | None, size: str = "400x400") -> str | None:
    if not uri:
        return None
    return "https://" + uri.replace("%%", size)


async def yandex_search(q: str, limit: int = 10) -> dict:
    """Search Yandex Music (tracks + albums + artists in one call)."""
    empty = {"tracks": [], "albums": [], "artists": []}
    if not yandex_available():
        return empty
    key = _key("yandex.search", q, str(limit))
    cached = await _cache_get(key)
    if cached is not None:
        return cached

    r = await _http().get(
        "https://api.music.yandex.net/search",
        params={"text": q, "type": "all", "page": 0, "nocorrect": "false"},
        headers={"Authorization": f"OAuth {YANDEX_TOKEN}"},
    )
    r.raise_for_status()
    res = r.json().get("result", {})

    out = {"tracks": [], "albums": [], "artists": []}
    for t in (res.get("tracks") or {}).get("results", [])[:limit]:
        albums = t.get("albums") or [{}]
        out["tracks"].append({
            "source": "yandex",
            "provider": "yandex",
            "provider_id": str(t.get("id")),
            "title": t.get("title"),
            "artist": ", ".join(a.get("name", "") for a in (t.get("artists") or [])),
            "album": albums[0].get("title"),
            "album_id": str(albums[0].get("id")) if albums[0].get("id") else None,
            "coverUrl": _ya_cover(t.get("coverUri") or albums[0].get("coverUri")),
            "previewUrl": None,
            "explicit": (t.get("contentWarning") == "explicit"),
        })
    for a in (res.get("albums") or {}).get("results", [])[:limit]:
        out["albums"].append({
            "source": "yandex",
            "album_id": str(a.get("id")),
            "title": a.get("title"),
            "artist": ", ".join(x.get("name", "") for x in (a.get("artists") or [])),
            "coverUrl": _ya_cover(a.get("coverUri")),
            "year": a.get("year"),
        })
    for a in (res.get("artists") or {}).get("results", [])[:limit]:
        cover = (a.get("cover") or {}).get("uri")
        out["artists"].append({
            "source": "yandex",
            "artist_id": str(a.get("id")),
            "name": a.get("name"),
            "coverUrl": _ya_cover(cover),
        })
    await _cache_put(key, "yandex", out)
    return out


async def yandex_album(album_id: str) -> dict | None:
    """Fetch a Yandex Music album with full tracklist."""
    key = _key("yandex.album", album_id)
    cached = await _cache_get(key)
    if cached is not None:
        return cached
    try:
        r = await _http().get(
            f"https://api.music.yandex.net/albums/{album_id}/with-tracks",
            headers={"Authorization": f"OAuth {YANDEX_TOKEN}"},
            timeout=15.0,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        d = r.json().get("result", {})
    except Exception:
        return None

    if not d or d.get("error"):
        return None

    artists = d.get("artists") or []
    tracks = []
    for vol in d.get("volumes") or []:
        for t in (vol if isinstance(vol, list) else []):
            tr_artists = t.get("artists") or artists
            tracks.append({
                "source": "yandex",
                "provider": "yandex",
                "provider_id": str(t.get("id")),
                "title": t.get("title"),
                "artist": ", ".join(a.get("name", "") for a in tr_artists),
                "album": d.get("title"),
                "trackNumber": len(tracks) + 1,
                "explicit": (t.get("contentWarning") == "explicit"),
                "coverUrl": _ya_cover(t.get("coverUri") or d.get("coverUri")),
            })
    out = {
        "source": "yandex",
        "album_id": str(d.get("id", album_id)),
        "title": d.get("title"),
        "artist": ", ".join(a.get("name", "") for a in artists),
        "artist_id": str(artists[0].get("id")) if artists else None,
        "coverUrl": _ya_cover(d.get("coverUri")),
        "year": d.get("year"),
        "type": d.get("type", "album"),
        "tracks": tracks,
    }
    await _cache_put(key, "yandex", out, ttl_hours=24)
    return out


async def yandex_artist(artist_id: str) -> dict | None:
    """Fetch a Yandex Music artist page: top tracks + albums."""
    key = _key("yandex.artist", artist_id)
    cached = await _cache_get(key)
    if cached is not None:
        return cached
    try:
        r = await _http().get(
            f"https://api.music.yandex.net/artists/{artist_id}/tracks",
            params={"page": 0, "page_size": 20},
            headers={"Authorization": f"OAuth {YANDEX_TOKEN}"},
            timeout=15.0,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        tracks_data = r.json().get("result", {})
    except Exception:
        return None

    # Fetch artist info
    artist_info = {}
    try:
        ri = await _http().get(
            f"https://api.music.yandex.net/artists/{artist_id}",
            headers={"Authorization": f"OAuth {YANDEX_TOKEN}"},
            timeout=10.0,
        )
        if ri.status_code == 200:
            artist_info = ri.json().get("result", {}).get("artist", {})
    except Exception:
        pass

    # Fetch artist albums
    albums = []
    try:
        ra = await _http().get(
            f"https://api.music.yandex.net/artists/{artist_id}/direct-albums",
            params={"page": 0, "page_size": 20},
            headers={"Authorization": f"OAuth {YANDEX_TOKEN}"},
            timeout=10.0,
        )
        if ra.status_code == 200:
            for a in (ra.json().get("result", {}).get("albums") or [])[:20]:
                albums.append({
                    "source": "yandex",
                    "album_id": str(a.get("id")),
                    "title": a.get("title"),
                    "artist": ", ".join(x.get("name", "") for x in (a.get("artists") or [])),
                    "coverUrl": _ya_cover(a.get("coverUri")),
                    "year": a.get("year"),
                })
    except Exception:
        pass

    out = {
        "source": "yandex",
        "artist_id": str(artist_id),
        "name": artist_info.get("name", ""),
        "coverUrl": _ya_cover((artist_info.get("cover") or {}).get("uri")),
        "topTracks": [
            {
                "source": "yandex",
                "provider": "yandex",
                "provider_id": str(t.get("id")),
                "title": t.get("title"),
                "artist": ", ".join(a.get("name", "") for a in (t.get("artists") or [])),
                "album": ((t.get("albums") or [{}])[0].get("title")),
                "coverUrl": _ya_cover(t.get("coverUri")),
            }
            for t in (tracks_data.get("tracks") or [])[:20]
        ],
        "albums": albums,
    }
    await _cache_put(key, "yandex", out, ttl_hours=24)
    return out


# ── SoundCloud (API v2, client_id scraped from public page) ──────────────────

_SC_CLIENT_ID: str | None = None
_SC_CLIENT_ID_FETCHED: float = 0
_SC_CLIENT_ID_TTL = 86400  # 24 hours


async def _scrape_soundcloud_client_id() -> str | None:
    """Scrape a working client_id from SoundCloud's JS bundles on sndcdn.com.

    soundcloud.com itself times out from some networks (Russia), but
    a-v2.sndcdn.com (the CDN that hosts the JS bundles) is reachable.
    """
    import time
    import re
    import logging

    log = logging.getLogger("kwhale")
    global _SC_CLIENT_ID, _SC_CLIENT_ID_FETCHED
    if _SC_CLIENT_ID and (time.time() - _SC_CLIENT_ID_FETCHED) < _SC_CLIENT_ID_TTL:
        return _SC_CLIENT_ID

    # Try DB cache first
    cached = await _cache_get("sc.client_id")
    if cached:
        _SC_CLIENT_ID = cached if isinstance(cached, str) else str(cached)
        _SC_CLIENT_ID_FETCHED = time.time()
        print(f"[SoundCloud] using cached client_id={_SC_CLIENT_ID[:8]}...", flush=True)
        return _SC_CLIENT_ID

    print("[SoundCloud] no cached client_id, attempting scrape", flush=True)

    try:
        # Fetch JS bundles from sndcdn.com CDN (works even when soundcloud.com is blocked)
        js_base = "https://a-v2.sndcdn.com/assets/"
        # Fetch the main page from soundcloud.com to discover JS URLs —
        # fallback: try a curated list of known JS bundle patterns
        candidate_urls = []
        try:
            r = await _http().get("https://soundcloud.com", timeout=5.0)
            if r.status_code == 200:
                script_urls = re.findall(r'<script[^>]+src="([^"]+)"', r.text)
                candidate_urls = [
                    u if u.startswith("https://") else f"https://soundcloud.com{u}"
                    for u in script_urls
                ]
        except Exception:
            pass  # soundcloud.com blocked, try sndcdn directly

        # If soundcloud.com failed, scan a range of sndcdn bundles
        if not candidate_urls:
            # Try fetching the manifest from sndcdn
            try:
                r = await _http().get(
                    "https://a-v2.sndcdn.com/assets/0-82c92e18.js",
                    timeout=5.0,
                )
                if r.status_code == 200:
                    match = re.search(
                        r'client_id["\']?\s*[:=]\s*["\']([a-zA-Z0-9]{32})["\']',
                        r.text,
                    )
                    if match:
                        _SC_CLIENT_ID = match.group(1)
                        _SC_CLIENT_ID_FETCHED = time.time()
                        await _cache_put("sc.client_id", "soundcloud", _SC_CLIENT_ID, ttl_hours=24)
                        return _SC_CLIENT_ID
            except Exception:
                pass

        # Download each JS and search for client_id
        for js_url in candidate_urls[:20]:
            try:
                jr = await _http().get(js_url, timeout=5.0)
                if jr.status_code != 200:
                    continue
                match = re.search(
                    r'client_id["\']?\s*[:=]\s*["\']([a-zA-Z0-9]{32})["\']',
                    jr.text,
                )
                if match:
                    _SC_CLIENT_ID = match.group(1)
                    _SC_CLIENT_ID_FETCHED = time.time()
                    await _cache_put("sc.client_id", "soundcloud", _SC_CLIENT_ID, ttl_hours=24)
                    return _SC_CLIENT_ID
            except Exception:
                continue
    except Exception:
        pass

    return _SC_CLIENT_ID


def soundcloud_available() -> bool:
    return True  # Always available — client_id is scraped, no token needed


async def soundcloud_search(q: str, limit: int = 20) -> dict:
    """Search SoundCloud tracks. Returns {"tracks": [...], "albums": [], "artists": []}."""
    empty = {"tracks": [], "albums": [], "artists": []}
    client_id = await _scrape_soundcloud_client_id()
    if not client_id:
        import logging
        logging.getLogger("klauncher").warning("SoundCloud: no client_id available")
        return empty

    key = _key("soundcloud.search", q, str(limit))
    cached = await _cache_get(key)
    if cached is not None:
        return cached

    try:
        r = await _http().get(
            "https://api-v2.soundcloud.com/search/tracks",
            params={"q": q, "limit": limit, "client_id": client_id},
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        import logging
        logging.getLogger("klauncher").warning(f"SoundCloud search error: {e}")
        return empty

    out = {"tracks": [], "albums": [], "artists": []}
    for t in data.get("collection", [])[:limit]:
        user = t.get("user", {})
        out["tracks"].append({
            "source": "soundcloud",
            "provider": "soundcloud",
            "provider_id": str(t.get("id")),
            "title": t.get("title", ""),
            "artist": user.get("username", ""),
            "album": "",
            "coverUrl": t.get("artwork_url"),
            "duration": (t.get("duration", 0) or 0) // 1000,
            "explicit": t.get("publisher", {}).get("explicit", False) if t.get("publisher") else False,
        })
    await _cache_put(key, "soundcloud", out)
    return out


async def soundcloud_resolve(track_id: str) -> str | None:
    """Resolve a stream URL for a SoundCloud track."""
    client_id = await _scrape_soundcloud_client_id()
    if not client_id:
        return None
    try:
        r = await _http().get(
            f"https://api-v2.soundcloud.com/tracks/{track_id}/stream",
            params={"client_id": client_id},
            timeout=10.0,
            follow_redirects=True,
        )
        if r.status_code == 200:
            data = r.json()
            # Prefer progressive (direct MP3) over HLS
            for t in data.get("streams", []):
                if t.get("format", {}).get("protocol") == "progressive":
                    return t.get("url")
            # Fallback to first available stream
            for t in data.get("streams", []):
                return t.get("url")
    except Exception:
        pass
    return None


# ── VK Music (direct VK API with access token) ──────────────────────────────

VK_ACCESS_TOKEN = os.environ.get("VK_ACCESS_TOKEN", "")
VK_API_VERSION = "5.95"
_VK_UA = "KateMobile/56 (Android 14; SDK 34; arm64-v8a; en)"


def vk_available() -> bool:
    return bool(VK_ACCESS_TOKEN)


async def vk_search(q: str, limit: int = 20) -> dict:
    """Search VK Music. Returns {"tracks": [...], "albums": [], "artists": []}."""
    empty = {"tracks": [], "albums": [], "artists": []}
    if not vk_available():
        return empty

    key = _key("vk.search", q, str(limit))
    cached = await _cache_get(key)
    if cached is not None:
        return cached

    try:
        r = await _http().get(
            "https://api.vk.com/method/audio.search",
            params={
                "q": q,
                "count": limit,
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION,
            },
            headers={"User-Agent": _VK_UA},
            timeout=12.0,
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            return empty
    except Exception:
        return empty

    items = (data.get("response") or {}).get("items", [])
    out = {"tracks": [], "albums": [], "artists": []}
    for t in items[:limit]:
        url = t.get("url", "")
        out["tracks"].append({
            "source": "vk",
            "provider": "vk",
            "provider_id": f"{t.get('owner_id')}_{t.get('id')}",
            "title": t.get("title", ""),
            "artist": t.get("artist", ""),
            "album": "",
            "duration": t.get("duration", 0),
            "previewUrl": url,
            "explicit": False,
        })
    await _cache_put(key, "vk", out)
    return out


async def vk_resolve(track_id: str) -> str | None:
    """Resolve a stream URL for a VK track by owner_id_id."""
    if not vk_available():
        return None
    # track_id is "owner_id_audio_id"
    try:
        r = await _http().get(
            "https://api.vk.com/method/audio.getById",
            params={
                "audios": track_id,
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION,
            },
            headers={"User-Agent": _VK_UA},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            return None
        items = (data.get("response") or {}).get("items", [])
        if items:
            return items[0].get("url") or None
    except Exception:
        pass
    return None


# ── Unified resolve dispatcher ───────────────────────────────────────────────

async def resolve_stream_url(provider: str, track_id: str) -> str | None:
    """Resolve a stream URL for any provider."""
    if provider == "icm":
        return await icm_resolve(track_id)
    if provider == "yandex":
        return await _yandex_resolve(track_id)
    if provider == "soundcloud":
        return await soundcloud_resolve(track_id)
    return None


async def _yandex_resolve(track_id: str) -> str | None:
    """Resolve a Yandex Music track to a direct download URL."""
    if not yandex_available():
        return None
    try:
        from yandex_music import Client
        client = Client(YANDEX_TOKEN).init()
        tracks = client.tracks([int(track_id)])
        if not tracks:
            return None
        dl_info = tracks[0].get_download_info()
        if not dl_info:
            return None
        best = sorted(dl_info, key=lambda x: x.bitrate_in_kbps or 0, reverse=True)[0]
        return best.get_direct_link()
    except Exception:
        return None


async def icm_resolve(track_id: str) -> str | None:
    """Resolve a stream URL for an ICM track."""
    if not icm_available():
        return None
    try:
        r = await _http().get(
            f"{ICM_BASE}/api/partner/track/{track_id}/stream",
            params={"region": settings.icm_default_region},
            headers=_icm_headers(),
            timeout=10.0,
        )
        if r.status_code == 200:
            return r.json().get("url")
    except Exception:
        pass
    return None


# ── Library matching (mark online tracks already present locally) ────────────
async def mark_in_library(tracks: list[dict]) -> None:
    """Set in_library + navidrome_id on online track dicts, in place.

    Match order: exact provider_track_map hit, then case-insensitive
    artist|title match against track_features.
    """
    if not tracks:
        return
    pool = await get_pool()

    pairs = [(t.get("provider"), str(t.get("provider_id"))) for t in tracks]
    rows = await pool.fetch(
        """
        SELECT provider, provider_id, navidrome_id FROM provider_track_map
        WHERE navidrome_id IS NOT NULL
          AND (provider, provider_id) IN (SELECT unnest($1::text[]), unnest($2::text[]))
        """,
        [p for p, _ in pairs], [i for _, i in pairs],
    )
    by_provider = {(r["provider"], r["provider_id"]): r["navidrome_id"] for r in rows}

    keys = [f"{(t.get('artist') or '').lower()}|{(t.get('title') or '').lower()}"
            for t in tracks]
    rows = await pool.fetch(
        """
        SELECT navidrome_id, lower(artist) || '|' || lower(title) AS k
        FROM track_features
        WHERE lower(artist) || '|' || lower(title) = ANY($1::text[])
        """,
        keys,
    )
    by_name = {r["k"]: r["navidrome_id"] for r in rows}

    for t, k in zip(tracks, keys):
        nid = by_provider.get((t.get("provider"), str(t.get("provider_id")))) or by_name.get(k)
        t["in_library"] = nid is not None
        if nid:
            t["navidrome_id"] = nid
