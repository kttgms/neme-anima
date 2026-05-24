"""Override-merge semantics for _resolve_thresholds."""

from __future__ import annotations

from pathlib import Path

from neme_anima.pipeline import _resolve_thresholds
from neme_anima.storage.project import Project


def test_exclude_tags_list_override_coerced_to_tuple(tmp_path: Path):
    project = Project.create(tmp_path / "p", name="p")
    project.thresholds_overrides = {
        "tag": {"exclude_tags": ["simple_background", "watermark"]},
    }
    project.save()

    thresholds = _resolve_thresholds(project)

    # The TagConfig field is typed `tuple[str, ...]` — keep the runtime
    # type matching even though set() accepts either.
    assert isinstance(thresholds.tag.exclude_tags, tuple)
    assert thresholds.tag.exclude_tags == ("simple_background", "watermark")
