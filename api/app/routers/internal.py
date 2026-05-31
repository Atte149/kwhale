"""Internal endpoints — called only by MCP server and other internal services.
Not exposed to the public client. No auth required (network-isolated).
"""
from fastapi import APIRouter
from pydantic import BaseModel
from ..db import get_pool
from ..tasks import celery_app
import uuid

router = APIRouter(prefix="/internal", tags=["internal"])


class SearchRequest(BaseModel):
    query: str
    limit: int = 10


@router.post("/search-providers")
async def search_providers(body: SearchRequest):
    import asyncio
    loop = asyncio.get_event_loop()
    task = celery_app.send_task("app.tasks.search_providers", args=[body.query, body.limit])
    result = await loop.run_in_executor(None, lambda: task.get(timeout=15))
    return result or []


class AcquireRequest(BaseModel):
    query: str | None = None
    provider: str | None = None
    provider_id: str | None = None


@router.post("/acquire")
async def acquire(body: AcquireRequest):
    task_id = str(uuid.uuid4())
    pool = await get_pool()
    query_str = body.query or f"{body.provider}:{body.provider_id}"
    await pool.execute(
        "INSERT INTO download_queue (id, user_id, query, provider, provider_id) "
        "VALUES ($1,'internal',$2,$3,$4)",
        task_id, query_str, body.provider, body.provider_id,
    )
    celery_app.send_task(
        "app.tasks.download_provider_track",
        args=[body.provider, body.provider_id, task_id],
        kwargs={"query": body.query},
        task_id=task_id,
    )
    return {"task_id": task_id, "status": "queued"}
