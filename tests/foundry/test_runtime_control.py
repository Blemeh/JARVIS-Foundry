from __future__ import annotations

import json
import os
import sys

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, EventConflict
from jarvis_foundry.supervisor.control import (
    inspect_control,
    record_claim,
    submit_request,
)
from jarvis_foundry.supervisor.leases import LeaseManager, LeaseUnavailable
from jarvis_foundry.supervisor.service import Supervisor


def _database(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _sleep_command() -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(60)"]


def test_control_request_is_idempotent_and_conflicts_fail_closed(tmp_path):
    database = _database(tmp_path)
    first = submit_request(database, "start", request_id="request-1")
    duplicate = submit_request(database, "START", request_id="request-1")

    assert duplicate == first
    assert database.event_count(event_type="runtime.control.requested") == 1
    with pytest.raises(EventConflict):
        submit_request(database, "stop", request_id="request-1")


def test_start_request_is_reconciled_after_supervisor_start(tmp_path):
    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    request = submit_request(database, "start", request_id="start-1")
    supervisor = Supervisor(database, stop_path, instance_id="start-instance")

    assert supervisor.start() == "RUNNING"
    state = inspect_control(database, stop_path=str(stop_path))
    assert state.status == "RUNNING"
    assert state.request_id == request.request_id
    assert state.request_status == "APPLIED"
    assert state.pid == os.getpid()
    supervisor.stop()


def test_duplicate_start_on_same_instance_is_idempotent(tmp_path):
    database = _database(tmp_path)
    supervisor = Supervisor(database, tmp_path / "EMERGENCY_STOP", instance_id="same-instance")

    assert supervisor.start() == "RUNNING"
    assert supervisor.start() == "RUNNING"
    assert database.event_count(event_type="supervisor.started") == 1
    supervisor.stop()
    supervisor.stop()
    assert database.event_count(event_type="supervisor.stopped") == 1


def test_second_supervisor_object_cannot_reuse_a_live_instance_id(tmp_path):
    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    first = Supervisor(database, stop_path, instance_id="same-live-instance")
    assert first.start() == "RUNNING"
    second = Supervisor(database, stop_path, instance_id="same-live-instance")

    with pytest.raises(LeaseUnavailable, match="already held"):
        second.start()
    assert first.started is True
    first.stop()


def test_reused_instance_id_allocates_a_new_durable_generation(tmp_path):
    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    first = Supervisor(database, stop_path, instance_id="reused-instance")
    assert first.start() == "RUNNING"
    first.stop()

    second = Supervisor(database, stop_path, instance_id="reused-instance")
    assert second.start() == "RUNNING"
    generations = [
        int(event.payload["generation"])
        for event in database.events()
        if event.event_type == "supervisor.started"
    ]
    assert generations == [1, 2]
    second.stop()


def test_heartbeat_does_not_reacquire_a_stolen_supervisor_lease(tmp_path):
    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    supervisor = Supervisor(database, stop_path, instance_id="heartbeat-owner")
    assert supervisor.start() == "RUNNING"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE leases SET owner_id = ? WHERE lease_key = ?",
            ("intruder", "supervisor:singleton"),
        )

    with pytest.raises(LeaseUnavailable):
        supervisor.heartbeat()
    degraded = inspect_control(database, stop_path=str(stop_path))
    assert degraded.status == "DEGRADED"
    assert "lease" in (degraded.reason or "").lower()
    supervisor.started = False


def test_stop_request_is_applied_by_next_heartbeat(tmp_path):
    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    supervisor = Supervisor(database, stop_path, instance_id="stop-instance")
    assert supervisor.start() == "RUNNING"
    request = submit_request(database, "stop", request_id="stop-1")

    assert supervisor.heartbeat() == "STOPPED"
    assert supervisor.started is False
    state = inspect_control(database, stop_path=str(stop_path))
    assert state.status == "STOPPED"
    assert state.request_id == request.request_id
    assert state.request_status == "APPLIED"


def test_restart_request_stops_then_new_instance_adopts_request(tmp_path):
    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    first = Supervisor(database, stop_path, instance_id="restart-old")
    assert first.start() == "RUNNING"
    submit_request(database, "restart", request_id="restart-1")

    assert first.heartbeat() == "STOPPED"
    between = inspect_control(database, stop_path=str(stop_path))
    assert between.status == "STOPPED"
    assert between.request_status == "PENDING"

    second = Supervisor(database, stop_path, instance_id="restart-new")
    assert second.start() == "RUNNING"
    after = inspect_control(database, stop_path=str(stop_path))
    assert after.status == "RUNNING"
    assert after.request_status == "APPLIED"
    second.stop()


def test_duplicate_scheduler_does_not_resolve_stop_while_lease_is_live(tmp_path):
    database = _database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    owner = Supervisor(database, stop_path, instance_id="live-owner")
    assert owner.start() == "RUNNING"
    submit_request(database, "stop", request_id="stop-live-owner")

    duplicate = Supervisor(database, stop_path, instance_id="duplicate-trigger")
    with pytest.raises(LeaseUnavailable):
        duplicate.start()
    pending = inspect_control(database, stop_path=str(stop_path))
    assert pending.status == "RUNNING"
    assert pending.request_status == "PENDING"

    assert owner.heartbeat() == "STOPPED"
    assert inspect_control(database, stop_path=str(stop_path)).status == "STOPPED"


def test_missing_creation_identity_is_unknown(tmp_path):
    database = _database(tmp_path)
    instance_id = "identity-unavailable"
    database.append(Event.create(
        "supervisor.started",
        {
            "instance_id": instance_id,
            "trust_state": "LAB",
            "pid": os.getpid(),
        },
        source_type="supervisor", source_id=instance_id,
        idempotency_key="supervisor.started:identity-unavailable",
    ))
    LeaseManager(database).acquire("supervisor:singleton", instance_id, 900)

    state = inspect_control(database)
    assert state.status == "UNKNOWN"
    assert "identity" in (state.reason or "")


def test_crashed_claim_with_dead_pid_is_reclaimed(tmp_path):
    database = _database(tmp_path)
    record_claim(
        database,
        instance_id="dead-instance",
        pid=999999,
        creation_identity={"pid": 999999, "create_time": 1, "exe": "dead.exe"},
    )
    LeaseManager(database).acquire("supervisor:singleton", "dead-instance", 900)
    fresh = Supervisor(database, tmp_path / "EMERGENCY_STOP", instance_id="fresh-instance")

    assert fresh.start() == "RUNNING"
    assert database.row("leases", "lease_key", "supervisor:singleton")["owner_id"] == "fresh-instance"
    assert database.event_count(event_type="runtime.control.lease_reclaimed") == 1
    fresh.stop()


def test_pid_reuse_is_unknown_and_never_running(tmp_path):
    database = _database(tmp_path)
    instance_id = "reused-instance"
    database.append(Event.create(
        "supervisor.started",
        {
            "instance_id": instance_id,
            "trust_state": "LAB",
            "pid": os.getpid(),
            "creation_identity": {"pid": os.getpid(), "create_time": 1, "exe": "wrong.exe"},
        },
        source_type="supervisor", source_id=instance_id,
        idempotency_key="supervisor.started:reused-instance",
    ))
    LeaseManager(database).acquire("supervisor:singleton", instance_id, 900)

    state = inspect_control(database)
    assert state.status == "UNKNOWN"
    assert "identity" in (state.reason or "") or "reused" in (state.reason or "")


def test_spawn_registration_failure_kills_process_and_releases_lease(tmp_path, monkeypatch):
    import jarvis_foundry.supervisor.worker as worker_module

    database = _database(tmp_path)
    controller = Supervisor(database, tmp_path / "EMERGENCY_STOP", instance_id="worker-owner").workers

    def fail_registration(*args, **kwargs):
        raise OSError("registration interrupted")

    monkeypatch.setattr(worker_module, "register_adapter_pid", fail_registration)
    with pytest.raises(OSError, match="registration interrupted"):
        controller.spawn("registration-failure", _sleep_command(), cwd=tmp_path)

    assert database.row("leases", "lease_key", "worker:registration-failure") is None
    assert controller.active_runs() == []


def test_terminate_all_does_not_kill_pid_with_reused_identity(tmp_path):
    database = _database(tmp_path)
    controller = Supervisor(database, tmp_path / "EMERGENCY_STOP", instance_id="worker-owner").workers
    pid = os.getpid()
    controller.pid_table_path().write_text(json.dumps({
        "reused": {
            "pid": pid,
            "run_id": "reused",
            "creation_identity": {"pid": pid, "create_time": 1, "exe": "wrong.exe"},
        },
    }), encoding="utf-8")
    LeaseManager(database).acquire("worker:reused", "worker:reused", 900)

    assert controller.terminate_all() == ["reused"]
    assert database.row("leases", "lease_key", "worker:reused") is None
    assert os.getpid() > 0
