"""Persisted training-run state: roundtrip, corruption logging."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from neme_anima.server.training_runner import (
    RunState,
    _load_persisted_state,
    _persist_state,
)
from neme_anima.storage.project import Project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.create(tmp_path / "p", name="p")


def test_persist_then_load_roundtrip(project: Project):
    st = RunState(
        project_slug=project.slug,
        run_dir="/tmp/run",
        status="running",
        started_at="2026-06-10T00:00:00+00:00",
        epoch=3,
        step=10,
        loss=0.5,
        last_log_line="hello",
    )
    _persist_state(project, st)
    loaded = _load_persisted_state(project)
    assert loaded is not None
    assert (loaded.epoch, loaded.step, loaded.loss) == (3, 10, 0.5)
    assert loaded.last_log_line == "hello"
    assert loaded.status == "running"


def test_corrupt_json_logs_warning_and_returns_none(project: Project, caplog):
    project.training_dir.mkdir(parents=True, exist_ok=True)
    project.training_state_path.write_text("{not json")
    with caplog.at_level(logging.WARNING):
        assert _load_persisted_state(project) is None
    assert any("corrupt" in r.message for r in caplog.records)


def test_missing_required_key_logs_warning_and_returns_none(
    project: Project,
    caplog,
):
    project.training_dir.mkdir(parents=True, exist_ok=True)
    project.training_state_path.write_text(json.dumps({"status": "stopped"}))
    with caplog.at_level(logging.WARNING):
        assert _load_persisted_state(project) is None
    assert any("persisted state" in r.message for r in caplog.records)
