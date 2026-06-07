"""ICM Music source plugin — byicloud.online Partner API.

Set ICM_PARTNER_KEY (and optionally ICM_BASE_URL, ICM_REGION,
ICM_PARTNER_USER_ID, ICM_QUALITY) in worker/.env to enable this provider.

Docs: https://byicloud.online/partners/api-docs
  - search:   GET  /api/partner/search?q=&region=&limit=
              -> {items: [...]}, mixed entities; a track is
                 isArtist==false && isAlbum==false.
  - resolve:  POST /api/partner/track {trackId, region, quality}
              -> {url, source, ...}  (signed stream URL, ~600s TTL)
"""
import os
import re
from pathlib import Path

import httpx

from .base import BaseProvider, TrackMeta

# Map response Content-Type → file extension. The partner audio endpoint may
# serve mp3 even for "apple" source, so the extension is decided by the actual
# bytes delivered, not by the catalogue source.
_CT_EXT = {
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "m4a",
    "audio/x-m4a": "m4a",
    "audio/flac": "flac",
    "audio/ogg": "ogg",
}


class ICMProvider(BaseProvider):
    name = "icm"
    # Preferred over Yandex (priority 20): ICM is the primary catalogue.
    priority = 10

    def __init__(self):
        self.partner_key = os.environ.get("ICM_PARTNER_KEY", "")
        self.user_id = os.environ.get("ICM_PARTNER_USER_ID", "")
        self.base_url = os.environ.get("ICM_BASE_URL", "https://byicloud.online").rstrip("/")
        self.region = os.environ.get("ICM_REGION", "us")
        self.quality = os.environ.get("ICM_QUALITY", "256K")

    def is_available(self) -> bool:
        return bool(self.partner_key)

    def _headers(self) -> dict:
        h = {"X-Partner-Key": self.partner_key}
        if self.user_id:
            h["X-Partner-User-Id"] = self.user_id
        return h

    def _client(self, timeout: float = 30.0) -> httpx.Client:
        # A fresh client per call — keeps the provider stateless and avoids the
        # connection-pool leak of holding a long-lived client on the instance.
        return httpx.Client(timeout=timeout, headers=self._headers())

    # ── search ──────────────────────────────────────────────────────────────
    def search(self, query: str, limit: int = 10) -> list[TrackMeta]:
        try:
            with self._client() as client:
                r = client.get(
                    f"{self.base_url}/api/partner/search",
                    params={"q": query, "region": self.region, "limit": limit},
                )
                r.raise_for_status()
                items = r.json().get("items", [])
        except Exception as e:
            print(f"ICM search error for {query!r}: {e}")
            return []

        # items mixes artists/albums/tracks; keep only tracks.
        tracks = [it for it in items if not it.get("isArtist") and not it.get("isAlbum")]
        return [self._to_meta(t) for t in tracks[:limit]]

    def _to_meta(self, t: dict) -> TrackMeta:
        # ICM mirrors Apple artwork at {1000x1000, 600x600, 300x300} sizes via
        # string substitution. Keep the 1000x1000 URL so the worker downloads
        # the high-res cover alongside the audio (Navidrome picks up
        # cover.jpg from the album folder on its next scan). Clients that
        # only need a thumbnail can downsize by replacing the size token.
        cover = t.get("cover") or ""
        duration_ms = t.get("duration") or 0
        return TrackMeta(
            provider=self.name,
            provider_id=str(t.get("id", "")),
            title=t.get("title", ""),
            artist=t.get("artist") or t.get("artistName") or "",
            album=t.get("album") or "",
            duration_sec=int(duration_ms) // 1000 if duration_ms else 0,
            cover_url=cover,
            preview_url=t.get("preview") or "",
            raw=t,
        )

    # ── resolve / download ──────────────────────────────────────────────────
    def _resolve_track(self, provider_id: str) -> dict:
        """POST /track → playback info dict. Follows a single region redirect
        (451 region_unavailable → required_region) to avoid loops."""
        region = self.region
        tried: set[str] = set()
        with self._client() as client:
            while True:
                tried.add(region)
                r = client.post(
                    f"{self.base_url}/api/partner/track",
                    json={"trackId": str(provider_id), "region": region, "quality": self.quality},
                )
                if r.status_code == 451:
                    req = (r.json() or {}).get("required_region")
                    if req and req not in tried:
                        region = req
                        continue
                r.raise_for_status()
                return r.json()

    def resolve(self, provider_id: str) -> str | None:
        try:
            return self._resolve_track(provider_id).get("url")
        except Exception as e:
            print(f"ICM resolve error for {provider_id}: {e}")
            return None

    def download(self, provider_id: str, dest_dir: Path) -> Path | None:
        try:
            info = self._resolve_track(provider_id)
        except Exception as e:
            print(f"ICM download resolve error for {provider_id}: {e}")
            return None

        url = info.get("url")
        if not url:
            print(f"ICM download: no stream url for {provider_id}")
            return None

        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            # No auth header needed — the signature is in the URL query.
            with httpx.Client(timeout=120.0) as client:
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    ext = self._ext_from_response(resp, info.get("source"))
                    dest = dest_dir / f"{provider_id}.{ext}"
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=65536):
                            f.write(chunk)
            return dest
        except Exception as e:
            print(f"ICM download stream error for {provider_id}: {e}")
            return None

    @staticmethod
    def _ext_from_response(resp: httpx.Response, source: str | None) -> str:
        """Decide the file extension from the actual response, not the source."""
        ct = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ct in _CT_EXT:
            return _CT_EXT[ct]
        cd = resp.headers.get("content-disposition") or ""
        m = re.search(r'filename="?[^"]*\.([a-z0-9]{2,4})"?', cd, re.I)
        if m:
            return m.group(1).lower()
        return "mp3" if source == "vk" else "m4a"

    # The base class's fetch_cover() default works for ICM (it just GETs the
    # URL) — no override needed. Yandex covers also fit the default pattern.
