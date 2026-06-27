"""Split merged artists in library files.

Many tracks have a single `artist` tag like "Artist1 & Artist2" or
"Artist1 feat. Artist2" instead of a proper multi-value `artists` tag.
This module:
  1. Finds tracks where `artist` contains separators but `artists` tag is missing.
  2. Splits the artist tag using the same logic as tagging.extract_all_artists.
  3. Writes a multi-value `artists` tag to the file (VorBis: multiple ARTISTS
     fields; MP3: TPE1 with null separator).
  4. Sets `albumartist` to the primary (first) artist.
  5. Updates `all_artists` in track_features.
  6. Optionally moves the file to a new folder based on the primary artist.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import mutagen

from .tagging import extract_all_artists

DATABASE_URL = os.environ.get("DATABASE_URL", "")
NAVIDROME_DB = os.environ.get("NAVIDROME_DB", "/navidrome/navidrome.db")
LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", "/library"))

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}

# Separators that indicate a merged artist tag (besides feat./ft. which
# extract_all_artists already handles via the title).
SEPARATOR_RE = re.compile(
    r"\s*(?:;|/|&|,| and )\s*",
    re.IGNORECASE,
)


def needs_split(artist_tag: str, artists_tag: str | list | None) -> bool:
    """Check if a track's artist tag needs splitting.

    True when:
      - `artists` tag is missing/empty AND
      - `artist` tag contains a separator (&, /, ;, feat., etc.)
    """
    if artists_tag:
        # Already has multi-value artists tag — no split needed
        return False
    if not artist_tag:
        return False
    # Check for separators
    if SEPARATOR_RE.search(artist_tag):
        return True
    # Check for feat./ft. in artist (uncommon but possible)
    if re.search(r"\bfeat\.?|\bft\.?|\bfeaturing\b", artist_tag, re.IGNORECASE):
        return True
    return False


def split_artist_tag(artist: str) -> list[str]:
    """Split a merged artist string into individual artists."""
    # Use extract_all_artists with the artist tag as both artists_tag and artist_tag
    # This handles feat. in the artist field too
    return extract_all_artists(None, artist)


def write_artists_tag(filepath: str, artists: list[str]) -> bool:
    """Write a multi-value `artists` tag to the file.

    For VorBis (FLAC/Ogg): writes multiple `ARTISTS` fields.
    For MP3/M4A: uses `artists` easy-tag which maps to TPE1 (multi-value).
    """
    try:
        mf = mutagen.File(filepath, easy=True)
        if not mf:
            return False
        mf["artists"] = artists
        mf.save()
        return True
    except Exception as e:
        print(f"write_artists_tag error for {filepath}: {e}")
        return False


def set_albumartist(filepath: str, primary_artist: str) -> bool:
    """Set albumartist to the primary (first) artist."""
    try:
        mf = mutagen.File(filepath, easy=True)
        if not mf:
            return False
        mf["albumartist"] = [primary_artist]
        mf.save()
        return True
    except Exception as e:
        print(f"set_albumartist error for {filepath}: {e}")
        return False


def get_navidrome_tracks() -> list[tuple[str, str, str, str]]:
    """Get (id, path, title, artist) for all tracks from Navidrome DB."""
    if not Path(NAVIDROME_DB).exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{NAVIDROME_DB}?mode=ro&immutable=1", uri=True)
        rows = conn.execute("SELECT id, path, title, artist FROM media_file").fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"get_navidrome_tracks error: {e}")
        return []


def split_library_artists(move_files: bool = False, limit: int | None = None) -> dict:
    """Scan library, split merged artist tags, write multi-value `artists` tag.

    Args:
        move_files: if True, move files to new folders based on primary artist.
        limit: max tracks to process.

    Returns: {
        "total": int,
        "needs_split": int,
        "split": int,
        "skipped": int,
        "failed": int,
        "moved": int,
    }
    """
    import psycopg2

    files = sorted(
        f for f in LIBRARY_DIR.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS
    )

    # Build navidrome_id map
    nav_tracks = get_navidrome_tracks()
    path_to_id = {row[1]: row[0] for row in nav_tracks}

    stats = {
        "total": len(files), "needs_split": 0, "split": 0,
        "skipped": 0, "failed": 0, "moved": 0,
    }
    processed = 0

    for fp in files:
        fpath = str(fp)
        try:
            mf = mutagen.File(fpath, easy=True)
            if not mf:
                stats["skipped"] += 1
                continue

            artist_val = mf.get("artist", [""])[0]
            artists_val = mf.get("artists", [])

            if not needs_split(artist_val, artists_val):
                stats["skipped"] += 1
                continue

            stats["needs_split"] += 1
            artists = split_artist_tag(artist_val)

            if len(artists) <= 1:
                stats["skipped"] += 1
                continue

            primary = artists[0]

            # Write multi-value artists tag
            if not write_artists_tag(fpath, artists):
                stats["failed"] += 1
                continue

            # Set albumartist to primary
            set_albumartist(fpath, primary)

            # Update all_artists in track_features
            rel = str(fp.relative_to(LIBRARY_DIR)) if LIBRARY_DIR in fp.parents else fpath
            nav_id = path_to_id.get(rel) or path_to_id.get(fpath)
            if nav_id and DATABASE_URL:
                all_artists_text = " ".join(artists)
                try:
                    with psycopg2.connect(DATABASE_URL) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE track_features "
                                "SET all_artists=%s, all_artists_text=%s, "
                                "artist=%s, artists_indexed_at=NOW(), updated_at=NOW() "
                                "WHERE navidrome_id=%s",
                                (artists, all_artists_text, primary, nav_id),
                            )
                except Exception as e:
                    print(f"DB update error for {nav_id}: {e}")

            # Optionally move file to new folder
            if move_files:
                new_dir = LIBRARY_DIR / primary
                new_dir.mkdir(parents=True, exist_ok=True)
                dest = new_dir / fp.name
                if not dest.exists():
                    import shutil
                    shutil.move(fpath, str(dest))
                    stats["moved"] += 1

            stats["split"] += 1
        except Exception as e:
            print(f"split error for {fpath}: {e}")
            stats["failed"] += 1

        processed += 1
        if processed % 50 == 0:
            print(f"Split progress: {processed}/{stats['total']} "
                  f"({stats['split']} split, {stats['failed']} failed)", flush=True)

        if limit and processed >= limit:
            break

    return stats