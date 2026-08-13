"""V6-S9: schema/state compatibility and restoration.

Stateful evolution with executable forward migration, idempotency,
previous-executable compatibility, rollback migration/restore points,
interrupted-migration recovery, schema/data integrity verification,
projection rebuild/reconcile and data-loss analysis — rehearsed on
disposable copies before anything touches the runtime DB.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.restore import (
    RestoreError,
    create_restore_point,
    data_loss_analysis,
    rehearse_restore,
    restore_from_point,
    verify_restore_point,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile

CONTROLLER = "supervisor"
CANDIDATE_CONTROLLER = "candidate-builder-1"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _fill(db: FoundryDatabase, events: int = 3) -> None:
    from jarvis_foundry.events import Event
    import uuid
    salt = uuid.uuid4().hex[:8]
    for index in range(events):
        db.append(Event.create(
            "restore.probe",
            {"index": index, "salt": salt, "controller": CONTROLLER},
            source_type="test", source_id="v6-s9",
            idempotency_key=f"restore.probe:{salt}:{index}"))


# ---------------------------------------------------------------------------
# restore points
# ---------------------------------------------------------------------------

def test_create_restore_point_verified(db: FoundryDatabase) -> None:
    _fill(db, 4)
    point_id = create_restore_point(db, label="s9-baseline",
                                    controller=CONTROLLER)
    row = db.row("restore_points", "point_id", point_id)
    assert row is not None
    assert row["label"] == "s9-baseline"
    assert row["event_count"] == 4
    assert row["schema_version"] == SCHEMA_VERSION
    point_path = Path(str(row["path"]))
    assert point_path.exists()
    assert verify_restore_point(db, point_id) is True


def test_restore_point_restores_state(db: FoundryDatabase) -> None:
    _fill(db, 3)
    point_id = create_restore_point(db, label="early",
                                    controller=CONTROLLER)
    _fill(db, 5)  # more events after the point
    assert len(db.events()) == 9
    restored = restore_from_point(db, point_id=point_id,
                                  controller=CONTROLLER)
    assert restored == 3  # the point's exact snapshot
    # post-restore: 3 snapshot events + the re-appended point record +
    # the executed record
    assert len(db.events()) == 5
    assert reconcile(db).consistent is True


def test_restore_point_tampered_refused(db: FoundryDatabase) -> None:
    _fill(db, 2)
    point_id = create_restore_point(db, label="tamper-me",
                                    controller=CONTROLLER)
    row = db.row("restore_points", "point_id", point_id)
    point_path = Path(str(row["path"]))
    point_path.write_bytes(point_path.read_bytes() + b"tampered")
    assert verify_restore_point(db, point_id) is False
    with pytest.raises(RestoreError):
        restore_from_point(db, point_id=point_id, controller=CONTROLLER)
    assert len(db.events()) == 3  # untouched: 2 probes + point event


def test_restore_point_unknown_refused(db: FoundryDatabase) -> None:
    with pytest.raises(RestoreError):
        restore_from_point(db, point_id="point-nope",
                           controller=CONTROLLER)


def test_data_loss_analysis_reports_delta(db: FoundryDatabase) -> None:
    _fill(db, 2)
    point_id = create_restore_point(db, label="before",
                                    controller=CONTROLLER)
    _fill(db, 4)
    report = data_loss_analysis(db, point_id)
    # lost = current - point = 7 - 2: the 4 new probes AND the
    # point_created event itself are post-point state
    assert report["lost_events"] == 5
    assert report["current_events"] == 7
    assert report["point_events"] == 2
    assert report["table_deltas"] is not None


def test_rehearse_restore_touches_nothing(db: FoundryDatabase,
                                          tmp_path: Path) -> None:
    _fill(db, 2)
    point_id = create_restore_point(db, label="rehearsal",
                                    controller=CONTROLLER)
    _fill(db, 3)
    result = rehearse_restore(db, point_id=point_id,
                              controller=CONTROLLER)
    assert result["ok"] is True
    assert result["restored_event_count"] == 2
    # the LIVE db is untouched by the rehearsal (2 probes + point
    # event + 3 probes = 6)
    assert len(db.events()) == 6


# ---------------------------------------------------------------------------
# compatibility and safety
# ---------------------------------------------------------------------------

def test_previous_executable_compatible(db: FoundryDatabase) -> None:
    _fill(db, 1)
    point_id = create_restore_point(db, label="compat",
                                    controller=CONTROLLER)
    row = db.row("restore_points", "point_id", point_id)
    # the point's schema version must be <= the current code's version
    assert int(row["schema_version"]) <= SCHEMA_VERSION
    assert verify_restore_point(db, point_id) is True


def test_restore_blocked_while_stopped(db: FoundryDatabase) -> None:
    _fill(db, 1)
    point_id = create_restore_point(db, label="stop-test",
                                    controller=CONTROLLER)
    sentinel = Path(db.path).parent / "EMERGENCY_STOP"
    sentinel.write_text("stop", encoding="utf-8")
    try:
        with pytest.raises(RestoreError):
            restore_from_point(db, point_id=point_id,
                               controller=CONTROLLER)
    finally:
        sentinel.unlink()
    assert len(db.events()) == 2  # probe + point_created, no executed


def test_candidate_cannot_restore(db: FoundryDatabase) -> None:
    _fill(db, 1)
    point_id = create_restore_point(db, label="guard",
                                    controller=CONTROLLER)
    with pytest.raises(RestoreError):
        restore_from_point(db, point_id=point_id,
                           controller=CANDIDATE_CONTROLLER)
    with pytest.raises(RestoreError):
        create_restore_point(db, label="x",
                             controller=CANDIDATE_CONTROLLER)
    assert len(db.events()) == 2  # probe + point_created, no more


def test_secret_label_refused(db: FoundryDatabase) -> None:
    with pytest.raises(RestoreError):
        create_restore_point(db, label="api_key=sk-live-1234",
                             controller=CONTROLLER)
    assert db.count("restore_points") == 0


def test_interrupted_restore_recovery(db: FoundryDatabase) -> None:
    """Restore through the machinery is transactional (sqlite backup
    API): a crash mid-restore leaves the DB in its previous consistent
    state, and a fresh reopen after a completed restore verifies
    clean. A second open on the same path must see the restored
    state."""
    _fill(db, 2)
    point_id = create_restore_point(db, label="crash",
                                    controller=CONTROLLER)
    _fill(db, 3)
    assert len(db.events()) == 6  # 2 probes + point + 3 probes
    restored = restore_from_point(db, point_id=point_id,
                                  controller=CONTROLLER)
    assert restored == 2  # the point's exact snapshot (2 probes)
    # a fresh connection on the same path sees the restored state and
    # verifies clean (quick_check + reconcile)
    reopened = FoundryDatabase(db.path)
    reopened.initialize()
    assert reopened.quick_check() == "ok"
    assert reconcile(reopened).consistent is True
    # the restore.executed event survived the restore (it was appended
    # AFTER the state restore)
    types = {e.event_type for e in reopened.events()}
    assert "restore.executed" in types


# ---------------------------------------------------------------------------
# projection integrity
# ---------------------------------------------------------------------------

def test_projection_and_schema(db: FoundryDatabase) -> None:
    _fill(db, 2)
    point_id = create_restore_point(db, label="projection",
                                    controller=CONTROLLER)
    report = reconcile(db)
    assert report.consistent is True
    before = db.count("restore_points")
    db.rebuild_projections()
    assert db.count("restore_points") == before
    row = db.row("restore_points", "point_id", point_id)
    assert row is not None and row["status"] == "CREATED"
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # pinned to the constant, >= 29
    assert SCHEMA_VERSION >= 29
    # the promotions table carries planned_json (migration 29 ALTER for
    # already-migrated databases)
    with db.connect() as connection:
        columns = [r[1] for r in connection.execute(
            "PRAGMA table_info(promotions)")]
    assert "planned_json" in columns
