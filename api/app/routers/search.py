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
    user: str = Depends(current_user),
):
    want_local = scope in ("all", "library")
    want_online = scope in ("all", "online")

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
            "songs": [_local_song(s, feat_map.get(s["id"])) for s in songs],
            "albums": [_local_album(a) for a in results.get("album", [])],
            "artists": [_local_artist(a) for a in results.get("artist", [])],
        }

    async def _nothing_local():
        return {"songs": [], "albums": [], "artists": []}

    async def _nothing_online():
        return {"tracks": [], "albums": [], "artists": []}

    local_res, icm_res, ya_res = await asyncio.gather(
        _local() if want_local else _nothing_local(),
        online.icm_search(q, limit=limit) if want_online else _nothing_online(),
        online.yandex_search(q, limit=max(5, limit // 2)) if want_online else _nothing_online(),
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

    online_tracks = icm_res["tracks"] + ya_res["tracks"]
    try:
        await online.mark_in_library(online_tracks)
    except Exception as e:
        errors.append(f"in_library: {e}")

    return {
        "query": q,
        "songs": local_res["songs"] + online_tracks,
        "albums": local_res["albums"] + icm_res["albums"] + ya_res["albums"],
        "artists": local_res["artists"] + icm_res["artists"] + ya_res["artists"],
        "online": {
            "available": online.icm_available() or online.yandex_available(),
            "errors": errors,
        },
    }
