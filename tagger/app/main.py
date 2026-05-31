"""Auto-tagger service.
Watches incoming/ for new audio files, resolves metadata, moves to library/.
Ported from musicbrain/tagger with minimal changes.
"""
import asyncio
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel

from .metadata import resolve_metadata
from .organizer import build_path

INCOMING_DIR = Path(os.environ.get("INCOMING_DIR", "/data/incoming"))
LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", "/data/library"))
NAVIDROME_URL = os.environ.get("NAVIDROME_URL", "http://navidrome:4533")
NAVIDROME_USERNAME = os.environ.get("NAVIDROME_USERNAME", "admin")
NAVIDROME_PASSWORD = os.environ.get("NAVIDROME_PASSWORD", "admin")
TAGGER_MAX_CONCURRENT = int(os.environ.get("TAGGER_MAX_CONCURRENT", "20"))

app = FastAPI(title="KWhale Tagger")
_semaphore = asyncio.Semaphore(TAGGER_MAX_CONCURRENT)
_scan_debounce: asyncio.Task | None = None


class TagRequest(BaseModel):
    filepath: str


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.post("/tag")
async def tag_file(req: TagRequest, bg: BackgroundTasks):
    bg.add_task(_process_file, Path(req.filepath))
    return {"status": "queued", "filepath": req.filepath}


async def _process_file(filepath: Path):
    async with _semaphore:
        try:
            meta = await asyncio.to_thread(resolve_metadata, str(filepath))
            if not meta:
                failed = INCOMING_DIR / "failed" / filepath.name
                failed.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(filepath), str(failed))
                return

            dest = LIBRARY_DIR / build_path(meta)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(filepath), str(dest))

            await _trigger_navidrome_scan()
        except Exception as e:
            print(f"Tagger error for {filepath}: {e}")


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
