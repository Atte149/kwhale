"""Transliteration endpoints: rename transliterated artist folders to Cyrillic."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import current_user
from ..tasks import celery_app

router = APIRouter(prefix="/library", tags=["translit"])


class TranslitRequest(BaseModel):
    dry_run: bool = False
    limit: int | None = None


@router.post("/translit")
async def transliterate_library(req: TranslitRequest, user: str = Depends(current_user)):
    """Trigger transliteration-based folder renaming.

    Renames transliterated (Latin) artist folders to canonical Cyrillic form.
    Set dry_run=True to preview without making changes.
    """
    task = celery_app.send_task(
        "app.tasks.transliterate_library",
        kwargs={"dry_run": req.dry_run, "limit": req.limit},
    )
    return {"task_id": task.id, "status": "queued", "dry_run": req.dry_run}


@router.get("/translit/result/{task_id}")
async def translit_result(task_id: str, user: str = Depends(current_user)):
    """Check transliteration task status."""
    result = celery_app.AsyncResult(task_id)
    resp = {"task_id": task_id, "status": result.state}
    if result.state == "SUCCESS":
        resp["result"] = result.result
    elif result.state == "FAILURE":
        resp["error"] = str(result.result)
    return resp