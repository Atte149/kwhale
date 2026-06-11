"""Metadata resolution pipeline: reads existing tags → Shazam (via SOCKS5 VPN) → AcoustID fallback."""
import json
import os
import subprocess
from pathlib import Path

import mutagen
import httpx

ACOUSTID_CLIENT = "v8pQ6oyB"
SHAZAM_PROXY = os.environ.get("SHAZAM_PROXY", "socks5h://singbox:2080")


def resolve_metadata(filepath: str, force: bool = False, meta_hint: dict | None = None) -> dict | None:
    meta = _read_existing_tags(filepath)
    if meta_hint:
        if meta_hint.get("title"):
            meta["title"] = meta_hint["title"]
        if meta_hint.get("artist"):
            meta["artist"] = meta_hint["artist"]
    if not force and meta.get("title") and meta.get("artist"):
        _write_tags(filepath, meta)
        return meta

    shazam_meta = _shazam_lookup(filepath)
    if shazam_meta:
        meta.update(shazam_meta)
        if meta.get("title") and meta.get("artist"):
            _write_tags(filepath, meta)
            return meta

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
        data = json.loads(result.stdout)
        duration = data.get("duration")
        fingerprint = data.get("fingerprint")
        if not fingerprint:
            return None

        r = httpx.get(
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
        }
    except Exception:
        return None


def _shazam_lookup(filepath: str) -> dict | None:
    try:
        from shazamio import Shazam
        from aiohttp import ClientSession, ClientTimeout
        from aiohttp_socks import ProxyConnector
        import asyncio

        async def _recognize():
            connector = ProxyConnector.from_url(SHAZAM_PROXY)
            timeout = ClientTimeout(total=30)
            async with ClientSession(connector=connector, timeout=timeout) as session:
                s = Shazam()

                async def _proxy_request(method, url, **kwargs):
                    async with session.request(method, url, **kwargs) as resp:
                        text = await resp.text()
                        if resp.status == 200:
                            return json.loads(text)
                        raise Exception(f"Shazam HTTP {resp.status}: {text[:200]}")

                s.http_client.request = _proxy_request
                return await s.recognize(filepath)

        out = asyncio.run(_recognize())
        track = out.get("track", {})
        title = track.get("title", "")
        artist = track.get("subtitle", "")
        if not title or not artist:
            return None
        genre_data = track.get("genres", {})
        genre = genre_data.get("primary", "") if isinstance(genre_data, dict) else ""
        return {
            "title": title,
            "artist": artist,
            "genre": genre,
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
        if meta.get("genre"):
            mf["genre"] = [meta["genre"]]
        mf.save()
    except Exception:
        pass
