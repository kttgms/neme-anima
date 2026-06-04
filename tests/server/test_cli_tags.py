# tests/server/test_cli_tags.py
"""Tests for the `neme-anima tags fetch` CLI command."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from neme_anima.cli import app

runner = CliRunner()


def test_tags_fetch_downloads(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("NEME_STATE_DIR", str(tmp_path))

    def fake_fetch(dest, *, url, force=False, transport=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("1girl,0,7641780,\n", encoding="utf-8")
        return Path(dest)

    monkeypatch.setattr("neme_anima.tag_vocabulary.fetch_tag_vocabulary", fake_fetch)
    result = runner.invoke(app, ["tags", "fetch"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "danbooru-tags.csv").exists()


def test_tags_fetch_skips_when_present(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("NEME_STATE_DIR", str(tmp_path))
    (tmp_path / "danbooru-tags.csv").write_text("x,0,1,\n", encoding="utf-8")
    result = runner.invoke(app, ["tags", "fetch"])
    assert result.exit_code == 0, result.output
    assert "present" in result.output
