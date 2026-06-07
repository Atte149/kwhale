"""Tests for ICMProvider.search() cover handling and the base fetch_cover().

The previous behaviour was to downgrade the cover URL to 300x300. We now
keep 1000x1000 so the worker downloads the high-res cover alongside the
audio. This file pins that down so a future refactor doesn't accidentally
regress the size.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_icm_meta_keeps_1000x1000_cover():
    """The cover URL returned in search results must keep the 1000x1000 size
    (was previously downgraded to 300x300)."""
    from app.providers.icm import ICMProvider

    p = ICMProvider()
    item = {
        "id": "track-1",
        "title": "Song",
        "artist": "Artist",
        "album": "Album",
        "duration": 180000,
        "cover": "https://is1-ssl.mzstatic.com/image/thumb/foo/1000x1000.jpg",
    }
    with patch("app.providers.registry.get_providers"), \
         patch("os.environ.get", return_value=""):
        meta = p._to_meta(item)
    assert "1000x1000" in meta.cover_url
    assert "300x300" not in meta.cover_url


def test_icm_meta_handles_missing_cover():
    from app.providers.icm import ICMProvider
    p = ICMProvider()
    item = {"id": "t2", "title": "T", "artist": "A", "album": "", "duration": 0}
    meta = p._to_meta(item)
    assert meta.cover_url == ""


def test_base_fetch_cover_writes_jpg(tmp_path):
    """BaseProvider.fetch_cover() should GET the URL and write bytes to
    dest_dir/cover.jpg. We patch httpx so the test doesn't hit the network."""
    from app.providers.base import BaseProvider, TrackMeta

    class Stub(BaseProvider):
        name = "stub"
        def search(self, query, limit=10): return []
        def resolve(self, provider_id): return None
        def download(self, provider_id, dest_dir): return None

    p = Stub()
    fake_resp = MagicMock()
    fake_resp.content = b"FAKE_JPEG"
    fake_resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get = MagicMock(return_value=fake_resp)

    with patch("httpx.Client", return_value=client):
        out = p.fetch_cover("https://example.com/cover.jpg", tmp_path)

    assert out == tmp_path / "cover.jpg"
    assert out.read_bytes() == b"FAKE_JPEG"
    assert client.get.call_args.args == ("https://example.com/cover.jpg",)


def test_base_fetch_cover_empty_url_returns_none(tmp_path):
    from app.providers.base import BaseProvider

    class Stub(BaseProvider):
        name = "stub"
        def search(self, query, limit=10): return []
        def resolve(self, provider_id): return None
        def download(self, provider_id, dest_dir): return None

    assert Stub().fetch_cover("", tmp_path) is None


def test_base_fetch_cover_handles_http_error(tmp_path):
    from app.providers.base import BaseProvider
    import httpx

    class Stub(BaseProvider):
        name = "stub"
        def search(self, query, limit=10): return []
        def resolve(self, provider_id): return None
        def download(self, provider_id, dest_dir): return None

    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock(side_effect=httpx.HTTPError("boom"))
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get = MagicMock(return_value=fake_resp)

    with patch("httpx.Client", return_value=client):
        out = Stub().fetch_cover("https://example.com/cover.jpg", tmp_path)

    assert out is None
    assert not (tmp_path / "cover.jpg").exists()
