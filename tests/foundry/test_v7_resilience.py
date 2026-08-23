from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import effect_state
from jarvis_foundry.events import Event
from jarvis_foundry.resilience import (
    AnomalyDetector,
    DependencyGraph,
    EffectAmbiguity,
    HealthSignal,
    IncidentLifecycleError,
    IncidentStatus,
    IncidentStore,
    RepairResult,
    ResilienceCoordinator,
    rebuild_incidents,
)


def database(tmp_path: Path) -> FoundryDatabase:
    result = FoundryDatabase(tmp_path / "foundry.db")
    result.initialize()
    return result


def register_resource(db: FoundryDatabase, *, resource_id: str = "resource-1") -> None:
    db.append(Event.create(
        "project.registered",
        {
            "resource_id": resource_id,
            "kind": "git_worktree",
            "root_path": "/tmp/worktree",
            "resolved_root": "/tmp/worktree",
            "baseline": {"head": "base"},
            "capabilities": {"safe-work": "ACTIVE", "other-work": "READY"},
            "readiness_state": "READY",
        },
        source_type="test", source_id="resilience-tests",
        idempotency_key=f"project.registered:{resource_id}",
    ))


def test_incident_lifecycle_is_durable_and_scoped(tmp_path: Path) -> None:
    db = database(tmp_path)
    register_resource(db)
    graph = DependencyGraph()
    graph.bind("resource-1", "safe-work", connector_id="connector-1", mission_ids=("mission-1",))
    coordinator = ResilienceCoordinator(
        db,
        graph=graph,
        repair_handlers={
            "worker_crash_loop": lambda incident, report: RepairResult(
                True, "owned worker reclaimed", {"reclaimed": True}
            )
        },
        health_probe=lambda incident, report: True,
    )

    run = coordinator.run_once(
        now="2026-08-23T10:00:00Z",
        metrics={
            "worker_failures": 3,
            "worker_id": "worker-1",
            "resource_id": "resource-1",
            "capability": "safe-work",
            "mission_id": "mission-1",
        },
    )

    assert len(run.anomalies) == 1
    assert run.recovered_ids
    incident = run.incidents[0]
    assert incident.status == IncidentStatus.RECOVERED.value
    assert incident.repair_attempts == 1
    assert any(action.get("kind") == "capability_suspended" for action in incident.actions)
    assert any(action.get("kind") == "capability_resume" for action in incident.actions)
    capabilities = json.loads(str(db.row("resources", "resource_id", "resource-1")["capabilities_json"]))
    assert capabilities["safe-work"] == "ACTIVE"
    assert capabilities["other-work"] == "READY"

    before = rebuild_incidents(db)
    db.rebuild_projections()
    assert rebuild_incidents(db) == before


def test_repeated_anomaly_scan_is_idempotent(tmp_path: Path) -> None:
    db = database(tmp_path)
    coordinator = ResilienceCoordinator(db)
    kwargs = {
        "now": "2026-08-23T10:00:00Z",
        "metrics": {"database_errors": 1},
        "auto_repair": False,
    }
    first = coordinator.run_once(**kwargs)
    second = coordinator.run_once(**kwargs)
    assert first.anomalies[0].anomaly_id == second.anomalies[0].anomaly_id
    assert db.event_count(event_type="incident.detected") == 1
    assert db.event_count(event_type="incident.signal_observed") == 0
    assert len(rebuild_incidents(db)) == 1


def test_invalid_transition_and_unknown_effect_fail_closed(tmp_path: Path) -> None:
    db = database(tmp_path)
    store = IncidentStore(db)
    incident = store.detect(HealthSignal("signal-1", "provider_outage", correlation_key="p:1"))
    with pytest.raises(IncidentLifecycleError):
        store.transition(incident.incident_id, IncidentStatus.RECOVERED)
    store.transition(incident.incident_id, IncidentStatus.TRIAGED)
    store.transition(incident.incident_id, IncidentStatus.CONTAINING)
    store.transition(incident.incident_id, IncidentStatus.CONTAINED)
    store.transition(incident.incident_id, IncidentStatus.DIAGNOSING)
    blocked = store.transition(incident.incident_id, IncidentStatus.UNKNOWN,
                               reason="provider-side effect is unknown",
                               effect_ambiguity=EffectAmbiguity.UNKNOWN)
    assert blocked.status == IncidentStatus.UNKNOWN.value
    assert blocked.effect_ambiguity == EffectAmbiguity.UNKNOWN.value
    with pytest.raises(IncidentLifecycleError):
        store.transition(incident.incident_id, IncidentStatus.RECOVERED)


def test_effect_ambiguity_freezes_and_never_retries(tmp_path: Path) -> None:
    db = database(tmp_path)
    db.append(Event.create(
        "system.action_started",
        {"action_key": "effect-1", "system_id": "fake", "target_ref": "target",
         "payload_hash": "hash", "kind": "write"},
        source_type="test", source_id="effect-test",
        idempotency_key="system.action_started:effect-1",
    ))
    coordinator = ResilienceCoordinator(db)
    run = coordinator.run_once(now="2026-08-23T10:00:00Z")
    assert run.incidents[0].status == IncidentStatus.UNKNOWN.value
    assert effect_state(db, "effect-1")["status"] == "FROZEN"
    assert db.event_count(event_type="system.action_started") == 1
    assert db.event_count(event_type="effect.frozen") == 1


def test_stop_is_stronger_than_recovery(tmp_path: Path) -> None:
    db = database(tmp_path)
    register_resource(db)
    stop_path = tmp_path / "EMERGENCY_STOP"
    stop_path.write_text("stop", encoding="utf-8")
    graph = DependencyGraph()
    graph.bind("resource-1", "safe-work")
    coordinator = ResilienceCoordinator(
        db,
        stop_path=stop_path,
        graph=graph,
        repair_handlers={
            "worker_crash_loop": lambda incident, report: RepairResult(True, "restarted")
        },
        health_probe=lambda incident, report: True,
    )
    run = coordinator.run_once(metrics={
        "worker_failures": 3, "worker_id": "worker-1",
        "resource_id": "resource-1", "capability": "safe-work",
    })
    assert run.incidents[0].status == IncidentStatus.BLOCKED.value
    capabilities = json.loads(str(db.row("resources", "resource_id", "resource-1")["capabilities_json"]))
    assert capabilities["safe-work"] == "STOPPED"


def test_dependency_graph_contains_shared_dependency_only(tmp_path: Path) -> None:
    graph = DependencyGraph()
    graph.bind("resource-a", "cap-a", connector_id="shared")
    graph.bind("resource-b", "cap-b", connector_id="other")
    impacted = graph.impacted("shared")
    assert [(item.resource_id, item.capability) for item in impacted] == [("resource-a", "cap-a")]


def test_deterministic_resource_and_cost_rules(tmp_path: Path) -> None:
    db = database(tmp_path)
    detector = AnomalyDetector()
    anomalies = detector.detect(
        db,
        now="2026-08-23T10:00:00Z",
        metrics={"task_count": 101, "cost_usd": 26.0},
    )
    assert [item.kind for item in anomalies] == ["cost_anomaly", "resource_anomaly"]
    assert all(item.evidence for item in anomalies)

