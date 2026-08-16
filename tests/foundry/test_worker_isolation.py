"""Worker state isolation + private held-out boundary (H12, H13)."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.workers.isolation import (
    DisposableWorkerState,
    HeldOutBoundary,
    IsolationError,
    diff_openhands_containment,
    snapshot_openhands_tree,
)


def test_disposable_worker_state_isolated_and_idempotent_cleanup(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    state = DisposableWorkerState.create(
        run_id="run-1", framework="openhands", lab_root=lab)
    assert state.root.is_relative_to(lab.resolve())
    assert state.openhands_data_dir is not None
    assert state.openhands_data_dir.exists()
    assert state.hermes_home is None
    assert state.cleanup() is True
    assert state.root.exists() is False
    assert state.cleanup() is False  # idempotent


def test_disposable_state_refuses_protected_roots(tmp_path):
    protected = tmp_path / "protected"
    protected.mkdir()
    with pytest.raises(IsolationError):
        DisposableWorkerState.create(
            run_id="r", framework="hermes",
            lab_root=protected, protected_roots=(protected,))


def test_hermes_disposable_state_creates_isolated_home(tmp_path):
    state = DisposableWorkerState.create(
        run_id="r", framework="hermes", lab_root=tmp_path)
    assert state.hermes_home is not None
    assert state.hermes_home.exists()
    assert state.openhands_data_dir is None


def test_held_out_boundary_layout(tmp_path):
    boundary = HeldOutBoundary.create(
        mission_id="m1", lab_root=tmp_path)
    for directory in (boundary.worker_visible, boundary.worktree,
                      boundary.task_contract, boundary.public_tests,
                      boundary.controller_private, boundary.held_out_tests,
                      boundary.expected_properties,
                      boundary.verifier_evidence):
        assert directory.exists()


def test_private_data_unreachable_from_worker_roots(tmp_path):
    boundary = HeldOutBoundary.create(
        mission_id="m1", lab_root=tmp_path)
    failures = boundary.assert_private_unreachable(
        boundary.worktree, boundary.task_contract, boundary.public_tests)
    assert failures == ()


def test_private_data_detected_when_inside_worker_root(tmp_path):
    boundary = HeldOutBoundary.create(
        mission_id="m1", lab_root=tmp_path)
    failures = boundary.assert_private_unreachable(boundary.root)
    assert any("inside worker-visible root" in f for f in failures)


def test_openhands_containment_detects_new_probe_file(tmp_path):
    home = tmp_path / "openhands-home"
    (home / "profiles").mkdir(parents=True)
    (home / "profiles" / "owner.json").write_text("owner", encoding="utf-8")
    before = snapshot_openhands_tree(home)
    (home / "conversations").mkdir()
    (home / "conversations" / "probe-abc").write_text("state", encoding="utf-8")
    after = snapshot_openhands_tree(home)
    violations = diff_openhands_containment(before, after, home_root=home)
    assert any("new:conversations/probe-abc" in v for v in violations)


def test_openhands_containment_detects_owner_removal(tmp_path):
    home = tmp_path / "openhands-home"
    (home / "profiles").mkdir(parents=True)
    owner = home / "profiles" / "owner.json"
    owner.write_text("owner", encoding="utf-8")
    before = snapshot_openhands_tree(home)
    owner.unlink()
    after = snapshot_openhands_tree(home)
    violations = diff_openhands_containment(before, after, home_root=home)
    assert any("removed:profiles/owner.json" in v for v in violations)


def test_openhands_containment_detects_owner_modification(tmp_path):
    home = tmp_path / "openhands-home"
    (home / "profiles").mkdir(parents=True)
    owner = home / "profiles" / "owner.json"
    owner.write_text("owner", encoding="utf-8")
    before = snapshot_openhands_tree(home)
    owner.write_text("owner-modified", encoding="utf-8")
    after = snapshot_openhands_tree(home)
    violations = diff_openhands_containment(before, after, home_root=home)
    assert any("modified:profiles/owner.json" in v for v in violations)


def test_openhands_containment_clean_when_untouched(tmp_path):
    home = tmp_path / "openhands-home"
    (home / "profiles").mkdir(parents=True)
    (home / "profiles" / "owner.json").write_text("owner", encoding="utf-8")
    before = snapshot_openhands_tree(home)
    after = snapshot_openhands_tree(home)
    assert diff_openhands_containment(before, after, home_root=home) == ()
