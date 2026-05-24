"""auto_delete_rejected wipes <video_stem>__* from rejected/ after a run."""

from __future__ import annotations

from pathlib import Path

from neme_anima.pipeline import _maybe_delete_rejected_for_stem
from neme_anima.storage.project import Project


def test_skips_when_flag_off(tmp_path: Path):
    project = Project.create(tmp_path / "p", name="p")
    # Seed a fake rejected file for the video stem.
    sample = project.rejected_dir / "ep01__s001_t002_f000010.png"
    sample.write_bytes(b"")
    _maybe_delete_rejected_for_stem(project, "ep01")
    assert sample.exists(), "must not delete when auto_delete_rejected is False"


def test_deletes_only_matching_stem_when_flag_on(tmp_path: Path):
    project = Project.create(tmp_path / "p", name="p")
    project.auto_delete_rejected = True

    # Two stems: ep01 should go, ep02 should stay.
    keep = project.rejected_dir / "ep02__s001_t002_f000010.png"
    drop = project.rejected_dir / "ep01__s001_t002_f000010.png"
    keep.write_bytes(b"")
    drop.write_bytes(b"")

    _maybe_delete_rejected_for_stem(project, "ep01")

    assert not drop.exists(), "matching stem must be deleted"
    assert keep.exists(), "other stems must be preserved"


def test_tolerates_missing_rejected_dir(tmp_path: Path):
    project = Project.create(tmp_path / "p", name="p")
    project.auto_delete_rejected = True
    # Remove the auto-created rejected/ folder.
    import shutil
    shutil.rmtree(project.rejected_dir)
    _maybe_delete_rejected_for_stem(project, "ep01")  # no raise
