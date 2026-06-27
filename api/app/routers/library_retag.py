"""Library retagging endpoints: scan, classify, and retag tracks."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..tasks import celery_app

router = APIRouter(prefix="/library", tags=["library-retag"])


class RetagRequest(BaseModel):
    force: bool = False
    limit: int | None = None


@router.get("/retag/status")
async def retag_status(user: str = Depends(current_user)):
    """Scan library and return classification stats (bad/uncertain/good).

    Does NOT modify anything — just reports which tracks need retagging.
    """
    task = celery_app.send_task("app.tasks.scan_library_tags")
    return {"task_id": task.id, "status": "scanning"}


@router.post("/retag")
async def retag_library(req: RetagRequest, user: str = Depends(current_user)):
    """Trigger library retagging. Bad + uncertain tracks are re-resolved
    via Shazam/AcoustID and tags are rewritten. Old tags backed up to tag_revisions.

    Set force=True to retag even tracks with seemingly-valid tags.
    """
    task = celery_app.send_task(
        "app.tasks.retag_library",
        kwargs={"force": req.force, "limit": req.limit},
    )
    return {"task_id": task.id, "status": "queued", "force": req.force}


@router.get("/retag/result/{task_id}")
async def retag_result(task_id: str, user: str = Depends(current_user)):
    """Check retag/scan task status and result."""
    result = celery_app.AsyncResult(task_id)
    resp = {"task_id": task_id, "status": result.state}
    if result.state == "SUCCESS":
        resp["result"] = result.result
    elif result.state == "FAILURE":
        resp["error"] = str(result.result)
    return resp


@router.get("/retag/history")
async def retag_history(limit: int = 50, user: str = Depends(current_user)):
    """Return recent tag revisions (backup history)."""
    from ..db import get_pool
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, navidrome_id, filepath, old_tags, new_tags, source, classification, created_at "
        "FROM tag_revisions ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    return {
        "revisions": [
            {
                "id": r["id"],
                "navidrome_id": r["navidrome_id"],
                "filepath": r["filepath"],
                "old_tags": r["old_tags"],
                "new_tags": r["new_tags"],
                "source": r["source"],
                "classification": r["classification"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    }