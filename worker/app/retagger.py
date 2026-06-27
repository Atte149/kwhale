"""Smart library retagging: classify tracks by tag quality and re-resolve bad ones.

Classification:
  - bad: empty title/artist, "Unknown Artist", "Various Artists", title = filename,
         title contains artist separators, generic names
  - uncertain: filename parses as Artist-Title but differs from current tags
  - good: tags look valid

Resolution for bad/uncertain:
  1. Parse filename for hints (Artist - Title.mp3)
  2. Shazam lookup (via SOCKS5 proxy)
  3. AcoustID fingerprint fallback
  4. Write new tags, backup old ones to tag_revisions

Used by the Celery task `retag_library` and the API endpoint /api/library/retag.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import psycopg2
import mutagen

DATABASE_URL = os.environ.get("DATABASE_URL", "")
NAVIDROME_DB = os.environ.get("NAVIDROME_DB", "/navidrome/navidrome.db")
LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", "/library"))

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}

BAD_ARTISTS = {
    "unknown artist",
    "various artists",
    "various",
    "unknown",
    "",
    "неизвестный артист",
    "неизвестный исполнитель",
}

SEPARATOR_RE = re.compile(
    r"\s*(?:;|/|&| feat\.?| ft\.?| featuring| with| and )\s*",
    re.IGNORECASE,
)

FILENAME_ARTIST_RE = re.compile(
    r"^(?P<artist>.+?)\s*[-–—]\s*(?P<title>.+?)$",
)

GENERIC_TITLES = {
    "track", "audio", "song", "music", "unknown", "audio track",
    "трек", "аудио", "песня", "музыка",
    "track01", "track1", "track 01", "track 1",
}


def classify_track(meta: dict, filepath: str) -> str:
    """Classify a track's tag quality: 'bad', 'uncertain', or 'good'."""
    artist = (meta.get("artist") or "").strip()
    title = (meta.get("title") or "").strip()
    album = (meta.get("album") or "").strip()

    # Bad: empty or unknown artist
    if artist.lower() in BAD_ARTISTS:
        return "bad"

    # Bad: empty or generic title
    if not title or title.lower() in GENERIC_TITLES:
        return "bad"

    # Bad: title looks like a filename (has extension or path separator)
    if "/" in title or "\\" in title or title.endswith((".mp3", ".flac", ".m4a")):
        return "bad"

    # Bad: title contains artist separators (likely "Artist1 & Artist2 - Title")
    if SEPARATOR_RE.search(title) and " - " not in title:
        # If title itself contains feat./& etc, it might be "Artist feat. X"
        # but if it's "Artist1 & Artist2" as the title, that's bad
        if SEPARATOR_RE.search(title) and not re.match(r"^[A-Z]", title, re.IGNORECASE):
            return "bad"

    # Uncertain: filename looks like "Artist - Title" but differs from tags
    fname = Path(filepath).stem
    m = FILENAME_ARTIST_RE.match(fname)
    if m:
        fn_artist = m.group("artist").strip()
        fn_title = m.group("title").strip()
        # If filename artist differs significantly from tag artist
        if fn_artist.lower() not in artist.lower() and artist.lower() not in fn_artist.lower():
            return "uncertain"

    # Good: has artist and title that don't look like junk
    return "good"


def parse_filename_hint(filepath: str) -> dict | None:
    """Try to extract Artist and Title from filename.

    Supports:
      - "Artist - Title.mp3"
      - "Artist - Album - Title.mp3"
      - "01 - Title.mp3" (title only)
    """
    fname = Path(filepath).stem
    parts = re.split(r"\s*[-–—]\s*", fname, maxsplit=2)

    if len(parts) >= 3:
        # Artist - Album - Title
        return {"artist": parts[0].strip(), "album": parts[1].strip(), "title": parts[2].strip()}
    if len(parts) == 2:
        # Artist - Title or 01 - Title
        if re.match(r"^\d+$", parts[0]):
            # Track number - Title
            return {"title": parts[1].strip()}
        return {"artist": parts[0].strip(), "title": parts[1].strip()}
    return None


def read_tags(filepath: str) -> dict:
    """Read current tags from audio file."""
    mf = mutagen.File(filepath, easy=True)
    if not mf:
        return {"ext": Path(filepath).suffix.lstrip(".").lower()}

    def _get(key):
        v = mf.get(key)
        return v[0] if v else ""

    return {
        "title": _get("title"),
        "artist": _get("artist"),
        "albumartist": _get("albumartist"),
        "album": _get("album"),
        "track_number": int(_get("tracknumber").split("/")[0]) if _get("tracknumber") else 0,
        "year": _get("date")[:4] if _get("date") else "",
        "genre": _get("genre"),
        "compilation": bool(mf.get("compilation")),
        "ext": Path(filepath).suffix.lstrip(".").lower(),
    }


def write_tags(filepath: str, meta: dict) -> bool:
    """Write tags to audio file."""
    try:
        mf = mutagen.File(filepath, easy=True)
        if not mf:
            return False
        if meta.get("title"):
            mf["title"] = [meta["title"]]
        if meta.get("artist"):
            mf["artist"] = [meta["artist"]]
        if meta.get("album"):
            mf["album"] = [meta["album"]]
        if meta.get("albumartist"):
            mf["albumartist"] = [meta["albumartist"]]
        if meta.get("year"):
            mf["date"] = [meta["year"]]
        if meta.get("genre"):
            mf["genre"] = [meta["genre"]]
        mf.save()
        return True
    except Exception as e:
        print(f"write_tags error for {filepath}: {e}")
        return False


def backup_tags(filepath: str, old_tags: dict, new_tags: dict, navidrome_id: str | None,
                source: str, classification: str) -> None:
    """Backup old tags to tag_revisions table."""
    if not DATABASE_URL:
        return
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO tag_revisions (navidrome_id, filepath, old_tags, new_tags, source, classification) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (navidrome_id, filepath, json.dumps(old_tags), json.dumps(new_tags), source, classification),
                )
    except Exception as e:
        print(f"backup_tags error: {e}")


def get_navidrome_id_map() -> dict[str, str]:
    """Build filepath → navidrome_id map from Navidrome's DB."""
    if not Path(NAVIDROME_DB).exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{NAVIDROME_DB}?mode=ro&immutable=1", uri=True)
        rows = conn.execute("SELECT id, path FROM media_file").fetchall()
        conn.close()
        return {row[1]: row[0] for row in rows}
    except Exception as e:
        print(f"get_navidrome_id_map error: {e}")
        return {}


def retag_track(filepath: str, navidrome_id: str | None = None, force: bool = False) -> dict:
    """Classify and retag a single track.

    Returns: {
        "filepath": str,
        "classification": "bad"|"uncertain"|"good",
        "action": "skipped"|"retagged"|"failed",
        "old_tags": dict,
        "new_tags": dict | None,
        "source": str,
    }
    """
    from .metadata_proxy import resolve_metadata_remote

    old_tags = read_tags(filepath)
    classification = classify_track(old_tags, filepath)

    if classification == "good" and not force:
        return {
            "filepath": filepath,
            "classification": "good",
            "action": "skipped",
            "old_tags": old_tags,
            "new_tags": None,
            "source": "existing",
        }

    # Build hint from filename
    hint = parse_filename_hint(filepath)

    # Try Shazam + AcoustID
    new_meta = resolve_metadata_remote(filepath, hint=hint)

    if not new_meta or not new_meta.get("title"):
        # Failed to resolve
        backup_tags(filepath, old_tags, {}, navidrome_id, "failed", classification)
        return {
            "filepath": filepath,
            "classification": classification,
            "action": "failed",
            "old_tags": old_tags,
            "new_tags": None,
            "source": "failed",
        }

    # Merge: keep existing good fields, override with resolved
    merged = {**old_tags, **new_meta}

    # Write new tags
    if write_tags(filepath, merged):
        backup_tags(filepath, old_tags, merged, navidrome_id, new_meta.get("_source", "remote"), classification)
        return {
            "filepath": filepath,
            "classification": classification,
            "action": "retagged",
            "old_tags": old_tags,
            "new_tags": merged,
            "source": new_meta.get("_source", "remote"),
        }

    backup_tags(filepath, old_tags, {}, navidrome_id, "write_failed", classification)
    return {
        "filepath": filepath,
        "classification": classification,
        "action": "failed",
        "old_tags": old_tags,
        "new_tags": None,
        "source": "write_failed",
    }


def scan_library() -> list[dict]:
    """Scan the entire library, classify all tracks, return stats.

    Does NOT retag — only classifies. Used for the /api/library/retag/status endpoint.
    """
    files = sorted(
        f for f in LIBRARY_DIR.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS
    )
    results = []
    for fp in files:
        meta = read_tags(str(fp))
        classification = classify_track(meta, str(fp))
        results.append({
            "filepath": str(fp),
            "classification": classification,
            "artist": meta.get("artist", ""),
            "title": meta.get("title", ""),
        })
    return results


def retag_library(force: bool = False, limit: int | None = None) -> dict:
    """Scan library, retag bad + uncertain tracks. Returns summary stats.

    Args:
        force: if True, retag even 'good' tracks
        limit: max tracks to retag (None = all)

    Returns: {
        "total": int,
        "good": int,
        "bad": int,
        "uncertain": int,
        "retagged": int,
        "failed": int,
        "skipped": int,
    }
    """
    files = sorted(
        f for f in LIBRARY_DIR.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS
    )

    # Build navidrome_id map for backup
    id_map = get_navidrome_id_map()

    stats = {"total": len(files), "good": 0, "bad": 0, "uncertain": 0,
             "retagged": 0, "failed": 0, "skipped": 0}
    processed = 0

    for fp in files:
        fpath = str(fp)
        # Find navidrome_id by trying relative path
        rel = str(fp.relative_to(LIBRARY_DIR)) if LIBRARY_DIR in fp.parents else fpath
        nav_id = id_map.get(rel) or id_map.get(fpath)

        result = retag_track(fpath, nav_id, force=force)
        stats[result["classification"]] = stats.get(result["classification"], 0) + 1
        stats[result["action"]] = stats.get(result["action"], 0) + 1

        processed += 1
        if processed % 50 == 0:
            print(f"Retag progress: {processed}/{stats['total']} "
                  f"({stats['retagged']} retagged, {stats['failed']} failed)", flush=True)

        if limit and processed >= limit:
            break

    return stats