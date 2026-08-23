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
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import effect_state
from jarvis_foundry.events import Event
from jarvis_foundry.evolution.promote import is_frozen
from jarvis_foundry.resilience import (
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
        "V7C-09-resource-cost", "runaway resource and cost metrics", "resource/cost incidents BLOCKED",
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
        "V7C-10-duplicate-authority", "duplicate Supervisor wake", "second owner BLOCKED",
        "BLOCKED" if duplicate_blocked else "RUNNING",
        {"duplicate_blocked": duplicate_blocked, "event_count": database.event_count()},
        "singleton lease retained by first owner", "first owner stopped cleanly",
        "PASS" if duplicate_blocked else "FAIL",
    )


def _case_restart(root: Path) -> dict[str, Any]:
    database = _database(root)
    first = ResilienceCoordinator(database)
    first.run_once(now="2026-08-23T10:00:00Z", metrics={"worker_failures": 3}, auto_repair=False)
    before = rebuild_incidents(database)
    second = ResilienceCoordinator(database)
    second.run_once(now="2026-08-23T10:00:00Z", metrics={"worker_failures": 3}, auto_repair=False)
    after = rebuild_incidents(database)
    detected = database.event_count(event_type="incident.detected")
    passed = before == after and detected == 1
    return _row(
        "V7C-11-restart-replay", "fresh coordinator after interrupted run", "same incident; no duplicate detection",
        "REPLAY_STABLE" if passed else "REPLAY_DRIFT",
        {"incident_count": len(after), "detected_events": detected},
        "durable incident state reconstructed from ledger", "resume remains bounded from DIAGNOSING",
        "PASS" if passed else "FAIL",
    )


def run_matrix() -> dict[str, Any]:
    cases: list[tuple[str, Callable[[Path], dict[str, Any]]]] = [
        ("worker", _case_worker_crash),
        ("stop", _case_stop),
        ("diagnostic", _case_diagnostic_gate),
        ("provider", _case_provider),
        ("database", _case_database),
        ("database_restore", _case_database_restore),
        ("release", _case_release),
        ("effect", _case_effect),
        ("resource_cost", _case_resource_cost),
        ("concurrency", _case_concurrency),
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
