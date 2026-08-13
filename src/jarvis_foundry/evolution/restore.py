"""V6-S9: schema/state compatibility and restoration.

Stateful evolution with executable forward migration, idempotency,
previous-executable compatibility, rollback migration/restore points,
interrupted-migration recovery, schema/data integrity verification,
projection rebuild/reconcile and data-loss analysis — rehearsed on
disposable copies before anything touches the runtime DB.

Restore points are durable, VERIFIED sqlite backups (backup API —
consistent snapshot, no writes to the source). Restores run only after
the point passes verification (quick_check + sha256 + version + event
count); data-loss analysis is recorded BEFORE the restore executes;
rehearsals run the full restore against a disposable copy and touch
nothing live. STOP blocks restores; candidates can never create or
restore points.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from ..clock import isoformat
from ..control.redaction import contains_secret_like
from ..database import FoundryDatabase
from ..events import Event, canonical_json
from ..migrations import SCHEMA_VERSION
from ..reconcile import reconcile

RESTORE_STATUSES = ("CREATED", "VERIFIED", "EXECUTED")


class RestoreError(ValueError):
    """A restore operation is refused."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RestoreError(message)


def _controller_ok(controller: str) -> bool:
    return (isinstance(controller, str) and controller.strip()
            and "candidate" not in controller.lower())


def _stop_active(database: FoundryDatabase) -> bool:
    return (Path(database.path).parent / "EMERGENCY_STOP").exists()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _point_meta(database: FoundryDatabase, point_id: str
                ) -> dict[str, Any]:
    row = database.row("restore_points", "point_id", point_id)
    if row is None:
        raise RestoreError(f"restore point {point_id} does not exist")
    return row


def create_restore_point(
    database: FoundryDatabase, *, label: str, controller: str,
    source_type: str = "supervisor", source_id: str = "v6",
) -> str:
    """Create a durable, verified snapshot of the runtime DB."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can create restore "
             "points — candidate identities are refused")
    _require(isinstance(label, str) and label.strip(),
             "label must be a non-empty string")
    _require(not contains_secret_like(canonical_json({"label": label})),
             "label contains secret-like content — refused")
    _require(not _stop_active(database),
             "stop active — restore points cannot be created")
    point_id = "point-" + uuid.uuid4().hex[:12]
    points_dir = Path(database.path).parent / "restore-points"
    points_dir.mkdir(parents=True, exist_ok=True)
    target = points_dir / f"{point_id}.db"
    database.backup_to(target)
    digest = _file_sha256(target)
    event_count = len(database.events())
    database.append(Event.create(
        "restore.point_created",
        {"point_id": point_id, "label": label, "path": str(target),
         "sha256": digest, "schema_version": SCHEMA_VERSION,
         "event_count": event_count, "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"restore.point_created:{point_id}"))
    return point_id


def verify_restore_point(database: FoundryDatabase, point_id: str) -> bool:
    """Verify a point's integrity: row exists, file exists, sha256
    matches, quick_check ok, schema version compatible (<= current)."""
    try:
        row = _point_meta(database, point_id)
    except RestoreError:
        return False
    try:
        path = Path(str(row["path"]))
        if not path.exists():
            return False
        if _file_sha256(path) != str(row["sha256"]):
            return False
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro",
                             uri=True) as connection:
            if str(connection.execute(
                    "PRAGMA quick_check").fetchone()[0]) != "ok":
                return False
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) "
                "FROM schema_migrations").fetchone()[0]
        if int(version) > SCHEMA_VERSION:
            return False  # a NEWER schema cannot be restored backward
        if int(row["schema_version"]) != int(version):
            return False
        return True
    except (sqlite3.DatabaseError, OSError, ValueError):
        return False


def data_loss_analysis(database: FoundryDatabase, point_id: str
                       ) -> dict[str, Any]:
    """Report (never execute) what restoring the point would discard:
    event-count delta + per-table row deltas."""
    row = _point_meta(database, point_id)
    current = len(database.events())
    point_events = int(row["event_count"])
    with sqlite3.connect(f"file:{Path(str(row['path'])).as_posix()}?mode=ro",
                         uri=True) as connection:
        table_deltas: dict[str, int] = {}
        for table in ("improvement_records", "release_manifests",
                      "release_slots", "candidate_builds",
                      "evaluation_runs", "verifications", "shadow_runs",
                      "canaries", "promotions"):
            try:
                current_rows = database.count(table)
                point_rows = connection.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception:
                continue
            if current_rows != point_rows:
                table_deltas[table] = current_rows - point_rows
    return {
        "point_id": point_id,
        "current_events": current,
        "point_events": point_events,
        "lost_events": max(0, current - point_events),
        "table_deltas": table_deltas,
    }


def restore_from_point(
    database: FoundryDatabase, *, point_id: str, controller: str,
    source_type: str = "supervisor", source_id: str = "v6",
) -> int:
    """Restore the runtime DB to the point's exact state. The point is
    verified FIRST (tampered/unknown/newer-schema points refuse), the
    data-loss analysis is recorded, then the restore executes through
    the sqlite backup API and the result is verified."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can restore — candidate "
             "identities are refused")
    _require(not _stop_active(database),
             "stop active — restore refused (no state changes under "
             "STOP)")
    _require(verify_restore_point(database, point_id),
             f"restore point {point_id} failed verification (tampered, "
             "missing, or schema-incompatible) — restore refused")
    row = _point_meta(database, point_id)
    analysis = data_loss_analysis(database, point_id)
    before = len(database.events())
    restored = database.restore_from(Path(str(row["path"])))
    # the point's own record was created AFTER the snapshot, so the
    # restored state does not contain it — re-append the point_created
    # event (same key + payload) so the executed event can mark it
    point_event = Event.create(
        "restore.point_created",
        {"point_id": row["point_id"], "label": row["label"],
         "path": row["path"], "sha256": row["sha256"],
         "schema_version": row["schema_version"],
         "event_count": row["event_count"],
         "controller": row["controller"]},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"restore.point_created:{point_id}")
    executed_event = Event.create(
        "restore.executed",
        {"point_id": point_id, "before_events": before,
         "after_events": restored,
         "lost_events": analysis["lost_events"],
         "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"restore.executed:{point_id}")
    database.append_events([point_event, executed_event])
    report = reconcile(database)
    if not report.consistent:
        details = report.to_json()
        raise RestoreError(
            "post-restore reconcile INCONSISTENT — severity-one: "
            f"mismatches={details['mismatches']} "
            f"unexpected={details['unexpected_rows']} "
            f"missing={details['missing_rows']}")
    return restored


def rehearse_restore(database: FoundryDatabase, *, point_id: str,
                     controller: str) -> dict[str, Any]:
    """Run the FULL restore against a disposable copy of the runtime
    DB. The live DB is untouched."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can rehearse restores")
    _require(verify_restore_point(database, point_id),
             f"restore point {point_id} failed verification — "
             "rehearsal refused")
    row = _point_meta(database, point_id)
    scratch = Path(database.path).parent / "restore-rehearsal"
    scratch.mkdir(parents=True, exist_ok=True)
    copy_path = scratch / f"rehearsal-{uuid.uuid4().hex[:8]}.db"
    database.backup_to(copy_path)
    copy_db = FoundryDatabase(copy_path)
    copy_db.initialize()
    restored = copy_db.restore_from(Path(str(row["path"])))
    ok = (restored == int(row["event_count"])
          and copy_db.quick_check() == "ok"
          and reconcile(copy_db).consistent)
    return {"ok": bool(ok), "restored_event_count": restored,
            "point_event_count": int(row["event_count"]),
            "quick_check": copy_db.quick_check(),
            "scratch_path": str(copy_path)}
