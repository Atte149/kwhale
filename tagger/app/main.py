"""Auto-tagger service.
Watches incoming/ for new audio files, resolves metadata, moves to library/.
Ported from musicbrain/tagger with minimal changes.
"""
import asyncio
import os
import shutil
import uuid
from pathlib import Path

import asyncpg
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel

from .metadata import resolve_metadata
from .organizer import build_path

INCOMING_DIR = Path(os.environ.get("INCOMING_DIR", "/data/incoming"))
LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", "/data/library"))
NAVIDROME_URL = os.environ.get("NAVIDROME_URL", "http://navidrome:4533")
NAVIDROME_USERNAME = os.environ.get("NAVIDROME_USERNAME", "admin")
NAVIDROME_PASSWORD = os.environ.get("NAVIDROME_PASSWORD", "admin")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
TAGGER_MAX_CONCURRENT = int(os.environ.get("TAGGER_MAX_CONCURRENT", "20"))

app = FastAPI(title="KWhale Tagger")
_semaphore = asyncio.Semaphore(TAGGER_MAX_CONCURRENT)
_scan_debounce: asyncio.Task | None = None


class TagRequest(BaseModel):
    filepath: str
    force: bool = False


def _read_metadata_json(filepath: Path) -> dict | None:
    meta_path = filepath.parent / "metadata.json"
    if not meta_path.is_file():
        return None
    try:
        import json
        data = json.loads(meta_path.read_text())
        if data.get("title") or data.get("artist"):
            return data
    except Exception:
        pass
    return None


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.post("/tag")
async def tag_file(req: TagRequest, bg: BackgroundTasks):
    bg.add_task(_process_file, Path(req.filepath), req.force)
    return {"status": "queued", "filepath": req.filepath, "force": req.force}


async def _process_file(filepath: Path, force: bool = False):
    async with _semaphore:
        task_id = filepath.parent.name
        try:
            meta_hint = _read_metadata_json(filepath)
            meta = await asyncio.to_thread(resolve_metadata, str(filepath), force, meta_hint)
            if not meta:
                failed = INCOMING_DIR / "failed" / filepath.name
                failed.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(filepath), str(failed))
                await _update_queue(task_id, "failed",
                                    error="Could not resolve metadata (no tags / no AcoustID match)")
                return

            dest = LIBRARY_DIR / build_path(meta)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(filepath), str(dest))

            # If the worker also dropped cover.jpg into the same incoming
            # dir, move it next to the audio so Navidrome picks it up on its
            # next scan (it reads cover.jpg / folder.jpg from each album dir).
            src_cover = filepath.parent / "cover.jpg"
            if src_cover.is_file():
                dest_cover = dest.parent / "cover.jpg"
                # Don't clobber a higher-res cover that already lives there.
                if not dest_cover.exists() or dest_cover.stat().st_size < src_cover.stat().st_size:
                    shutil.move(str(src_cover), str(dest_cover))

            # Track is in the library; Navidrome will index it on the next scan.
            await _update_queue(task_id, "done", pct=100)
            await _trigger_navidrome_scan()
        except Exception as e:
            print(f"Tagger error for {filepath}: {e}")
            await _update_queue(task_id, "failed", error=str(e)[:500])


async def _update_queue(task_id: str, status: str, error: str | None = None,
                        pct: float | None = None):
    """Reflect the tagging outcome back to download_queue so the acquire UI can
    progress past 'tagging'. No-op for files that didn't come from /discover."""
    if not DATABASE_URL:
        return
    try:
        uuid.UUID(task_id)  # acquire task_ids are UUIDs; skip anything else
    except (ValueError, TypeError, AttributeError):
        return
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute(
                "UPDATE download_queue SET status=$1, error=$2, "
                "progress_pct=COALESCE($3, progress_pct), updated_at=NOW() "
                "WHERE id=$4",
                status, error, pct, task_id,
            )
        finally:
            await conn.close()
    except Exception as e:
        print(f"Tagger queue update failed for {task_id}: {e}")


async def _trigger_navidrome_scan():
    global _scan_debounce
    if _scan_debounce and not _scan_debounce.done():
        _scan_debounce.cancel()
    _scan_debounce = asyncio.create_task(_debounced_scan())


async def _debounced_scan():
    await asyncio.sleep(60)
    import hashlib, secrets, httpx
    salt = secrets.token_hex(6)
    token = hashlib.md5(f"{NAVIDROME_PASSWORD}{salt}".encode()).hexdigest()
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.get(
                f"{NAVIDROME_URL}/rest/startScan.view",
                params={"u": NAVIDROME_USERNAME, "t": token, "s": salt,
                        "v": "1.16.1", "c": "kwhale-tagger", "f": "json"},
            )
        except Exception:
            pass
