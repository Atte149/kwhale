"""One-shot bulk retag: scan library, split comma-joined artists,
write proper multi-value ARTIST tags.

Usage:
    python -m app.bulk_retag --dry-run          # preview only
    python -m app.bulk_retag --limit 5          # test on 5 files
    python -m app.bulk_retag                    # full library
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import mutagen

from .artist_splitter import needs_split, split_artist_tag, SEPARATOR_RE
from .multivalue import write_multi_artists, read_multi_artists

LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", "/library"))
BACKUP_DIR = Path(os.environ.get("DATA_DIR", "/data") + "/retag_backups")
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}


def backup_tags(filepath: Path, old_artist: str, old_multi: list[str], new_artists: list[str]) -> dict:
    """Record tag state for rollback (JSONL, not file copy — tags are small)."""
    return {
        "path": str(filepath),
        "old_artist": old_artist,
        "old_multi": old_multi,
        "new_artists": new_artists,
        "timestamp": datetime.now().isoformat(),
    }


def bulk_retag(dry_run: bool = False, limit: int | None = None) -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_file = BACKUP_DIR / f"retag_{datetime.now():%Y%m%d_%H%M%S}.jsonl"

    files = sorted(
        f for f in LIBRARY_DIR.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS
    )

    stats = {
        "total": len(files),
        "needs_split": 0,
        "retagged": 0,
        "skipped": 0,
        "failed": 0,
    }
    backups: list[dict] = []

    for i, fp in enumerate(files):
        fpath = str(fp)
        try:
            mf = mutagen.File(fpath, easy=True)
            if not mf:
                stats["skipped"] += 1
                continue

            artist_val = mf.get("artist", [""])[0] if mf.get("artist") else ""
            if not artist_val:
                # Try multi-value read
                existing_multi = read_multi_artists(fpath)
                artist_val = existing_multi[0] if existing_multi else ""
            else:
                existing_multi = read_multi_artists(fpath)

            # Skip if already has proper multi-value ARTIST (more than 1 value)
            if len(existing_multi) > 1:
                stats["skipped"] += 1
                continue

            if not needs_split(artist_val):
                stats["skipped"] += 1
                continue

            stats["needs_split"] += 1
            artists = split_artist_tag(artist_val)

            if len(artists) <= 1:
                stats["skipped"] += 1
                continue

            rec = backup_tags(fp, artist_val, existing_multi, artists)
            backups.append(rec)

            if dry_run:
                print(f"  DRY: {fp.name} | '{artist_val}' -> {artists}")
                stats["retagged"] += 1
                continue

            if write_multi_artists(fpath, artists):
                stats["retagged"] += 1
                print(f"  OK: {fp.name}: '{artist_val}' -> {artists}")
            else:
                stats["failed"] += 1
                print(f"  FAIL: {fp.name}: write failed")

        except Exception as e:
            print(f"  ERROR: {fpath}: {e}")
            stats["failed"] += 1

        if (i + 1) % 100 == 0:
            print(
                f"Progress: {i+1}/{stats['total']} "
                f"(retagged={stats['retagged']}, failed={stats['failed']})",
                flush=True,
            )

        if limit and (i + 1) >= limit:
            break

    # Write backup file (even in dry-run for audit)
    if backups:
        with open(backup_file, "w") as f:
            for r in backups:
                f.write(json.dumps(r) + "\n")
        print(f"\nBackup: {backup_file} ({len(backups)} records)")

    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk multi-artist retag")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, help="Max files to process")
    args = parser.parse_args()

    print(f"Library: {LIBRARY_DIR}")
    print(f"Backup dir: {BACKUP_DIR}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()

    stats = bulk_retag(dry_run=args.dry_run, limit=args.limit)

    print(f"\n{'='*40}")
    print(f"COMPLETE")
    for k, v in stats.items():
        print(f"  {k}: {v}")