"""Command Center health must describe current runtime, not failure history."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.supervisor.estop import EmergencyStop


NOW = "2026-08-20T12:00:00Z"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "supervisor.heartbeat",
        {"instance_id": "supervisor-test", "trust_state": "LAB"},
        source_type="supervisor", source_id="supervisor-test",
        idempotency_key="supervisor.heartbeat:health-test",
        occurred_at=NOW,
    ))
    return database


def _append_run(
    database: FoundryDatabase,
    run_id: str,
    *,
    mission_id: str = "mission-health",
    step_id: str = "step-a",
    role: str = "builder",
    status: str = "FAILED",
    failure_class: str = "evidence_mismatch",
) -> None:
    database.append(Event.create(
        "agent_run.started",
        {
            "run_id": run_id,
            "mission_id": mission_id,
            "step_id": step_id,
            "role": role,
            "provider": "opencode_go",
            "model": "deepseek-v4-flash",
        },
        source_type="supervisor", source_id="health-test",
        idempotency_key=f"agent_run.started:{run_id}",
        occurred_at=NOW,
    ))
    if status == "FAILED":
        database.append(Event.create(
            "agent_run.failed",
            {"run_id": run_id, "failure_class": failure_class},
            source_type="supervisor", source_id="health-test",
            idempotency_key=f"agent_run.failed:{run_id}",
            occurred_at=NOW,
        ))


def _insert_lease(
    database: FoundryDatabase,
    lease_key: str,
    owner_id: str,
    *,
    expires_at: str,
) -> None:
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO leases(lease_key, owner_id, expires_at, heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            (lease_key, owner_id, expires_at, NOW),
        )


def _snapshot(database: FoundryDatabase, stop_path: Path) -> dict:
    return build_snapshot(database, stop_path=stop_path, now=NOW)


def test_terminal_evidence_mismatch_is_preserved_but_not_current_health_failure(
    db: FoundryDatabase, tmp_path: Path
) -> None:
    _append_run(db, "run-historical-evidence-mismatch")

    snapshot = _snapshot(db, tmp_path / "STOP")

    worker = snapshot["workers"][0]
    assert worker["status"] == "FAILED"
    assert worker["failure_class"] == "evidence_mismatch"
    assert snapshot["health"]["crashed_workers"] == []
    assert snapshot["health"]["degraded"] is False


def test_failed_run_with_its_live_role_lease_is_current_and_degrades(
    db: FoundryDatabase, tmp_path: Path
) -> None:
    run_id = "run-current-failure"
    mission_id = "mission-current-failure"
    step_id = "step-a"
    _append_run(db, run_id, mission_id=mission_id, step_id=step_id)
    _insert_lease(
        db,
        f"role:builder:{mission_id}:{step_id}",
        run_id,
        expires_at="2026-08-20T12:30:00Z",
    )

    health = _snapshot(db, tmp_path / "STOP")["health"]

    assert [worker["run_id"] for worker in health["crashed_workers"]] == [run_id]
    assert health["degraded"] is True


def test_running_projection_without_live_lease_is_current_failure(
    db: FoundryDatabase, tmp_path: Path
) -> None:
    run_id = "run-running-without-lease"
    _append_run(db, run_id, status="RUNNING")

    health = _snapshot(db, tmp_path / "STOP")["health"]

    assert [worker["run_id"] for worker in health["crashed_workers"]] == [run_id]
    assert health["degraded"] is True


def test_running_projection_with_live_lease_is_not_a_crash(
    db: FoundryDatabase, tmp_path: Path
) -> None:
    run_id = "run-running-with-lease"
    mission_id = "mission-running-with-lease"
    step_id = "step-a"
    _append_run(db, run_id, mission_id=mission_id, step_id=step_id, status="RUNNING")
    _insert_lease(
        db,
        f"role:builder:{mission_id}:{step_id}",
        run_id,
        expires_at="2026-08-20T12:30:00Z",
    )

    health = _snapshot(db, tmp_path / "STOP")["health"]

    assert health["crashed_workers"] == []
    assert health["degraded"] is False


def test_stale_lease_and_supervisor_degraded_still_degrade(
    db: FoundryDatabase, tmp_path: Path
) -> None:
    run_id = "run-stale-lease"
    mission_id = "mission-stale-lease"
    step_id = "step-a"
    _append_run(db, run_id, mission_id=mission_id, step_id=step_id, status="RUNNING")
    _insert_lease(
        db,
        f"role:builder:{mission_id}:{step_id}",
        run_id,
        expires_at="2026-08-20T11:59:00Z",
    )

    health = _snapshot(db, tmp_path / "STOP")["health"]
    assert health["stale_leases"]
    assert health["degraded"] is True

    db.append(Event.create(
        "supervisor.degraded",
        {"instance_id": "supervisor-test", "trust_state": "LAB", "reason": "test"},
        source_type="supervisor", source_id="supervisor-test",
        idempotency_key="supervisor.degraded:health-test",
        occurred_at=NOW,
    ))
    assert _snapshot(db, tmp_path / "STOP")["health"]["degraded"] is True


def test_stop_remains_active_without_rewriting_health_history(
    db: FoundryDatabase, tmp_path: Path
) -> None:
    _append_run(db, "run-stop-history")
    stop = tmp_path / "STOP"
    EmergencyStop(stop).activate("health test")

    snapshot = _snapshot(db, stop)

    assert snapshot["stop"]["active"] is True
    assert snapshot["health"]["crashed_workers"] == []
    assert snapshot["health"]["degraded"] is False
