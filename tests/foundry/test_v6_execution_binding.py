"""V6 Phase 2: durable execution bindings for Improvements.

An Improvement becomes executable ONLY through a complete binding
(improvement + registered resource + pinned baseline + bounded scope +
workload objective + required tests + risk class + evidence +
measurement + success criterion).  Anything less fails closed and the
Improvement stays durable unresolved work.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, EventConflict
from jarvis_foundry.evolution.binding import (
    ImprovementBindingError,
    get_execution_binding,
    map_risk_class,
    materialize_bound_improvement,
    record_binding_consumed,
    record_execution_binding,
    verify_binding_for_execution,
)
from jarvis_foundry.evolution.lab import register_workload, run_evaluation
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.executive.portfolio_bridge import materialize_portfolio_selection

CONTROLLER = "supervisor"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=True,
    )
    return result.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@local")
    _git(root, "config", "user.name", "test")
    (root / "probe.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    return root


def _register(db: FoundryDatabase, resource_id: str, root: Path) -> None:
    db.append(Event.create(
        "project.registered",
        {
            "resource_id": resource_id,
            "kind": "git_worktree",
            "root_path": str(root),
            "resolved_root": str(root.resolve()),
            "baseline": {},
            "capabilities": {
                "read_registered_files": "READY",
                "write_registered_worktree_files": "READY",
                "run_registered_local_commands": "READY",
            },
            "readiness_state": "READY",
        },
        source_type="supervisor",
        source_id="fixture",
        idempotency_key=f"project.registered:{resource_id}",
    ))


def _improvement(db: FoundryDatabase) -> str:
    return record_improvement(
        db,
        problem="probe loop repeats a stale read on every cycle",
        evidence_refs=["var/evidence/v6-bind/probe-log.txt"],
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
        surfaces=["tests/probe.py"],
        budget={"max_cost_usd": 0.0, "capability_scope": []},
        reversibility={"plan": "revert one commit",
                       "rollback_threshold": "cost above baseline"},
    )


def _bind(db: FoundryDatabase, improvement_id: str, resource_id: str,
           root: Path, **overrides) -> dict:
    head = _git(root, "rev-parse", "HEAD")
    params: dict = {
        "improvement_id": improvement_id,
        "resource_id": resource_id,
        "baseline_sha": head,
        "allowed_paths": ["probe.py"],
        "forbidden_paths": [],
        "workload_objective": "memoize the stable probe read behind a TTL",
        "required_tests": ["tests/foundry/test_probe.py"],
        "risk_class": "R1",
        "evidence_refs": ["var/evidence/v6-bind/probe-log.txt"],
        "measurement_ref": "improvement-baseline",
        "success_criterion": "cycle cost drops by >= 5ms, output unchanged",
    }
    params.update(overrides)
    return record_execution_binding(db, **params)


# ---------------------------------------------------------------------------
# risk mapping
# ---------------------------------------------------------------------------

def test_risk_map_is_ordinal() -> None:
    assert map_risk_class("R0") == "C0"
    assert map_risk_class("R1") == "C1"
    assert map_risk_class("R2") == "C2"
    assert map_risk_class("r3") == "C3"
    with pytest.raises(ImprovementBindingError):
        map_risk_class("C1")
    with pytest.raises(ImprovementBindingError):
        map_risk_class("R9")


# ---------------------------------------------------------------------------
# record fail-closed
# ---------------------------------------------------------------------------

def test_no_binding_initially(db: FoundryDatabase) -> None:
    assert get_execution_binding(db, "improvement-missing") is None
    with pytest.raises(ImprovementBindingError):
        verify_binding_for_execution(db, "improvement-missing")


def test_record_requires_known_improvement_and_resource(
        db: FoundryDatabase, repo: Path) -> None:
    _register(db, "res-a", repo)
    with pytest.raises(ImprovementBindingError):
        _bind(db, "improvement-nope", "res-a", repo)
    improvement_id = _improvement(db)
    with pytest.raises(ImprovementBindingError):
        _bind(db, improvement_id, "res-nope", repo)


def test_record_rejects_bad_sha_scope_tests_and_risk(
        db: FoundryDatabase, repo: Path) -> None:
    _register(db, "res-a", repo)
    improvement_id = _improvement(db)
    with pytest.raises(ImprovementBindingError):
        _bind(db, improvement_id, "res-a", repo, baseline_sha="deadbeef")
    with pytest.raises(ImprovementBindingError):
        _bind(db, improvement_id, "res-a", repo, allowed_paths=[])
    with pytest.raises(ImprovementBindingError):
        _bind(db, improvement_id, "res-a", repo,
               allowed_paths=["../escape.py"])
    with pytest.raises(ImprovementBindingError):
        _bind(db, improvement_id, "res-a", repo,
               allowed_paths=["probe.py"], forbidden_paths=["probe.py"])
    with pytest.raises(ImprovementBindingError):
        _bind(db, improvement_id, "res-a", repo, required_tests=[])
    with pytest.raises(ImprovementBindingError):
        _bind(db, improvement_id, "res-a", repo, risk_class="C1")
    with pytest.raises(ImprovementBindingError):
        _bind(db, improvement_id, "res-a", repo, measurement_ref="  ")
    assert get_execution_binding(db, improvement_id) is None


# ---------------------------------------------------------------------------
# record / get / verify
# ---------------------------------------------------------------------------

def test_record_get_verify_round_trip(db: FoundryDatabase, repo: Path) -> None:
    _register(db, "res-a", repo)
    improvement_id = _improvement(db)
    recorded = _bind(db, improvement_id, "res-a", repo)
    fetched = get_execution_binding(db, improvement_id)
    assert fetched is not None
    assert fetched["baseline_sha"] == recorded["baseline_sha"]
    verified = verify_binding_for_execution(db, improvement_id)
    assert verified["workforce_risk_class"] == "C1"
    assert verified["resource_root"] == str(repo.resolve())
    assert verified["execute_capability_state"] == "READY"


def test_verify_fails_when_head_moves(db: FoundryDatabase, repo: Path) -> None:
    _register(db, "res-a", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-a", repo)
    (repo / "probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "drift")
    with pytest.raises(ImprovementBindingError) as caught:
        verify_binding_for_execution(db, improvement_id)
    assert "pinned baseline" in str(caught.value)


def test_verify_rejects_unknown_measurement_run(
        db: FoundryDatabase, repo: Path) -> None:
    _register(db, "res-a", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-a", repo, measurement_ref="run-nope")
    with pytest.raises(ImprovementBindingError):
        verify_binding_for_execution(db, improvement_id)


def test_verify_accepts_real_evaluation_run(
        db: FoundryDatabase, repo: Path, tmp_path: Path) -> None:
    _register(db, "res-a", repo)
    improvement_id = _improvement(db)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "case-1.txt").write_text("ping", encoding="utf-8")
    workload_id = register_workload(db, name="w1", input_dir=inputs,
                                    config={}, seeds=[], controller=CONTROLLER)
    run_id = run_evaluation(db, workload_id=workload_id,
                            subject_kind="baseline", subject_identity="base",
                            command=["python", "-c", "print('ok')"],
                            controller=CONTROLLER)
    _bind(db, improvement_id, "res-a", repo, measurement_ref=run_id)
    verified = verify_binding_for_execution(db, improvement_id)
    assert verified["measurement_ref"] == run_id


# ---------------------------------------------------------------------------
# materialization
# ---------------------------------------------------------------------------

def test_materialize_bound_improvement(db: FoundryDatabase, repo: Path) -> None:
    _register(db, "res-a", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-a", repo)
    result = materialize_bound_improvement(
        db, item_id="item-1", decision_id="decision-1",
        improvement_id=improvement_id, objective="memoize the probe read",
    )
    assert result["materialized"] is True
    mission_id = result["mission_id"]
    mission = db.row("missions", "mission_id", mission_id)
    assert mission is not None
    assert mission["resource_id"] == "res-a"
    from jarvis_foundry.mission.contracts import latest_contracts_by_step
    contracts = latest_contracts_by_step(db, mission_id)
    assert sorted(contracts) == ["01-plan", "02-execute"]
    plan, execute = contracts["01-plan"], contracts["02-execute"]
    assert plan.capability == "read_registered_files"
    assert execute.capability == "write_registered_worktree_files"
    assert execute.target_relative_path == "probe.py"
    assert execute.risk_class == "C1"
    assert success_text(execute) == "cycle cost drops by >= 5ms, output unchanged"
    assert "tests/foundry/test_probe.py" in " ".join(execute.verification_criteria)
    assert improvement_id in execute.instructions
    consumed = [event for event in db.events(
        event_types=("improvement.binding_consumed",), readonly=True)
        if event.payload.get("mission_id") == mission_id]
    assert len(consumed) == 1
    materialized = [event for event in db.events(
        event_types=("portfolio.selection_materialized",), readonly=True)
        if event.payload.get("decision_id") == "decision-1"]
    assert len(materialized) == 1


def success_text(contract) -> str:
    return contract.verification_criteria[0]


def test_materialize_is_idempotent_per_decision(
        db: FoundryDatabase, repo: Path) -> None:
    _register(db, "res-a", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-a", repo)
    first = materialize_bound_improvement(
        db, item_id="item-1", decision_id="decision-1",
        improvement_id=improvement_id, objective="memoize the probe read",
    )
    second = materialize_bound_improvement(
        db, item_id="item-1", decision_id="decision-1",
        improvement_id=improvement_id, objective="memoize the probe read",
    )
    assert first["mission_id"] == second["mission_id"]
    created = [event for event in db.events(
        event_types=("mission.created",), readonly=True)
        if event.payload.get("portfolio_item_id") == "item-1"]
    assert len(created) == 1


def test_binding_consumed_conflict_is_honest(
        db: FoundryDatabase, repo: Path) -> None:
    _register(db, "res-a", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-a", repo)
    record_binding_consumed(db, improvement_id=improvement_id,
                            mission_id="mission-x", decision_id="d-1")
    with pytest.raises(EventConflict):
        record_binding_consumed(db, improvement_id=improvement_id,
                                mission_id="mission-x", decision_id="d-1")


# ---------------------------------------------------------------------------
# portfolio gate: unbound still fails closed, bound materializes
# ---------------------------------------------------------------------------

def _decision(improvement_id: str) -> dict:
    return {
        "selected_class": "IMPROVEMENT",
        "decision_id": "decision-9",
        "reason": "test selection",
        "selected_item": {
            "item_id": "item-9",
            "objective": "memoize the probe read",
            "evidence_refs": [],
            "provenance": {},
            "metadata": {"improvement_id": improvement_id},
        },
    }


def test_missing_binding_module_still_fails_closed(
        db: FoundryDatabase, monkeypatch) -> None:
    import sys
    import jarvis_foundry.evolution as evolution_pkg
    improvement_id = _improvement(db)
    monkeypatch.setitem(sys.modules, "jarvis_foundry.evolution.binding", None)
    monkeypatch.delattr(evolution_pkg, "binding")
    result = materialize_portfolio_selection(
        db, decision=_decision(improvement_id), resource_id="res-a",
    )
    assert result["materialized"] is False
    assert "binding machinery unavailable" in (result["blocked_reason"] or "")


def test_unbound_improvement_still_fails_closed(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    result = materialize_portfolio_selection(
        db, decision=_decision(improvement_id), resource_id="res-a",
    )
    assert result["materialized"] is False
    assert "binding check" in (result["blocked_reason"] or "")
    blocked = db.events(event_types=("improvement.implementation_blocked",),
                        readonly=True)
    assert any(event.payload.get("improvement_id") == improvement_id
               for event in blocked)


def test_bound_improvement_materializes_through_gate(
        db: FoundryDatabase, repo: Path) -> None:
    _register(db, "res-a", repo)
    improvement_id = _improvement(db)
    _bind(db, improvement_id, "res-a", repo)
    decision = _decision(improvement_id)
    result = materialize_portfolio_selection(
        db, decision=decision, resource_id="res-a",
    )
    assert result["materialized"] is True
    assert result["blocked_reason"] is None
    mission = db.row("missions", "mission_id", result["mission_id"])
    assert mission is not None
    assert mission["resource_id"] == "res-a"
