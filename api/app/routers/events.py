"""Telemetry ingest — receives rich playback events from the client.

The client (Navic fork) batches events from just_audio's positionStream and
POSTs them here. This replaces the old stream_counter hack with real data:
duration listened, completion %, skip detection, seek count, time-of-day.

POST /events          — batch ingest (preferred)
POST /events/single   — single event (simpler for initial integration)
"""
from typing import Literal
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import current_user
from ..db import get_pool
from ..config import settings

router = APIRouter(prefix="/events", tags=["telemetry"])


class PlaybackEvent(BaseModel):
    navidrome_id: str
    event_type: Literal["play", "pause", "complete", "skip", "seek", "heartbeat"]
    position_sec: float = 0
    duration_sec: float = 0
    completion_pct: float = Field(0, ge=0, le=1)
    skipped: bool = False
    seek_count: int = 0
    source: str = "local"
    context: dict = {}


class EventBatch(BaseModel):
    events: list[PlaybackEvent]


@router.post("", status_code=204)
async def ingest_batch(batch: EventBatch, user: str = Depends(current_user)):
    if not batch.events:
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO playback_events
                (user_id, navidrome_id, event_type,
                 position_sec, duration_sec, completion_pct,
                 skipped, seek_count, source, context)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            """,
            [
                (
                    user,
                    e.navidrome_id,
                    e.event_type,
                    e.position_sec,
                    e.duration_sec,
                    e.completion_pct,
                    e.skipped,
                    e.seek_count,
                    e.source,
                    e.context,
                )
                for e in batch.events
            ],
        )

        # Update stream_counter for remote tracks so auto-acquire still works
        remote_plays = [
            e for e in batch.events
            if e.event_type in ("play", "complete")
            and e.source.startswith("remote:")
        ]
        for e in remote_plays:
            provider = e.source.replace("remote:", "")
            provider_id = e.context.get("provider_id", "")
            if not provider_id:
                continue
            await conn.execute(
                """
                INSERT INTO stream_counter (provider, provider_id, user_id, play_count, last_played_at)
                VALUES ($1, $2, $3, 1, NOW())
                ON CONFLICT (provider, provider_id, user_id)
                DO UPDATE SET
                    play_count = stream_counter.play_count + 1,
                    last_played_at = NOW()
                """,
                provider, provider_id, user,
            )
            # Check auto-acquire threshold
            row = await conn.fetchrow(
                "SELECT play_count, auto_acquired FROM stream_counter "
                "WHERE provider=$1 AND provider_id=$2 AND user_id=$3",
                provider, provider_id, user,
            )
            if row and row["play_count"] >= settings.stream_auto_acquire_threshold \
                    and not row["auto_acquired"]:
                await _enqueue_auto_acquire(conn, provider, provider_id, user)


@router.post("/single", status_code=204)
async def ingest_single(event: PlaybackEvent, user: str = Depends(current_user)):
    await ingest_batch(EventBatch(events=[event]), user)


async def _enqueue_auto_acquire(conn, provider: str, provider_id: str, user: str):
    import uuid
    from ..tasks import celery_app
    task_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO download_queue (id, user_id, query, provider, provider_id, status)
        VALUES ($1, $2, $3, $4, $5, 'pending')
        ON CONFLICT DO NOTHING
        """,
        task_id, user, f"{provider}:{provider_id}", provider, provider_id,
    )
    await conn.execute(
        "UPDATE stream_counter SET auto_acquired=TRUE "
        "WHERE provider=$1 AND provider_id=$2 AND user_id=$3",
        provider, provider_id, user,
    )
    celery_app.send_task(
        "app.tasks.download_provider_track",
        args=[provider, provider_id, task_id],
    )
