"""Shared FastAPI dependencies for the /api/projects/{slug}/* routers.

One ``Project.load`` per request: FastAPI caches dependency results, so a
route that declares both ``project: Project = Depends(get_project)`` and
``character: Character = Depends(get_character)`` resolves the project once
and both bindings see the same instance (mutations + ``project.save()``
behave exactly like the old in-handler loads).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, HTTPException, Request

from neme_anima.storage.project import Character, Project, Source


def get_project(slug: str, request: Request) -> Project:
    """Resolve the ``{slug}`` path param to a loaded :class:`Project`, or 404."""
    entry = request.app.state.registry.get(slug)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown project: {slug}")
    try:
        return Project.load(Path(entry.folder))
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=(
                f"project files missing for {slug!r} at {entry.folder} — "
                "folder was moved or deleted; remove the registry entry or "
                "restore the files"
            ),
        ) from e


def get_character(
    character_slug: str,
    project: Project = Depends(get_project),  # noqa: B008
) -> Character:
    """Resolve the ``{character_slug}`` path param within the project, or 404."""
    c = project.character_by_slug(character_slug)
    if c is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown character: {character_slug}",
        )
    return c


def get_source(idx: int, project: Project = Depends(get_project)) -> Source:  # noqa: B008
    """Resolve the ``{idx}`` path param to the project's source, or 404."""
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    return project.sources[idx]


def require_character(project: Project, slug: str) -> Character:
    """Validate a body-supplied character slug (strict: '' is unknown too)."""
    c = project.character_by_slug(slug)
    if c is None:
        raise HTTPException(status_code=404, detail=f"unknown character: {slug}")
    return c


def optional_character_slug(project: Project, raw: str | None) -> str | None:
    """Validate an optional ``?character_slug=`` query value.

    ``None``/``""`` mean "use the default character" and pass through as
    ``None``; an unknown slug is a 404 — silent fallback would mask UI bugs.
    """
    if raw is None or raw == "":
        return None
    if project.character_by_slug(raw) is None:
        raise HTTPException(status_code=404, detail=f"unknown character: {raw}")
    return raw
