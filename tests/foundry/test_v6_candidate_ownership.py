"""V6 closure: autonomous candidate ownership (no operator packaging).

A verified worker change must become a JARVIS-owned durable candidate
commit without manual git packaging:

1. ``ensure_commit_capability`` promotes ``git_local_commit`` to READY for
   a bound resource only when the worktree is a clean checkout exactly at
   the pinned baseline (fail closed otherwise).
2. ``commit_after_pass`` for an improvement-lineage mission stages ONLY
   the bound allowed paths and refuses when the worktree holds anything
   outside the scope (fail closed, no commit).
3. The landed commit is persisted as ``improvement.candidate_recorded``
   bound to producer mission/run + verifier verdict (idempotent replay).
4. Legacy missions (no improvement lineage) keep the unbounded path.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.control.capabilities import set_capability_state  # noqa: E402
from jarvis_foundry.control.policy import ActionPolicy  # noqa: E402
from jarvis_foundry.control.registration import (  # noqa: E402
    inspect_git_resource,
    registration_event,
)
from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.domain import ActionEnvelope  # noqa: E402
from jarvis_foundry.events import Event  # noqa: E402
from jarvis_foundry.evolution.binding import (  # noqa: E402
    CANDIDATE_RECORDED_EVENT,
    ImprovementBindingError,
    commit_scope_for_mission,
    ensure_commit_capability,
    materialize_bound_improvement,
    record_candidate,
    record_execution_binding,
)
from jarvis_foundry.evolution.records import record_improvement  # noqa: E402
from jarvis_foundry.mission.commit import CommitError, commit_after_pass  # noqa: E402


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=True,
    )
    return result.stdout.strip()


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-09-28T00:00:00Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build",
    ))
    return database


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@local")
    _git(root, "config", "user.name", "test")
    (root / "probe.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    return root


def _register(db: FoundryDatabase, resource_id: str, root: Path) -> None:
    db.append(registration_event(
        inspect_git_resource(root.resolve(), resource_id=resource_id)))


def _improvement(db: FoundryDatabase) -> str:
    return record_improvement(
        db,
        problem="probe loop repeats a stale read on every cycle",
        evidence_refs=["var/evidence/v6-candidate/probe-log.txt"],
        provenance={"source": "fixture"},
        hypothesis="caching the read removes the repeated cost",
        expected_mechanism="memoize the stable read behind a TTL",
        baseline_metric={"name": "cycle_cost", "unit": "ms",
                         "source": "probe", "value": 41.0},
        mmi={"minimum_delta": 5.0, "direction": "decrease", "unit": "ms"},
        guardrails={"correctness": "probe output unchanged",
                    "safety": "no new writes", "cost": "none",
                    "latency": "p99 under baseline"},
        workload={"ref": "probe-workload",
                  "hash": hashlib.sha256(b"probe").hexdigest()},
        observation={"requirement": "observe two cycles", "cycles": 2},
        declared_risk="R1",
        surfaces=["probe.py"],
        budget={"max_cost_usd": 0.0, "capability_scope": []},
        reversibility={"plan": "revert one commit",
                       "rollback_threshold": "cost above baseline"},
    )


def _bind(db: FoundryDatabase, improvement_id: str, resource_id: str,
           root: Path) -> dict:
    return record_execution_binding(
        db,
        improvement_id=improvement_id,
        resource_id=resource_id,
        baseline_sha=_git(root, "rev-parse", "HEAD"),
        allowed_paths=["probe.py"],
        forbidden_paths=[],
        workload_objective="memoize the probe read",
        required_tests=["tests/probe.py"],
        risk_class="R1",
        evidence_refs=["var/evidence/v6-candidate/probe-log.txt"],
        measurement_ref="improvement-baseline",
        success_criterion="cycle cost drops",
    )


def _mission(db: FoundryDatabase, mission_id: str, resource_id: str,
             improvement_id: str | None) -> None:
    created_from = (
        f"portfolio:improvement:{improvement_id}" if improvement_id
        else "owner-goal:standing"
    )
    db.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": "candidate ownership probe",
         "resource_id": resource_id, "state": "PLANNED",
         "acceptance_criteria": ["change lands as a JARVIS-owned commit"],
         "created_from": created_from},
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.created:{mission_id}",
    ))


def _verdict(db: FoundryDatabase, mission_id: str, step_id: str,
             run_id: str, verdict_id: str) -> None:
    db.append(Event.create(
        "verification.started",
        {"verdict_id": verdict_id, "mission_id": mission_id,
         "subject_type": "mission_step", "subject_id": step_id,
         "verifier_run_id": f"{run_id}-verify", "builder_run_id": run_id,
         "independence": {}, "criteria": ["bounded change only"]},
        source_type="test", source_id="test-suite",
        idempotency_key=f"verification.started:{verdict_id}",
    ))
    db.append(Event.create(
        "verification.passed",
        {"verdict_id": verdict_id, "verdict": "PASS",
         "verifier_run_id": f"{run_id}-verify",
         "findings": [], "missing_proof": [], "scope_violations": []},
        source_type="test", source_id="test-suite",
        idempotency_key=f"verification.passed:{verdict_id}",
    ))


def _envelope(resource_id: str, mission_id: str, root: Path) -> ActionEnvelope:
    return ActionEnvelope(
        action_id=f"commit:{mission_id}:02-execute:v1",
        mission_id=mission_id,
        resource_id=resource_id,
        action_kind="git_commit",
        target=root.resolve(),
        capability="git_local_commit",
        expected_effects=("create local commit of accepted step",),
        rollback={"kind": "git_reset_soft", "reversible": True},
    )


def _commit(db: FoundryDatabase, tmp_path: Path, mission_id: str,
            resource_id: str, root: Path, run_id: str):
    policy = ActionPolicy(db, tmp_path / "STOP")
    return commit_after_pass(
        db, mission_id=mission_id, step_id="02-execute", run_id=run_id,
        worktree=root, policy=policy,
        envelope=_envelope(resource_id, mission_id, root),
    )


# --- ensure_commit_capability ------------------------------------------------

def test_promotes_ready_on_clean_baseline(db, repo):
    _register(db, "res-1", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-1", repo)
    result = ensure_commit_capability(
        db, resource_id="res-1",
        baseline_sha=_git(repo, "rev-parse", "HEAD"),
        improvement_id=improvement_id, binding_id="v6-binding:test")
    assert result["state"] == "READY"
    assert result["head"] == _git(repo, "rev-parse", "HEAD").lower()


def test_refuses_dirty_worktree(db, repo):
    _register(db, "res-1", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-1", repo)
    (repo / "probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(ImprovementBindingError):
        ensure_commit_capability(
            db, resource_id="res-1",
            baseline_sha=_git(repo, "rev-parse", "HEAD"),
            improvement_id=improvement_id)


def test_refuses_off_baseline(db, repo):
    _register(db, "res-1", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-1", repo)
    (repo / "probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "drift")
    with pytest.raises(ImprovementBindingError):
        ensure_commit_capability(
            db, resource_id="res-1",
            baseline_sha="0" * 40, improvement_id=improvement_id)


def test_refuses_unregistered_resource(db, repo):
    with pytest.raises(ImprovementBindingError):
        ensure_commit_capability(
            db, resource_id="res-missing", baseline_sha="0" * 40,
            improvement_id="improvement-missing")


# --- scope resolution ---------------------------------------------------------

def test_scope_none_for_legacy_mission(db, repo):
    _register(db, "res-1", repo)
    _mission(db, "mission-legacy", "res-1", None)
    assert commit_scope_for_mission(db, "mission-legacy") is None


def test_scope_resolves_for_bound_mission(db, repo):
    _register(db, "res-1", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-1", repo)
    _mission(db, "mission-bound", "res-1", improvement_id)
    scope = commit_scope_for_mission(db, "mission-bound")
    assert scope is not None
    assert scope["improvement_id"] == improvement_id
    assert scope["allowed_paths"] == ("probe.py",)


def test_scope_fails_closed_without_binding(db, repo):
    _register(db, "res-1", repo)
    improvement_id = _improvement(db)
    _mission(db, "mission-orphan", "res-1", improvement_id)
    with pytest.raises(ImprovementBindingError):
        commit_scope_for_mission(db, "mission-orphan")


# --- autonomous commit + candidate --------------------------------------------

def _bound_setup(db, repo, mission_id="mission-1", run_id="run-1"):
    _register(db, "res-1", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-1", repo)
    ensure_commit_capability(
        db, resource_id="res-1",
        baseline_sha=_git(repo, "rev-parse", "HEAD"),
        improvement_id=improvement_id)
    set_capability_state(db, "res-1", "git_local_commit", "READY",
                         source_id="v6-binding:test-cap",
                         evidence={"improvement_id": improvement_id})
    _mission(db, mission_id, "res-1", improvement_id)
    return improvement_id


def test_bound_commit_lands_candidate(db, repo, tmp_path):
    improvement_id = _bound_setup(db, repo)
    (repo / "probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    verdict_id = f"verdict-{uuid.uuid4().hex[:8]}"
    _verdict(db, "mission-1", "02-execute", "run-1", verdict_id)

    result = _commit(db, tmp_path, "mission-1", "res-1", repo, "run-1")

    assert result["performed"] is True
    assert result["commit_head"] != result["baseline_commit"]
    head = _git(repo, "rev-parse", "HEAD")
    assert result["commit_head"] == head
    assert result["tree_sha"] == _git(repo, "rev-parse", "HEAD^{tree}")
    assert result["files_changed"] == ["probe.py"]
    candidate_id = result["candidate_id"]
    assert candidate_id.startswith("candidate:mission-1:02-execute:")
    events = [e for e in db.events(event_types=(CANDIDATE_RECORDED_EVENT,))
              if e.payload.get("candidate_id") == candidate_id]
    assert len(events) == 1
    payload = events[0].payload
    assert payload["producer_run_id"] == "run-1"
    assert payload["verdict_id"] == verdict_id
    assert payload["improvement_id"] == improvement_id
    assert payload["commit_head"] == head
    assert payload["state"] == "RECORDED"


def test_replay_is_idempotent(db, repo, tmp_path):
    _bound_setup(db, repo)
    (repo / "probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    _verdict(db, "mission-1", "02-execute", "run-1", "verdict-replay-1")

    first = _commit(db, tmp_path, "mission-1", "res-1", repo, "run-1")
    second = _commit(db, tmp_path, "mission-1", "res-1", repo, "run-1")

    assert second["commit_head"] == first["commit_head"]
    assert second["candidate_id"] == first["candidate_id"]
    assert _git(repo, "rev-list", "--count", "HEAD") == "2"
    events = [e for e in db.events(event_types=(CANDIDATE_RECORDED_EVENT,))]
    assert len(events) == 1


def test_outside_scope_fails_closed_without_commit(db, repo, tmp_path):
    _bound_setup(db, repo)
    (repo / "probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "other.py").write_text("OTHER = 2\n", encoding="utf-8")
    _verdict(db, "mission-1", "02-execute", "run-1", "verdict-scope-1")
    before = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(CommitError, match="outside the bound scope"):
        _commit(db, tmp_path, "mission-1", "res-1", repo, "run-1")

    assert _git(repo, "rev-parse", "HEAD") == before
    assert list(db.events(event_types=(CANDIDATE_RECORDED_EVENT,))) == []


def test_legacy_mission_keeps_unbounded_commit(db, repo, tmp_path):
    _register(db, "res-1", repo)
    set_capability_state(db, "res-1", "git_local_commit", "READY",
                         source_id="test-legacy",
                         evidence={"reason": "legacy path"})
    _mission(db, "mission-legacy", "res-1", None)
    (repo / "probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "other.py").write_text("OTHER = 2\n", encoding="utf-8")
    _verdict(db, "mission-legacy", "02-execute", "run-legacy", "verdict-legacy-1")

    result = _commit(db, tmp_path, "mission-legacy", "res-1", repo, "run-legacy")

    assert result["performed"] is True
    assert sorted(result["files_changed"]) == ["other.py", "probe.py"]
    assert "candidate_id" not in result
    assert list(db.events(event_types=(CANDIDATE_RECORDED_EVENT,))) == []


def test_no_change_records_no_candidate(db, repo, tmp_path):
    _bound_setup(db, repo)
    _verdict(db, "mission-1", "02-execute", "run-1", "verdict-empty-1")

    result = _commit(db, tmp_path, "mission-1", "res-1", repo, "run-1")

    assert result.get("no_change") is True
    assert "candidate_id" not in result
    assert list(db.events(event_types=(CANDIDATE_RECORDED_EVENT,))) == []


def test_conflicting_envelope_refused(db, repo):
    record_candidate(
        db, mission_id="mission-1", step_id="02-execute", run_id="run-1",
        verdict_id="verdict-a", resource_id="res-1",
        improvement_id="improvement-9f8e7d6c5b4a",
        baseline_sha="0" * 40, commit_head="a" * 40, tree_sha="b" * 40,
        worktree=str(repo), files_changed=["probe.py"])
    with pytest.raises(ImprovementBindingError, match="different content"):
        record_candidate(
            db, mission_id="mission-1", step_id="02-execute", run_id="run-1",
            verdict_id="verdict-B", resource_id="res-1",
            improvement_id="improvement-9f8e7d6c5b4a",
            baseline_sha="0" * 40, commit_head="a" * 40,
            tree_sha="c" * 40, worktree=str(repo),
            files_changed=["probe.py"])


def test_dirty_worktree_refuses_before_any_mission_write(db, repo):
    _register(db, "res-1", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-1", repo)
    (repo / "probe.py").write_text("VALUE = 9\n", encoding="utf-8")
    with pytest.raises(ImprovementBindingError, match="dirty"):
        materialize_bound_improvement(
            db, item_id=f"improvement:{improvement_id}",
            decision_id="decision-dirty",
            improvement_id=improvement_id, objective="memoize the probe read",
        )
    assert list(db.events(event_types=("mission.created",))) == []
    assert list(db.events(
        event_types=("portfolio.selection_materialized",))) == []


def test_no_verdict_refuses_commit(db, repo, tmp_path):
    _bound_setup(db, repo)
    (repo / "probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    before = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(CommitError, match="no verification verdict"):
        _commit(db, tmp_path, "mission-1", "res-1", repo, "run-1")
    assert _git(repo, "rev-parse", "HEAD") == before


def test_fail_verdict_refuses_commit(db, repo, tmp_path):
    _bound_setup(db, repo)
    (repo / "probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    db.append(Event.create(
        "verification.started",
        {"verdict_id": "verdict-fail-1", "mission_id": "mission-1",
         "subject_type": "mission_step", "subject_id": "02-execute",
         "verifier_run_id": "run-1-verify", "builder_run_id": "run-1",
         "independence": {}, "criteria": ["bounded change only"]},
        source_type="test", source_id="test-suite",
        idempotency_key="verification.started:verdict-fail-1",
    ))
    db.append(Event.create(
        "verification.failed",
        {"verdict_id": "verdict-fail-1", "verdict": "FAIL",
         "findings": ["out of scope"], "missing_proof": [],
         "scope_violations": ["other.py"]},
        source_type="test", source_id="test-suite",
        idempotency_key="verification.failed:verdict-fail-1",
    ))
    before = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(CommitError, match="verified PASS"):
        _commit(db, tmp_path, "mission-1", "res-1", repo, "run-1")
    assert _git(repo, "rev-parse", "HEAD") == before
    assert list(db.events(event_types=(CANDIDATE_RECORDED_EVENT,))) == []


def test_unlinkable_item_refuses_before_any_write(db, repo):
    _register(db, "res-1", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-1", repo)
    with pytest.raises(ImprovementBindingError, match="no improvement lineage"):
        materialize_bound_improvement(
            db, item_id="v6-closure-proof-m1", decision_id="decision-nolineage",
            improvement_id=improvement_id, objective="memoize the probe read",
        )
    assert list(db.events(event_types=("mission.created",))) == []
    assert list(db.events(
        event_types=("portfolio.selection_materialized",))) == []


def test_worker_committed_change_is_claimed(db, repo, tmp_path):
    _bound_setup(db, repo)
    (repo / "probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", "probe.py")
    _git(repo, "commit", "-qm", "worker: memoize the probe read")
    worker_head = _git(repo, "rev-parse", "HEAD")
    _verdict(db, "mission-1", "02-execute", "run-1", "verdict-claim-1")

    result = _commit(db, tmp_path, "mission-1", "res-1", repo, "run-1")

    assert result["performed"] is False
    assert result["worker_committed"] is True
    assert result["commit_head"] == worker_head
    assert result["files_changed"] == ["probe.py"]
    candidate_id = result["candidate_id"]
    events = [e for e in db.events(event_types=(CANDIDATE_RECORDED_EVENT,))
              if e.payload.get("candidate_id") == candidate_id]
    assert len(events) == 1
    assert events[0].payload["producer_run_id"] == "run-1"
    assert _git(repo, "rev-list", "--count", "HEAD") == "2"


def test_worker_committed_outside_scope_fails_closed(db, repo, tmp_path):
    _bound_setup(db, repo)
    (repo / "other.py").write_text("OTHER = 2\n", encoding="utf-8")
    _git(repo, "add", "other.py")
    _git(repo, "commit", "-qm", "worker: out of scope")
    _verdict(db, "mission-1", "02-execute", "run-1", "verdict-claim-2")

    with pytest.raises(CommitError, match="outside the bound scope"):
        _commit(db, tmp_path, "mission-1", "res-1", repo, "run-1")

    assert list(db.events(event_types=(CANDIDATE_RECORDED_EVENT,))) == []


def test_plan_file_and_caches_tolerated_not_committed(db, repo, tmp_path):
    _bound_setup(db, repo)
    (repo / "probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "01-plan.md").write_text("# plan\n", encoding="utf-8")
    cache = repo / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "probe.cpython-313.pyc").write_bytes(b"\x00")
    (repo / ".jarvis-result.json").write_text(
        '{"files_changed": ["probe.py"]}', encoding="utf-8")
    _verdict(db, "mission-1", "02-execute", "run-1", "verdict-tolerated-1")

    result = _commit(db, tmp_path, "mission-1", "res-1", repo, "run-1")

    assert result["performed"] is True
    assert result["files_changed"] == ["probe.py"]
    committed = _git(repo, "diff", "--name-only",
                     result["baseline_commit"], result["commit_head"], "--")
    assert committed.splitlines() == ["probe.py"]
    status = _git(repo, "status", "--porcelain=v1")
    assert "01-plan.md" in status  # still untracked evidence


def test_caches_only_is_no_change(db, repo, tmp_path):
    _bound_setup(db, repo)
    cache = repo / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "probe.cpython-313.pyc").write_bytes(b"\x00")
    _verdict(db, "mission-1", "02-execute", "run-1", "verdict-cache-1")

    result = _commit(db, tmp_path, "mission-1", "res-1", repo, "run-1")

    assert result.get("no_change") is True
    assert "candidate_id" not in result
