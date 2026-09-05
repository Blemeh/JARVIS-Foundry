from __future__ import annotations

from datetime import timedelta
import json

import pytest

from jarvis_foundry.clock import isoformat, utc_now
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.supervisor.leases import LeaseManager, LeaseUnavailable
from jarvis_foundry.supervisor.service import Supervisor


def test_supervisor_runs_and_freezes_without_hermes(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    stop_path = tmp_path / "EMERGENCY_STOP"
    supervisor = Supervisor(database, stop_path, instance_id="supervisor-test")

    assert supervisor.start() == "RUNNING"
    EmergencyStop(stop_path).activate("fault injection")
    assert supervisor.heartbeat() == "FROZEN"

    with database.connect() as connection:
        state = dict(connection.execute("SELECT * FROM supervisor_state WHERE singleton = 1").fetchone())
    assert state["status"] == "FROZEN"
    assert state["trust_state"] == "SUSPENDED"
    assert any(event.event_type == "emergency_stop.frozen" for event in database.events())


def test_supervisor_start_reconciles_stale_pid_ledger(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    supervisor = Supervisor(database, tmp_path / "STOP", instance_id="startup-reaper")
    ledger = supervisor.workers.pid_table_path()
    ledger.write_text(
        '{"stale-run": {"pid": -1, "run_id": "stale-run"}}', encoding="utf-8")

    assert supervisor.start() == "RUNNING"

    assert not ledger.exists()
    started = [event for event in database.events() if event.event_type == "supervisor.started"]
    assert started[-1].payload["reaped_workers"] == []


def test_second_live_supervisor_cannot_take_lease(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    first = LeaseManager(database)
    second = LeaseManager(database)
    first.acquire("supervisor:singleton", "one", 90)

    with pytest.raises(LeaseUnavailable):
        second.acquire("supervisor:singleton", "two", 90)


def test_duplicate_supervisor_launcher_exits_without_heartbeat_loop(
        tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from jarvis_foundry.supervisor import run as supervisor_run

    def unavailable(self):
        raise LeaseUnavailable("supervisor singleton already held")

    monkeypatch.setattr(supervisor_run.Supervisor, "start", unavailable)
    monkeypatch.setattr(
        "jarvis_foundry.runtime.authority.resolve_runtime_authority",
        lambda **kwargs: SimpleNamespace(
            spawn_worker_loop=False,
            worker_yaml=None,
            provider_settings=None,
        ),
    )
    code = supervisor_run.main(["--home", str(tmp_path), "--once"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload == {
        "supervisor": "LEASE_UNAVAILABLE",
        "error": "supervisor singleton already held",
    }


def test_supervisor_takeover_during_active_stop_does_not_collide(tmp_path):
    """A fresh supervisor instance taking over WHILE the STOP sentinel is
    active must be able to heartbeat (frozen watchdog) — the
    emergency_stop.frozen event is keyed per (stop_id, instance_id), so a
    takeover never collides with the previous instance's frozen record.
    (Found during V8 canonical promotion: takeover hit EventConflict and
    crashed the heartbeat loop.)"""
    database = FoundryDatabase(tmp_path / "foundry.db")
    stop_path = tmp_path / "EMERGENCY_STOP"
    EmergencyStop(stop_path).activate("takeover drill")

    first = Supervisor(database, stop_path, instance_id="supervisor-first")
    assert first.start() == "FROZEN"
    assert first.heartbeat() == "FROZEN"

    # the first instance dies without releasing anything (force-kill);
    # its lease expires after the TTL, then a NEW instance takes over
    # under the same active STOP
    from jarvis_foundry.clock import isoformat
    from datetime import timedelta
    with database.transaction() as conn:
        conn.execute(
            "UPDATE leases SET expires_at=? WHERE lease_key='supervisor:singleton'",
            (isoformat(utc_now() - timedelta(seconds=1)),))
    second = Supervisor(database, stop_path, instance_id="supervisor-second")
    assert second.start() == "FROZEN"
    assert second.heartbeat() == "FROZEN"  # must not raise EventConflict
    assert second.heartbeat() == "FROZEN"

    frozen = [e for e in database.events()
              if e.event_type == "emergency_stop.frozen"]
    assert len(frozen) >= 3  # first instance + second instance records both


def test_expired_lease_is_reclaimed_once(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    expired = isoformat(utc_now() - timedelta(seconds=1))
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO leases(lease_key, owner_id, expires_at, heartbeat_at) VALUES (?, ?, ?, ?)",
            ("step:1", "dead-worker", expired, expired),
        )
    LeaseManager(database).acquire("step:1", "fresh-worker", 90)
    assert database.row("leases", "lease_key", "step:1")["owner_id"] == "fresh-worker"


def test_unparseable_sentinel_still_stops(tmp_path):
    path = tmp_path / "EMERGENCY_STOP"
    path.write_text("not json", encoding="utf-8")
    stop = EmergencyStop(path)

    assert stop.active() is True
    assert stop.read()["stop_id"] == "unparseable-sentinel"


def test_clear_retries_transient_windows_sharing_violation(
    tmp_path, monkeypatch,
):
    path = tmp_path / "EMERGENCY_STOP"
    stop = EmergencyStop(path)
    stop.activate("concurrent readback")
    path_type = type(path)
    unlink = path_type.unlink
    attempts = 0

    def flaky_unlink(candidate, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if candidate == path and attempts < 3:
            raise PermissionError(32, "sharing violation", str(candidate))
        return unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(path_type, "unlink", flaky_unlink)

    assert stop.clear() is True
    assert attempts == 3
    assert not path.exists()


def test_incomplete_sentinel_still_stops_with_defaults(tmp_path):
    """A parseable sentinel missing fields must fail closed (adversarial
    finding: missing 'reason' crashed heartbeat with KeyError)."""
    import json as jsonlib

    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.supervisor.service import Supervisor

    path = tmp_path / "EMERGENCY_STOP"
    path.write_text(jsonlib.dumps({"stop_id": "partial"}), encoding="utf-8")
    stop = EmergencyStop(path)

    record = stop.read()
    assert record["stop_id"] == "partial"
    assert record["reason"] == "sentinel exists but is incomplete"
    assert record["source"] == "filesystem"

    # The freeze state machine must be reached, not a KeyError.
    database = FoundryDatabase(tmp_path / "foundry.db")
    supervisor = Supervisor(database, path, instance_id="incomplete-sentinel")
    assert supervisor.start() == "FROZEN"
    assert supervisor.heartbeat() == "FROZEN"
    with database.connect() as connection:
        state = dict(connection.execute("SELECT * FROM supervisor_state WHERE singleton = 1").fetchone())
    assert state["status"] == "FROZEN"
    supervisor.stop()


def test_spawn_worker_loop_defaults_off(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    supervisor = Supervisor(database, tmp_path / "STOP", instance_id="no-spawn")
    assert supervisor.spawn_worker_loop is False
    assert supervisor.start() == "RUNNING"
    assert supervisor.heartbeat() == "RUNNING"
    assert "worker-loop" not in supervisor.workers.active_runs()
    supervisor.stop()


def test_spawn_worker_loop_noops_when_yaml_disabled(tmp_path):
    config = tmp_path / "workers.yaml"
    config.write_text(
        "enabled: false\nprovider: opencode_go\nmodel: deepseek-v4-flash\n",
        encoding="utf-8")
    database = FoundryDatabase(tmp_path / "foundry.db")
    supervisor = Supervisor(
        database, tmp_path / "STOP", instance_id="disabled-loop",
        spawn_worker_loop=True, worker_loop_config=config,
    )
    assert supervisor.start() == "RUNNING"
    assert supervisor.heartbeat() == "RUNNING"
    assert "worker-loop" not in supervisor.workers.active_runs()
    supervisor.stop()


def test_spawn_worker_loop_registers_child_when_enabled(tmp_path, monkeypatch):

    config = tmp_path / "workers.yaml"
    config.write_text(
        "enabled: true\nstatus: ACTIVE\nprovider: opencode_go\n"
        "model: deepseek-v4-flash\n",
        encoding="utf-8")
    database = FoundryDatabase(tmp_path / "foundry.db")
    supervisor = Supervisor(
        database, tmp_path / "STOP", instance_id="spawn-loop",
        spawn_worker_loop=True, worker_loop_config=config,
    )
    spawned: list[list[str]] = []

    def fake_spawn(run_id, command, *, cwd=None, **kwargs):
        spawned.append(command)
        from jarvis_foundry.supervisor.worker import WorkerStatus
        return WorkerStatus(
            run_id=run_id, lease_key=f"worker:{run_id}",
            owner_id=f"worker:{run_id}", expires_at=None,
            alive=True, lease_expired=False, process=None,
        )

    monkeypatch.setattr(supervisor.workers, "spawn", fake_spawn)
    assert supervisor.start() == "RUNNING"
    assert supervisor.heartbeat() == "RUNNING"
    assert spawned
    # Production spawns the worker loop through the platform's background
    # (windowless) interpreter, NOT sys.executable — visible console windows
    # from scheduled/background workers are an owner-rejected behavior
    # (cron pause 2026-08-12). The assertion must pin production intent.
    from jarvis_foundry.platform.windows_subprocess import background_python_executable
    expected_python = background_python_executable()
    assert spawned[0][0] == expected_python, spawned[0]
    assert spawned[0][1:3] == ["-m", "jarvis_foundry.runtime.worker_loop"]
    assert "--once" in spawned[0]
    supervisor.stop()


def test_spawn_worker_loop_bad_yaml_does_not_crash_heartbeat(tmp_path):
    config = tmp_path / "workers.yaml"
    config.write_text("- not-a-mapping\n", encoding="utf-8")
    database = FoundryDatabase(tmp_path / "foundry.db")
    supervisor = Supervisor(
        database, tmp_path / "STOP", instance_id="bad-yaml",
        spawn_worker_loop=True, worker_loop_config=config,
    )
    assert supervisor.start() == "RUNNING"
    assert supervisor.heartbeat() == "RUNNING"
    blocked = [event for event in database.events()
               if event.event_type == "worker_loop.activation_blocked"]
    assert len(blocked) == 1
    assert blocked[0].payload["phase"] == "configuration"
    supervisor.stop()

