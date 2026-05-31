"""Discover — search and acquire tracks from remote sources (source plugins)."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from ..auth import current_user
from ..db import get_pool
from ..tasks import celery_app
import uuid

router = APIRouter(prefix="/discover", tags=["discover"])


@router.get("")
async def search_all(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, le=50),
    user: str = Depends(current_user),
):
    import asyncio
    loop = asyncio.get_event_loop()
    task = celery_app.send_task("app.tasks.search_providers", args=[q, limit])
    result = await loop.run_in_executor(None, lambda: task.get(timeout=15))
    return {"query": q, "results": result or []}


@router.get("/queue")
async def list_queue(limit: int = 20, user: str = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, status, query, provider, provider_id, navidrome_id, "
        "error, progress_pct, created_at, updated_at "
        "FROM download_queue WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2",
        user, limit,
    )
    return {"items": [dict(r) for r in rows]}


class AcquireRequest(BaseModel):
    query: str | None = None
    provider: str | None = None
    provider_id: str | None = None


@router.post("/acquire", status_code=202)
async def acquire(body: AcquireRequest, user: str = Depends(current_user)):
    task_id = str(uuid.uuid4())
    pool = await get_pool()
    query_str = body.query or f"{body.provider}:{body.provider_id}"
    await pool.execute(
        "INSERT INTO download_queue (id, user_id, query, provider, provider_id) "
        "VALUES ($1,$2,$3,$4,$5)",
        task_id, user, query_str, body.provider, body.provider_id,
    )
    celery_app.send_task(
        "app.tasks.download_provider_track",
        args=[body.provider, body.provider_id, task_id],
        kwargs={"query": body.query},
        task_id=task_id,
    )
    return {"task_id": task_id, "status": "queued"}


@router.get("/acquire/{task_id}")
async def acquire_status(task_id: str, user: str = Depends(current_user)):
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM download_queue WHERE id=$1 AND user_id=$2", task_id, user,
    )
    if not row:
        from fastapi import HTTPException
        raise HTTPException(404, "Task not found")
    return dict(row)


@router.get("/{provider}")
async def search_provider(
    provider: str,
    q: str = Query(..., min_length=1),
    limit: int = Query(20, le=50),
    user: str = Depends(current_user),
):
    import asyncio
    loop = asyncio.get_event_loop()
    task = celery_app.send_task("app.tasks.search_provider", args=[provider, q, limit])
    result = await loop.run_in_executor(None, lambda: task.get(timeout=15))
    return {"query": q, "provider": provider, "results": result or []}
