"""Metadata resolution pipeline: reads existing tags → MusicBrainz fallback."""
import subprocess
from pathlib import Path

import mutagen
import httpx


def resolve_metadata(filepath: str) -> dict | None:
    meta = _read_existing_tags(filepath)
    if meta.get("title") and meta.get("artist"):
        return meta

    # MusicBrainz lookup by AcoustID fingerprint
    acoustid_meta = _acoustid_lookup(filepath)
    if acoustid_meta:
        meta.update(acoustid_meta)
        if meta.get("title") and meta.get("artist"):
            _write_tags(filepath, meta)
            return meta

    return meta if meta.get("title") else None


def _read_existing_tags(filepath: str) -> dict:
    ext = Path(filepath).suffix.lower().lstrip(".")
    mf = mutagen.File(filepath, easy=True)
    if not mf:
        return {"ext": ext}

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
        "ext": ext,
    }


def _acoustid_lookup(filepath: str) -> dict | None:
    try:
        result = subprocess.run(
            ["fpcalc", "-json", filepath],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        import json
        data = json.loads(result.stdout)
        duration = data.get("duration")
        fingerprint = data.get("fingerprint")
        if not fingerprint:
            return None

        r = httpx.get(
            "https://api.acoustid.org/v2/lookup",
            params={
                "client": "kwhale",
                "duration": int(duration),
                "fingerprint": fingerprint,
                "meta": "recordings+releases",
            },
            timeout=10.0,
        )
        results = r.json().get("results", [])
        if not results:
            return None

        recordings = results[0].get("recordings", [])
        if not recordings:
            return None

        rec = recordings[0]
        artists = rec.get("artists", [{}])
        releases = rec.get("releases", [{}])
        release = releases[0] if releases else {}

        return {
            "title": rec.get("title", ""),
            # Full credit (incl. featured artists) goes in `artist`...
            "artist": ", ".join(a.get("name", "") for a in artists),
            # ...but group by the primary artist so featured tracks don't each
            # collapse into one combined-string artist card.
            "albumartist": artists[0].get("name", "") if artists else "",
            "album": release.get("title", ""),
            "year": str(release.get("date", {}).get("year", "")),
        }
    except Exception:
        return None


def _write_tags(filepath: str, meta: dict):
    try:
        mf = mutagen.File(filepath, easy=True)
        if not mf:
            return
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
        mf.save()
    except Exception:
        pass
