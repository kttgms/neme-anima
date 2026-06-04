"""Tests for /api/tags/vocabulary."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from neme_anima.server.app import create_app


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def app(state_dir: Path):
    return create_app(state_dir=state_dir)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_404_when_absent(client):
    resp = await client.get("/api/tags/vocabulary")
    assert resp.status_code == 404


async def test_serves_csv_when_present(client, state_dir: Path):
    body = '1girl,0,7641780,"sole_female,1girls"\n'
    (state_dir / "danbooru-tags.csv").write_text(body, encoding="utf-8")
    resp = await client.get("/api/tags/vocabulary")
    assert resp.status_code == 200
    assert resp.text == body
    assert resp.headers["content-type"].startswith("text/csv")
