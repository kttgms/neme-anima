"""Route-level tests for the shared dependency error paths.

The unit tests in test_deps.py call the functions directly; these go through
the full FastAPI stack to prove the wiring (path params, Depends caching,
consistent 404 detail) on each migrated router.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from neme_anima.server.app import create_app
from neme_anima.storage.project import Project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.create(tmp_path / "p", name="p")


@pytest.fixture
def app(tmp_path: Path, project: Project):
    a = create_app(state_dir=tmp_path / "state")
    a.state.registry.register(project)
    return a


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_unknown_project_404_is_consistent_across_routers(client):
    for url in (
        "/api/projects/nope/frames",
        "/api/projects/nope/characters",
        "/api/projects/nope/training/config",
    ):
        resp = await client.get(url)
        assert resp.status_code == 404, url
        assert resp.json()["detail"] == "unknown project: nope", url


async def test_missing_folder_404(client, project: Project):
    shutil.rmtree(project.root)
    resp = await client.get(f"/api/projects/{project.slug}/frames")
    assert resp.status_code == 404
    assert "project files missing" in resp.json()["detail"]


async def test_source_index_out_of_range(client, project: Project):
    resp = await client.delete(f"/api/projects/{project.slug}/sources/99")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "source index out of range"


async def test_unknown_character_path_param(client, project: Project):
    resp = await client.patch(
        f"/api/projects/{project.slug}/characters/nope",
        json={"name": "x"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown character: nope"


async def test_unknown_character_query_param(client, project: Project):
    resp = await client.post(
        f"/api/projects/{project.slug}/refs?character_slug=nope",
        json={"paths": []},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown character: nope"
