"""Unit tests for the sidecar read/update helpers.

The two-line contract: line 1 = comma-separated danbooru tags, line 2 =
optional natural-language description (see tag.split_sidecar/join_sidecar).
"""

from __future__ import annotations

from pathlib import Path

from neme_anima.storage.sidecar import read_sidecar, update_sidecar


def test_read_missing_file_is_empty(tmp_path: Path):
    assert read_sidecar(tmp_path / "nope.txt") == ("", "")


def test_read_two_line_sidecar(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("1girl, smile\nA girl smiling.\n", encoding="utf-8")
    assert read_sidecar(p) == ("1girl, smile", "A girl smiling.")


def test_update_tags_preserves_description(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("old_tag\nKeep me.\n", encoding="utf-8")
    final = update_sidecar(p, tags="1girl, smile")
    assert p.read_text(encoding="utf-8") == "1girl, smile\nKeep me.\n"
    assert final == "1girl, smile\nKeep me.\n"


def test_update_description_preserves_tags(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("1girl, smile\nOld words.\n", encoding="utf-8")
    update_sidecar(p, description="New words.")
    assert p.read_text(encoding="utf-8") == "1girl, smile\nNew words.\n"


def test_update_description_empty_string_clears_it(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("1girl\nGone soon.\n", encoding="utf-8")
    update_sidecar(p, description="")
    assert p.read_text(encoding="utf-8") == "1girl\n"


def test_update_both_creates_fresh_file(tmp_path: Path):
    p = tmp_path / "new.txt"
    update_sidecar(p, tags="1girl, 1girl, smile", description="Hi.")
    # join_sidecar dedupes the tag line — the helper inherits that.
    assert p.read_text(encoding="utf-8") == "1girl, smile\nHi.\n"


def test_update_nothing_normalizes_in_place(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("a, a, b\ndesc\n", encoding="utf-8")
    final = update_sidecar(p)
    assert final == "a, b\ndesc\n"
