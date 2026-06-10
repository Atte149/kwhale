"""Online catalog browsing (album / artist pages) and lyrics.

Browse endpoints proxy ICM metadata so the client can open an online album or
artist like a local one. Lyrics prefer synced LRC (lrclib, then ICM) and fall
back to the plain text already stored by the indexer.
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..db import get_pool
from .. import online

router = APIRouter(tags=["online"])


@router.get("/online/album/{provider}/{album_id}")
async def online_album(provider: str, album_id: str, user: str = Depends(current_user)):
    if provider != "icm":
        raise HTTPException(501, f"album browsing not supported for '{provider}'")
    try:
        album = await online.icm_album(album_id)
    except httpx.TimeoutException:
        raise HTTPException(504, "online catalog timed out, try again")
    if not album:
        raise HTTPException(404, "Album not found")
    await online.mark_in_library(album.get("tracks", []))
    return album


@router.get("/online/artist/{provider}/{artist_id}")
async def online_artist(provider: str, artist_id: str, user: str = Depends(current_user)):
    if provider != "icm":
        raise HTTPException(501, f"artist browsing not supported for '{provider}'")
    try:
        artist = await online.icm_artist(artist_id)
    except httpx.TimeoutException:
        raise HTTPException(504, "online catalog timed out, try again")
    if not artist:
        raise HTTPException(404, "Artist not found")
    await online.mark_in_library(artist.get("topTracks", []))
    return artist


@router.get("/online/resolve/{provider}/{track_id}")
async def online_resolve(provider: str, track_id: str,
                         user: str = Depends(current_user)):
    """Short-lived stream URL for listen-before-download (ICM only)."""
    if provider != "icm":
        raise HTTPException(501, f"resolve not supported for '{provider}'")
    try:
        url = await online.icm_resolve(track_id)
    except httpx.TimeoutException:
        raise HTTPException(504, "resolve timed out, try again")
    if not url:
        raise HTTPException(404, "track not resolvable")
    return {"stream_url": url}


@router.get("/lyrics/{navidrome_id}")
async def lyrics_for_track(navidrome_id: str, user: str = Depends(current_user)):
    """Lyrics for a library track. Preference: synced LRC > plain text.

    1. lrclib.net by artist+title+duration (free, has syncedLyrics);
    2. ICM synced lyrics via provider_track_map (for tracks we downloaded);
    3. plain lyrics stored in track_features by the indexer.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT artist, title, duration_sec, lyrics FROM track_features "
        "WHERE navidrome_id = $1",
        navidrome_id,
    )
    if not row:
        raise HTTPException(404, "Track not indexed")

    # 1. lrclib synced
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(
                "https://lrclib.net/api/get",
                params={
                    "artist_name": row["artist"] or "",
                    "track_name": row["title"] or "",
                    "duration": int(row["duration_sec"] or 0),
                },
            )
        if r.status_code == 200:
            d = r.json()
            if d.get("syncedLyrics"):
                return {"format": "lrc", "synced": True, "lyrics": d["syncedLyrics"]}
            if d.get("plainLyrics") and not row["lyrics"]:
                return {"format": "text", "synced": False, "lyrics": d["plainLyrics"]}
    except Exception:
        pass

    # 2. ICM synced (for tracks acquired through ICM)
    icm_row = await pool.fetchrow(
        "SELECT provider_id FROM provider_track_map "
        "WHERE provider = 'icm' AND navidrome_id = $1",
        navidrome_id,
    )
    if icm_row:
        try:
            lrc = await online.icm_lyrics(icm_row["provider_id"])
            if lrc:
                return {"format": "lrc", "synced": True, "lyrics": lrc}
        except Exception:
            pass

    # 3. plain text from the indexer
    if row["lyrics"]:
        return {"format": "text", "synced": False, "lyrics": row["lyrics"]}
    raise HTTPException(404, "No lyrics found")


@router.get("/online/lyrics/{provider}/{track_id}")
async def lyrics_for_online_track(provider: str, track_id: str,
                                  user: str = Depends(current_user)):
    """Synced lyrics for an online (not yet downloaded) track."""
    if provider != "icm":
        raise HTTPException(501, f"lyrics not supported for '{provider}'")
    lrc = await online.icm_lyrics(track_id)
    if not lrc:
        raise HTTPException(404, "No lyrics found")
    return {"format": "lrc", "synced": True, "lyrics": lrc}
