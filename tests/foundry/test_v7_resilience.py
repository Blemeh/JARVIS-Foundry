from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.effects import effect_state
from jarvis_foundry.events import Event
from jarvis_foundry.resilience import (
    AnomalyDetector,
    DependencyGraph,
    DiagnosticReport,
    DiagnosticResult,
    EffectAmbiguity,
    HealthSignal,
    IncidentLifecycleError,
    IncidentStatus,
    IncidentStore,
    RepairResult,
    ResilienceCoordinator,
    rebuild_incidents,
)
from jarvis_foundry.supervisor.service import Supervisor


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
    repair_calls: list[str] = []

    def repair(incident, report):
        repair_calls.append(incident.incident_id)
        return RepairResult(True, "restarted")

    coordinator = ResilienceCoordinator(
        db,
        stop_path=stop_path,
        graph=graph,
        repair_handlers={"worker_crash_loop": repair},
        health_probe=lambda incident, report: True,
    )
    run = coordinator.run_once(metrics={
        "worker_failures": 3, "worker_id": "worker-1",
        "resource_id": "resource-1", "capability": "safe-work",
    })
    assert run.incidents[0].status == IncidentStatus.BLOCKED.value
    assert repair_calls == []
    capabilities = json.loads(str(db.row("resources", "resource_id", "resource-1")["capabilities_json"]))
    assert capabilities["safe-work"] == "STOPPED"


def test_failed_diagnostics_block_repair_callback(tmp_path: Path) -> None:
    db = database(tmp_path)
    calls: list[str] = []

    class FailingPlaybook:
        def run(self, database, incident, **kwargs):
            from jarvis_foundry.resilience import DiagnosticReport, DiagnosticResult
            return DiagnosticReport(
                incident.incident_id,
                (DiagnosticResult("database", "FAIL", {"reason": "locked"}),),
                "2026-08-23T10:00:00Z",
            )

    coordinator = ResilienceCoordinator(
        db,
        playbook=FailingPlaybook(),
        repair_handlers={
            "database_fault": lambda incident, report: calls.append(incident.incident_id),
        },
    )
    run = coordinator.run_once(
        now="2026-08-23T10:00:00Z", metrics={"database_errors": 1}
    )
    assert calls == []
    assert run.incidents[0].status == IncidentStatus.BLOCKED.value


@pytest.mark.parametrize("protected_state", ["VETOED", "STOPPED"])
def test_preexisting_protected_capability_is_never_resumed(
    tmp_path: Path, protected_state: str,
) -> None:
    db = database(tmp_path)
    register_resource(db)
    set_capability_state(db, "resource-1", "safe-work", protected_state,
                         source_id="owner", evidence={"owner": True})
    graph = DependencyGraph()
    graph.bind("resource-1", "safe-work")
    coordinator = ResilienceCoordinator(
        db,
        graph=graph,
        repair_handlers={"worker_crash_loop": lambda incident, report: RepairResult(True, "ok")},
        health_probe=lambda incident, report: True,
    )
    run = coordinator.run_once(metrics={
        "worker_failures": 3, "resource_id": "resource-1", "capability": "safe-work",
    })
    assert run.incidents[0].status == IncidentStatus.RECOVERED.value
    capabilities = json.loads(str(db.row("resources", "resource_id", "resource-1")["capabilities_json"]))
    assert capabilities["safe-work"] == protected_state


def test_effect_reconciliation_requires_proof_and_mismatch_blocks_repair(tmp_path: Path) -> None:
    db = database(tmp_path)
    store = IncidentStore(db)
    incident = store.detect(HealthSignal(
        "effect-signal", "effect_unknown", correlation_key="effect:e-1",
        effect_action_key="e-1", effect_ambiguity=EffectAmbiguity.UNKNOWN,
    ))
    with pytest.raises(IncidentLifecycleError):
        store.reconcile_effect(incident.incident_id,
                               classification=EffectAmbiguity.NONE, evidence={})
    store.transition(incident.incident_id, IncidentStatus.TRIAGED)
    store.transition(incident.incident_id, IncidentStatus.CONTAINING)
    store.transition(incident.incident_id, IncidentStatus.CONTAINED)
    store.transition(incident.incident_id, IncidentStatus.DIAGNOSING)
    store.reconcile_effect(incident.incident_id,
                           classification=EffectAmbiguity.MISMATCH,
                           evidence={"provider": "oracle", "matched": False})
    incident = store.get(incident.incident_id)
    assert incident is not None
    coordinator = ResilienceCoordinator(
        db,
        repair_handlers={"effect_unknown": lambda incident, report: pytest.fail("repair called")},
    )
    result = coordinator._repair(
        incident,
        AnomalyDetector().detect(db, metrics={"unknown_effects": 1})[0],
        DiagnosticReport(incident.incident_id, (DiagnosticResult("database", "PASS"),),
                         "2026-08-23T10:00:00Z"),
    )
    assert result.status == IncidentStatus.UNKNOWN.value


def test_health_truth_exposes_capability_failure(tmp_path: Path) -> None:
    from jarvis_foundry.resilience import health_truth

    db = database(tmp_path)
    register_resource(db)
    set_capability_state(db, "resource-1", "safe-work", "DEGRADED",
                         source_id="test", evidence={"reason": "fault"})
    truth = health_truth(db)
    capabilities = truth["components"]["capabilities"]
    assert capabilities["status"] == "DEGRADED"
    assert capabilities["resources"]["resource-1"]["capabilities"]["safe-work"] == {
        "state": "DEGRADED", "status": "DEGRADED"
    }


def test_supervisor_heartbeat_invokes_resilience_without_widening_scope(
    tmp_path: Path,
) -> None:
    db = database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    calls: list[dict[str, object]] = []

    class FakeCoordinator:
        def run_once(self, *, metrics=None):
            calls.append(dict(metrics or {}))
            return {"status": "ok"}

    supervisor = Supervisor(
        db, stop_path, instance_id="resilience-hook", resilience=FakeCoordinator()
    )
    assert supervisor.start() == "RUNNING"
    assert supervisor.heartbeat(resilience_metrics={"worker_failures": 0}) == "RUNNING"
    assert calls == [{"worker_failures": 0}]
    assert not any(
        event.event_type == "supervisor.degraded"
        and event.payload.get("reason") == "resilience_tick_failure"
        for event in db.events()
    )
    supervisor.stop()


def test_supervisor_resilience_failure_is_bounded_and_recorded(tmp_path: Path) -> None:
    db = database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"

    class BrokenCoordinator:
        def run_once(self, *, metrics=None):
            raise RuntimeError("disposable coordinator failure")

    supervisor = Supervisor(
        db, stop_path, instance_id="broken-resilience-hook", resilience=BrokenCoordinator()
    )
    assert supervisor.start() == "RUNNING"
    assert supervisor.heartbeat() == "RUNNING"
    degraded = [event for event in db.events() if event.event_type == "supervisor.degraded"]
    assert len(degraded) == 1
    assert degraded[0].payload["reason"] == "resilience_tick_failure"
    supervisor.stop()


def test_disposable_fault_matrix_emits_all_pass_rows(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "v7_resilience_fault_matrix.py"
    output = tmp_path / "fault-matrix.json"
    result = subprocess.run(
        [sys.executable, str(script), "--output", str(output)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    matrix = json.loads(output.read_text(encoding="utf-8"))
    assert matrix["scope"] == "disposable homes only"
    assert matrix["summary"] == {"fail": 0, "pass": 10, "total": 10}
    assert matrix["verdict"] == "PASS"
    assert all(row["evidence"] for row in matrix["rows"])


def test_resilience_run_output_contract_is_structured(tmp_path: Path) -> None:
    db = database(tmp_path)
    run = ResilienceCoordinator(db).run_once(
        now="2026-08-23T10:00:00Z", metrics={"database_errors": 1}
    )
    payload = run.to_payload()
    assert set(payload) == {"observed_at", "anomalies", "incidents", "recovered_ids", "blocked_ids", "errors"}
    assert payload["incidents"][0]["status"] == IncidentStatus.BLOCKED.value
    json.dumps(payload)


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


def test_release_degradation_freezes_before_rollback_callback(tmp_path: Path) -> None:
    db = database(tmp_path)
    callback_observations: list[bool] = []

    def rollback(incident, report):
        from jarvis_foundry.evolution.promote import is_frozen
        callback_observations.append(is_frozen(db))
        return RepairResult(True, "rollback rehearsal complete", {"rolled_back": True})

    coordinator = ResilienceCoordinator(
        db,
        repair_handlers={"release_degradation": rollback},
        health_probe=lambda incident, report: True,
    )
    run = coordinator.run_once(metrics={"release_degraded": True, "release_id": "candidate-1"})
    assert run.incidents[0].status == IncidentStatus.RECOVERED.value
    assert callback_observations == [True]
