"""Read/update helpers for a kept frame's two-line ``.txt`` sidecar.

Line 1 = comma-separated danbooru tags, line 2 = optional LLM description
(the kohya training pair). ``update_sidecar`` is the single chokepoint for
the "replace one line, preserve the other" dance the frames API needs in
eight places; the parsing/joining contract itself stays in
:mod:`neme_anima.tag` (``split_sidecar``/``join_sidecar``) because the
frontend mirrors it (``frontend/src/lib/sidecar.ts``).
"""

from __future__ import annotations

from pathlib import Path

from neme_anima.tag import join_sidecar, split_sidecar


def read_sidecar(txt_path: Path) -> tuple[str, str]:
    """Return ``(danbooru_line, description)``; ``("", "")`` when the file
    is missing or unreadable — callers treat both the same way."""
    if not txt_path.is_file():
        return "", ""
    try:
        return split_sidecar(txt_path.read_text(encoding="utf-8"))
    except OSError:
        return "", ""


def update_sidecar(
    txt_path: Path,
    *,
    tags: str | None = None,
    description: str | None = None,
) -> str:
    """Rewrite the sidecar, replacing only the given line(s).

    ``None`` means "preserve what's on disk"; an explicit empty string
    overwrites (clearing a description collapses the file to one line).
    Returns the final text written (trailing newline included) — the tag
    line goes through ``join_sidecar``'s dedupe/normalization, and callers
    echo the result back to the client so the UI cache stays consistent.
    """
    current_tags, current_description = read_sidecar(txt_path)
    final = join_sidecar(
        tags if tags is not None else current_tags,
        description if description is not None else current_description,
    )
    txt_path.write_text(final, encoding="utf-8")
    return final
