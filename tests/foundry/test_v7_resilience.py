from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.deployment.layout import current_pointer
from jarvis_foundry.deployment.release import ReleaseError, rollback_current, set_current
from jarvis_foundry.delivery import (
    DeliveryBoundary, DeliveryRequest, DeliveryStatus, FakeDeliveryProvider,
)
from jarvis_foundry.effects import effect_state
from jarvis_foundry.events import Event
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.resilience import (
    AnomalyDetector,
    AnomalyThresholds,
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


def fake_release(root: Path, sha: str, *, schema: int = SCHEMA_VERSION,
                 previous: str | None = None) -> Path:
    directory = root / "releases" / sha
    python_dir = directory / ".venv" / "Scripts"
    python_dir.mkdir(parents=True)
    (python_dir / "python.exe").write_text("fake", encoding="utf-8")
    manifest: dict[str, object] = {
        "release_id": f"rel-{sha[:8]}",
        "source_sha": sha,
        "git_commit": sha,
        "git_tree": "1" * 40,
        "python_version": "3.13.0",
        "package_version": "0.1.0",
        "schema_compatibility": {
            "package_schema": schema, "opens_min": schema, "opens_max": schema,
        },
        "provenance": {
            "builder": "v7-resilience-tests",
            "source_repo": str(root / "source"),
            "git_commit": sha,
            "git_tree": "1" * 40,
        },
        "previous_runtime": None,
        "created_at": "2026-08-23T00:00:00Z",
    }
    if previous is not None:
        previous_manifest = json.loads(
            (root / "releases" / previous / "RELEASE.json").read_text(encoding="utf-8")
        )
        manifest["previous_runtime"] = {
            "source_sha": previous,
            "release_id": previous_manifest["release_id"],
            "git_commit": previous_manifest["git_commit"],
            "git_tree": previous_manifest["git_tree"],
            "schema": previous_manifest["schema_compatibility"]["package_schema"],
        }
    (directory / "RELEASE.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return directory


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


def test_provider_network_continuity_preserves_safe_delivery_and_false_success_guard(
    tmp_path: Path,
) -> None:
    db = database(tmp_path)

    def request(provider: str, key: str) -> DeliveryRequest:
        return DeliveryRequest(
            request_id=f"request-{key}", mission_id="mission-1", action_id=f"action-{key}",
            channel="PROVIDER", provider=provider, target="local-sink",
            payload="safe deterministic payload", idempotency_key=f"delivery-{key}",
        )

    unavailable = DeliveryBoundary(db, {})
    blocked = unavailable.dispatch(request("offline", "blocked"))
    assert blocked["status"] == DeliveryStatus.BLOCKED.value
    assert blocked["error_class"] == "ProviderUnavailable"

    timeout_provider = FakeDeliveryProvider(
        provider_id="timed", scripted=[TimeoutError("network timeout")]
    )
    timed = DeliveryBoundary(db, {"timed": timeout_provider})
    timed_out = timed.dispatch(request("timed", "timeout"))
    assert timed_out["status"] == DeliveryStatus.TIMED_OUT.value
    assert timed_out["provider_request_id"] is None

    healthy_provider = FakeDeliveryProvider(provider_id="healthy")
    partial = DeliveryBoundary(db, {"healthy": healthy_provider})
    safe = partial.dispatch(request("healthy", "safe"))
    assert safe["status"] == DeliveryStatus.DELIVERED.value
    assert len(healthy_provider.calls) == 1

    anomalies = AnomalyDetector().detect(
        db, metrics={"provider_failures": 3, "provider": "offline"}
    )
    assert any(item.kind == "provider_outage" for item in anomalies)
    assert not any(event.event_type == "delivery.outcome"
                   and event.payload.get("status") == "DELIVERED"
                   and event.payload.get("provider_requested") == "offline"
                   for event in db.events())
    db.append(Event.create(
        "worker.fallback_triggered",
        {"run_id": "false-success", "trigger": "claimed_success_contradicted_by_evidence"},
        source_type="worker", source_id="worker-1",
        idempotency_key="worker.fallback_triggered:false-success",
    ))
    worker_anomalies = AnomalyDetector(
        AnomalyThresholds(worker_failures=1)
    ).detect(db)
    assert any(item.kind == "worker_crash_loop" for item in worker_anomalies)


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


def test_reconciled_effect_can_continue_only_through_explicit_seam(tmp_path: Path) -> None:
    db = database(tmp_path)
    db.append(Event.create(
        "system.action_started",
        {"action_key": "effect-reconcile", "system_id": "fake", "target_ref": "target",
         "payload_hash": "hash", "kind": "write"},
        source_type="test", source_id="effect-test",
        idempotency_key="system.action_started:effect-reconcile",
    ))
    calls: list[str] = []
    coordinator = ResilienceCoordinator(
        db,
        repair_handlers={
            "effect_unknown": lambda incident, report: (
                calls.append(incident.incident_id),
                RepairResult(True, "reconciled continuation", {"matched": True}),
            )[1]
        },
        health_probe=lambda incident, report: True,
    )
    initial = coordinator.run_once()
    incident = initial.incidents[0]
    assert incident.status == IncidentStatus.UNKNOWN.value
    assert incident.effect_action_key == "effect-reconcile"
    with pytest.raises(IncidentLifecycleError, match="does not match"):
        coordinator.reconcile_incident_effect(
            incident.incident_id,
            classification=EffectAmbiguity.RECONCILED,
            evidence={"provider": "wrong-key"},
            effect_action_key="different-effect",
        )
    reconciled = coordinator.reconcile_incident_effect(
        incident.incident_id,
        classification=EffectAmbiguity.RECONCILED,
        evidence={"provider": "fake-oracle", "matched": True},
        effect_action_key="effect-reconcile",
    )
    assert reconciled.status == IncidentStatus.RECOVERED.value
    assert calls == [incident.incident_id]
    assert effect_state(db, "effect-reconcile")["status"] == "FROZEN"
    assert coordinator.reconcile_incident_effect(
        incident.incident_id,
        classification=EffectAmbiguity.RECONCILED,
        evidence={"provider": "fake-oracle", "matched": True},
        effect_action_key="effect-reconcile",
    ).status == IncidentStatus.RECOVERED.value
    assert calls == [incident.incident_id]


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


def test_configured_release_diagnostics_validate_manifest_before_repair(
    tmp_path: Path,
) -> None:
    current = "c" * 40
    valid_root = tmp_path / "valid-release"
    fake_release(valid_root, current)
    set_current(current, root=valid_root)
    valid_home = tmp_path / "valid-home"
    valid_db = FoundryDatabase(valid_home / "foundry.db")
    valid_db.initialize()
    valid_run = ResilienceCoordinator(
        valid_db,
        release_root=valid_root,
        foundry_home=valid_home,
    ).run_once(metrics={"database_errors": 1}, auto_repair=False)
    valid_report = next(
        action["report"] for action in valid_run.incidents[0].actions
        if action.get("kind") == "diagnostics"
    )
    valid_release = next(
        result for result in valid_report["results"] if result["name"] == "release"
    )
    assert valid_release["status"] == "PASS"
    assert valid_release["evidence"]["manifest_checks"][0]["status"] == "PASS"

    invalid_root = tmp_path / "invalid-release"
    invalid_root.mkdir()
    current_pointer(invalid_root).write_text("missing", encoding="utf-8")
    invalid_home = tmp_path / "invalid-home"
    invalid_db = FoundryDatabase(invalid_home / "foundry.db")
    invalid_db.initialize()
    calls: list[str] = []
    invalid_run = ResilienceCoordinator(
        invalid_db,
        release_root=invalid_root,
        foundry_home=invalid_home,
        repair_handlers={
            "database_fault": lambda incident, report: calls.append(incident.incident_id),
        },
    ).run_once(metrics={"database_errors": 1})
    invalid_report = next(
        action["report"] for action in invalid_run.incidents[0].actions
        if action.get("kind") == "diagnostics"
    )
    invalid_release = next(
        result for result in invalid_report["results"] if result["name"] == "release"
    )
    assert invalid_release["status"] == "FAIL"
    assert invalid_run.incidents[0].status == IncidentStatus.BLOCKED.value
    assert calls == []


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
    assert matrix["summary"] == {"fail": 0, "pass": 20, "total": 20}
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


def test_release_rollback_failure_modes_fail_closed(tmp_path: Path) -> None:
    def run_release_case(
        case_root: Path, *, previous_schema: int = SCHEMA_VERSION,
        has_previous: bool = True, healthy: bool = True,
    ) -> tuple[str, str | None, tuple[dict[str, object], ...]]:
        root = case_root / "releases"
        root.mkdir(parents=True)
        current = "b" * 40
        previous = "a" * 40
        if has_previous:
            fake_release(root, previous, schema=SCHEMA_VERSION)
        fake_release(root, current, previous=previous if has_previous else None)
        set_current(previous if has_previous else current, root=root)
        if has_previous:
            set_current(current, root=root)
            if previous_schema != SCHEMA_VERSION:
                previous_path = root / "releases" / previous / "RELEASE.json"
                previous_manifest = json.loads(previous_path.read_text(encoding="utf-8"))
                previous_manifest["schema_compatibility"]["package_schema"] = previous_schema
                previous_path.write_text(json.dumps(previous_manifest), encoding="utf-8")
        home = case_root / "foundry"
        FoundryDatabase(home / "foundry.db").initialize()

        def rollback(incident, report):
            try:
                result = rollback_current(root=root, foundry_home=home)
                return RepairResult(True, "rollback completed", result)
            except ReleaseError as error:
                return RepairResult(False, "rollback refused", {"error": str(error)})

        coordinator = ResilienceCoordinator(
            FoundryDatabase(home / "foundry.db"),
            max_repair_attempts=1,
            repair_handlers={"release_degradation": rollback},
            health_probe=lambda incident, report: healthy,
        )
        run = coordinator.run_once(metrics={"release_degraded": True, "release_id": current})
        return (
            run.incidents[0].status,
            current_pointer(root).read_text(encoding="utf-8").strip(),
            tuple(dict(action) for action in run.incidents[0].actions),
        )

    missing_status, missing_current, missing_actions = run_release_case(
        tmp_path / "missing-previous", has_previous=False
    )
    assert missing_status == IncidentStatus.ESCALATED.value
    assert missing_current == "b" * 40
    assert any(action.get("kind") == "repair_failed" for action in missing_actions)
    assert any("no PREVIOUS" in str(action) for action in missing_actions)

    schema_status, schema_current, schema_actions = run_release_case(
        tmp_path / "schema-mismatch", previous_schema=SCHEMA_VERSION - 1
    )
    assert schema_status == IncidentStatus.ESCALATED.value
    assert schema_current == "b" * 40
    assert any(action.get("kind") == "repair_failed" for action in schema_actions)
    assert any("schema" in str(action).lower() for action in schema_actions)

    unhealthy_status, unhealthy_current, _ = run_release_case(
        tmp_path / "health-not-restored", healthy=False
    )
    assert unhealthy_status == IncidentStatus.ESCALATED.value
    assert unhealthy_current == "a" * 40
