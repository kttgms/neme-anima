"""Tests for the danbooru tag-vocabulary downloader."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from neme_anima.tag_vocabulary import (
    fetch_tag_vocabulary,
    tag_vocabulary_path,
)

VALID_CSV = b'1girl,0,7641780,"sole_female,1girls"\nlong_hair,0,5624146,"/lh,longhair"\n'


def _transport(body: bytes, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


def test_tag_vocabulary_path(tmp_path: Path):
    assert tag_vocabulary_path(tmp_path) == tmp_path / "danbooru-tags.csv"


def test_downloads_when_absent(tmp_path: Path):
    dest = tmp_path / "danbooru-tags.csv"
    out = fetch_tag_vocabulary(dest, url="https://x/y.csv", transport=_transport(VALID_CSV))
    assert out == dest
    assert dest.read_bytes() == VALID_CSV


def test_skips_when_present_and_not_force(tmp_path: Path):
    dest = tmp_path / "danbooru-tags.csv"
    dest.write_bytes(b"existing,0,1,\n")
    fetch_tag_vocabulary(dest, url="https://x/y.csv", transport=_transport(VALID_CSV))
    # Untouched because it already exists and force=False.
    assert dest.read_bytes() == b"existing,0,1,\n"


def test_force_redownloads(tmp_path: Path):
    dest = tmp_path / "danbooru-tags.csv"
    dest.write_bytes(b"old,0,1,\n")
    fetch_tag_vocabulary(dest, url="https://x/y.csv", force=True, transport=_transport(VALID_CSV))
    assert dest.read_bytes() == VALID_CSV


def test_rejects_non_csv(tmp_path: Path):
    dest = tmp_path / "danbooru-tags.csv"
    with pytest.raises(ValueError):
        fetch_tag_vocabulary(
            dest, url="https://x/y.csv", transport=_transport(b"<html>nope</html>")
        )
    assert not dest.exists()
