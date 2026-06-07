"""Library endpoints — proxies Navidrome + enriches with vibe data from our DB."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..auth import current_user
from ..db import get_pool
from .. import navidrome

router = APIRouter(prefix="/library", tags=["library"])


def _enrich_song(song: dict, features: dict | None) -> dict:
    """Add kwhale-specific fields to a Navidrome song dict."""
    result = {
        "id": song.get("id"),
        "title": song.get("title"),
        "artist": song.get("artist"),
        "artistId": song.get("artistId"),
        "album": song.get("album"),
        "albumId": song.get("albumId"),
        "duration": song.get("duration"),
        "track": song.get("track"),
        "year": song.get("year"),
        "genre": song.get("genre"),
        "coverArt": song.get("coverArt"),
        "size": song.get("size"),
        "bitRate": song.get("bitRate"),
        "suffix": song.get("suffix"),
        "contentType": song.get("contentType"),
        "streamUrl": f"/stream/{song['id']}",
        "coverUrl": f"/library/cover/{song.get('coverArt', song['id'])}",
    }
    if features:
        result["vibe"] = {
            "bpm": features.get("bpm"),
            "energy": features.get("energy"),
            "valence": features.get("valence"),
            "danceability": features.get("danceability"),
            "tags": features.get("vibe_tags", []),
        }
    return result


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(30, le=100),
    user: str = Depends(current_user),
):
    results = await navidrome.search(q, song_count=limit)
    songs = results.get("song", [])

    pool = await get_pool()
    ids = [s["id"] for s in songs]
    if ids:
        rows = await pool.fetch(
            "SELECT navidrome_id, bpm, energy, valence, danceability, vibe_tags "
            "FROM track_features WHERE navidrome_id = ANY($1)",
            ids,
        )
        feat_map = {r["navidrome_id"]: dict(r) for r in rows}
    else:
        feat_map = {}

    return {"songs": [_enrich_song(s, feat_map.get(s["id"])) for s in songs]}


@router.get("/albums")
async def list_albums(
    size: int = Query(50, le=500),
    offset: int = 0,
    user: str = Depends(current_user),
):
    albums = await navidrome.get_albums(size=size, offset=offset)
    return {"albums": albums}


@router.get("/albums/{album_id}")
async def get_album(album_id: str, user: str = Depends(current_user)):
    album = await navidrome.get_album(album_id)
    if not album:
        raise HTTPException(404, "Album not found")
    return album


@router.get("/artists")
async def list_artists(user: str = Depends(current_user)):
    artists = await navidrome.get_artists()
    return {"artists": artists}


@router.get("/artists/{artist_id}")
async def get_artist(artist_id: str, user: str = Depends(current_user)):
    artist = await navidrome.get_artist(artist_id)
    if not artist:
        raise HTTPException(404, "Artist not found")
    return artist


@router.get("/songs/{song_id}")
async def get_song(song_id: str, user: str = Depends(current_user)):
    song = await navidrome.get_song(song_id)
    if not song:
        raise HTTPException(404, "Song not found")
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT bpm, energy, valence, danceability, loudness, key, mode, "
        "lyrics, vibe_tags FROM track_features WHERE navidrome_id = $1",
        song_id,
    )
    return _enrich_song(song, dict(row) if row else None)


@router.get("/cover/{cover_id}")
async def get_cover(cover_id: str, size: int = 1200, user: str = Depends(current_user)):
    # Proxy Navidrome's getCoverArt response through the API instead of
    # redirecting the client to the internal Docker URL (which is unreachable
    # from the open internet and from mobile networks). We forward the body
    # bytes verbatim and propagate the image Content-Type so the client gets
    # the same bytes Navidrome would have served directly.
    import httpx

    # cap size to a sane max — clients occasionally pass huge values
    if size > 1500:
        size = 1500
    url = navidrome.cover_url(cover_id, size=size)
    timeout = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0)

    # Open the streaming response so we can read its headers (status,
    # content-type) and return a clean error if Navidrome reports a missing
    # cover. The body iterator lives inside the StreamingResponse and the
    # httpx client closes itself when iteration ends — single connection,
    # proper lifecycle.
    client = httpx.AsyncClient(timeout=timeout)
    try:
        upstream = await client.send(client.build_request("GET", url), stream=True)
    except Exception:
        await client.aclose()
        raise HTTPException(502, "navidrome unreachable")

    if upstream.status_code >= 400:
        await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(404, "cover not found" if upstream.status_code == 404 else "navidrome error")

    content_type = (upstream.headers.get("content-type") or "image/jpeg").split(";")[0].strip()

    async def _iter():
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=65536):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        _iter(),
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Cover-Source": "kwhale-api",
        },
    )


@router.post("/songs/{song_id}/star")
async def star_song(song_id: str, user: str = Depends(current_user)):
    await navidrome.star(song_id)
    return {"ok": True}


@router.delete("/songs/{song_id}/star")
async def unstar_song(song_id: str, user: str = Depends(current_user)):
    await navidrome.unstar(song_id)
    return {"ok": True}
