"""Thin async client wrapping Navidrome's OpenSubsonic API.
Used internally by the API to proxy library calls to Navidrome.

All Subsonic calls use admin credentials (from env), but accept an optional
``music_folder_id`` to scope results to a specific library — this is how
per-user library isolation works even though the API authenticates as admin.
The caller resolves the user's library IDs via ``get_user_library_ids()``.
"""
import hashlib
import secrets
import time
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


async def _call(endpoint: str, music_folder_id: int | None = None, **params) -> dict[str, Any]:
    auth = _auth_params()
    if music_folder_id is not None:
        auth["musicFolderId"] = str(music_folder_id)
    r = await _get_client().get(
        f"/rest/{endpoint}", params={**auth, **params}
    )
    r.raise_for_status()
    resp = r.json().get("subsonic-response", {})
    if resp.get("status") != "ok":
        raise RuntimeError(f"Navidrome error: {resp.get('error')}")
    return resp


# ── User → library resolution ─────────────────────────────────────────────────

_admin_jwt_cache: tuple[str, float] | None = None


async def _get_admin_jwt() -> str | None:
    """Get a Navidrome admin JWT token (cached for ~50 minutes)."""
    global _admin_jwt_cache
    if _admin_jwt_cache and time.time() - _admin_jwt_cache[1] < 3000:
        return _admin_jwt_cache[0]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{settings.navidrome_url}/auth/login",
                json={
                    "username": settings.navidrome_username,
                    "password": settings.navidrome_password,
                },
            )
            if r.status_code != 200:
                return None
            token = r.json().get("token", "")
            if token:
                _admin_jwt_cache = (token, time.time())
            return token
    except Exception:
        return None


async def get_user_library_ids(username: str) -> list[int]:
    """Resolve a Navidrome username to their accessible library IDs.

    Returns an empty list for admin users (they see all libraries — no
    filtering needed) or on any error.
    """
    if username == settings.navidrome_username:
        return []

    try:
        admin_jwt = await _get_admin_jwt()
        if not admin_jwt:
            return []

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{settings.navidrome_url}/api/user",
                headers={"x-nd-authorization": f"Bearer {admin_jwt}"},
            )
            if r.status_code != 200:
                return []
            users = r.json()
            user = next((u for u in users if u["userName"] == username), None)
            if not user:
                return []

            r2 = await client.get(
                f"{settings.navidrome_url}/api/user/{user['id']}/library",
                headers={"x-nd-authorization": f"Bearer {admin_jwt}"},
            )
            if r2.status_code != 200:
                return []
            libs = r2.json()
            return [l["id"] for l in libs]
    except Exception as e:
        print(f"get_user_library_ids error for {username}: {e}")
        return []


async def get_first_library_id(username: str) -> int | None:
    """Convenience: return the first (usually only) library ID for a user."""
    ids = await get_user_library_ids(username)
    return ids[0] if ids else None


async def search(query: str, artist_count=0, album_count=0, song_count=50,
                 music_folder_id: int | None = None) -> dict:
    data = await _call(
        "search3.view",
        music_folder_id=music_folder_id,
        query=query,
        artistCount=artist_count,
        albumCount=album_count,
        songCount=song_count,
    )
    return data.get("searchResult3", {})


async def get_random_songs(size=50, genre=None,
                           music_folder_id: int | None = None) -> list[dict]:
    params = {"size": size}
    if genre:
        params["genre"] = genre
    data = await _call("getRandomSongs.view", music_folder_id=music_folder_id, **params)
    return data.get("randomSongs", {}).get("song", [])


async def get_starred_songs(music_folder_id: int | None = None) -> list[dict]:
    data = await _call("getStarred2.view", music_folder_id=music_folder_id)
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


async def get_albums(size=500, offset=0, type="alphabeticalByName",
                     music_folder_id: int | None = None) -> list[dict]:
    data = await _call("getAlbumList2.view", music_folder_id=music_folder_id,
                       type=type, size=size, offset=offset)
    return data.get("albumList2", {}).get("album", [])


async def get_album(album_id: str) -> dict | None:
    data = await _call("getAlbum.view", id=album_id)
    return data.get("album")


async def get_artists(music_folder_id: int | None = None) -> list[dict]:
    data = await _call("getArtists.view", music_folder_id=music_folder_id)
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
    """Return a Navidrome stream URL for redirect.
    Uses PUBLIC_NAVIDROME_URL when configured (the public host the client can
    actually reach — e.g. https://music.dueattendant149.org), otherwise falls
    back to navidrome_url (internal Docker hostname, used in tests and by
    in-network clients). The client follows the 302 and streams bytes from
    Navidrome directly, keeping audio bytes out of our FastAPI process.
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
    base = settings.public_navidrome_url or settings.navidrome_url
    return f"{base}/rest/stream.view?{qs}"


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
    base = settings.public_navidrome_url or settings.navidrome_url
    return f"{base}/rest/getCoverArt.view?{qs}"
