from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.overnight_release_factory import (
    FactoryGuardError,
    FactoryRoots,
    guard_lane_root,
    plan,
    validate_run_roots,
)


def test_factory_root_guard_requires_named_lane(tmp_path):
    lane = tmp_path / "JARVIS-Overnight-Release-Candidate"
    lane.mkdir()
    assert guard_lane_root(lane, expected_lane=lane, source_repo=lane) == lane.resolve()
    with pytest.raises(FactoryGuardError):
        guard_lane_root(tmp_path, expected_lane=lane, source_repo=lane)


def test_factory_root_guard_rejects_protected_environment(monkeypatch, tmp_path):
    lane = tmp_path / "JARVIS-Overnight-Release-Candidate"
    lane.mkdir()
    monkeypatch.setenv("JARVIS_HOME", str(lane))
    with pytest.raises(FactoryGuardError):
        guard_lane_root(lane, expected_lane=lane, source_repo=lane)


def test_factory_roots_are_descendants_and_new_run_is_required(tmp_path):
    lane = tmp_path / "JARVIS-Overnight-Release-Candidate"
    lane.mkdir()
    roots = FactoryRoots.for_run(lane, "run-a")
    validate_run_roots(roots)
    roots.run_root.mkdir(parents=True)
    (roots.run_root / "evidence.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FactoryGuardError):
        validate_run_roots(roots)


def test_dry_run_plan_has_no_filesystem_side_effect(tmp_path):
    lane = tmp_path / "JARVIS-Overnight-Release-Candidate"
    lane.mkdir()
    result = plan(
        lane, source_repo=lane, source_sha="a" * 40, run_id="plan-only",
        expected_lane=lane,
    )
    assert result["status"] == "PLAN"
    assert result["task_scheduler"] == "NOT_RUN"
    assert result["production_mutations"] == []
    assert not (lane / "OVERNIGHT_FACTORY_ROOT.json").exists()
    assert not (lane / "var").exists()
