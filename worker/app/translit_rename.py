"""Transliteration-based file/folder renaming.

Scans the library for artist folders with transliterated (Latin) names,
renames them to the canonical Cyrillic form, and updates paths in the
track_features table. Also populates artist_aliases.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import psycopg2

from .translit import (
    normalize_artist_name,
    has_cyrillic,
    has_latin,
    populate_static_aliases,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
NAVIDROME_DB = os.environ.get("NAVIDROME_DB", "/navidrome/navidrome.db")
LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", "/library"))

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}


def get_navidrome_path_map() -> dict[str, str]:
    """Build filepath → navidrome_id map."""
    if not Path(NAVIDROME_DB).exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{NAVIDROME_DB}?mode=ro&immutable=1", uri=True)
        rows = conn.execute("SELECT id, path FROM media_file").fetchall()
        conn.close()
        return {row[1]: row[0] for row in rows}
    except Exception as e:
        print(f"get_navidrome_path_map error: {e}")
        return {}


def transliterate_library(dry_run: bool = False, limit: int | None = None) -> dict:
    """Rename transliterated artist folders to Cyrillic.

    For each top-level artist folder in the library:
      1. If the name is Latin (transliterated), find the canonical Cyrillic form.
      2. Rename the folder.
      3. Update all track_features.filepath for tracks in that folder.
      4. Record the alias in artist_aliases.

    Args:
        dry_run: if True, only report what would be renamed without doing it.
        limit: max folders to process.

    Returns: {
        "total_folders": int,
        "needs_rename": int,
        "renamed": int,
        "skipped": int,
        "failed": int,
        "details": list[dict],
    }
    """
    # First populate static aliases
    if not dry_run:
        populate_static_aliases()

    # Find artist folders (top-level dirs that contain audio files)
    artist_folders: list[Path] = []
    for entry in sorted(LIBRARY_DIR.iterdir()):
        if entry.is_dir():
            # Check if it contains audio files (directly or in subdirs)
            has_audio = any(
                f.suffix.lower() in AUDIO_EXTS
                for f in entry.rglob("*")
                if f.is_file()
            )
            if has_audio:
                artist_folders.append(entry)

    stats = {
        "total_folders": len(artist_folders),
        "needs_rename": 0,
        "renamed": 0,
        "skipped": 0,
        "failed": 0,
        "details": [],
    }

    nav_path_map = get_navidrome_path_map()

    processed = 0
    for folder in artist_folders:
        original_name = folder.name
        processed += 1
        if limit and processed > limit:
            break

        # Skip if already Cyrillic or mixed
        if not has_latin(original_name) or has_cyrillic(original_name):
            stats["skipped"] += 1
            continue

        canonical = normalize_artist_name(original_name)

        if canonical == original_name:
            stats["skipped"] += 1
            continue

        stats["needs_rename"] += 1

        new_path = LIBRARY_DIR / canonical

        # Handle collision: if canonical folder already exists, merge
        if new_path.exists():
            # Merge: move all contents from old to new
            if dry_run:
                stats["details"].append({
                    "old": original_name,
                    "new": canonical,
                    "action": "merge (dry_run)",
                })
                stats["skipped"] += 1
                continue

            try:
                for item in folder.iterdir():
                    dest = new_path / item.name
                    if dest.exists():
                        # Collision on album folder — skip
                        continue
                    shutil.move(str(item), str(dest))
                # Remove empty old folder
                folder.rmdir()

                # Update track_features paths
                _update_paths_in_db(str(folder), str(new_path), nav_path_map)

                stats["renamed"] += 1
                stats["details"].append({
                    "old": original_name,
                    "new": canonical,
                    "action": "merged",
                })
            except Exception as e:
                print(f"Merge error for {original_name}: {e}")
                stats["failed"] += 1
                stats["details"].append({
                    "old": original_name,
                    "new": canonical,
                    "action": f"failed: {e}",
                })
        else:
            # Simple rename
            if dry_run:
                stats["details"].append({
                    "old": original_name,
                    "new": canonical,
                    "action": "rename (dry_run)",
                })
                continue

            try:
                folder.rename(new_path)
                _update_paths_in_db(str(folder), str(new_path), nav_path_map)
                stats["renamed"] += 1
                stats["details"].append({
                    "old": original_name,
                    "new": canonical,
                    "action": "renamed",
                })
            except Exception as e:
                print(f"Rename error for {original_name}: {e}")
                stats["failed"] += 1
                stats["details"].append({
                    "old": original_name,
                    "new": canonical,
                    "action": f"failed: {e}",
                })

    return stats


def _update_paths_in_db(old_prefix: str, new_prefix: str,
                        nav_path_map: dict[str, str]) -> None:
    """Update filepath in track_features for tracks that moved."""
    if not DATABASE_URL:
        return
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                # Update all paths that start with old_prefix
                cur.execute(
                    "UPDATE track_features SET filepath = REPLACE(filepath, %s, %s), "
                    "updated_at = NOW() WHERE filepath LIKE %s",
                    (old_prefix, new_prefix, old_prefix + "%"),
                )
    except Exception as e:
        print(f"_update_paths_in_db error: {e}")