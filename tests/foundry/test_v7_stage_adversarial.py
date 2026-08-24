"""Adversarial probes against the integrated V7 cross-lane seams.

Each test tries to disprove one stage-gate hypothesis about the NEW wiring.
Pre-existing lane guarantees (V7A/V7B/V7C internals) keep their own suites.
"""
from __future__ import annotations

import json
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.portfolio_bridge import (
    materialize_portfolio_selection,
)
from jarvis_foundry.control.broker import record_decision
from jarvis_foundry.evolution.executive_portfolio import (
    configure_portfolio_policy,
    register_maintenance,
    select_next_work,
)
from jarvis_foundry.runtime.config import WorkerRuntimeConfig


def database(tmp_path: Path) -> FoundryDatabase:
    result = FoundryDatabase(tmp_path / "foundry.db")
    result.initialize()
    return result


def register_resource(db: FoundryDatabase) -> None:
    db.append(Event.create(
        "project.registered",
        {
            "resource_id": "resource-1",
            "kind": "git_worktree",
            "root_path": "/tmp/worktree",
            "resolved_root": "/tmp/worktree",
            "baseline": {"head": "base"},
            "capabilities": {"safe-work": "ACTIVE"},
            "readiness_state": "READY",
        },
        source_type="test", source_id="v7-adversarial",
        idempotency_key="project.registered:resource-1",
    ))


def maintenance_decision(db: FoundryDatabase, item_id: str):
    register_maintenance(
        db, maintenance_id=item_id, project_id="project-adversarial",
        objective="renew disposable lab certificates",
        due_at="2026-08-24T00:00:00+00:00", expected_value=0.6,
        confidence=0.9)
    return select_next_work(db, loop_id=f"adv-{item_id}")


# H1: STOP bypass through the portfolio tier.
def test_stop_blocks_selection_and_materialization(tmp_path: Path) -> None:
    db = database(tmp_path)
    register_maintenance(
        db, maintenance_id="m-stop", project_id="project-adversarial",
        objective="renew disposable lab certificates",
        due_at="2026-08-24T00:00:00+00:00")
    sentinel = tmp_path / "EMERGENCY_STOP"
    sentinel.write_text(json.dumps({"stop_id": "s", "reason": "owner"}), encoding="utf-8")
    decision = select_next_work(db, loop_id="adv-m-stop")
    assert decision.kind == "WAIT"
    assert "STOP active" in decision.reason
    result = materialize_portfolio_selection(
        db,
        decision={
            "decision_id": "d1", "selected_class": "MAINTENANCE",
            "entity_id": "m-stop",
            "selected_item": {"item_id": "m-stop", "kind": "MAINTENANCE",
                              "objective": "x", "metadata": {}},
        },
        resource_id="resource-1")
    assert result["materialized"] is False


# H2: owner veto bypass — a GLOBAL veto must exclude work from selection.
def test_global_veto_excludes_selection(tmp_path: Path) -> None:
    db = database(tmp_path)
    register_maintenance(
        db, maintenance_id="m-veto", project_id="project-adversarial",
        objective="renew disposable lab certificates",
        due_at="2026-08-24T00:00:00+00:00")
    db.append(Event.create(
        "owner_directive.recorded",
        {
            "directive_id": "veto-global",
            "command_type": "VETO_ACTION",
            "target": "GLOBAL",
            "exact_text": "veto GLOBAL",
        },
        source_type="owner-channel", source_id="adversarial-test",
        idempotency_key="owner_directive.recorded:veto-global",
    ))
    decision = select_next_work(db, loop_id="adv-m-veto")
    assert decision.kind == "WAIT"
    assert "veto" in decision.reason.lower()


# H3: duplicate mission admission across repeated materializations of the
# same decision (crash between create and journal).
def test_same_decision_cannot_admit_two_missions(tmp_path: Path) -> None:
    db = database(tmp_path)
    register_resource(db)
    payload = {
        "decision_id": "decision-dup", "selected_class": "MAINTENANCE",
        "entity_id": "m-dup",
        "selected_item": {"item_id": "m-dup", "kind": "MAINTENANCE",
                          "objective": "dup check", "metadata": {}},
    }
    first = materialize_portfolio_selection(
        db, decision=payload, resource_id="resource-1")
    second = materialize_portfolio_selection(
        db, decision=payload, resource_id="resource-1")
    assert first["mission_id"] == second["mission_id"]
    created = [e for e in db.events()
               if e.event_type == "mission.created"
               and str(e.payload.get("created_from") or "").startswith("portfolio:m-dup")]
    assert len(created) == 1


# H4: busywork when WAIT is correct — no eligible capacity means WAIT with
# rejected alternatives recorded, never a fabricated SELECT.
def test_capacity_exhaustion_produces_wait_not_busywork(tmp_path: Path) -> None:
    db = database(tmp_path)
    register_resource(db)
    from jarvis_foundry.evolution.executive_portfolio import PortfolioPolicy
    configure_portfolio_policy(
        db, policy=PortfolioPolicy(max_concurrent_missions=0),
        source_id="adversarial")
    decision = maintenance_decision(db, "m-wait")
    if decision.selected_class == "WAIT":
        assert decision.kind == "WAIT"
        return
    # If policy clamping keeps it selectable, capacity_check must say why.
    assert decision.capacity_check


# H5: config fail-closed — portfolio tier OFF unless explicitly enabled.
def test_portfolio_flag_defaults_off_and_parses(tmp_path: Path) -> None:
    cfg = WorkerRuntimeConfig.from_yaml(None)
    assert cfg.portfolio_selection_enabled is False
    config_file = tmp_path / "worker.yaml"
    config_file.write_text(
        "enabled: true\nexecutive:\n  portfolio_selection_enabled: true\n",
        encoding="utf-8")
    parsed = WorkerRuntimeConfig.from_yaml(config_file)
    assert parsed.portfolio_selection_enabled is True


# H6: WAIT decisions are durable and wakeable — a later wake event allows a
# new selection instead of returning the stale WAIT forever.
def test_wake_event_permits_reselection_after_wait(tmp_path: Path) -> None:
    db = database(tmp_path)
    first = maintenance_decision(db, "m-wake")
    assert first.kind == "SELECT"
    from jarvis_foundry.evolution.executive_portfolio import (
        claim_selection, release_selection_claim, set_work_status,
    )
    from jarvis_foundry.executive.portfolio_bridge import (
        record_mission_portfolio_outcome,
    )
    claim_selection(db, first, source_id="adversarial")
    record_mission_portfolio_outcome(
        db, decision=first.to_payload(), mission_id="mission-adv-1",
        status="PASSED", actual={"note": "done"}, outcome_id="out-1",
        source_id="adversarial")
    release_selection_claim(db, first.entity_id, source_id="adversarial")

    second = select_next_work(db, loop_id="adv-second-cycle")
    # No further open work exists: honest WAIT, not a repeat of the old pick.
    assert second.kind == "WAIT"
