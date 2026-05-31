"""Yandex Music source plugin.

Set YANDEX_MUSIC_TOKEN in worker/.env to enable this provider.
"""
import os
from pathlib import Path

from .base import BaseProvider, TrackMeta


class YandexProvider(BaseProvider):
    name = "yandex"

    def __init__(self):
        self.token = os.environ.get("YANDEX_MUSIC_TOKEN", "")
        self._client = None

    def is_available(self) -> bool:
        return bool(self.token)

    def _get_client(self):
        if self._client is None:
            from yandex_music import Client
            self._client = Client(self.token).init()
        return self._client

    def search(self, query: str, limit: int = 10) -> list[TrackMeta]:
        try:
            client = self._get_client()
            results = client.search(query, type_="track")
            tracks = results.tracks.results if results.tracks else []
            return [self._to_meta(t) for t in tracks[:limit]]
        except Exception:
            return []

    def _to_meta(self, t) -> TrackMeta:
        artists = [a.name for a in (t.artists or [])]
        album = t.albums[0] if t.albums else None
        return TrackMeta(
            provider=self.name,
            provider_id=str(t.id),
            title=t.title or "",
            artist=", ".join(artists),
            album=album.title if album else "",
            duration_sec=(t.duration_ms or 0) // 1000,
            cover_url=f"https://avatars.yandex.net/get-music-content/{t.cover_uri}/200x200"
            if t.cover_uri else "",
            raw={"id": t.id},
        )

    def resolve(self, provider_id: str) -> str | None:
        try:
            client = self._get_client()
            track = client.tracks([int(provider_id)])[0]
            dl_info = track.get_download_info()
            if not dl_info:
                return None
            best = sorted(dl_info, key=lambda x: x.bitrate_in_kbps or 0, reverse=True)[0]
            return best.get_direct_link()
        except Exception:
            return None

    def download(self, provider_id: str, dest_dir: Path) -> Path | None:
        try:
            client = self._get_client()
            track = client.tracks([int(provider_id)])[0]
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{provider_id}.mp3"
            track.download(str(dest), codec="mp3", bitrate_in_kbps=320)
            return dest
        except Exception:
            return None
