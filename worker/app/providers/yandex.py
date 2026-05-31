"""Yandex Music source plugin.

Set YANDEX_MUSIC_TOKEN in worker/.env to enable this provider.
"""
import os
from pathlib import Path

from .base import BaseProvider, TrackMeta


class YandexProvider(BaseProvider):
    name = "yandex"
    # Fallback after ICM (priority 10).
    priority = 20

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
        # t.cover_uri already includes the host and a trailing "%%" size
        # placeholder, e.g. "avatars.yandex.net/get-music-content/.../%%".
        # Build the URL by substituting the size, not by appending one.
        cover_url = ""
        if t.cover_uri:
            cover_url = f"https://{t.cover_uri.replace('%%', '400x400')}"
        return TrackMeta(
            provider=self.name,
            provider_id=str(t.id),
            title=t.title or "",
            artist=", ".join(artists),
            album=album.title if album else "",
            duration_sec=(t.duration_ms or 0) // 1000,
            cover_url=cover_url,
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
            self._embed_tags(dest, track)
            return dest
        except Exception:
            return None

    def _embed_tags(self, dest: Path, track) -> None:
        """Yandex delivers untagged MP3s. Embed metadata so the tagger (which
        requires title+artist) can ingest the file, and so featured tracks group
        by the primary artist via albumartist instead of collapsing."""
        try:
            import mutagen
            artists = [a.name for a in (track.artists or [])]
            album = track.albums[0] if track.albums else None
            mf = mutagen.File(str(dest), easy=True)
            if mf is None:
                return
            if track.title:
                mf["title"] = [track.title]
            if artists:
                mf["artist"] = [", ".join(artists)]
                mf["albumartist"] = [artists[0]]
            if album and album.title:
                mf["album"] = [album.title]
                if getattr(album, "year", None):
                    mf["date"] = [str(album.year)]
            mf.save()
        except Exception:
            pass
