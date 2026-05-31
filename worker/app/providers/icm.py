"""ICM Music source plugin.

ICM is a music streaming service with a partner API. Set ICM_PARTNER_KEY
in worker/.env to enable this provider.
"""
import os
from pathlib import Path

import httpx

from .base import BaseProvider, TrackMeta


class ICMProvider(BaseProvider):
    name = "icm"

    def __init__(self):
        self.partner_key = os.environ.get("ICM_PARTNER_KEY", "")
        self.user_id = os.environ.get("ICM_PARTNER_USER_ID", "")
        self.default_region = os.environ.get("ICM_DEFAULT_REGION", "us")
        self.fallback_region = os.environ.get("ICM_FALLBACK_REGION", "ru")
        self.base_url = "https://music.icm.hk/api/v2"
        self._client = httpx.Client(timeout=20.0)

    def is_available(self) -> bool:
        return bool(self.partner_key)

    def _headers(self) -> dict:
        h = {"X-Partner-Key": self.partner_key}
        if self.user_id:
            h["X-Partner-User-Id"] = self.user_id
        return h

    def search(self, query: str, limit: int = 10) -> list[TrackMeta]:
        try:
            r = self._client.get(
                f"{self.base_url}/search",
                params={"q": query, "type": "track", "limit": limit, "region": self.default_region},
                headers=self._headers(),
            )
            r.raise_for_status()
            tracks = r.json().get("tracks", {}).get("items", [])
            return [self._to_meta(t) for t in tracks]
        except Exception:
            return []

    def _to_meta(self, t: dict) -> TrackMeta:
        artists = t.get("artists", [{}])
        return TrackMeta(
            provider=self.name,
            provider_id=str(t.get("id", "")),
            title=t.get("name", ""),
            artist=", ".join(a.get("name", "") for a in artists),
            album=t.get("album", {}).get("name", "") if t.get("album") else "",
            duration_sec=t.get("duration", 0) // 1000,
            cover_url=(t.get("album", {}) or {}).get("images", [{}])[0].get("url", ""),
            preview_url=t.get("preview_url", ""),
            raw=t,
        )

    def resolve(self, provider_id: str) -> str | None:
        for region in (self.default_region, self.fallback_region):
            try:
                r = self._client.get(
                    f"{self.base_url}/tracks/{provider_id}/stream",
                    params={"region": region},
                    headers=self._headers(),
                )
                if r.status_code == 451:
                    continue
                r.raise_for_status()
                return r.json().get("url")
            except Exception:
                continue
        return None

    def download(self, provider_id: str, dest_dir: Path) -> Path | None:
        url = self.resolve(provider_id)
        if not url:
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{provider_id}.m4a"
        try:
            with self._client.stream("GET", url) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=65536):
                        f.write(chunk)
            return dest
        except Exception:
            return None
