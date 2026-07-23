"""Format-aware multi-value tag writer/reader.

Writes proper multi-value ARTIST tags that Navidrome reads:
  - FLAC (Vorbis): multiple ARTIST fields, one per artist
  - MP3 (ID3v2.4): TPE1 with null-separated values
  - M4A (MP4): '©ART' atom as list of strings
  - Ogg/Opus: Vorbis comments, multiple ARTIST fields

Also sets ALBUMARTIST to the primary (first) artist.
"""
from __future__ import annotations

from pathlib import Path
import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TPE1, TPE2, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4


def write_multi_artists(filepath: str, artists: list[str]) -> bool:
    """Write multi-value ARTIST tag + set ALBUMARTIST to primary.

    Returns True on success.
    """
    if not artists:
        return False
    ext = Path(filepath).suffix.lower()
    primary = artists[0]

    try:
        if ext == ".flac":
            return _write_flac(filepath, artists, primary)
        elif ext == ".mp3":
            return _write_mp3(filepath, artists, primary)
        elif ext in (".m4a", ".mp4", ".aac"):
            return _write_m4a(filepath, artists, primary)
        elif ext in (".ogg", ".opus"):
            return _write_vorbis(filepath, artists, primary)
        else:
            return _write_generic(filepath, artists, primary)
    except Exception as e:
        print(f"multivalue write error for {filepath}: {e}")
        return False


def _write_flac(filepath: str, artists: list[str], primary: str) -> bool:
    """FLAC: multiple ARTIST Vorbis fields + ALBUMARTIST."""
    f = FLAC(filepath)
    # Delete old single-value ARTIST and non-standard ARTISTS (plural)
    if "artist" in f:
        del f["artist"]
    if "artists" in f:
        del f["artists"]
    f["artist"] = artists  # list -> multiple Vorbis fields
    f["albumartist"] = [primary]
    f.save()
    return True


def _write_vorbis(filepath: str, artists: list[str], primary: str) -> bool:
    """Ogg/Opus: Vorbis comments, multiple ARTIST fields."""
    f = mutagen.File(filepath, easy=False)
    if f is None:
        return False
    if "artist" in f:
        del f["artist"]
    if "artists" in f:
        del f["artists"]
    f["artist"] = artists
    f["albumartist"] = [primary]
    f.save()
    return True


def _write_mp3(filepath: str, artists: list[str], primary: str) -> bool:
    """MP3: ID3v2.4 TPE1 with null separator + TPE2 for albumartist."""
    audio = MP3(filepath)
    if audio.tags is None:
        try:
            audio.add_tags()
        except Exception:
            audio.tags = ID3()

    # Delete old TPE1/TPE2 and write new
    audio.tags.delall("TPE1")
    audio.tags.delall("TPE2")

    # TPE1: multi-value via null separator in ID3v2.4
    tpe1 = TPE1(encoding=3)  # UTF-8
    for a in artists:
        tpe1.text.append(a)
    audio.tags.add(tpe1)

    # TPE2: albumartist = primary
    audio.tags.add(TPE2(encoding=3, text=[primary]))

    audio.save(filepath, v2_version=4)
    return True


def _write_m4a(filepath: str, artists: list[str], primary: str) -> bool:
    """M4A/MP4: '©ART' atom as list + 'aART' for albumartist."""
    f = MP4(filepath)
    f["\xa9ART"] = artists  # list = multi-value
    f["aART"] = [primary]
    f.save()
    return True


def _write_generic(filepath: str, artists: list[str], primary: str) -> bool:
    """Fallback using easy mode."""
    f = mutagen.File(filepath, easy=True)
    if f is None:
        return False
    f["artist"] = artists
    f["albumartist"] = [primary]
    f.save()
    return True


def read_multi_artists(filepath: str) -> list[str]:
    """Read all ARTIST values, format-aware."""
    ext = Path(filepath).suffix.lower()
    try:
        if ext == ".flac":
            f = FLAC(filepath)
            return list(f.get("artist", []))
        elif ext == ".mp3":
            audio = MP3(filepath)
            if audio.tags and "TPE1" in audio.tags:
                return [str(x) for x in audio.tags["TPE1"].text]
            return []
        elif ext in (".m4a", ".mp4", ".aac"):
            f = MP4(filepath)
            val = f.get("\xa9ART", [])
            return list(val) if isinstance(val, list) else [str(val)]
        else:
            f = mutagen.File(filepath, easy=False)
            if f is None:
                return []
            v = f.get("artist", [])
            return list(v) if isinstance(v, list) else [str(v)]
    except Exception:
        return []