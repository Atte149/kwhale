"""Internal endpoints — called only by MCP server and other internal services.
Not exposed to the public client. No auth required (network-isolated).
"""
from fastapi import APIRouter
from pydantic import BaseModel
from ..db import get_pool

router = APIRouter(prefix="/internal", tags=["internal"])


class SearchRequest(BaseModel):
    query: str
    limit: int = 10


@router.post("/search-providers")
async def search_providers(body: SearchRequest):
    from ..celery_helpers import poll_celery_task
    result = await poll_celery_task("app.tasks.search_providers", args=[body.query, body.limit])
    return result


class AcquireRequest(BaseModel):
    query: str | None = None
    provider: str | None = None
    provider_id: str | None = None


@router.post("/acquire")
async def acquire(body: AcquireRequest):
    from ..celery_helpers import fire_acquire_task
    pool = await get_pool()
    return await fire_acquire_task(
        pool, "internal", body.provider, body.provider_id, body.query)
