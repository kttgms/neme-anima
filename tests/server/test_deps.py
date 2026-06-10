"""Direct-call unit tests for the shared FastAPI dependencies."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from neme_anima.server.api.deps import (
    get_character,
    get_project,
    get_source,
    optional_character_slug,
    require_character,
)
from neme_anima.server.app import create_app
from neme_anima.storage.project import Project


@pytest.fixture
def app(tmp_path: Path):
    return create_app(state_dir=tmp_path / "state")


@pytest.fixture
def project(tmp_path: Path, app) -> Project:
    p = Project.create(tmp_path / "p", name="p")
    app.state.registry.register(p)
    return p


def _request(app) -> SimpleNamespace:
    # get_project only touches request.app.state.registry — a stub suffices.
    return SimpleNamespace(app=app)


def test_get_project_loads_registered(app, project):
    loaded = get_project(project.slug, _request(app))
    assert loaded.slug == project.slug


def test_get_project_404_unknown(app):
    with pytest.raises(HTTPException) as ei:
        get_project("nope", _request(app))
    assert ei.value.status_code == 404
    assert "unknown project" in ei.value.detail


def test_get_project_404_when_folder_missing(app, project):
    shutil.rmtree(project.root)
    with pytest.raises(HTTPException) as ei:
        get_project(project.slug, _request(app))
    assert ei.value.status_code == 404
    assert "missing" in ei.value.detail


def test_get_source_bounds_checked(project):
    with pytest.raises(HTTPException) as ei:
        get_source(0, project)  # project has no sources
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException):
        get_source(-1, project)


def test_get_character_returns_character(project):
    c = project.characters[0]
    assert get_character(c.slug, project).slug == c.slug


def test_get_character_404_unknown(project):
    with pytest.raises(HTTPException) as ei:
        get_character("nope", project)
    assert ei.value.status_code == 404
    assert "unknown character" in ei.value.detail


def test_require_character(project):
    c = project.characters[0]
    assert require_character(project, c.slug).slug == c.slug
    with pytest.raises(HTTPException):
        require_character(project, "nope")
    # Empty string is NOT a valid slug for body-supplied fields.
    with pytest.raises(HTTPException):
        require_character(project, "")


def test_optional_character_slug(project):
    c = project.characters[0]
    assert optional_character_slug(project, None) is None
    assert optional_character_slug(project, "") is None
    assert optional_character_slug(project, c.slug) == c.slug
    with pytest.raises(HTTPException):
        optional_character_slug(project, "nope")
