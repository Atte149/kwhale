"""Thin async client wrapping Navidrome's OpenSubsonic API.
Used internally by the API to proxy library calls to Navidrome.
The client is intentionally narrow — we only call what we need.
"""
import hashlib
import secrets
from typing import Any

import httpx

from .config import settings

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.navidrome_url,
            timeout=httpx.Timeout(20.0),
        )
    return _client


def _auth_params() -> dict:
    salt = secrets.token_hex(6)
    token = hashlib.md5(
        f"{settings.navidrome_password}{salt}".encode()
    ).hexdigest()
    return {
        "u": settings.navidrome_username,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "kwhale",
        "f": "json",
    }


async def _call(endpoint: str, **params) -> dict[str, Any]:
    r = await _get_client().get(
        f"/rest/{endpoint}", params={**_auth_params(), **params}
    )
    r.raise_for_status()
    resp = r.json().get("subsonic-response", {})
    if resp.get("status") != "ok":
        raise RuntimeError(f"Navidrome error: {resp.get('error')}")
    return resp


async def search(query: str, artist_count=0, album_count=0, song_count=50) -> dict:
    data = await _call(
        "search3.view",
        query=query,
        artistCount=artist_count,
        albumCount=album_count,
        songCount=song_count,
    )
    return data.get("searchResult3", {})


async def get_random_songs(size=50, genre=None) -> list[dict]:
    params = {"size": size}
    if genre:
        params["genre"] = genre
    data = await _call("getRandomSongs.view", **params)
    return data.get("randomSongs", {}).get("song", [])


async def get_starred_songs() -> list[dict]:
    data = await _call("getStarred2.view")
    return data.get("starred2", {}).get("song", [])


async def get_genres() -> list[dict]:
    data = await _call("getGenres.view")
    return data.get("genres", {}).get("genre", [])


async def get_songs_by_genre(genre: str, count=50, offset=0) -> list[dict]:
    data = await _call("getSongsByGenre.view", genre=genre, count=count, offset=offset)
    return data.get("songsByGenre", {}).get("song", [])


async def get_song(song_id: str) -> dict | None:
    data = await _call("getSong.view", id=song_id)
    return data.get("song")


async def get_albums(size=500, offset=0, type="alphabeticalByName") -> list[dict]:
    data = await _call("getAlbumList2.view", type=type, size=size, offset=offset)
    return data.get("albumList2", {}).get("album", [])


async def get_album(album_id: str) -> dict | None:
    data = await _call("getAlbum.view", id=album_id)
    return data.get("album")


async def get_artists() -> list[dict]:
    data = await _call("getArtists.view")
    indices = data.get("artists", {}).get("index", [])
    artists = []
    for idx in indices:
        artists.extend(idx.get("artist", []))
    return artists


async def get_artist(artist_id: str) -> dict | None:
    data = await _call("getArtist.view", id=artist_id)
    return data.get("artist")


async def star(song_id: str) -> None:
    await _call("star.view", id=song_id)


async def unstar(song_id: str) -> None:
    await _call("unstar.view", id=song_id)


async def scrobble(song_id: str, submission: bool = True) -> None:
    await _call("scrobble.view", id=song_id, submission=str(submission).lower())


async def trigger_scan() -> None:
    await _call("startScan.view")


def stream_url(song_id: str, max_bitrate: int = 0) -> str:
    """Return a direct Navidrome stream URL for redirect.
    The client follows the 302 and streams bytes from Navidrome directly,
    keeping audio bytes out of our FastAPI process.
    """
    import urllib.parse
    salt = secrets.token_hex(6)
    token = hashlib.md5(
        f"{settings.navidrome_password}{salt}".encode()
    ).hexdigest()
    params = {
        "id": song_id,
        "u": settings.navidrome_username,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "kwhale",
        "f": "json",
    }
    if max_bitrate:
        params["maxBitRate"] = str(max_bitrate)
    qs = urllib.parse.urlencode(params)
    return f"{settings.navidrome_url}/rest/stream.view?{qs}"


def cover_url(cover_id: str, size: int = 300) -> str:
    salt = secrets.token_hex(6)
    token = hashlib.md5(
        f"{settings.navidrome_password}{salt}".encode()
    ).hexdigest()
    import urllib.parse
    params = {
        "id": cover_id,
        "size": str(size),
        "u": settings.navidrome_username,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "kwhale",
        "f": "json",
    }
    qs = urllib.parse.urlencode(params)
    return f"{settings.navidrome_url}/rest/getCoverArt.view?{qs}"
