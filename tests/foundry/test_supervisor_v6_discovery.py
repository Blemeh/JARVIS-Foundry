from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jarvis_foundry.clock import utc_now
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.evolution.detector import (
    detect_weaknesses,
    discovery_sources,
)
from jarvis_foundry.evolution.executive_portfolio import (
    _improvement_items,
    select_next_work,
)
from jarvis_foundry.executive.portfolio_bridge import materialize_portfolio_selection
from jarvis_foundry.fabric.gap_bridge import (
    BUDGET_EXHAUSTED,
    GAP_OPENED,
    RESUMED,
    SOURCE_ID as GAP_SOURCE_ID,
)
from jarvis_foundry.supervisor.evolution_control import (
    EvolutionControl,
    EvolutionControlError,
    SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID,
    daily_discovery_window,
)
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.supervisor.service import Supervisor


def _database(tmp_path: Path) -> FoundryDatabase:
    tmp_path.mkdir(parents=True, exist_ok=True)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _append_backdated(
    database: FoundryDatabase, event_type: str, payload: dict,
    key: str, recorded_at: str,
) -> None:
    event = Event.create(
        event_type, payload, source_type="test", source_id="v6-intake",
        idempotency_key=key,
    )
    database.append(replace(event, recorded_at=recorded_at))


def _seed_failures(database: FoundryDatabase, window: dict[str, str]) -> None:
    start = datetime.fromisoformat(window["start"])
    for index in range(3):
        run_id = f"intake-failed-{index}"
        at = (start + timedelta(hours=index + 1)).isoformat()
        _append_backdated(
            database, "agent_run.started",
            {"run_id": run_id, "mission_id": "mission-retry-cluster",
             "role": "builder", "provider": "fixture", "model": "local"},
            f"intake-start:{run_id}", at,
        )
        _append_backdated(
            database, "agent_run.failed",
            {"run_id": run_id, "failure_class": "test"},
            f"intake-fail:{run_id}", at,
        )


def _register_resource(database: FoundryDatabase) -> None:
    database.append(Event.create(
        "project.registered",
        {"resource_id": "intake-resource", "kind": "git_worktree",
         "root_path": "/tmp/intake-worktree",
         "resolved_root": "/tmp/intake-worktree",
         "baseline": {"head": "base"},
         "capabilities": {"write_registered_worktree_files": "ACTIVE"},
         "readiness_state": "READY"},
        source_type="test", source_id="v6-intake",
        idempotency_key="intake-resource-registered",
    ))


def test_supervisor_intake_discovers_once_and_preserves_evidence_to_mission(
        tmp_path: Path) -> None:
    database = _database(tmp_path)
    window = daily_discovery_window()
    _seed_failures(database, window)
    _register_resource(database)
    mode = EvolutionControl(
        database, source_id=SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID)
    mode.enable(controller="supervisor")

    supervisor = Supervisor(
        database, tmp_path / "EMERGENCY_STOP", instance_id="v6-intake-owner")
    assert supervisor.start() == "RUNNING"
    assert supervisor._evolution_control is not None
    assert supervisor._evolution_control.mode() == "active"
    assert supervisor.heartbeat() == "RUNNING"

    observed = [
        event for event in database.events()
        if event.event_type == "discovery.observed"
        and event.source_id == SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID
    ]
    assert len(observed) == 1
    assert observed[0].payload["window"] == window
    assert observed[0].payload["recorded_count"] >= 1
    improvements = database.all("improvement_records")
    assert improvements
    improvement = improvements[0]
    refs = json.loads(improvement["evidence_refs_json"])
    provenance = json.loads(improvement["provenance_json"])
    expected_refs = {f"agent_runs/intake-failed-{index}" for index in range(3)}
    assert set(refs) == expected_refs
    assert all(ref.startswith("agent_runs/") for ref in refs)
    assert provenance["detector_rule"]

    assert supervisor.heartbeat() == "RUNNING"
    assert len([
        event for event in database.events()
        if event.event_type == "discovery.observed"
        and event.source_id == SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID
    ]) == 1
    assert len(database.all("improvement_records")) == len(improvements)

    decision = select_next_work(
        database, loop_id="v6-intake-portfolio", resource_id="intake-resource")
    assert decision.kind == "SELECT"
    assert decision.selected_class == "IMPROVEMENT"
    assert set(decision.selected_item["evidence_refs"]) == set(refs)
    materialized = materialize_portfolio_selection(
        database, decision=decision.to_payload(), resource_id="intake-resource")
    assert materialized["materialized"] is True
    mission = next(
        event for event in database.events()
        if event.event_type == "mission.created"
        and event.payload.get("mission_id") == materialized["mission_id"]
    )
    assert mission.payload["portfolio_item_id"] == decision.entity_id
    assert set(mission.payload["evidence_refs"]) == set(refs)
    assert mission.payload["provenance"]["detector_rule"]


def test_observe_and_stop_keep_supervisor_intake_write_free(
        tmp_path: Path) -> None:
    observe_db = _database(tmp_path / "observe")
    _seed_failures(observe_db, daily_discovery_window())
    observe = Supervisor(
        observe_db, tmp_path / "observe" / "STOP", instance_id="v6-observe")
    assert observe.start() == "RUNNING"
    assert observe.heartbeat() == "RUNNING"
    assert observe_db.count("improvement_records") == 0
    assert not [e for e in observe_db.events()
                if e.event_type == "discovery.observed"]

    stop_db = _database(tmp_path / "stopped")
    _seed_failures(stop_db, daily_discovery_window())
    EvolutionControl(
        stop_db, source_id=SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID,
    ).enable(controller="supervisor")
    stop_path = tmp_path / "stopped" / "EMERGENCY_STOP"
    stopped = Supervisor(stop_db, stop_path, instance_id="v6-stopped")
    assert stopped.start() == "RUNNING"
    EmergencyStop(stop_path).activate("V6 intake STOP fixture")
    assert stopped.heartbeat() == "FROZEN"
    assert stop_db.count("improvement_records") == 0
    assert not [e for e in stop_db.events()
                if e.event_type == "discovery.observed"]


def test_supervisor_observes_durable_disable_without_restart(
        tmp_path: Path) -> None:
    database = _database(tmp_path)
    _seed_failures(database, daily_discovery_window())
    enable = EvolutionControl(
        database, source_id=SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID)
    enable.enable(controller="supervisor")
    supervisor = Supervisor(
        database, tmp_path / "EMERGENCY_STOP", instance_id="v6-mode-refresh")
    assert supervisor.start() == "RUNNING"

    external_disable = EvolutionControl(
        database, source_id=SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID)
    external_disable.disable(controller="supervisor")
    assert supervisor.heartbeat() == "RUNNING"
    assert database.count("improvement_records") == 0
    assert not [event for event in database.events()
                if event.event_type == "discovery.observed"]


def test_same_day_late_evidence_is_discovered_and_then_deduped(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = _database(tmp_path)
    clock = [utc_now()]
    monkeypatch.setattr(
        "jarvis_foundry.supervisor.evolution_control.utc_now",
        lambda: clock[0],
    )
    control = EvolutionControl(
        database, source_id=SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID)
    control.enable(controller="supervisor")
    window = daily_discovery_window(clock[0])
    first = control.run_discovery_window(window=window, controller="supervisor")
    assert first["status"] == "RECORDED"
    assert first["observation_count"] == 0
    assert database.count("improvement_records") == 0
    _seed_failures(database, window)

    second = control.run_discovery_window(window=window, controller="supervisor")
    assert second["status"] == "RECORDED"
    assert second["improvement_ids"]
    third = control.run_discovery_window(window=window, controller="supervisor")
    assert third["status"] == "ALREADY_RECORDED"
    assert database.count("improvement_records") == len(second["improvement_ids"])


def test_incremental_evidence_extends_one_improvement_and_reaches_mission(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = _database(tmp_path)
    clock = [utc_now()]
    monkeypatch.setattr(
        "jarvis_foundry.supervisor.evolution_control.utc_now",
        lambda: clock[0],
    )
    window = daily_discovery_window(clock[0])
    _seed_failures(database, window)
    _register_resource(database)
    control = EvolutionControl(
        database, source_id=SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID)
    control.enable(controller="supervisor")

    first = control.run_discovery_window(window=window, controller="supervisor")
    assert len(first["improvement_ids"]) == 1
    improvement_id = first["improvement_ids"][0]
    row = database.row("improvement_records", "improvement_id", improvement_id)
    assert row is not None
    initial_provenance = json.loads(row["provenance_json"])
    assert initial_provenance["detector_rule"] == "mission_retry_count"
    initial_refs = set(json.loads(row["evidence_refs_json"]))
    assert len(initial_refs) == 3

    start = datetime.fromisoformat(window["start"])
    for index in (3, 4):
        run_id = f"intake-failed-{index}"
        at = (start + timedelta(hours=index + 1)).isoformat()
        _append_backdated(
            database, "agent_run.started",
            {"run_id": run_id, "mission_id": "mission-retry-cluster",
             "role": "builder", "provider": "fixture", "model": "local"},
            f"intake-start:{run_id}", at,
        )
        _append_backdated(
            database, "agent_run.failed",
            {"run_id": run_id, "failure_class": "test"},
            f"intake-fail:{run_id}", at,
        )
        extension = control.run_discovery_window(
            window=window, controller="supervisor")
        assert improvement_id in extension["improvement_ids"]

    assert database.count("improvement_records") == 1
    extension_events = [
        event for event in database.events()
        if event.event_type == "improvement.evidence_extended"
        and event.payload.get("improvement_id") == improvement_id
    ]
    assert len(extension_events) == 2
    all_refs = set(extension_events[-1].payload["evidence_refs"])
    assert all_refs == {
        f"agent_runs/intake-failed-{index}" for index in range(5)
    }
    item = next(
        item for item in _improvement_items(database)
        if item.item_id == f"improvement:{improvement_id}"
    )
    assert set(item.evidence_refs) == all_refs

    decision = select_next_work(
        database, loop_id="v6-incremental-portfolio",
        resource_id="intake-resource")
    assert decision.entity_id == f"improvement:{improvement_id}"
    materialized = materialize_portfolio_selection(
        database, decision=decision.to_payload(), resource_id="intake-resource")
    mission = next(
        event for event in database.events()
        if event.event_type == "mission.created"
        and event.payload.get("mission_id") == materialized["mission_id"]
    )
    assert set(mission.payload["evidence_refs"]) == all_refs


def test_terminal_agent_outcome_is_attributed_by_finish_day(
        tmp_path: Path) -> None:
    database = _database(tmp_path)
    window = daily_discovery_window()
    day_start = datetime.fromisoformat(window["start"])
    started = (day_start - timedelta(minutes=1)).isoformat()
    finished = (day_start + timedelta(minutes=1)).isoformat()
    run_id = "cross-midnight-failure"
    _append_backdated(
        database, "agent_run.started",
        {"run_id": run_id, "mission_id": "cross-midnight-mission",
         "role": "builder", "provider": "fixture", "model": "local"},
        f"cross-midnight-start:{run_id}", started,
    )
    _append_backdated(
        database, "agent_run.failed",
        {"run_id": run_id, "failure_class": "test"},
        f"cross-midnight-fail:{run_id}", finished,
    )

    result = detect_weaknesses(
        database, window_start=window["start"], window_end=window["end"])
    repair_sample = next(
        entry for entry in result["suppressed"]
        if entry.get("rule") == "agent_repair_rate"
    )
    assert repair_sample["sample_size"] == 1


def test_discovery_catches_up_one_day_per_heartbeat_after_outage(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = _database(tmp_path)
    clock = [datetime(2026, 9, 27, 12, tzinfo=utc_now().tzinfo)]
    monkeypatch.setattr(
        "jarvis_foundry.supervisor.evolution_control.utc_now",
        lambda: clock[0],
    )
    control = EvolutionControl(
        database, source_id=SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID)
    control.enable(controller="supervisor")
    first_window = control.next_discovery_window()
    assert first_window == daily_discovery_window(clock[0])
    control.run_discovery_window(
        window=first_window, controller="supervisor")

    clock[0] = datetime(2026, 9, 30, 12, tzinfo=utc_now().tzinfo)
    processed = []
    for _ in range(3):
        window = control.next_discovery_window()
        processed.append(window)
        control.run_discovery_window(window=window, controller="supervisor")
    assert processed == [
        {"start": "2026-09-27T00:00:00Z",
         "end": "2026-09-28T00:00:00Z"},
        {"start": "2026-09-28T00:00:00Z",
         "end": "2026-09-29T00:00:00Z"},
        {"start": "2026-09-29T00:00:00Z",
         "end": "2026-09-30T00:00:00Z"},
    ]
    assert control.next_discovery_window() == daily_discovery_window(clock[0])


def test_discovery_failures_advance_backoff_and_reject_forged_windows(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = _database(tmp_path)
    clock = [utc_now()]
    monkeypatch.setattr(
        "jarvis_foundry.supervisor.evolution_control.utc_now",
        lambda: clock[0],
    )
    control = EvolutionControl(
        database, source_id=SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID)
    control.enable(controller="supervisor")
    window = daily_discovery_window(clock[0])
    control.record_discovery_failure(
        window=window, error_class="RuntimeError", controller="supervisor")
    assert control.run_discovery_window(
        window=window, controller="supervisor")["status"] == "RETRY_BACKOFF"

    clock[0] += timedelta(minutes=6)
    control.record_discovery_failure(
        window=window, error_class="RuntimeError", controller="supervisor")
    failures = [e for e in database.events()
                if e.event_type == "evolution.discovery_failed"]
    assert len(failures) == 2
    assert control.run_discovery_window(
        window=window, controller="supervisor")["status"] == "RETRY_BACKOFF"

    forged = {"start": "2026-09-24T00:00:00+00:00",
              "end": "2026-09-25T00:00:00+00:00"}
    with pytest.raises(EvolutionControlError, match="next UTC day"):
        control.run_discovery_window(window=forged, controller="supervisor")
    with pytest.raises(EvolutionControlError, match="next UTC day"):
        control.record_discovery_failure(
            window=forged, error_class="RuntimeError", controller="supervisor")


def test_gap_discovery_requires_canonical_events_and_resolvable_refs(
        tmp_path: Path) -> None:
    database = _database(tmp_path)
    gap_id = "canonical-gap"
    opened = Event.create(
        GAP_OPENED,
        {"gap_id": gap_id, "origin_objective_id": "objective-x",
         "root_objective_id": "objective-x", "plan_id": "plan-x",
         "step_id": "step-x", "required_capability_id": "cap.fixture",
         "requirement_set_id": "requirements-x",
         "requirement_snapshot_id": "snapshot-x", "policy": {},
         "expansion_depth": 0, "created_at": utc_now().isoformat()},
        source_type="supervisor", source_id=GAP_SOURCE_ID,
        idempotency_key=f"{GAP_OPENED}:{gap_id}",
    )
    blocked = Event.create(
        BUDGET_EXHAUSTED,
        {"gap_id": gap_id, "reason": "bounded expansion budget exhausted"},
        source_type="supervisor", source_id=GAP_SOURCE_ID,
        idempotency_key=f"{BUDGET_EXHAUSTED}:{gap_id}",
    )
    database.append(opened)
    database.append(blocked)
    forged_gap = "forged-gap"
    database.append(Event.create(
        GAP_OPENED,
        {"gap_id": forged_gap, "origin_objective_id": "objective-y",
         "root_objective_id": "objective-y", "plan_id": "plan-y",
         "step_id": "step-y", "required_capability_id": "cap.forged",
         "requirement_set_id": "requirements-y",
         "requirement_snapshot_id": "snapshot-y", "policy": {},
         "expansion_depth": 0, "created_at": utc_now().isoformat()},
        source_type="supervisor", source_id="untrusted-gap-source",
        idempotency_key=f"forged:{GAP_OPENED}:{forged_gap}",
    ))
    database.append(Event.create(
        BUDGET_EXHAUSTED, {"gap_id": forged_gap},
        source_type="supervisor", source_id="untrusted-gap-source",
        idempotency_key=f"forged:{BUDGET_EXHAUSTED}:{forged_gap}",
    ))
    database.append(Event.create(
        RESUMED, {"gap_id": gap_id, "effective_state": "ACTIVE"},
        source_type="supervisor", source_id="untrusted-gap-source",
        idempotency_key=f"forged:{RESUMED}:{gap_id}",
    ))

    window = daily_discovery_window()
    gaps = discovery_sources(
        database, window_start=window["start"], window_end=window["end"]
    )["capability_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["gap_id"] == gap_id
    assert gaps[0]["provenance"]["observation_scope"] == \
        "current_terminal_gap_state"
    refs = set(gaps[0]["evidence_refs"])
    assert refs == {f"events/{opened.event_id}", f"events/{blocked.event_id}"}
    ledger_refs = {f"events/{event.event_id}" for event in database.events()}
    assert refs <= ledger_refs
