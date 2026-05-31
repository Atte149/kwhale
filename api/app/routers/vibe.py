"""Vibe endpoints — Essentia features, vibe tags, similar tracks."""
from fastapi import APIRouter, Depends, Query
from ..auth import current_user
from ..db import get_pool
from ..tasks import celery_app

router = APIRouter(prefix="/vibe", tags=["vibe"])


@router.get("/{song_id}")
async def get_vibe(song_id: str, user: str = Depends(current_user)):
    """Return full audio features and vibe tags for a track."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT navidrome_id, bpm, energy, valence, instrumentalness, "
        "danceability, loudness, key, mode, lyrics, vibe_tags, indexed_at "
        "FROM track_features WHERE navidrome_id=$1",
        song_id,
    )
    if not row:
        return {"navidrome_id": song_id, "indexed": False}
    return {"indexed": True, **dict(row)}


@router.get("/{song_id}/similar")
async def get_similar(
    song_id: str,
    limit: int = Query(10, le=50),
    user: str = Depends(current_user),
):
    """Return similar tracks using pgvector cosine search."""
    pool = await get_pool()

    base = await pool.fetchrow(
        "SELECT features_vector, lyrics_embedding FROM track_features WHERE navidrome_id=$1",
        song_id,
    )
    if not base or base["features_vector"] is None:
        return {"similar": [], "message": "Track not indexed yet"}

    rows = await pool.fetch(
        """
        SELECT
            navidrome_id,
            1 - (features_vector <=> $1::vector) AS audio_score
        FROM track_features
        WHERE navidrome_id != $2
          AND features_vector IS NOT NULL
        ORDER BY features_vector <=> $1::vector
        LIMIT $3
        """,
        base["features_vector"], song_id, limit,
    )
    return {
        "similar": [
            {"navidrome_id": r["navidrome_id"], "score": float(r["audio_score"])}
            for r in rows
        ]
    }


@router.post("/{song_id}/index", status_code=202)
async def trigger_index(song_id: str, user: str = Depends(current_user)):
    """Trigger Essentia feature extraction + vibe tag generation for a track."""
    task = celery_app.send_task("app.tasks.index_track", args=[song_id])
    return {"task_id": task.id, "status": "queued"}


@router.post("/index-all", status_code=202)
async def trigger_index_all(user: str = Depends(current_user)):
    """Index all tracks not yet in track_features."""
    task = celery_app.send_task("app.tasks.index_all_tracks")
    return {"task_id": task.id, "status": "queued"}
