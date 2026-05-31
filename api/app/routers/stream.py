"""Stream endpoint — 302-redirect to Navidrome.
Audio bytes never pass through our process; Navidrome handles range requests,
transcoding, and bandwidth efficiently. The client follows the redirect.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse

from ..auth import current_user
from .. import navidrome

router = APIRouter(prefix="/stream", tags=["stream"])


@router.get("/{song_id}")
async def stream(
    song_id: str,
    max_bitrate: int = Query(0, description="0 = original quality"),
    user: str = Depends(current_user),
):
    url = navidrome.stream_url(song_id, max_bitrate=max_bitrate)
    return RedirectResponse(url=url, status_code=302)
