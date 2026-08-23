from __future__ import annotations

import json

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.diagnostics.runtime import diagnostic_snapshot, derive_health
from jarvis_foundry.events import Event
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.supervisor.service import Supervisor


def _database(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_derive_health_never_defaults_unknown_to_running():
    assert derive_health(
        runtime_status="UNKNOWN",
        reconciliation_consistent=True,
        release_status="VALID",
        stop_active=False,
    )["status"] == "UNKNOWN"
    assert derive_health(
        runtime_status="RUNNING",
        reconciliation_consistent=False,
        release_status="VALID",
        stop_active=False,
    )["status"] == "DEGRADED"
    assert derive_health(
        runtime_status="RUNNING",
        reconciliation_consistent=True,
        release_status="VALID",
        stop_active=True,
    )["status"] == "BLOCKED"


def test_doctor_reports_runtime_correlation_and_health(tmp_path):
    database = _database(tmp_path)
    supervisor = Supervisor(database, tmp_path / "STOP", instance_id="diagnostic-owner")
    assert supervisor.start() == "RUNNING"
    report = supervisor.doctor()

    assert report["runtime_control"]["status"] == "RUNNING"
    assert report["health"]["status"] == "DEGRADED"
    correlation = report["diagnostic"]["correlation"]
    assert "diagnostic-owner" in correlation["process_instances"]
    assert "diagnostic-owner" in correlation["lease_owners"]
    supervisor.stop()


def test_active_stop_is_blocked_in_doctor(tmp_path):
    database = _database(tmp_path)
    stop_path = tmp_path / "STOP"
    supervisor = Supervisor(database, stop_path, instance_id="blocked-owner")
    assert supervisor.start() == "RUNNING"
    EmergencyStop(stop_path).activate("diagnostic fault")

    report = supervisor.doctor()

    assert report["health"]["status"] == "BLOCKED"
    assert report["runtime_control"]["status"] == "BLOCKED"
    supervisor.stop()


def test_diagnostic_snapshot_correlates_mission_and_omits_untrusted_fields(tmp_path):
    database = _database(tmp_path)
    database.append(Event.create(
        "delivery.outcome",
        {
            "mission_id": "mission-diagnostic",
            "action_id": "action-diagnostic",
            "provider_requested": "fake-local",
            "status": "DELIVERED",
            "secret": "api_key=must-not-appear",
        },
        source_type="delivery-boundary", source_id="fake-local",
        idempotency_key="diagnostic-delivery-event",
    ))

    snapshot = diagnostic_snapshot(database)
    serialized = json.dumps(snapshot)

    assert "mission-diagnostic" in snapshot["correlation"]["mission_ids"]
    assert "action-diagnostic" in serialized
    assert "must-not-appear" not in serialized
    assert "secret" not in serialized
