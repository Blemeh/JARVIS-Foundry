from __future__ import annotations

import json
import os
import sys

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, EventConflict
from jarvis_foundry.supervisor.control import (
    CONTROL_REQUESTED,
    inspect_control,
    record_claim,
    reconcile_control,
    request_is_stale,
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
    assert first.expires_at is not None
    assert request_is_stale(first) is False


def _append_stale_request(database, *, action: str, request_id: str) -> None:
    database.append(Event.create(
        CONTROL_REQUESTED,
        {
            "request_id": request_id,
            "action": action,
            "requested_by": "old-owner",
            "requested_at": "2020-01-01T00:00:00Z",
            "expected_release": None,
            "expires_at": "2020-01-01T01:00:00Z",
        },
        source_type="owner",
        source_id="old-owner",
        idempotency_key=f"runtime.control.requested:{request_id}",
    ))


def test_stale_start_request_is_resolved_without_launching(tmp_path):
    database = _database(tmp_path)
    _append_stale_request(database, action="START", request_id="stale-start")
    supervisor = Supervisor(database, tmp_path / "EMERGENCY_STOP", instance_id="stale-start-owner")

    assert supervisor.start() == "STOPPED"
    assert supervisor.started is False
    state = inspect_control(database)
    assert state.status == "STOPPED"
    assert state.request_status == "STALE"
    assert state.reason == "owner request expired before application"
    assert database.event_count(event_type="supervisor.started") == 0


def test_resolved_stale_request_does_not_wedge_next_startup(tmp_path):
    """Live incident 2026-08-24 regression: one expired START request must
    not permanently block supervisor recovery. The first start resolves it;
    the NEXT instance must reach RUNNING instead of exiting UNKNOWN forever."""
    database = _database(tmp_path)
    _append_stale_request(database, action="START", request_id="stale-wedge")
    first = Supervisor(database, tmp_path / "EMERGENCY_STOP", instance_id="first-attempt")
    assert first.start() == "STOPPED"
    assert first.started is False

    second = Supervisor(database, tmp_path / "EMERGENCY_STOP", instance_id="second-attempt")
    assert second.start() == "RUNNING"
    assert second.started is True
    # exactly ONE stale resolution: the consumed request is never re-resolved
    assert database.event_count(event_type="runtime.control.resolved") == 1
    assert database.event_count(event_type="supervisor.started") == 1
    second.stop()
    assert inspect_control(database).status == "STOPPED"


def test_resolved_stale_stop_request_does_not_block_restart(tmp_path):
    """A stale STOP that was already resolved must not stop a later healthy
    startup either — the EMERGENCY_STOP sentinel remains the hard authority."""
    database = _database(tmp_path)
    _append_stale_request(database, action="STOP", request_id="stale-stop-wedge")
    first = Supervisor(database, tmp_path / "EMERGENCY_STOP", instance_id="stop-first")
    assert first.start() == "STOPPED"  # resolves the expired request, launches nothing
    # Reaching a resolved state for the stale STOP request:
    from jarvis_foundry.supervisor.control import latest_resolution
    assert latest_resolution(database, "stale-stop-wedge") is not None

    second = Supervisor(database, tmp_path / "EMERGENCY_STOP", instance_id="stop-second")
    assert second.start() == "RUNNING"
    assert second.started is True
    second.stop()


def test_reconcile_closes_stale_request_without_inventing_runtime(tmp_path):
    database = _database(tmp_path)
    _append_stale_request(database, action="RESTART", request_id="stale-restart")

    state = reconcile_control(database)

    assert state.status == "STOPPED"
    assert state.request_status == "STALE"
    assert database.event_count(event_type="supervisor.started") == 0
    assert database.event_count(event_type="runtime.control.resolved") == 1


def test_stale_stop_request_does_not_stop_live_owner(tmp_path):
    database = _database(tmp_path)
    supervisor = Supervisor(database, tmp_path / "EMERGENCY_STOP", instance_id="stale-stop-owner")
    assert supervisor.start() == "RUNNING"
    _append_stale_request(database, action="STOP", request_id="stale-stop")

    assert supervisor.heartbeat() == "RUNNING"
    state = inspect_control(database)
    assert state.status == "RUNNING"
    assert state.request_status == "STALE"
    assert supervisor.started is True
    assert database.event_count(event_type="supervisor.stopped") == 0
    supervisor.stop()
    assert inspect_control(database).status == "STOPPED"


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
    # The replacement instance must not re-apply the same durable RESTART on
    # its first heartbeat, or the repeating scheduler can never stay healthy.
    assert second.heartbeat() == "RUNNING"
    assert second.started is True
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
