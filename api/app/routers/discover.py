"""Discover — search and acquire tracks from remote sources (source plugins)."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from ..auth import current_user
from ..db import get_pool

router = APIRouter(prefix="/discover", tags=["discover"])


@router.get("")
async def search_all(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, le=50),
    user: str = Depends(current_user),
):
    from ..celery_helpers import poll_celery_task
    result = await poll_celery_task("app.tasks.search_providers", args=[q, limit])
    return {"query": q, "results": result}


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
    from ..celery_helpers import fire_acquire_task
    pool = await get_pool()
    return await fire_acquire_task(
        pool, user, body.provider, body.provider_id, body.query)


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
    from ..celery_helpers import poll_celery_task
    result = await poll_celery_task("app.tasks.search_provider", args=[provider, q, limit])
    return {"query": q, "provider": provider, "results": result}
