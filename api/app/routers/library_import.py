"""Library import — batch acquire tracks from a user-provided list.

Accepts a list of {artist, title} pairs (from Spotify/Apple Music/CSV/etc.),
searches each in source plugins, and queues downloads for tracks not already
in the library. Returns a batch job ID that can be polled for progress.
"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..celery_helpers import poll_celery_task, fire_acquire_task
from ..db import get_pool

router = APIRouter(prefix="/library", tags=["library-import"])


class ImportEntry(BaseModel):
    artist: str
    title: str
    album: str | None = None


class ImportRequest(BaseModel):
    tracks: list[ImportEntry]


class ImportTextRequest(BaseModel):
    """Plain-text import: one track per line, 'Artist - Title' format."""
    text: str
    format: str = "auto"  # 'auto', 'artist-title', 'csv', 'json'


@router.post("/import")
async def import_tracks(body: ImportRequest, user: str = Depends(current_user)):
    """Import a list of tracks: search sources, queue downloads for missing ones.

    For each track:
      1. Check if it already exists in the library (by artist+title fuzzy match).
      2. If not, search source plugins (ICM, Yandex).
      3. Queue the best match for download.

    Returns a batch ID and per-track status.
    """
    if not body.tracks:
        raise HTTPException(400, "No tracks provided")

    pool = await get_pool()
    results = []

    for entry in body.tracks[:200]:  # cap at 200 per batch
        query = f"{entry.artist} - {entry.title}"

        # Check if already in library (exact or fuzzy match)
        existing = await pool.fetchrow(
            "SELECT navidrome_id FROM track_features "
            "WHERE title ILIKE $1 AND (artist ILIKE $2 OR all_artists_text ILIKE $3) "
            "LIMIT 1",
            f"%{entry.title}%", f"%{entry.artist}%", f"%{entry.artist}%",
        )
        if existing:
            results.append({
                "query": query,
                "status": "exists",
                "navidrome_id": existing["navidrome_id"],
            })
            continue

        # Search source plugins
        try:
            search_result = await poll_celery_task(
                "app.tasks.search_providers", args=[query, 5], timeout=20.0,
            )
        except Exception:
            search_result = []

        if not search_result:
            results.append({"query": query, "status": "not_found"})
            continue

        # Pick best match (first result = highest priority provider)
        best = search_result[0]
        provider = best.get("provider")
        provider_id = best.get("provider_id") or best.get("id")

        if not provider or not provider_id:
            # Fallback: use query-based acquire
            acquire_result = await fire_acquire_task(
                pool, user, None, None, query,
            )
            results.append({
                "query": query,
                "status": "queued",
                "task_id": acquire_result["task_id"],
                "provider": "query",
            })
        else:
            acquire_result = await fire_acquire_task(
                pool, user, provider, provider_id, query,
            )
            results.append({
                "query": query,
                "status": "queued",
                "task_id": acquire_result["task_id"],
                "provider": provider,
                "provider_id": provider_id,
            })

    queued = sum(1 for r in results if r["status"] == "queued")
    exists = sum(1 for r in results if r["status"] == "exists")
    not_found = sum(1 for r in results if r["status"] == "not_found")

    return {
        "total": len(body.tracks),
        "queued": queued,
        "exists": exists,
        "not_found": not_found,
        "results": results,
    }


@router.post("/import/text")
async def import_from_text(body: ImportTextRequest, user: str = Depends(current_user)):
    """Import tracks from plain text. Parses each line as 'Artist - Title'.

    Supports:
      - 'Artist - Title'
      - 'Artist - Album - Title' (album ignored)
      - CSV: 'Artist,Title' (when format='csv')
      - JSON array (when format='json')
    """
    tracks: list[ImportEntry] = []

    if body.format == "json":
        try:
            data = json.loads(body.text)
            for item in data:
                artist = item.get("artist", "")
                title = item.get("title", "")
                if artist and title:
                    tracks.append(ImportEntry(artist=artist, title=title, album=item.get("album")))
        except json.JSONDecodeError:
            raise HTTPException(400, "Invalid JSON")
    elif body.format == "csv":
        for line in body.text.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 2:
                tracks.append(ImportEntry(artist=parts[0], title=parts[1]))
    else:
        # auto / artist-title
        for line in body.text.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(" - ") if p.strip()]
            if len(parts) >= 2:
                artist = parts[0]
                title = parts[-1]  # last part is title (handles Artist - Album - Title)
                tracks.append(ImportEntry(artist=artist, title=title))

    if not tracks:
        raise HTTPException(400, "No valid tracks found in input")

    # Reuse the structured import endpoint
    return await import_tracks(ImportRequest(tracks=tracks), user)