"""Standing-goal continuation: parent survives first child COMPLETE.

Focused regression for the P1 where a standing strategic owner goal closed
after one bounded child mission.  Covers the 12 required edges without
Hoowen-specific loops.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import OwnerControlService, sign_command

SECRET = b"standing-continuation-test-secret-0123456789abcdef"

STANDING_TEXT = (
    "Turn demo product into a commercially credible workforce-management product "
    "that I could confidently demonstrate to a real contact-center operations team. "
    "Inspect the current product and decide what the highest-value work is. "
    "Research when necessary, choose the work yourself, implement and independently "
    "verify it, learn from the outcome, and continue to the next highest-value improvement. "
    "Ask me only when there is a genuine owner-only decision or blocker."
)

ONESHOT_TEXT = (
    "Report the supervisor heartbeat age in seconds. "
    "success: one verified line stating the heartbeat age."
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "standing-continuation.db")
    database.initialize()
    return database


def _controls(db: FoundryDatabase) -> OwnerControlService:
    return OwnerControlService(
        db, secret=SECRET, stop_path=db.path.parent / "STOP", owner_id="test-owner",
    )


def _delegate(db: FoundryDatabase, text: str, nonce: str, metadata: dict | None = None) -> str:
    svc = _controls(db)
    meta = dict(metadata or {})
    meta.setdefault("mode", "HANDLE")
    meta.setdefault("request_id", f"req-{nonce}")
    applied = svc.apply(
        "DELEGATE", target="GLOBAL", exact_text=text, nonce=nonce,
        signature=sign_command(SECRET, "DELEGATE", "GLOBAL", text, nonce, metadata=meta),
        metadata=meta,
    )
    return applied.directive_id


def _goal(db: FoundryDatabase, text: str, nonce: str) -> str:
    applied = _controls(db).apply(
        "GOAL", target="GLOBAL", exact_text=text, nonce=nonce,
        signature=sign_command(SECRET, "GOAL", "GLOBAL", text, nonce),
    )
    return applied.directive_id


def _ready_resource(db: FoundryDatabase, tmp_path: Path, resource_id: str) -> str:
    root = tmp_path / resource_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "frontend").mkdir(exist_ok=True)
    (root / "README.md").write_text("demo", encoding="utf-8")
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO resources "
            "(resource_id, kind, root_path, resolved_root, baseline_json, "
            "capabilities_json, readiness_state, last_event_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                resource_id, "local_repository", str(root), str(root),
                '{"git": {"branch": "main", "head": "abc123"}}',
                "{}", "READY", "", "2026-10-06T00:00:00Z",
            ),
        )
    return str(root)


def _admit_standing_child(db: FoundryDatabase, directive_id: str, resource_id: str) -> str:
    from jarvis_foundry.delegation.service import reconcile_owner_delegations
    from jarvis_foundry.executive.autonomy import rebuild_executive_state, sync_owner_goals

    reconcile_owner_delegations(db)
    sync_owner_goals(db)
    state = rebuild_executive_state(db)
    oid = f"owner-goal:{directive_id}"
    assert oid in state.objectives
    # Bind the known test resource so follow-up derivation has project state.
    from jarvis_foundry.executive.autonomy import update_objective
    update_objective(db, oid, {"resource_id": resource_id})
    return oid


def _complete_child(db: FoundryDatabase, objective_id: str) -> None:
    from jarvis_foundry.executive.autonomy import (
        PlanStep,
        build_plan,
        decide_continuation,
        evaluate_objective_progress,
    )

    build_plan(db, objective_id, [PlanStep(
        step_id="execute", objective_id=objective_id, description="Execute bounded slice.",
        output_contract=("slice implemented",), verification_contract=("verifier PASS",),
    )])
    # Mark the single step COMPLETED with evidence, then reconcile progress.
    from jarvis_foundry.executive.autonomy import rebuild_executive_state
    state = rebuild_executive_state(db)
    plan = next(p for p in state.plans.values() if p.objective_id == objective_id)
    completed = tuple(
        __import__("dataclasses").replace(s, status="COMPLETED", evidence_refs=("verdict-test-1",))
        for s in plan.steps
    )
    db.append(Event.create(
        "executive.plan_updated",
        {"plan_id": plan.plan_id, "objective_id": objective_id,
         "steps": [s.to_payload() for s in completed],
         "critical_path": list(plan.critical_path), "status": "ACTIVE",
         "version": plan.version + 1},
        source_type="supervisor", source_id="test",
        idempotency_key=f"executive.plan_updated:{plan.plan_id}:{plan.version + 1}",
    ))
    evaluation = evaluate_objective_progress(db, objective_id)
    assert evaluation.status == "COMPLETED"
    decision = decide_continuation(db, objective_id)
    assert decision.action == "CLOSE"


def test_01_standing_survives_first_child_complete(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.executive.autonomy import rebuild_executive_state
    from jarvis_foundry.executive.standing_followup import standing_followup_needed

    resource_id = "resource-standing-01"
    _ready_resource(db, tmp_path, resource_id)
    directive_id = _delegate(db, STANDING_TEXT, "s01")
    oid = _admit_standing_child(db, directive_id, resource_id)
    _complete_child(db, oid)
    state = rebuild_executive_state(db)
    assert state.objectives[oid].status == "COMPLETED"
    needed, reason = standing_followup_needed(db, directive_id, state=state)
    assert needed, reason


def test_02_completed_child_immutable(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.executive.autonomy import rebuild_executive_state
    from jarvis_foundry.executive.standing_followup import ensure_standing_followup

    resource_id = "resource-standing-02"
    _ready_resource(db, tmp_path, resource_id)
    directive_id = _delegate(db, STANDING_TEXT, "s02")
    oid = _admit_standing_child(db, directive_id, resource_id)
    _complete_child(db, oid)
    before = rebuild_executive_state(db).objectives[oid].to_payload()
    followup = ensure_standing_followup(db, directive_id)
    assert followup is not None
    assert followup.objective_id != oid
    after = rebuild_executive_state(db).objectives[oid].to_payload()
    assert after["status"] == "COMPLETED"
    assert after["objective"] == before["objective"]
    assert after["version"] == before["version"]


def test_03_next_child_from_same_directive(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.executive.autonomy import rebuild_executive_state
    from jarvis_foundry.executive.standing_followup import ensure_standing_followup

    resource_id = "resource-standing-03"
    _ready_resource(db, tmp_path, resource_id)
    directive_id = _delegate(db, STANDING_TEXT, "s03")
    oid = _admit_standing_child(db, directive_id, resource_id)
    _complete_child(db, oid)
    followup = ensure_standing_followup(db, directive_id)
    assert followup is not None
    assert followup.provenance.get("directive_id") == directive_id
    assert followup.objective_id.startswith(f"owner-goal:{directive_id}:followup:")
    assert followup.status in {"ACTIVE", "PLANNING", "REGRESSED"}


def test_04_oneshot_terminates_normally(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.executive.autonomy import (
        rebuild_executive_state,
        sync_owner_goals,
    )
    from jarvis_foundry.executive.standing_followup import standing_followup_needed

    _goal(db, ONESHOT_TEXT, "o04")
    sync_owner_goals(db)
    state = rebuild_executive_state(db)
    oids = [k for k in state.objectives if k.startswith("owner-goal:")]
    assert len(oids) == 1
    oid = oids[0]
    _complete_child(db, oid)
    state = rebuild_executive_state(db)
    assert state.objectives[oid].status == "COMPLETED"
    # One-shot directive id is the suffix after owner-goal:.
    directive_id = oid.removeprefix("owner-goal:")
    needed, _ = standing_followup_needed(db, directive_id, state=state)
    assert not needed


def test_05_unrelated_objectives_do_not_block_standing(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.delegation.service import DelegationService
    from jarvis_foundry.executive.autonomy import rebuild_executive_state

    resource_id = "resource-standing-05"
    _ready_resource(db, tmp_path, resource_id)
    standing_id = _delegate(db, STANDING_TEXT, "s05")
    _admit_standing_child(db, standing_id, resource_id)
    # Two unrelated one-shot goals create global multiplicity.
    _goal(db, "Ship the observable outcome. success: receipt file exists.", "u05a")
    _goal(db, "Second outcome. success: second receipt exists.", "u05b")
    from jarvis_foundry.executive.autonomy import sync_owner_goals
    sync_owner_goals(db)
    # Standing binding must scope to its own directive, not fail on global count.
    other_id = _delegate(db, "Finish the observable outcome work quickly.", "u05c")
    outcome = DelegationService(db).process_directive(other_id)
    assert outcome.status == "WAITING_FOR_EXECUTIVE_PLAN"
    # Standing directive itself binds to its own child, not the global pool.
    standing_outcome = DelegationService(db).process_directive(standing_id)
    assert standing_outcome.status in {"ADMITTED", "WAITING_FOR_EXECUTIVE_PLAN", "BLOCKED"}
    # It must never report generic multiple-choice when its own child exists.
    if standing_outcome.status == "BLOCKED":
        assert "multiple Executive objectives" not in standing_outcome.reason


def test_06_post_outcome_wake_reaches_fresh_decision(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.executive.autonomy import rebuild_executive_state, run_autonomy_cycle
    from jarvis_foundry.executive.standing_followup import ensure_standing_followup

    resource_id = "resource-standing-06"
    _ready_resource(db, tmp_path, resource_id)
    directive_id = _delegate(db, STANDING_TEXT, "s06")
    oid = _admit_standing_child(db, directive_id, resource_id)
    _complete_child(db, oid)
    followup = ensure_standing_followup(db, directive_id)
    assert followup is not None
    cycle = run_autonomy_cycle(db, cycle_id="standing-wake-06")
    # Fresh decision: either the follow-up is selected or a new plan exists.
    assert cycle.objective_id is not None or cycle.portfolio_decision.get("decision") in {"SELECT", "WAIT"}
    assert cycle.continuation is None or cycle.continuation.action != "CLOSE" or cycle.objective_id != oid


def test_07_no_duplicate_child_on_replay(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.executive.autonomy import rebuild_executive_state
    from jarvis_foundry.executive.standing_followup import ensure_standing_followup

    resource_id = "resource-standing-07"
    _ready_resource(db, tmp_path, resource_id)
    directive_id = _delegate(db, STANDING_TEXT, "s07")
    oid = _admit_standing_child(db, directive_id, resource_id)
    _complete_child(db, oid)
    first = ensure_standing_followup(db, directive_id)
    second = ensure_standing_followup(db, directive_id)
    assert first is not None and second is not None
    assert first.objective_id == second.objective_id
    state = rebuild_executive_state(db)
    followups = [k for k in state.objectives if k.startswith(f"owner-goal:{directive_id}:followup:")]
    assert len(followups) == 1


def test_08_no_duplicate_mission(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.executive.autonomy import run_autonomy_cycle

    resource_id = "resource-standing-08"
    _ready_resource(db, tmp_path, resource_id)
    directive_id = _delegate(db, STANDING_TEXT, "s08")
    oid = _admit_standing_child(db, directive_id, resource_id)
    _complete_child(db, oid)
    first = run_autonomy_cycle(db, cycle_id="standing-mission-08a")
    second = run_autonomy_cycle(db, cycle_id="standing-mission-08a")
    assert first.to_payload() == second.to_payload()
    missions_first = first.materialization.get("mission_id")
    missions_second = second.materialization.get("mission_id")
    assert missions_first == missions_second


def test_09_withdrawal_stops_continuation(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.executive.autonomy import rebuild_executive_state, sync_owner_goals
    from jarvis_foundry.executive.standing_followup import ensure_standing_followup, standing_followup_needed

    resource_id = "resource-standing-09"
    _ready_resource(db, tmp_path, resource_id)
    directive_id = _delegate(db, STANDING_TEXT, "s09")
    oid = _admit_standing_child(db, directive_id, resource_id)
    _complete_child(db, oid)
    _controls(db).revoke(
        directive_id, nonce="rev09",
        signature=sign_command(SECRET, "REVOKE", directive_id, "revoke owner directive", "rev09"),
    )
    sync_owner_goals(db)
    needed, _ = standing_followup_needed(db, directive_id)
    assert not needed
    assert ensure_standing_followup(db, directive_id) is None


def test_10_genuine_complete_can_terminate(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.executive.standing_followup import standing_followup_needed

    resource_id = "resource-standing-10"
    _ready_resource(db, tmp_path, resource_id)
    directive_id = _delegate(
        db, STANDING_TEXT, "s10",
        metadata={"standing_satisfied": True},
    )
    oid = _admit_standing_child(db, directive_id, resource_id)
    _complete_child(db, oid)
    needed, _ = standing_followup_needed(db, directive_id)
    assert not needed


def test_11_justified_wait_has_revisit(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.executive.continuous import rebuild_wake_state
    from jarvis_foundry.executive.standing_followup import ensure_standing_followup

    # No READY resource: follow-up cannot be executable, must WAIT with revisit.
    directive_id = _delegate(db, STANDING_TEXT, "s11")
    from jarvis_foundry.delegation.service import reconcile_owner_delegations
    from jarvis_foundry.executive.autonomy import rebuild_executive_state, sync_owner_goals
    reconcile_owner_delegations(db)
    sync_owner_goals(db)
    oid = f"owner-goal:{directive_id}"
    _complete_child(db, oid)
    assert ensure_standing_followup(db, directive_id) is None
    wakes = rebuild_wake_state(db)
    standing_wakes = [w for w in wakes.values() if str(w.get("signal") or "") == f"standing-followup:{directive_id}"]
    assert standing_wakes, "expected a durable standing revisit wake"
    wake = standing_wakes[0]
    assert str(wake.get("reason") or "")
    assert str(wake.get("signal") or "")


def test_12_restart_preserves_lineage(db: FoundryDatabase, tmp_path: Path) -> None:
    from jarvis_foundry.database import FoundryDatabase as _DB
    from jarvis_foundry.executive.autonomy import rebuild_executive_state
    from jarvis_foundry.executive.standing_followup import ensure_standing_followup

    resource_id = "resource-standing-12"
    _ready_resource(db, tmp_path, resource_id)
    directive_id = _delegate(db, STANDING_TEXT, "s12")
    oid = _admit_standing_child(db, directive_id, resource_id)
    _complete_child(db, oid)
    followup = ensure_standing_followup(db, directive_id)
    assert followup is not None
    # Simulate restart: reopen the same database file and rebuild.
    reopened = _DB(db.path)
    state = rebuild_executive_state(reopened)
    assert state.objectives[oid].status == "COMPLETED"
    assert followup.objective_id in state.objectives
    assert state.objectives[followup.objective_id].provenance.get("directive_id") == directive_id
    assert state.objectives[followup.objective_id].status != "COMPLETED"
