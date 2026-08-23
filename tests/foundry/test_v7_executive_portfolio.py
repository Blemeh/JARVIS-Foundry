"""Adversarial tests for the V7 executive portfolio adapter.

These tests stay on disposable databases and exercise the ledger boundary only.
They do not start Supervisor, invoke providers, or touch the canonical runtime.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.evolution.executive_portfolio import (
    PortfolioError,
    PortfolioPolicy,
    claim_selection,
    configure_portfolio_policy,
    management_packet,
    plan_team,
    portfolio_calibration,
    portfolio_snapshot,
    record_portfolio_outcome,
    register_maintenance,
    register_research,
    register_unknown,
    register_work_item,
    release_selection_claim,
    replace_team_worker,
    retire_team_worker,
    select_next_work,
    wake_is_due,
)
from jarvis_foundry.supervisor.authority import SUPERVISOR_CONTROLLER
from jarvis_foundry.supervisor.estop import EmergencyStop


UTC = timezone.utc


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "portfolio.db")
    database.initialize()
    return database


def _register(
    database: FoundryDatabase,
    item_id: str,
    *,
    kind: str = "NEW_MISSION",
    score: float = 0.7,
    project_id: str | None = None,
    requires_verifier: bool = False,
    metadata: dict[str, object] | None = None,
    **kwargs: object,
) -> None:
    values: dict[str, object] = {
        "item_id": item_id,
        "kind": kind,
        "objective": f"Objective for {item_id}",
        "expected_value": score,
        "urgency": score,
        "information_gain": score,
        "confidence": 0.8,
        "effort": 0.2,
        "cost_usd": 0.0,
        "risk": 0.1,
        "reversibility": 0.8,
        "opportunity_cost": 0.1,
        "project_id": project_id,
        "requires_workers": 0,
        "requires_verifier": requires_verifier,
        "metadata": metadata or {},
    }
    values.update(kwargs)
    register_work_item(database, **values)  # type: ignore[arg-type]


def _policy(**changes: object) -> PortfolioPolicy:
    values: dict[str, object] = {
        "max_concurrent_missions": 1,
        "max_workers": 4,
        "max_verifiers": 1,
        "revisit_seconds": 300.0,
        "lease_ttl_seconds": 300.0,
    }
    values.update(changes)
    return PortfolioPolicy(**values)  # type: ignore[arg-type]


def _configure(database: FoundryDatabase, **changes: object) -> None:
    configure_portfolio_policy(database, _policy(**changes))


def _owner_veto(database: FoundryDatabase, *, target: str) -> None:
    database.append(Event.create(
        "owner_directive.recorded",
        {
            "directive_id": f"veto-{target}",
            "command_type": "VETO_ACTION",
            "target": target,
            "exact_text": f"veto {target}",
        },
        source_type="owner-channel",
        source_id="v7-test",
        idempotency_key=f"owner_directive.recorded:veto-{target}",
    ))


def test_selection_claims_replay_and_idempotent_claim(
    db: FoundryDatabase, tmp_path: Path,
) -> None:
    _configure(db)
    _register(db, "a", score=0.9)
    _register(db, "b", score=0.6)
    now = "2026-08-23T00:00:00+00:00"

    selected = select_next_work(db, loop_id="loop-1", now=now)
    assert selected.kind == "SELECT"
    assert selected.entity_id == "a"
    first = claim_selection(db, selected, now=now)
    second = claim_selection(db, selected, now=now)
    assert first["acquired"] is True
    assert second["reason"] == "idempotent_claim"

    fresh = FoundryDatabase(tmp_path / "restart.db")
    fresh.initialize()
    for event in db.events():
        fresh.append(Event.create(
            event.event_type,
            event.payload,
            occurred_at=event.recorded_at,
            source_type=event.source_type,
            source_id=event.source_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            idempotency_key=event.idempotency_key,
        ))
    snapshot = portfolio_snapshot(fresh)
    assert snapshot["claims"]["a"]["decision_id"] == selected.decision_id
    waiting = select_next_work(fresh, loop_id="loop-2", now=now)
    assert waiting.kind == "WAIT"
    assert "capacity:concurrent_missions" in waiting.alternatives[0]["reason"]

    release_selection_claim(fresh, "a")
    next_selection = select_next_work(
        fresh, loop_id="loop-3", now="2026-08-23T00:00:01+00:00",
    )
    assert next_selection.kind == "SELECT"
    assert next_selection.entity_id in {"a", "b"}


def test_reserved_capacity_and_claim_capacity_are_honest_waits(
    db: FoundryDatabase,
) -> None:
    _configure(db, reserved_capacity=1)
    _register(db, "normal", score=0.9)
    waiting = select_next_work(db, loop_id="reserved", now="2026-08-23T00:00:00+00:00")
    assert waiting.kind == "WAIT"
    assert "capacity:concurrent_missions" in waiting.alternatives[0]["reason"]
    assert waiting.capacity_check["snapshot"]["active_claims"] == 0


def test_claim_reserves_workers_verifiers_provider_calls_and_budget(
    db: FoundryDatabase,
) -> None:
    _configure(
        db,
        max_concurrent_missions=2,
        max_workers=1,
        max_verifiers=1,
        max_provider_calls=1,
        budget_usd=1.0,
    )
    _register(
        db,
        "expensive",
        score=0.9,
        requires_verifier=True,
        metadata={"provider": "opencode_go"},
        cost_usd=0.8,
        requires_workers=1,
    )
    _register(
        db,
        "second",
        score=0.8,
        requires_verifier=True,
        metadata={"provider": "opencode_go"},
        cost_usd=0.5,
        requires_workers=1,
    )
    first = select_next_work(db, loop_id="reservation-1", now="2026-08-23T00:00:00+00:00")
    assert first.entity_id == "expensive"
    claim_selection(db, first, now="2026-08-23T00:00:00+00:00")
    waiting = select_next_work(db, loop_id="reservation-2", now="2026-08-23T00:00:01+00:00")
    second = next(row for row in waiting.alternatives if row["entity_id"] == "second")
    assert waiting.kind == "WAIT"
    assert "capacity:workers" in second["reason"]
    assert "capacity:verifiers" in second["reason"]
    assert "capacity:provider_calls" in second["reason"]
    assert "budget:portfolio" in second["reason"]


def test_repair_budget_exhaustion_is_a_durable_rejection(db: FoundryDatabase) -> None:
    _configure(db, max_repair_attempts=2)
    _register(
        db,
        "repair-exhausted",
        kind="REPAIR",
        score=0.95,
        metadata={"repair_attempts": 2, "mission_id": "mission-x"},
    )
    decision = select_next_work(db, loop_id="repair-budget", now="2026-08-23T00:00:00+00:00")
    assert decision.kind == "WAIT"
    row = next(row for row in decision.alternatives if row["entity_id"] == "repair-exhausted")
    assert "repair_budget_exhausted" in row["reason"]


def test_stop_marker_and_scoped_owner_veto_never_select(db: FoundryDatabase) -> None:
    _register(db, "blocked", score=0.9)
    sentinel = EmergencyStop(db.path.parent / "EMERGENCY_STOP")
    sentinel.activate("owner STOP", source="v7-test")
    stopped = select_next_work(db, loop_id="stop", now="2026-08-23T00:00:00+00:00")
    assert stopped.kind == "WAIT"
    assert stopped.veto_check["stop"]
    sentinel.clear()

    _owner_veto(db, target="blocked")
    vetoed = select_next_work(db, loop_id="veto", now="2026-08-23T00:00:01+00:00")
    assert vetoed.kind == "WAIT"
    assert vetoed.alternatives
    assert "veto:active VETO_ACTION directive veto-blocked matches" in \
        vetoed.alternatives[0]["reason"]
    with pytest.raises(PortfolioError):
        select_next_work(db, loop_id="bad-controller", controller="worker")


def test_wait_is_quiet_until_new_work_or_scheduled_revisit(db: FoundryDatabase) -> None:
    _configure(db, revisit_seconds=300.0)
    first = select_next_work(db, loop_id="empty-1", now="2026-08-23T00:00:00+00:00")
    assert first.kind == "WAIT"
    count = db.event_count(event_type="portfolio.selection_recorded")
    quiet = select_next_work(db, loop_id="empty-2", now="2026-08-23T00:00:10+00:00")
    assert quiet.decision_id == first.decision_id
    assert db.event_count(event_type="portfolio.selection_recorded") == count

    _register(db, "new-work", score=0.8)
    selected = select_next_work(db, loop_id="new-work-loop", now="2026-08-23T00:00:11+00:00")
    assert selected.kind == "SELECT"

    db2 = FoundryDatabase(db.path.parent / "maintenance.db")
    db2.initialize()
    _configure(db2, revisit_seconds=300.0)
    register_maintenance(
        db2,
        maintenance_id="m1",
        project_id="p1",
        objective="revalidate project p1",
        due_at="2026-08-23T00:01:00+00:00",
    )
    wait = select_next_work(db2, loop_id="maintenance-wait", now="2026-08-23T00:00:00+00:00")
    assert wait.kind == "WAIT"
    assert not wake_is_due(db2, now="2026-08-23T00:00:30+00:00")
    assert wake_is_due(db2, now="2026-08-23T00:02:00+00:00")
    due = select_next_work(db2, loop_id="maintenance-due", now="2026-08-23T00:02:00+00:00")
    assert due.kind == "SELECT"
    assert due.selected_class == "MAINTENANCE"


def test_owner_only_research_and_unknown_are_blocked_for_packet(db: FoundryDatabase) -> None:
    _configure(db)
    register_research(
        db,
        research_id="r1",
        question="Which provider is currently healthy?",
        why_matters="provider routing",
        evidence_needed=["health probe"],
        confidence_threshold=0.9,
        owner_only=True,
    )
    register_unknown(
        db,
        unknown_id="u1",
        question="Is the external dependency approved?",
        why_matters="release safety",
        evidence_needed=["owner decision"],
        owner_only=True,
    )
    decision = select_next_work(db, loop_id="owner-only", now="2026-08-23T00:00:00+00:00")
    assert decision.kind == "WAIT"
    assert any("owner_only_fact" in row["reason"] for row in decision.alternatives)
    packet = management_packet(db, now="2026-08-23T00:00:01+00:00")
    assert len(packet["owner_decisions_needed"]) == 2
    assert packet["waiting"]["decision"] == "WAIT"


def test_fairness_aging_prevents_starvation(db: FoundryDatabase) -> None:
    _configure(db, fairness_after_cycles=2, aging_step=0.5, max_aging_bonus=0.5)
    _register(db, "high", score=0.9)
    _register(db, "low", score=0.6)
    first = select_next_work(db, loop_id="fair-1", now="2026-08-23T00:00:00+00:00")
    assert first.entity_id == "high"
    claim_selection(db, first, now="2026-08-23T00:00:00+00:00")
    release_selection_claim(db, "high")
    second = select_next_work(db, loop_id="fair-2", now="2026-08-23T00:00:01+00:00")
    assert second.entity_id == "high"
    claim_selection(db, second, now="2026-08-23T00:00:01+00:00")
    release_selection_claim(db, "high")
    third = select_next_work(db, loop_id="fair-3", now="2026-08-23T00:00:02+00:00")
    assert third.entity_id == "low"
    assert third.selected_item["fairness_bonus"] >= 0.5


def test_calibration_is_bounded_and_raw_outcome_is_preserved(db: FoundryDatabase) -> None:
    _configure(db, calibration_alpha=1.0, calibration_cap=0.1)
    record_portfolio_outcome(
        db,
        outcome_id="out-1",
        selection_id="selection-1",
        entity_id="candidate-1",
        kind="CANDIDATE",
        predicted={"expected_value": 0.0, "provider": "opencode_go"},
        actual={"actual_value": 1.0, "status_code": "PASS"},
        status="PASSED",
        actual_cost_usd=0.02,
    )
    calibration = portfolio_calibration(db)
    assert calibration["raw_outcomes"] == 1
    assert abs(calibration["adjustments"]["CANDIDATE"]) <= 0.1
    outcome = [e for e in db.events() if e.event_type == "portfolio.outcome_recorded"][-1]
    assert outcome.payload["predicted"]["provider"] == "opencode_go"
    assert outcome.payload["actual"]["status_code"] == "PASS"


def test_team_lifecycle_is_idempotent_and_rejects_stale_workers(
    db: FoundryDatabase,
) -> None:
    team = plan_team(
        db,
        mission_id="mission-1",
        team_id="team-1",
        roles=["builder", "tester", "verifier"],
        builder_provider="opencode_go",
        builder_run_id="builder-run-1",
        verifier_identity={"provider": "deepseek-v4-flash", "session_id": "verify-1"},
    )
    same = plan_team(
        db,
        mission_id="mission-1",
        team_id="team-1",
        roles=["builder", "tester", "verifier"],
        builder_provider="opencode_go",
        builder_run_id="builder-run-1",
        verifier_identity={"provider": "deepseek-v4-flash", "session_id": "verify-2"},
    )
    assert same == team
    old = team["workers"]["builder"]
    replace_team_worker(
        db,
        team_id="team-1",
        role="builder",
        old_worker_id=old,
        new_worker_id="team-1:builder:2",
        reason="builder lease expired",
    )
    with pytest.raises(PortfolioError):
        replace_team_worker(
            db,
            team_id="team-1",
            role="builder",
            old_worker_id=old,
            new_worker_id="team-1:builder:3",
            reason="stale retry",
        )
    retire_team_worker(db, team_id="team-1", worker_id="team-1:tester:1", reason="not needed")
    snapshot = portfolio_snapshot(db)
    assert snapshot["teams"]["team-1"]["workers"]["builder"] == "team-1:builder:2"
    assert "team-1:builder:1" in snapshot["teams"]["team-1"]["retired_workers"]
    assert "team-1:tester:1" in snapshot["teams"]["team-1"]["retired_workers"]


def test_concurrent_loops_and_claims_converge(db: FoundryDatabase) -> None:
    _configure(db)
    _register(db, "single", score=0.9)
    def choose(_: int):
        return select_next_work(db, loop_id="same-loop", now="2026-08-23T00:00:00+00:00")

    with ThreadPoolExecutor(max_workers=4) as pool:
        decisions = list(pool.map(choose, range(4)))
    assert {decision.decision_id for decision in decisions} == {"portfolio-same-loop"}
    assert db.event_count(event_type="portfolio.selection_recorded") == 1

    def claim(_: int):
        return claim_selection(db, decisions[0], now="2026-08-23T00:00:00+00:00")

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(claim, range(4)))
    assert all(result["acquired"] is True for result in claims)
    assert db.event_count(event_type="portfolio.selection_claimed") == 1
    assert db.event_count(event_type="portfolio.capacity_lease_acquired") == 1


def test_stale_completion_is_recorded_without_rewriting_lineage(db: FoundryDatabase) -> None:
    _configure(db)
    _register(db, "repeatable", score=0.8)
    first = select_next_work(db, loop_id="stale-1", now="2026-08-23T00:00:00+00:00")
    claim_selection(db, first, now="2026-08-23T00:00:00+00:00")
    release_selection_claim(db, "repeatable")
    second = select_next_work(db, loop_id="stale-2", now="2026-08-23T00:00:01+00:00")
    claim_selection(db, second, now="2026-08-23T00:00:01+00:00")
    record_portfolio_outcome(
        db,
        outcome_id="stale-outcome",
        selection_id=first.decision_id,
        entity_id="repeatable",
        kind="NEW_MISSION",
        predicted={"expected_value": 0.8},
        actual={"actual_value": 0.2},
        status="FAILED",
        repair_count=1,
    )
    outcome = portfolio_snapshot(db)["outcomes"]
    assert outcome == 1
    raw = [e for e in db.events() if e.event_type == "portfolio.outcome_recorded"][-1].payload
    assert raw["stale_selection"] is True
    assert raw["selection_id"] == first.decision_id
    assert raw["repair_count"] == 1


def test_management_packet_exposes_selection_capacity_and_calibration(
    db: FoundryDatabase,
) -> None:
    _configure(db)
    _register(db, "packet-work", score=0.8, project_id="p1")
    register_unknown(
        db,
        unknown_id="packet-unknown",
        question="Need owner confirmation",
        why_matters="safety",
        owner_only=True,
    )
    select_next_work(db, loop_id="packet-loop", now="2026-08-23T00:00:00+00:00")
    packet = management_packet(db, now="2026-08-23T00:00:01+00:00")
    assert packet["latest_selection"]["decision"] == "SELECT"
    assert packet["next_selected"]["entity_id"] == "packet-work"
    assert packet["resource_cost_state"]["active_missions"] == 0
    assert packet["calibration"]["raw_outcomes"] == 0
