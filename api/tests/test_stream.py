"""Tests for the /stream endpoint redirect URL behaviour.

We don't spin up the FastAPI app (it requires a live Postgres). Instead we
unit-test the URL builder directly and assert the 302 Location header
construction in a way that doesn't touch the DB. The redirect endpoint
itself is a one-liner; what matters is that the URL it builds points at
PUBLIC_NAVIDROME_URL when configured.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_stream_url_uses_public_url_when_configured():
    """PUBLIC_NAVIDROME_URL takes precedence over NAVIDROME_URL."""
    from app.config import settings
    from app import navidrome

    orig_public = settings.public_navidrome_url
    orig_internal = settings.navidrome_url
    try:
        settings.public_navidrome_url = "https://music.dueattendant149.org"
        settings.navidrome_url = "http://navidrome:4533"
        url = navidrome.stream_url("song-1", max_bitrate=192)
        assert url.startswith("https://music.dueattendant149.org/rest/stream.view?")
        assert "id=song-1" in url
        assert "maxBitRate=192" in url
        # Sanity: internal hostname must NOT appear
        assert "navidrome:4533" not in url
    finally:
        settings.public_navidrome_url = orig_public
        settings.navidrome_url = orig_internal


def test_stream_url_falls_back_to_internal_when_public_unset():
    """Empty PUBLIC_NAVIDROME_URL → keep legacy behaviour (302 to internal)."""
    from app.config import settings
    from app import navidrome

    orig_public = settings.public_navidrome_url
    orig_internal = settings.navidrome_url
    try:
        settings.public_navidrome_url = ""
        settings.navidrome_url = "http://navidrome:4533"
        url = navidrome.stream_url("song-2")
        assert url.startswith("http://navidrome:4533/rest/stream.view?")
        assert "id=song-2" in url
    finally:
        settings.public_navidrome_url = orig_public
        settings.navidrome_url = orig_internal


def test_stream_url_includes_open_subsonic_auth_params():
    """stream URL must carry u/t/s/v/c/f — required by Navidrome's auth."""
    from app import navidrome

    url = navidrome.stream_url("track-42")
    for required in ("u=", "t=", "s=", "v=1.16.1", "c=kwhale", "f=json", "id=track-42"):
        assert required in url, f"missing {required!r} in {url}"
