"""Unified search — one query, sectioned results from the library AND online
catalogs (ICM, Yandex), like a commercial streaming app.

Design (STABILIZATION_PLAN.md, search v2):
  * local Navidrome search and online catalog searches run in parallel;
  * online failures degrade to empty sections — local results always come back;
  * no stream-URL resolution here (lazy, on play/acquire) — search stays fast;
  * online metadata is cached in source_cache by the online module.

Response shape (all sections may mix sources, each item carries `source`):
  {query, songs: [...], albums: [...], artists: [...],
   online: {available: bool, errors: [...]}}
"""
import asyncio

from fastapi import APIRouter, Depends, Query

from ..auth import current_user
from ..db import get_pool
from .. import navidrome, online

router = APIRouter(prefix="/search", tags=["search"])


def _local_song(song: dict, features: dict | None) -> dict:
    return {
        "source": "library",
        "id": song.get("id"),
        "title": song.get("title"),
        "artist": song.get("artist"),
        "artistId": song.get("artistId"),
        "album": song.get("album"),
        "albumId": song.get("albumId"),
        "duration": song.get("duration"),
        "year": song.get("year"),
        "genre": song.get("genre"),
        "coverArt": song.get("coverArt"),
        "streamUrl": navidrome.stream_url(song["id"]),
        "coverUrl": navidrome.cover_url(song.get("coverArt", song["id"]), size=600),
        "vibe": {
            "bpm": features.get("bpm"),
            "energy": features.get("energy"),
            "valence": features.get("valence"),
            "tags": features.get("vibe_tags", []),
        } if features else None,
    }


def _local_album(album: dict) -> dict:
    return {
        "source": "library",
        "id": album.get("id"),
        "title": album.get("name") or album.get("title") or album.get("album"),
        "artist": album.get("artist"),
        "year": album.get("year"),
        "songCount": album.get("songCount"),
        "coverUrl": navidrome.cover_url(album.get("coverArt", album.get("id")), size=600),
    }


def _local_artist(artist: dict) -> dict:
    return {
        "source": "library",
        "id": artist.get("id"),
        "name": artist.get("name"),
        "albumCount": artist.get("albumCount"),
        "coverUrl": (navidrome.cover_url(artist["coverArt"], size=600)
                     if artist.get("coverArt") else artist.get("artistImageUrl")),
    }


@router.get("")
async def unified_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, le=50),
    scope: str = Query("all", description="all | library | online"),
    result_type: str = Query("all", alias="type", description="all | track | album | artist"),
    user: str = Depends(current_user),
):
    want_local = scope in ("all", "library")
    want_online = scope in ("all", "online")
    want_tracks = result_type in ("all", "track")
    want_albums = result_type in ("all", "album")
    want_artists = result_type in ("all", "artist")

    async def _local():
        results = await navidrome.search(
            q, artist_count=8, album_count=12, song_count=limit
        )
        songs = results.get("song", [])
        feat_map = {}
        if songs:
            pool = await get_pool()
            rows = await pool.fetch(
                "SELECT navidrome_id, bpm, energy, valence, vibe_tags "
                "FROM track_features WHERE navidrome_id = ANY($1)",
                [s["id"] for s in songs],
            )
            feat_map = {r["navidrome_id"]: dict(r) for r in rows}
        return {
            "songs": [_local_song(s, feat_map.get(s["id"])) for s in songs] if want_tracks else [],
            "albums": [_local_album(a) for a in results.get("album", [])] if want_albums else [],
            "artists": [_local_artist(a) for a in results.get("artist", [])] if want_artists else [],
        }

    async def _nothing_local():
        return {"songs": [], "albums": [], "artists": []}

    async def _nothing_online():
        return {"tracks": [], "albums": [], "artists": []}

    local_res, icm_res, ya_res, sc_res = await asyncio.gather(
        _local() if want_local else _nothing_local(),
        online.icm_search(q, limit=limit) if want_online else _nothing_online(),
        online.yandex_search(q, limit=max(5, limit // 2)) if want_online else _nothing_online(),
        online.soundcloud_search(q, limit=max(5, limit // 2)) if want_online else _nothing_online(),
        return_exceptions=True,
    )

    errors = []
    if isinstance(local_res, Exception):
        errors.append(f"library: {type(local_res).__name__}: {local_res}")
        local_res = {"songs": [], "albums": [], "artists": []}
    if isinstance(icm_res, Exception):
        errors.append(f"icm: {type(icm_res).__name__}: {icm_res}")
        icm_res = {"tracks": [], "albums": [], "artists": []}
    if isinstance(ya_res, Exception):
        errors.append(f"yandex: {type(ya_res).__name__}: {ya_res}")
        ya_res = {"tracks": [], "albums": [], "artists": []}
    if isinstance(sc_res, Exception):
        errors.append(f"soundcloud: {type(sc_res).__name__}: {sc_res}")
        sc_res = {"tracks": [], "albums": [], "artists": []}

    # ── Deduplicate online tracks ────────────────────────────────────────────
    # Group by normalised artist|title, keep one winner per group by priority:
    # ICM(10) > Yandex(20) > SoundCloud(40). Tracks already in the
    # library always win over any online duplicate.
    _PROVIDER_PRIORITY = {"icm": 10, "yandex": 20, "soundcloud": 40}
    all_tracks = icm_res["tracks"] + ya_res["tracks"] + sc_res["tracks"]
    by_key: dict[str, dict] = {}
    for t in all_tracks:
        k = f"{(t.get('artist') or '').lower().strip()}|{(t.get('title') or '').lower().strip()}"
        if not k or k == "|":
            continue
        existing = by_key.get(k)
        if existing is None:
            by_key[k] = t
            t.setdefault("sources", [t.get("provider", "")])
            continue
        # Merge sources list
        for s in (t.get("provider", ""),):
            if s and s not in existing.setdefault("sources", []):
                existing["sources"].append(s)
        # Pick winner: in_library > lower priority number
        if t.get("in_library") and not existing.get("in_library"):
            t["sources"] = existing["sources"]
            by_key[k] = t
        elif (
            not t.get("in_library")
            and not existing.get("in_library")
            and _PROVIDER_PRIORITY.get(t.get("provider", ""), 99)
                < _PROVIDER_PRIORITY.get(existing.get("provider", ""), 99)
        ):
            t["sources"] = existing["sources"]
            by_key[k] = t

    deduped_tracks = list(by_key.values())
    try:
        await online.mark_in_library(deduped_tracks)
    except Exception as e:
        errors.append(f"in_library: {e}")

    return {
        "query": q,
        "songs": (local_res["songs"] + deduped_tracks) if want_tracks else [],
        "albums": (local_res["albums"] + icm_res["albums"] + ya_res["albums"]) if want_albums else [],
        "artists": (local_res["artists"] + icm_res["artists"] + ya_res["artists"]) if want_artists else [],
        "online": {
            "available": (
                online.icm_available()
                or online.yandex_available()
                or online.soundcloud_available()
            ),
            "errors": errors,
        },
    }
