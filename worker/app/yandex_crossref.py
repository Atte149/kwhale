"""Yandex Music cross-reference for Cyrillic metadata.

Searches Yandex Music by title+artist to find the canonical Cyrillic
representation. Yandex results are prioritized over Shazam/AcoustID
when a Cyrillic match is found.

Also fetches cover art URLs from Yandex.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

YANDEX_TOKEN = os.environ.get("YANDEX_MUSIC_TOKEN", "")


def yandex_search_artist(artist: str) -> str | None:
    """Search Yandex Music for an artist name, return the canonical form.

    Used for reverse-translit: corrupted cyrillic -> latin -> Yandex -> correct name.
    Returns the artist name from Yandex, or None if not found.
    """
    if not YANDEX_TOKEN or not artist or len(artist) < 2:
        return None

    try:
        from yandex_music import Client
        client = Client(YANDEX_TOKEN).init()
        results = client.search(artist, type_="artist")
        artists_list = results.artists.results if results.artists else []
        if artists_list:
            return artists_list[0].name
    except Exception as e:
        print(f"Yandex artist search error for '{artist}': {e}")
    return None


def yandex_cross_ref(title: str, artist: str) -> dict[str, Any] | None:
    """Search Yandex Music for a track, return Cyrillic metadata if found.

    Returns dict with: title, artist, album, year, genre, cover_url
    All values are from Yandex (Cyrillic when available).
    """
    if not YANDEX_TOKEN:
        return None

    query = f"{artist} {title}".strip()
    if not query or len(query) < 2:
        return None

    try:
        from yandex_music import Client
        client = Client(YANDEX_TOKEN).init()
        results = client.search(query, type_="track")
        tracks = results.tracks.results if results.tracks else []
        if not tracks:
            return None

        # Find best match — prefer exact title match, else first result
        track = None
        for t in tracks:
            if t.title and t.title.lower() == title.lower():
                track = t
                break
        if not track:
            track = tracks[0]

        artists_list = [a.name for a in (track.artists or [])]
        album = track.albums[0] if track.albums else None

        cover_url = ""
        if track.cover_uri:
            cover_url = f"https://{track.cover_uri.replace('%%', '400x400')}"

        year = ""
        if album and getattr(album, "year", None):
            year = str(album.year)

        genre = ""
        try:
            if hasattr(track, 'genres') and track.genres:
                genre = track.genres[0] if isinstance(track.genres, list) else str(track.genres)
        except Exception:
            pass

        return {
            "title": track.title or "",
            "artist": ", ".join(artists_list),
            "albumartist": artists_list[0] if artists_list else "",
            "album": album.title if album else "",
            "year": year,
            "genre": genre,
            "cover_url": cover_url,
            "_source": "yandex",
        }
    except Exception as e:
        print(f"Yandex cross-ref error for '{query}': {e}")
        return None


def download_cover(cover_url: str, dest_path: str) -> bool:
    """Download cover art from URL to file."""
    if not cover_url:
        return False
    try:
        r = httpx.get(cover_url, timeout=15.0, follow_redirects=True)
        if r.status_code == 200 and len(r.content) > 1000:
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return True
    except Exception as e:
        print(f"Cover download error: {e}")
    return False