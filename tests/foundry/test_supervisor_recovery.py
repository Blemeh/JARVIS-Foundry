"""WP2 supervisor recovery tests: forced process death reconstruction and
degraded startup on inconsistent state."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.reconcile import reconcile
from jarvis_foundry.supervisor.service import Supervisor

FIXTURE = Path(__file__).resolve().parent / "supervisor_fixture.py"

MARKER_TIMEOUT_SECONDS = 30


def _spawn_and_kill(database_path: Path, stop_path: Path, marker: Path, lease_seconds: int) -> int:
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE), str(database_path), str(stop_path), str(marker), str(lease_seconds)],
        cwd=str(FIXTURE.parent),
    )
    deadline = time.monotonic() + MARKER_TIMEOUT_SECONDS
    while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if not marker.exists():
        process.kill()
        process.wait(timeout=10)
        raise AssertionError("fixture supervisor never reached the live state")
    process.kill()  # TerminateProcess — no clean shutdown
    process.wait(timeout=15)
    return process.returncode


def test_forced_death_reconstruction_creates_no_duplicates(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": "mission-1", "objective": "survives supervisor death",
            "resource_id": None, "state": "PLANNED",
            "acceptance_criteria": ["no duplicate after reconstruction"],
        },
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-1",
    ))
    stop_path = tmp_path / "EMERGENCY_STOP"
    marker = tmp_path / "supervisor-live.marker"

    returncode = _spawn_and_kill(database.path, stop_path, marker, lease_seconds=2)
    assert returncode != 0

    # The dead supervisor's singleton lease expires after 2 seconds; a fresh
    # supervisor must wait for expiry and reconstruct without duplicating work.
    time.sleep(2.5)

    fresh = Supervisor(database, stop_path, instance_id="fresh-supervisor")
    assert fresh.start() == "RUNNING"

    events = database.events()
    event_types = [event.event_type for event in events]
    assert event_types.count("mission.created") == 1
    assert event_types.count("supervisor.started") == 2  # dead instance + fresh instance
    assert database.row("missions", "mission_id", "mission-1")["state"] == "PLANNED"
    report = reconcile(database)
    assert report.consistent is True, report.to_json()
    assert database.quick_check() == "ok"
    fresh.stop()


def test_forced_death_lease_is_reclaimed_once(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    stop_path = tmp_path / "EMERGENCY_STOP"
    marker = tmp_path / "supervisor-live-2.marker"

    _spawn_and_kill(database.path, stop_path, marker, lease_seconds=1)
    time.sleep(1.5)  # lease expires

    first = Supervisor(database, stop_path, instance_id="reclaimer-1")
    second = Supervisor(database, stop_path, instance_id="reclaimer-2")
    assert first.start() == "RUNNING"
    # A second live supervisor cannot take the lease while reclaimer-1 holds it.
    from jarvis_foundry.supervisor.leases import LeaseUnavailable

    try:
        second.leases.acquire(Supervisor.LEASE_KEY, "reclaimer-2", 90)
        raised = False
    except LeaseUnavailable:
        raised = True
    assert raised is True
    first.stop()


def test_degraded_start_on_inconsistent_state_and_repair(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": "mission-1", "objective": "drift detection",
            "resource_id": None, "state": "PLANNED",
            "acceptance_criteria": ["detected"],
        },
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-1",
    ))
    with database.transaction() as connection:
        connection.execute(
            "UPDATE missions SET state = 'COMPLETE' WHERE mission_id = 'mission-1'"
        )
    stop_path = tmp_path / "EMERGENCY_STOP"

    supervisor = Supervisor(database, stop_path, instance_id="degraded-check")
    assert supervisor.start() == "DEGRADED"
    assert supervisor.started is False
    assert any(event.event_type == "supervisor.degraded" for event in database.events())

    # Repair: rebuild projections from the verified ledger, then start cleanly.
    assert database.rebuild_projections() == 2  # mission.created + supervisor.degraded
    repaired = Supervisor(database, stop_path, instance_id="repaired-check")
    assert repaired.start() == "RUNNING"
    assert reconcile(database).consistent is True
    repaired.stop()


def test_doctor_reports_workers_and_reconciliation(tmp_path):
    import sys

    database = FoundryDatabase(tmp_path / "foundry.db")
    stop_path = tmp_path / "EMERGENCY_STOP"
    supervisor = Supervisor(database, stop_path, instance_id="doctor-check")
    supervisor.start()
    supervisor.workers.spawn(
        "run-1", [sys.executable, "-c", "import time; time.sleep(120)"], cwd=tmp_path
    )

    report = supervisor.doctor()

    assert report["database_quick_check"] == "ok"
    assert report["hermes_required_for_supervisor"] is False
    assert report["reconciliation"]["consistent"] is True
    assert "run-1" in report["active_workers"]
    supervisor.stop()
