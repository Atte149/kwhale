#!/usr/bin/env python3
"""Full library re-tagging through AcoustID."""
import sys, time
from pathlib import Path

sys.path.insert(0, "/app")
from app.metadata import resolve_metadata

LIBRARY_DIR = Path("/data/library")
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}

files = sorted(f for f in LIBRARY_DIR.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO_EXTS)
total = len(files)
print(f"Found {total} audio files", flush=True)

ok = fail = 0
start = time.time()

for i, fp in enumerate(files):
    fpath = str(fp)
    try:
        meta = resolve_metadata(fpath, force=True)
        if meta and meta.get("title"):
            ok += 1
        else:
            fail += 1
    except Exception:
        fail += 1

    if (i + 1) % 50 == 0:
        elapsed = time.time() - start
        done = i + 1
        rate = done / elapsed * 60
        remaining = max(0, total - done) / rate if rate > 0 else 0
        print(f"Progress: {done}/{total} ({ok} ok, {fail} fail, {rate:.0f}/min, ~{remaining:.0f}min left)", flush=True)

elapsed = time.time() - start
print(f"\nDONE: {ok} matched, {fail} failed in {elapsed:.0f}s ({elapsed/60:.1f}min)", flush=True)
