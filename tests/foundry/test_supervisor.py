from __future__ import annotations

from datetime import timedelta

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


def test_second_live_supervisor_cannot_take_lease(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    first = LeaseManager(database)
    second = LeaseManager(database)
    first.acquire("supervisor:singleton", "one", 90)

    with pytest.raises(LeaseUnavailable):
        second.acquire("supervisor:singleton", "two", 90)


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

