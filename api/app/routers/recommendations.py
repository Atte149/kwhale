"""Recommendations endpoints.

Types exposed to the client:
  - hybrid     : personalised (ALS + content), cold-start fallback to random
  - genre      : tracks from a chosen genre (Navidrome metadata, no indexing needed)
  - discovery  : tracks the user hasn't played recently (fresh/random)
  - prompt     : free-text → LLM criteria → matched tracks (POST /recs/prompt)
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from ..auth import current_user
from ..db import get_pool
from ..tasks import celery_app
from .. import navidrome
from ..prompt_recs import run_prompt_agent

router = APIRouter(prefix="/recs", tags=["recommendations"])


def _enrich(song: dict, score=None) -> dict:
    return {
        **song,
        "streamUrl": navidrome.stream_url(song["id"]),
        "coverUrl": f"/library/cover/{song.get('coverArt', song['id'])}",
        "rec_score": score,
    }


async def _random(limit: int, genre: str | None = None) -> list[dict]:
    songs = await navidrome.get_random_songs(size=limit, genre=genre)
    return [_enrich(s) for s in songs]


# ── Main feed ─────────────────────────────────────────────────────────────────

@router.get("")
async def get_recommendations(
    type: str = Query("hybrid", description="hybrid | genre | discovery"),
    genre: str | None = Query(None),
    limit: int = Query(20, le=50),
    user: str = Depends(current_user),
):
    # Genre — straight from Navidrome metadata
    if type == "genre" and genre:
        songs = await _random(limit, genre=genre)
        return {"tracks": songs, "type": "genre", "genre": genre}

    # Discovery — random fresh picks (works without history/indexing)
    if type == "discovery":
        songs = await _random(limit)
        return {
            "tracks": songs,
            "type": "discovery",
            "message": "Открытия из вашей библиотеки.",
        }

    # Hybrid / personalised — from materialised recommendations, cold-start fallback
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT track_ids, scores, generated_at FROM recommendations "
        "WHERE user_id=$1 AND algorithm='hybrid' ORDER BY date DESC LIMIT 1",
        user,
    )
    track_ids = list(row["track_ids"])[:limit] if row and row["track_ids"] else []
    scores = (row["scores"] or {}) if row else {}

    songs = []
    for tid in track_ids:
        song = await navidrome.get_song(tid)
        if song:
            songs.append(_enrich(song, scores.get(tid)))

    if not songs:
        celery_app.send_task("app.tasks.generate_recommendations", args=[user, "hybrid"])
        cold = await _random(limit)
        return {
            "tracks": cold,
            "type": "cold_start",
            "message": "Свежие треки из вашей библиотеки — слушайте, и подборка станет персональной.",
        }

    return {
        "tracks": songs,
        "type": "hybrid",
        "generated_at": row["generated_at"].isoformat() if row["generated_at"] else None,
    }


# ── Genres list ───────────────────────────────────────────────────────────────

@router.get("/genres")
async def list_genres(user: str = Depends(current_user)):
    genres = await navidrome.get_genres()
    # keep only genres that actually have songs, sorted by song count
    items = [
        {"name": g.get("value"), "count": g.get("songCount", 0)}
        for g in genres
        if g.get("songCount", 0) > 0
    ]
    items.sort(key=lambda x: -x["count"])
    return {"genres": items}


# ── Prompt → playlist (LLM) ────────────────────────────────────────────────────

class PromptRequest(BaseModel):
    prompt: str
    limit: int = 30


@router.post("/prompt")
async def prompt_recommendations(body: PromptRequest, user: str = Depends(current_user)):
    pool = await get_pool()
    track_ids = await run_prompt_agent(pool, body.prompt, body.limit)

    songs = []
    for tid in track_ids:
        song = await navidrome.get_song(tid)
        if song:
            songs.append(_enrich(song))

    # If the agent surfaced nothing, fall back to random picks from the library
    if not songs:
        songs = await _random(body.limit)
        return {
            "tracks": songs,
            "type": "prompt",
            "message": "Не нашёл точного совпадения — вот близкое из библиотеки.",
        }

    return {"tracks": songs, "type": "prompt"}


# ── Misc ──────────────────────────────────────────────────────────────────────

@router.post("/generate", status_code=202)
async def trigger_generate(user: str = Depends(current_user)):
    task = celery_app.send_task("app.tasks.generate_recommendations", args=[user, "hybrid"])
    return {"task_id": task.id, "status": "queued"}


@router.get("/taste-profile")
async def get_taste_profile(user: str = Depends(current_user)):
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM taste_profile WHERE user_id=$1", user)
    if not row:
        return {"message": "No taste profile yet. Need more playback events."}
    return dict(row)
