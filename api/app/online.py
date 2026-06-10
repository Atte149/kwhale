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
    return json.loads(val) if isinstance(val, str) else val


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
