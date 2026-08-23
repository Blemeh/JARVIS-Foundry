"""Run the V7 resilience fault matrix in disposable homes only.

The harness exercises the source coordinator and existing control primitives
without reading or mutating a configured production home. Each row records the
expected outcome, observed state, evidence, containment, recovery proof, and a
machine-readable verdict. Pass ``--output`` to persist the result as a lane
evidence artifact.
"""

from __future__ import annotations

import argparse
import gc
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.layout import current_pointer
from jarvis_foundry.deployment.release import ReleaseError, rollback_current, set_current
from jarvis_foundry.delivery import (
    DeliveryBoundary,
    DeliveryOutcome,
    DeliveryRequest,
    DeliveryStatus,
    FakeDeliveryProvider,
)
from jarvis_foundry.effects import effect_state
from jarvis_foundry.events import Event
from jarvis_foundry.evolution.promote import is_frozen
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.resilience import (
    AnomalyDetector,
    AnomalyThresholds,
    DependencyGraph,
    DiagnosticReport,
    DiagnosticResult,
    EffectAmbiguity,
    IncidentStatus,
    RepairResult,
    ResilienceCoordinator,
    rebuild_incidents,
)
from jarvis_foundry.supervisor.leases import LeaseUnavailable
from jarvis_foundry.supervisor.service import Supervisor


def _database(root: Path) -> FoundryDatabase:
    database = FoundryDatabase(root / "foundry.db")
    database.initialize()
    return database


def _register_resource(database: FoundryDatabase) -> None:
    database.append(Event.create(
        "project.registered",
        {
            "resource_id": "resource-1",
            "kind": "git_worktree",
            "root_path": str(database.path.parent),
            "resolved_root": str(database.path.parent),
            "baseline": {"head": "disposable"},
            "capabilities": {"safe-work": "ACTIVE", "unrelated": "READY"},
            "readiness_state": "READY",
        },
        source_type="fault-matrix", source_id="v7-matrix",
        idempotency_key="project.registered:resource-1",
    ))


def _fake_release(
    root: Path,
    sha: str,
    *,
    schema: int = SCHEMA_VERSION,
    previous: str | None = None,
) -> Path:
    """Create the smallest release manifest accepted by rollback validation."""
    directory = root / "releases" / sha
    python_dir = directory / ".venv" / "Scripts"
    python_dir.mkdir(parents=True)
    (python_dir / "python.exe").write_text("disposable", encoding="utf-8")
    manifest: dict[str, Any] = {
        "release_id": f"rel-{sha[:8]}",
        "source_sha": sha,
        "git_commit": sha,
        "git_tree": "1" * 40,
        "python_version": "3.13.0",
        "package_version": "0.1.0",
        "schema_compatibility": {
            "package_schema": schema,
            "opens_min": schema,
            "opens_max": schema,
        },
        "provenance": {
            "builder": "v7-resilience-fault-matrix",
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
    (directory / "RELEASE.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def _delivery_request(provider: str, key: str) -> DeliveryRequest:
    return DeliveryRequest(
        request_id=f"request-{key}",
        mission_id="mission-disposable",
        action_id=f"action-{key}",
        channel="PROVIDER",
        provider=provider,
        target="local-sink",
        payload="deterministic disposable payload",
        idempotency_key=f"delivery:{key}",
    )


def _row(
    case_id: str,
    fault: str,
    expected: str,
    actual: str,
    evidence: dict[str, Any],
    containment: str,
    recovery: str,
    verdict: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "fault": fault,
        "expected": expected,
        "actual": actual,
        "evidence": evidence,
        "containment": containment,
        "recovery": recovery,
        "verdict": verdict,
    }


def _case_worker_crash(root: Path) -> dict[str, Any]:
    database = _database(root)
    _register_resource(database)
    graph = DependencyGraph()
    graph.bind("resource-1", "safe-work")
    coordinator = ResilienceCoordinator(
        database,
        graph=graph,
        repair_handlers={
            "worker_crash_loop": lambda incident, report: RepairResult(
                True, "worker reclaimed", {"reclaimed": True}
            )
        },
        health_probe=lambda incident, report: True,
    )
    run = coordinator.run_once(metrics={
        "worker_failures": 3,
        "resource_id": "resource-1",
        "capability": "safe-work",
    })
    state = json.loads(str(database.row("resources", "resource_id", "resource-1")["capabilities_json"]))
    actual = run.incidents[0].status
    passed = actual == IncidentStatus.RECOVERED.value and state["safe-work"] == "ACTIVE"
    return _row(
        "V7C-01-worker-crash-loop", "worker crash loop", "RECOVERED + scoped resume",
        actual,
        {"event_count": database.event_count(), "capability_state": state},
        "safe-work STOPPED; unrelated capability retained",
        "bounded reclaim callback and observed health restored ACTIVE",
        "PASS" if passed else "FAIL",
    )


def _case_stop(root: Path) -> dict[str, Any]:
    database = _database(root)
    _register_resource(database)
    stop_path = root / "EMERGENCY_STOP"
    stop_path.write_text("disposable STOP", encoding="utf-8")
    graph = DependencyGraph()
    graph.bind("resource-1", "safe-work")
    calls: list[str] = []

    def repair(incident, report):
        calls.append(incident.incident_id)
        return RepairResult(True, "must not run")

    coordinator = ResilienceCoordinator(
        database, stop_path=stop_path, graph=graph,
        repair_handlers={"worker_crash_loop": repair}, health_probe=lambda i, r: True,
    )
    run = coordinator.run_once(metrics={
        "worker_failures": 3, "resource_id": "resource-1", "capability": "safe-work",
    })
    state = json.loads(str(database.row("resources", "resource_id", "resource-1")["capabilities_json"]))
    actual = run.incidents[0].status
    passed = actual == IncidentStatus.BLOCKED.value and not calls and state["safe-work"] == "STOPPED"
    return _row(
        "V7C-02-stop-during-repair", "emergency STOP during repair window", "BLOCKED; callback not invoked",
        actual, {"repair_calls": len(calls), "capability_state": state},
        "capability frozen and STOP retained", "no automatic recovery under STOP",
        "PASS" if passed else "FAIL",
    )


def _case_diagnostic_gate(root: Path) -> dict[str, Any]:
    database = _database(root)
    calls: list[str] = []

    class FailingPlaybook:
        def run(self, database, incident, **kwargs):
            return DiagnosticReport(
                incident.incident_id,
                (DiagnosticResult("database", "FAIL", {"reason": "locked"}),),
                "2026-08-23T10:00:00Z",
            )

    coordinator = ResilienceCoordinator(
        database, playbook=FailingPlaybook(),
        repair_handlers={"database_fault": lambda i, r: calls.append(i.incident_id)},
    )
    run = coordinator.run_once(metrics={"database_errors": 1})
    actual = run.incidents[0].status
    passed = actual == IncidentStatus.BLOCKED.value and not calls
    return _row(
        "V7C-03-diagnostic-gate", "diagnostic failure", "BLOCKED; no repair mutation",
        actual, {"repair_calls": len(calls)}, "no repair until read-only diagnostics pass",
        "owner review required", "PASS" if passed else "FAIL",
    )


def _case_provider(root: Path) -> dict[str, Any]:
    database = _database(root)
    coordinator = ResilienceCoordinator(database)
    run = coordinator.run_once(metrics={"provider_failures": 3, "provider": "offline"})
    actual = run.incidents[0].status
    passed = actual == IncidentStatus.BLOCKED.value
    return _row(
        "V7C-04-provider-outage", "provider unavailable", "BLOCKED without provider substitution",
        actual, {"anomaly_kinds": [item.kind for item in run.anomalies]},
        "provider capability incident isolated", "resume requires fresh provider health",
        "PASS" if passed else "FAIL",
    )


def _case_provider_continuity(root: Path) -> dict[str, Any]:
    database = _database(root)
    unavailable = DeliveryBoundary(database, {})
    blocked = unavailable.dispatch(_delivery_request("offline", "unavailable"))

    timeout_provider = FakeDeliveryProvider(
        provider_id="timeout", scripted=[TimeoutError("disposable network timeout")]
    )
    timed = DeliveryBoundary(database, {"timeout": timeout_provider})
    timed_out = timed.dispatch(_delivery_request("timeout", "timeout"))

    partial_provider = FakeDeliveryProvider(
        provider_id="partial",
        scripted=[DeliveryOutcome(
            DeliveryStatus.FAILED,
            error_class="NetworkError",
            reason="disposable partial outage",
        )],
    )
    partial = DeliveryBoundary(database, {"partial": partial_provider})
    failed = partial.dispatch(_delivery_request("partial", "partial"))

    healthy_provider = FakeDeliveryProvider(provider_id="healthy")
    healthy = DeliveryBoundary(database, {"healthy": healthy_provider})
    delivered = healthy.dispatch(_delivery_request("healthy", "safe"))

    all_unavailable = DeliveryBoundary(database, {})
    all_blocked = [
        all_unavailable.dispatch(_delivery_request("offline-a", "all-a")),
        all_unavailable.dispatch(_delivery_request("offline-b", "all-b")),
    ]
    coordinator = ResilienceCoordinator(database)
    run = coordinator.run_once(auto_repair=True)
    provider_incidents = [
        item for item in run.incidents
        if any(anomaly.kind == "provider_outage" for anomaly in run.anomalies)
    ]
    statuses = [
        blocked["status"], timed_out["status"], failed["status"],
        delivered["status"], *(item["status"] for item in all_blocked),
    ]
    provider_anomaly = any(item.kind == "provider_outage" for item in run.anomalies)
    passed = (
        statuses[:3] == [
            DeliveryStatus.BLOCKED.value,
            DeliveryStatus.TIMED_OUT.value,
            DeliveryStatus.FAILED.value,
        ]
        and delivered["status"] == DeliveryStatus.DELIVERED.value
        and all(item["status"] == DeliveryStatus.BLOCKED.value for item in all_blocked)
        and provider_anomaly
        and provider_incidents
        and provider_incidents[0].status == IncidentStatus.BLOCKED.value
        and not any(
            event.event_type == "delivery.outcome"
            and event.payload.get("provider_requested") == "offline"
            and event.payload.get("status") == DeliveryStatus.DELIVERED.value
            for event in database.events()
        )
    )
    return _row(
        "V7C-09-provider-network-continuity",
        "provider unavailable, timeout, partial outage, all unavailable",
        "safe delivery continues; unavailable work is honest BLOCKED/TIMED_OUT",
        "PASS" if passed else "FAIL",
        {
            "delivery_statuses": statuses,
            "healthy_provider_calls": len(healthy_provider.calls),
            "provider_anomaly": provider_anomaly,
            "provider_incident_statuses": [item.status for item in provider_incidents],
        },
        "provider scope isolated; no false successful offline delivery",
        "durable requests/outcomes remain replayable; recovery requires fresh provider health",
        "PASS" if passed else "FAIL",
    )


def _case_database(root: Path) -> dict[str, Any]:
    database = _database(root)
    coordinator = ResilienceCoordinator(database)
    run = coordinator.run_once(metrics={"database_errors": 1}, auto_repair=True)
    actual = run.incidents[0].status
    passed = actual == IncidentStatus.BLOCKED.value
    return _row(
        "V7C-05-database-fault", "durable database fault signal", "BLOCKED; no fabricated recovery",
        actual, {"quick_check": database.quick_check(readonly=True), "event_count": database.event_count()},
        "database incident recorded; no write repair handler registered",
        "restore/reconcile remains owner-controlled", "PASS" if passed else "FAIL",
    )


def _case_release(root: Path) -> dict[str, Any]:
    database = _database(root)
    callback_observations: list[bool] = []

    def rollback(incident, report):
        callback_observations.append(is_frozen(database))
        return RepairResult(True, "disposable rollback callback", {"rollback": "rehearsed"})

    coordinator = ResilienceCoordinator(
        database,
        repair_handlers={"release_degradation": rollback},
        health_probe=lambda incident, report: True,
    )
    run = coordinator.run_once(metrics={"release_degraded": True, "release_id": "candidate"})
    actual = run.incidents[0].status
    passed = actual == IncidentStatus.RECOVERED.value and callback_observations == [True]
    return _row(
        "V7C-07-release-degradation", "release health degradation", "freeze precedes rollback callback",
        actual, {"frozen_before_callback": callback_observations},
        "release control frozen before repair", "disposable rollback callback returned success and health passed",
        "PASS" if passed else "FAIL",
    )


def _case_release_failure_modes(root: Path) -> dict[str, Any]:
    def run_release_case(
        case_root: Path,
        *,
        previous_schema: int = SCHEMA_VERSION,
        has_previous: bool = True,
        healthy: bool = True,
    ) -> tuple[str, str | None, tuple[dict[str, Any], ...]]:
        release_root = case_root / "release-root"
        release_root.mkdir(parents=True)
        current = "b" * 40
        previous = "a" * 40
        if has_previous:
            _fake_release(release_root, previous, schema=SCHEMA_VERSION)
        _fake_release(
            release_root, current,
            previous=previous if has_previous else None,
        )
        set_current(previous if has_previous else current, root=release_root)
        if has_previous:
            set_current(current, root=release_root)
            if previous_schema != SCHEMA_VERSION:
                previous_path = release_root / "releases" / previous / "RELEASE.json"
                previous_manifest = json.loads(previous_path.read_text(encoding="utf-8"))
                previous_manifest["schema_compatibility"]["package_schema"] = previous_schema
                previous_path.write_text(json.dumps(previous_manifest), encoding="utf-8")

        home = case_root / "foundry"
        durable = FoundryDatabase(home / "foundry.db")
        durable.initialize()

        def rollback(incident, report):
            try:
                result = rollback_current(root=release_root, foundry_home=home)
                return RepairResult(True, "rollback completed", result)
            except ReleaseError as error:
                return RepairResult(False, "rollback refused", {"error": str(error)})

        coordinator = ResilienceCoordinator(
            durable,
            max_repair_attempts=1,
            repair_handlers={"release_degradation": rollback},
            health_probe=lambda incident, report: healthy,
        )
        run = coordinator.run_once(metrics={
            "release_degraded": True,
            "release_id": current,
        })
        return (
            run.incidents[0].status,
            current_pointer(release_root).read_text(encoding="utf-8").strip(),
            tuple(dict(action) for action in run.incidents[0].actions),
        )

    missing_status, missing_current, missing_actions = run_release_case(
        root / "missing-previous", has_previous=False,
    )
    schema_status, schema_current, schema_actions = run_release_case(
        root / "schema-mismatch", previous_schema=SCHEMA_VERSION - 1,
    )
    unhealthy_status, unhealthy_current, unhealthy_actions = run_release_case(
        root / "health-not-restored", healthy=False,
    )
    passed = (
        missing_status == IncidentStatus.ESCALATED.value
        and missing_current == "b" * 40
        and schema_status == IncidentStatus.ESCALATED.value
        and schema_current == "b" * 40
        and unhealthy_status == IncidentStatus.ESCALATED.value
        and unhealthy_current == "a" * 40
        and any("no PREVIOUS" in str(action) for action in missing_actions)
        and any("schema" in str(action).lower() for action in schema_actions)
        and any(action.get("kind") == "repair" for action in unhealthy_actions)
    )
    return _row(
        "V7C-08-release-rollback-failures",
        "PREVIOUS missing, schema incompatible, recovery health not restored",
        "freeze first; rollback refuses or escalates without false recovery",
        "PASS" if passed else "FAIL",
        {
            "missing_previous": {"status": missing_status, "current": missing_current},
            "schema_mismatch": {"status": schema_status, "current": schema_current},
            "health_not_restored": {"status": unhealthy_status, "current": unhealthy_current},
        },
        "release control remains frozen while each failure is bounded",
        "failed rollback escalates; successful pointer switch with failed health remains escalated",
        "PASS" if passed else "FAIL",
    )


def _case_database_restore(root: Path) -> dict[str, Any]:
    database = _database(root)
    database.append(Event.create(
        "database.marker", {"marker": "snapshot"},
        source_type="fault-matrix", source_id="v7-matrix",
        idempotency_key="database.marker:snapshot",
    ))
    snapshot = root / "snapshot.db"
    database.backup_to(snapshot)
    snapshot_events = database.event_count()
    database.append(Event.create(
        "database.marker", {"marker": "mutated"},
        source_type="fault-matrix", source_id="v7-matrix",
        idempotency_key="database.marker:mutated",
    ))
    database.restore_from(snapshot)
    restored_events = database.event_count()
    corrupt = root / "corrupt.db"
    corrupt.write_bytes(b"not a sqlite database")
    corrupt_rejected = False
    try:
        database.restore_from(corrupt)
    except (OSError, sqlite3.DatabaseError):
        corrupt_rejected = True
    passed = restored_events == snapshot_events and corrupt_rejected and database.quick_check() == "ok"
    return _row(
        "V7C-06-database-restore", "corrupt copy and interrupted durable-state mutation",
        "valid snapshot restored; corrupt source rejected",
        "RESTORED" if passed else "RESTORE_UNPROVEN",
        {"snapshot_events": snapshot_events, "restored_events": restored_events,
         "corrupt_rejected": corrupt_rejected, "quick_check": database.quick_check()},
        "restore uses validated backup; corrupt source cannot replace live copy",
        "ledger integrity remains verifiable after restore",
        "PASS" if passed else "FAIL",
    )


def _case_effect(root: Path) -> dict[str, Any]:
    database = _database(root)
    database.append(Event.create(
        "system.action_started", {
            "action_key": "ambiguous-1", "system_id": "fake",
            "target_ref": "disposable", "payload_hash": "hash", "kind": "write",
        },
        source_type="fault-matrix", source_id="v7-matrix",
        idempotency_key="system.action_started:ambiguous-1",
    ))
    coordinator = ResilienceCoordinator(database)
    run = coordinator.run_once()
    actual = run.incidents[0].status
    effect = effect_state(database, "ambiguous-1")
    passed = actual == IncidentStatus.UNKNOWN.value and effect.get("status") == "FROZEN"
    return _row(
        "V7C-08-ambiguous-effect", "STARTED external effect with no terminal evidence", "UNKNOWN + FROZEN",
        actual, {"effect_state": effect}, "effect frozen; retry and undo withheld",
        "provider reconciliation required", "PASS" if passed else "FAIL",
    )


def _case_partial_effect_timeout(root: Path) -> dict[str, Any]:
    database = _database(root)
    action_key = "partial-timeout"
    database.append(Event.create(
        "system.action_started",
        {
            "action_key": action_key,
            "system_id": "fake-provider",
            "target_ref": "disposable-target",
            "payload_hash": "hash",
            "kind": "write",
        },
        source_type="fault-matrix",
        source_id="v7-matrix",
        idempotency_key=f"system.action_started:{action_key}",
    ))
    provider = FakeDeliveryProvider(
        provider_id="connector-timeout",
        scripted=[TimeoutError("timeout after external effect")],
    )
    receipt = DeliveryBoundary(database, {provider.provider_id: provider}).dispatch(
        _delivery_request(provider.provider_id, action_key)
    )
    database.append(Event.create(
        "worker.crashed",
        {"run_id": "partial-effect-worker", "reason": "process lost after dispatch"},
        source_type="fault-matrix",
        source_id="v7-matrix",
        idempotency_key="worker.crashed:partial-effect-worker",
    ))
    coordinator = ResilienceCoordinator(
        database,
        detector=AnomalyDetector(AnomalyThresholds(worker_failures=1)),
    )
    run = coordinator.run_once(auto_repair=True)
    effect = effect_state(database, action_key)
    effect_incidents = [item for item in run.incidents if item.effect_action_key == action_key]
    passed = (
        receipt["status"] == DeliveryStatus.TIMED_OUT.value
        and len(provider.calls) == 1
        and effect.get("status") == "FROZEN"
        and effect_incidents
        and effect_incidents[0].status == IncidentStatus.UNKNOWN.value
        and database.event_count(event_type="system.action_completed") == 0
        and database.event_count(event_type="system.action_failed") == 0
    )
    return _row(
        "V7C-12-worker-partial-effect",
        "worker crash and connector timeout after an external effect may have occurred",
        "freeze effect; no blind retry or compensation",
        "UNKNOWN + FROZEN" if passed else "FAIL",
        {
            "delivery_status": receipt["status"],
            "effect_state": effect,
            "incident_statuses": [item.status for item in run.incidents],
            "provider_calls": len(provider.calls),
        },
        "effect journal remains frozen and worker scope is not redispatched",
        "explicit external reconciliation required before any continuation",
        "PASS" if passed else "FAIL",
    )


def _case_effect_reconciliation(root: Path) -> dict[str, Any]:
    database = _database(root)
    action_key = "reconcile-explicit"
    database.append(Event.create(
        "system.action_started",
        {
            "action_key": action_key,
            "system_id": "fake-provider",
            "target_ref": "disposable-target",
            "payload_hash": "hash",
            "kind": "write",
        },
        source_type="fault-matrix",
        source_id="v7-matrix",
        idempotency_key=f"system.action_started:{action_key}",
    ))
    calls: list[str] = []

    def repair(incident, report):
        calls.append(incident.incident_id)
        return RepairResult(True, "reconciled effect repair", {"reconciled": True})

    coordinator = ResilienceCoordinator(
        database,
        repair_handlers={"effect_unknown": repair},
        health_probe=lambda incident, report: True,
    )
    first = coordinator.run_once(auto_repair=True)
    incident = first.incidents[0]
    reconciled = coordinator.reconcile_incident_effect(
        incident.incident_id,
        classification=EffectAmbiguity.RECONCILED,
        evidence={"provider": "fake-oracle", "matched": True},
        effect_action_key=action_key,
    )
    passed = (
        incident.status == IncidentStatus.UNKNOWN.value
        and reconciled.status == IncidentStatus.RECOVERED.value
        and calls == [incident.incident_id]
        and effect_state(database, action_key).get("status") == "FROZEN"
    )
    return _row(
        "V7C-12-effect-reconciliation",
        "remote effect confirmed after local result loss",
        "continue only through explicit RECONCILED evidence",
        reconciled.status,
        {
            "initial_status": incident.status,
            "final_status": reconciled.status,
            "repair_calls": len(calls),
            "effect_state": effect_state(database, action_key),
        },
        "UNKNOWN effect stays frozen until an explicit integration call",
        "bounded repair runs once after provider/system truth matches",
        "PASS" if passed else "FAIL",
    )


def _case_false_success(root: Path) -> dict[str, Any]:
    database = _database(root)
    database.append(Event.create(
        "worker.fallback_triggered",
        {
            "run_id": "false-success",
            "trigger": "claimed_success_contradicted_by_evidence",
        },
        source_type="worker",
        source_id="disposable-worker",
        idempotency_key="worker.fallback_triggered:false-success",
    ))
    detector = AnomalyDetector(AnomalyThresholds(worker_failures=1))
    run = ResilienceCoordinator(database, detector=detector).run_once()
    worker_anomaly = any(item.kind == "worker_crash_loop" for item in run.anomalies)
    incident = next((item for item in run.incidents if item.correlation_key.startswith("worker:")), None)
    passed = worker_anomaly and incident is not None and incident.status == IncidentStatus.BLOCKED.value
    return _row(
        "V7C-16-false-success-worker",
        "worker claimed completion but independent evidence contradicted it",
        "worker anomaly recorded; no false success accepted",
        incident.status if incident is not None else "MISSING",
        {
            "anomaly_kinds": [item.kind for item in run.anomalies],
            "incident_status": incident.status if incident is not None else None,
            "trigger": "claimed_success_contradicted_by_evidence",
        },
        "worker capability remains blocked pending bounded recovery",
        "verifier evidence, not worker claim, controls continuation",
        "PASS" if passed else "FAIL",
    )


def _case_resource_cost(root: Path) -> dict[str, Any]:
    database = _database(root)
    coordinator = ResilienceCoordinator(database)
    run = coordinator.run_once(metrics={"task_count": 101, "memory_bytes": 2_000_000_001, "cost_usd": 26.0})
    kinds = sorted(item.kind for item in run.anomalies)
    actual = sorted(item.status for item in run.incidents)
    passed = kinds == ["cost_anomaly", "resource_anomaly"] and all(
        status == IncidentStatus.BLOCKED.value for status in actual
    )
    return _row(
        "V7C-13-resource-cost", "runaway resource and cost metrics", "resource/cost incidents BLOCKED",
        ",".join(actual), {"anomaly_kinds": kinds}, "resource and provider scopes isolated",
        "owner quota/capacity action required", "PASS" if passed else "FAIL",
    )


def _case_concurrency(root: Path) -> dict[str, Any]:
    database = _database(root)
    stop_path = root / "STOP"
    first = Supervisor(database, stop_path, instance_id="matrix-owner")
    duplicate = Supervisor(database, stop_path, instance_id="matrix-duplicate")
    first.start()
    duplicate_blocked = False
    try:
        duplicate.start()
    except LeaseUnavailable:
        duplicate_blocked = True
    first.stop()
    return _row(
        "V7C-11-duplicate-authority", "duplicate Supervisor wake", "second owner BLOCKED",
        "BLOCKED" if duplicate_blocked else "RUNNING",
        {"duplicate_blocked": duplicate_blocked, "event_count": database.event_count()},
        "singleton lease retained by first owner", "first owner stopped cleanly",
        "PASS" if duplicate_blocked else "FAIL",
    )


def _case_duplicate_delivery(root: Path) -> dict[str, Any]:
    database = _database(root)
    started = threading.Event()
    release = threading.Event()

    class SlowProvider(FakeDeliveryProvider):
        def deliver(self, request):
            self.calls.append(request)
            started.set()
            release.wait(timeout=3)
            return DeliveryOutcome.delivered(
                provider_actual=self.provider_id,
                provider_request_id="slow-disposable-1",
            )

    provider = SlowProvider(provider_id="slow")
    boundary = DeliveryBoundary(database, {provider.provider_id: provider})
    request = _delivery_request(provider.provider_id, "duplicate")
    results: list[dict[str, Any]] = []

    def dispatch() -> None:
        results.append(boundary.dispatch(request))

    first = threading.Thread(target=dispatch)
    second = threading.Thread(target=dispatch)
    first.start()
    started.wait(timeout=2)
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    statuses = sorted(str(item.get("status")) for item in results)
    passed = (
        len(results) == 2
        and statuses == [DeliveryStatus.DELIVERED.value, DeliveryStatus.UNKNOWN.value]
        and len(provider.calls) == 1
    )
    return _row(
        "V7C-11-duplicate-delivery",
        "two dispatchers wake for one idempotent delivery",
        "one provider claim; competing owner reports UNKNOWN",
        ",".join(statuses) if statuses else "NO_RESULT",
        {
            "statuses": statuses,
            "provider_calls": len(provider.calls),
            "delivery_claims": database.event_count(event_type="delivery.claimed"),
        },
        "durable claim compare-and-set prevents duplicate external calls",
        "confirmed result is replayable by idempotency key",
        "PASS" if passed else "FAIL",
    )


def _case_lease_churn(root: Path) -> dict[str, Any]:
    database = _database(root)
    for index in range(2):
        database.append(Event.create(
            "lease.reclaimed",
            {
                "lease_key": "supervisor:singleton",
                "owner_id": f"stale-{index}",
                "pid": 40_000 + index,
                "reason": "process identity was stale or reused",
            },
            source_type="fault-matrix",
            source_id="v7-matrix",
            idempotency_key=f"lease.reclaimed:stale-{index}",
        ))
    detector = AnomalyDetector(AnomalyThresholds(lease_churn=1))
    run = ResilienceCoordinator(database, detector=detector).run_once()
    churn = any(item.kind == "duplicate_authority" for item in run.anomalies)
    passed = churn and run.incidents and run.incidents[0].status == IncidentStatus.BLOCKED.value
    return _row(
        "V7C-11-stale-lease",
        "stale lease and PID/creation-identity churn",
        "authority anomaly contained; no unbounded takeover",
        run.incidents[0].status if run.incidents else "MISSING",
        {
            "anomaly_kinds": [item.kind for item in run.anomalies],
            "lease_reclaimed": database.event_count(event_type="lease.reclaimed"),
        },
        "duplicate-authority capability is blocked pending owner reconciliation",
        "fresh owner must prove process identity before lease acquisition",
        "PASS" if passed else "FAIL",
    )


def _case_supervisor_crash_restart(root: Path) -> dict[str, Any]:
    database_path = root / "foundry.db"
    stop_path = root / "STOP"
    source_root = Path(__file__).resolve().parents[1] / "src"
    child_code = (
        "import sys,time\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, sys.argv[2])\n"
        "from jarvis_foundry.database import FoundryDatabase\n"
        "from jarvis_foundry.supervisor.service import Supervisor\n"
        "root=Path(sys.argv[1])\n"
        "db=FoundryDatabase(root / 'foundry.db')\n"
        "db.initialize()\n"
        "supervisor=Supervisor(db, root / 'STOP', instance_id='crash-lab', lease_seconds=60)\n"
        "supervisor.start()\n"
        "(root / 'started').write_text('started', encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(root), str(source_root)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    started = False
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and process.poll() is None:
            if (root / "started").is_file():
                started = True
                break
            time.sleep(0.05)
        process.terminate()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    replacement = Supervisor(
        FoundryDatabase(database_path), stop_path, instance_id="crash-replacement"
    )
    replacement_status = "NOT_STARTED"
    heartbeat_status = "NOT_HEARTBEAT"
    try:
        replacement_status = replacement.start()
        heartbeat_status = replacement.heartbeat()
    finally:
        replacement.stop()
    reclaimed = (
        database_path.exists()
        and FoundryDatabase(database_path).event_count(event_type="runtime.control.lease_reclaimed")
    )
    passed = started and replacement_status == "RUNNING" and heartbeat_status == "RUNNING" and reclaimed
    return _row(
        "V7C-15-supervisor-crash-restart",
        "disposable Supervisor process terminated before clean shutdown",
        "fresh Supervisor reclaims stale authority and resumes heartbeat",
        "RUNNING_AFTER_RESTART" if passed else "FAIL",
        {
            "child_started": started,
            "replacement_status": replacement_status,
            "heartbeat_status": heartbeat_status,
            "lease_reclaimed": reclaimed,
        },
        "stale lease is reclaimed only after the recorded process is dead",
        "new owner emits heartbeat and stops cleanly",
        "PASS" if passed else "FAIL",
    )


def _case_restart(root: Path) -> dict[str, Any]:
    database = _database(root)
    _register_resource(database)
    database.append(Event.create(
        "system.action_started",
        {
            "action_key": "restart-pending-effect",
            "system_id": "fake",
            "target_ref": "disposable",
            "payload_hash": "hash",
            "kind": "write",
        },
        source_type="fault-matrix",
        source_id="v7-matrix",
        idempotency_key="system.action_started:restart-pending-effect",
    ))
    graph = DependencyGraph()
    graph.bind("resource-1", "safe-work")
    first = ResilienceCoordinator(database, graph=graph)
    first.run_once(
        now="2026-08-23T10:00:00Z",
        metrics={
            "worker_failures": 3,
            "resource_id": "resource-1",
            "capability": "safe-work",
        },
        auto_repair=False,
    )
    before = rebuild_incidents(database)
    second = ResilienceCoordinator(database, graph=graph)
    second.run_once(
        now="2026-08-23T10:00:00Z",
        metrics={
            "worker_failures": 3,
            "resource_id": "resource-1",
            "capability": "safe-work",
        },
        auto_repair=False,
    )
    after = rebuild_incidents(database)
    detected = database.event_count(event_type="incident.detected")
    state = json.loads(str(database.row("resources", "resource_id", "resource-1")["capabilities_json"]))
    pending_effect = effect_state(database, "restart-pending-effect")
    passed = (
        before == after
        and detected == len(before)
        and state["safe-work"] == "STOPPED"
        and pending_effect.get("status") == "FROZEN"
    )
    return _row(
        "V7C-15-restart-replay",
        "fresh coordinator after process interruption with active incident and pending effect",
        "same incidents, suspension, and frozen effect reconstructed",
        "REPLAY_STABLE" if passed else "REPLAY_DRIFT",
        {
            "incident_count": len(after),
            "detected_events": detected,
            "capability_state": state,
            "effect_state": pending_effect,
        },
        "durable incident state, capability suspension, and effect freeze reconstructed from ledger",
        "fresh process can continue only after explicit health/effect reconciliation",
        "PASS" if passed else "FAIL",
    )


def run_matrix() -> dict[str, Any]:
    cases: list[tuple[str, Callable[[Path], dict[str, Any]]]] = [
        ("worker", _case_worker_crash),
        ("stop", _case_stop),
        ("diagnostic", _case_diagnostic_gate),
        ("provider", _case_provider),
        ("provider_continuity", _case_provider_continuity),
        ("database", _case_database),
        ("database_restore", _case_database_restore),
        ("release_freeze", _case_release),
        ("release_failures", _case_release_failure_modes),
        ("effect", _case_effect),
        ("partial_effect", _case_partial_effect_timeout),
        ("effect_reconciliation", _case_effect_reconciliation),
        ("false_success", _case_false_success),
        ("resource_cost", _case_resource_cost),
        ("concurrency", _case_concurrency),
        ("duplicate_delivery", _case_duplicate_delivery),
        ("lease_churn", _case_lease_churn),
        ("supervisor_crash", _case_supervisor_crash_restart),
        ("restart", _case_restart),
    ]
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="jarvis-v7-resilience-") as temp:
        root = Path(temp)
        for name, case in cases:
            case_root = root / name
            case_root.mkdir()
            try:
                rows.append(case(case_root))
            except Exception as error:  # noqa: BLE001 — matrix records honest failure
                rows.append(_row(
                    f"V7C-ERR-{name}", name, "PASS", "ERROR",
                    {"error_class": type(error).__name__, "error": str(error)[:300]},
                    "none", "blocked by harness error", "FAIL",
                ))
            gc.collect()
    return {
        "harness": "v7-resilience-fault-matrix",
        "scope": "disposable homes only",
        "rows": rows,
        "summary": {
            "total": len(rows),
            "pass": sum(row["verdict"] == "PASS" for row in rows),
            "fail": sum(row["verdict"] == "FAIL" for row in rows),
        },
        "verdict": "PASS" if all(row["verdict"] == "PASS" for row in rows) else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_matrix()
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
