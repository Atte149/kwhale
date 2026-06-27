"""Artist splitting endpoints: split merged artist tags into multi-value."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import current_user
from ..tasks import celery_app

router = APIRouter(prefix="/library", tags=["artist-split"])


class SplitRequest(BaseModel):
    move_files: bool = False
    limit: int | None = None


@router.post("/artists/split")
async def split_artists(req: SplitRequest, user: str = Depends(current_user)):
    """Trigger splitting of merged artist tags.

    Finds tracks with `artist` like "A & B" or "A feat. B" and:
      1. Splits into individual artists
      2. Writes multi-value `artists` tag
      3. Sets `albumartist` to primary (first) artist
      4. Updates `all_artists` in track_features
      5. Optionally moves file to new folder (move_files=True)
    """
    task = celery_app.send_task(
        "app.tasks.split_library_artists",
        kwargs={"move_files": req.move_files, "limit": req.limit},
    )
    return {"task_id": task.id, "status": "queued"}


@router.get("/artists/split/result/{task_id}")
async def split_result(task_id: str, user: str = Depends(current_user)):
    """Check artist split task status."""
    result = celery_app.AsyncResult(task_id)
    resp = {"task_id": task_id, "status": result.state}
    if result.state == "SUCCESS":
        resp["result"] = result.result
    elif result.state == "FAILURE":
        resp["error"] = str(result.result)
    return resp