"""Remote metadata resolution proxy: delegates Shazam/AcoustID to the tagger service.

The tagger container has shazamio, fpcalc (chromaprint), and SOCKS5 proxy access.
The worker does not — so we call the tagger's HTTP API at /tag with force=True.

Falls back to direct AcoustID lookup (fpcalc is installed in the worker too).
"""
from __future__ import annotations

import json
import os
import subprocess

import httpx

TAGGER_URL = os.environ.get("TAGGER_URL", "http://tagger:8093")
ACOUSTID_CLIENT = "v8pQ6oyB"


def resolve_metadata_remote(filepath: str, hint: dict | None = None) -> dict | None:
    """Resolve metadata via tagger service (Shazam + AcoustID).

    Returns dict with title, artist, album, etc. or None.
    """
    # Try the tagger service first — it has Shazam + proxy access
    try:
        resp = httpx.post(
            f"{TAGGER_URL}/resolve",
            json={"filepath": filepath, "hint": hint},
            timeout=45.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("title"):
                data["_source"] = data.get("_source", "tagger")
                return data
    except Exception as e:
        print(f"Tagger service resolve failed: {e}")

    # Fallback: direct AcoustID lookup (fpcalc is in the worker image)
    return _acoustid_lookup(filepath)


def _acoustid_lookup(filepath: str) -> dict | None:
    """Direct AcoustID fingerprint lookup."""
    try:
        result = subprocess.run(
            ["fpcalc", "-json", filepath],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        duration = data.get("duration")
        fingerprint = data.get("fingerprint")
        if not fingerprint:
            return None

        import httpx as _httpx
        r = _httpx.get(
            "https://api.acoustid.org/v2/lookup",
            params={
                "client": ACOUSTID_CLIENT,
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
            "artist": ", ".join(a.get("name", "") for a in artists),
            "albumartist": artists[0].get("name", "") if artists else "",
            "album": release.get("title", ""),
            "year": str(release.get("date", {}).get("year", "")),
            "_source": "acoustid",
        }
    except Exception as e:
        print(f"AcoustID lookup error: {e}")
        return None