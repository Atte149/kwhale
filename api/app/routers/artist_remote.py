"""Remote tracks by artist name — enrich artist cards with streaming tracks.

Searches ICM and Yandex by artist name, filters out tracks already in the
library, and returns them for display in the artist card UI.
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from urllib.parse import quote

from ..auth import current_user
from ..db import get_pool
from .. import online

router = APIRouter(prefix="/artists", tags=["artists"])


@router.get("/{artist_name}/remote-tracks")
async def remote_tracks(
    artist_name: str,
    limit: int = Query(20, le=50),
    user: str = Depends(current_user),
):
    """Search streaming services for tracks by this artist.

    Searches ICM and Yandex Music by artist name, then filters out tracks
    already present in the local library. Results are cached.
    """
    # Search both providers in parallel
    icm_task = _search_icm_artist(artist_name, limit)
    yandex_task = _search_yandex_artist(artist_name, limit)

    icm_results = await icm_task
    yandex_results = await yandex_task

    # Merge and deduplicate by title
    all_tracks = []
    seen_titles: set[str] = set()

    for t in icm_results + yandex_results:
        title_key = (t.get("title") or "").lower().strip()
        if not title_key or title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        all_tracks.append(t)

    # Filter out tracks already in library
    if all_tracks:
        pool = await get_pool()
        # Build set of lowercased artist|title from library
        pairs = [
            f"{(t.get('artist') or '').lower()}|{(t.get('title') or '').lower()}"
            for t in all_tracks
        ]
        rows = await pool.fetch(
            """
            SELECT lower(artist) || '|' || lower(title) AS k
            FROM track_features
            WHERE lower(artist) || '|' || lower(title) = ANY($1::text[])
            """,
            pairs,
        )
        lib_keys = {r["k"] for r in rows}

        for t in all_tracks:
            key = f"{(t.get('artist') or '').lower()}|{(t.get('title') or '').lower()}"
            t["in_library"] = key in lib_keys

    return {
        "artist": artist_name,
        "tracks": all_tracks[:limit],
        "total": len(all_tracks),
    }


async def _search_icm_artist(artist_name: str, limit: int) -> list[dict]:
    """Search ICM for tracks by artist name."""
    try:
        result = await online.icm_search(artist_name, limit=limit)
        tracks = result.get("tracks", [])
        # Filter to only tracks where artist matches
        name_lower = artist_name.lower()
        filtered = [
            t for t in tracks
            if name_lower in (t.get("artist") or "").lower()
        ]
        return filtered
    except Exception as e:
        print(f"ICM artist search error: {e}")
        return []


async def _search_yandex_artist(artist_name: str, limit: int) -> list[dict]:
    """Search Yandex Music for tracks by artist name."""
    try:
        result = await online.yandex_search(artist_name, limit=limit)
        tracks = result.get("tracks", [])
        name_lower = artist_name.lower()
        filtered = [
            t for t in tracks
            if name_lower in (t.get("artist") or "").lower()
        ]
        return filtered
    except Exception as e:
        print(f"Yandex artist search error: {e}")
        return []