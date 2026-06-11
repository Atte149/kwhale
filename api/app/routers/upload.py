"""Upload — accept audio files from the client for tagging and library import."""
import json
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form

from ..auth import current_user

router = APIRouter(prefix="/upload", tags=["upload"])

INCOMING_DIR = Path(os.environ.get("MUSIC_INCOMING_DIR", "/data/incoming"))
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_AUDIO_SIZE = 100 * 1024 * 1024
MAX_COVER_SIZE = 10 * 1024 * 1024


@router.post("")
async def upload_audio(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    artist: str | None = Form(None),
    cover: UploadFile | None = File(None),
    user: str = Depends(current_user),
):
    if not file.filename:
        raise HTTPException(400, "Filename is required")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in AUDIO_EXTS:
        raise HTTPException(400, f"Unsupported file type '{suffix}'. Allowed: {', '.join(sorted(AUDIO_EXTS))}")

    content = await file.read()
    if len(content) > MAX_AUDIO_SIZE:
        raise HTTPException(413, f"File too large ({len(content)} bytes). Max {MAX_AUDIO_SIZE} bytes.")

    upload_id = uuid.uuid4().hex[:12]
    dest_dir = INCOMING_DIR / "upload" / upload_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file.filename

    with open(dest_path, "wb") as f:
        f.write(content)

    if title or artist:
        with open(dest_dir / "metadata.json", "w") as f:
            json.dump({"title": title or "", "artist": artist or ""}, f)

    cover_saved = False
    if cover and cover.filename:
        cover_suffix = Path(cover.filename).suffix.lower()
        if cover_suffix in IMAGE_EXTS:
            cover_content = await cover.read()
            if len(cover_content) <= MAX_COVER_SIZE:
                cover_path = dest_dir / "cover.jpg"
                with open(cover_path, "wb") as f:
                    f.write(cover_content)
                cover_saved = True

    return {
        "status": "accepted",
        "upload_id": upload_id,
        "filename": file.filename,
        "size": len(content),
        "cover_saved": cover_saved,
        "message": "File queued for tagging. It will appear in your library after processing.",
    }
