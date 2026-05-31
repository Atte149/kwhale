"""Build library/ path from track metadata."""
import re


def _safe(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name[:max_len].strip()


def build_path(meta: dict) -> str:
    artist = _safe(meta.get("albumartist") or meta.get("artist") or "Unknown Artist")
    album = _safe(meta.get("album") or "Unknown Album")
    title = _safe(meta.get("title") or "Unknown Title")
    track_num = meta.get("track_number", 0)
    ext = meta.get("ext", "mp3")

    if meta.get("compilation"):
        return f"Compilations/{album}/{track_num:02d} - {title}.{ext}"
    return f"{artist}/{album}/{track_num:02d} - {title}.{ext}"
