"""Base class for all source providers (streaming services).

To add a new source:
1. Create worker/app/providers/my_source.py
2. Subclass BaseProvider
3. Implement search(), resolve(), download()
4. The plugin loader picks it up automatically.

The provider system is intentionally minimal: three methods, plain Python dicts.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TrackMeta:
    """Normalised track metadata returned by all providers."""
    provider: str
    provider_id: str
    title: str
    artist: str
    album: str = ""
    duration_sec: int = 0
    cover_url: str = ""
    preview_url: str = ""
    raw: dict = None

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "provider_id": self.provider_id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration_sec": self.duration_sec,
            "cover_url": self.cover_url,
            "preview_url": self.preview_url,
        }


class BaseProvider(ABC):
    """Abstract base for a streaming source plugin."""

    name: str = "base"
    enabled: bool = True
    # Lower number = preferred. Used to order/dedup merged search results and
    # to pick a fallback provider when none is specified.
    priority: int = 100

    @abstractmethod
    def search(self, query: str, limit: int = 10) -> list[TrackMeta]:
        """Search for tracks. Returns list of TrackMeta."""
        ...

    @abstractmethod
    def resolve(self, provider_id: str) -> str | None:
        """Resolve a stream URL for a provider_id. Returns URL or None."""
        ...

    @abstractmethod
    def download(self, provider_id: str, dest_dir: Path) -> Path | None:
        """Download a track to dest_dir. Returns path to downloaded file or None."""
        ...

    def is_available(self) -> bool:
        """Return False if the provider can't authenticate (missing token etc)."""
        return self.enabled
