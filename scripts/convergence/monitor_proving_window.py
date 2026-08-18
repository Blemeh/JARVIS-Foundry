"""Read-only, restart-safe proving-window checkpoint monitor.

The monitor never appends to the Foundry ledger and never treats a heartbeat
as proof of a completed proving window.  A caller supplies an explicit,
release-bound manifest with the real start time; every invocation reads the
database through SQLite's read-only URI and atomically replaces one JSON
checkpoint.  This keeps elapsed-time work owned by the program rather than by
the current chat while preserving honest WAITING states.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_MANIFEST_FIELDS = (
    "schema",
    "window_id",
    "release_source_commit",
    "release_pointer",
    "started_at",
    "required_duration_seconds",
    "max_heartbeat_gap_seconds",
)
EXPECTED_MANIFEST_SCHEMA = "jarvis-proving-window-manifest-v1"
CHECKPOINT_SCHEMA = "jarvis-proving-window-checkpoint-v1"


class ProvingWindowError(ValueError):
    """The manifest or read-only runtime evidence is unusable."""


def _parse_utc(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProvingWindowError(f"invalid UTC timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ProvingWindowError(f"timestamp must include timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvingWindowError(f"cannot read proving manifest {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise ProvingWindowError("proving manifest must be a JSON object")
    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in manifest]
    if missing:
        raise ProvingWindowError(f"manifest missing required fields: {missing}")
    if manifest["schema"] != EXPECTED_MANIFEST_SCHEMA:
        raise ProvingWindowError(
            f"unsupported proving manifest schema: {manifest['schema']!r}")
    if not str(manifest["window_id"]).strip():
        raise ProvingWindowError("window_id must be non-empty")
    if not str(manifest["release_source_commit"]).strip():
        raise ProvingWindowError("release_source_commit must be non-empty")
    if not str(manifest["release_pointer"]).strip():
        raise ProvingWindowError("release_pointer must be non-empty")
    _parse_utc(str(manifest["started_at"]))
    for field in ("required_duration_seconds", "max_heartbeat_gap_seconds"):
        try:
            value = float(manifest[field])
        except (TypeError, ValueError) as exc:
            raise ProvingWindowError(f"manifest field {field} must be numeric") from exc
        if value <= 0:
            raise ProvingWindowError(f"manifest field {field} must be positive")
    return manifest


def _connect_read_only(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise ProvingWindowError(f"database does not exist: {database}")
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True, timeout=2,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
    }


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _heartbeat_snapshot(
    connection: sqlite3.Connection,
    *,
    started_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT sequence, occurred_at, source_id, payload_json "
        "FROM events WHERE event_type='supervisor.heartbeat' "
        "ORDER BY sequence").fetchall()
    timestamps: list[tuple[datetime, str]] = []
    malformed = 0
    for row in rows:
        try:
            occurred = _parse_utc(str(row["occurred_at"]))
        except ProvingWindowError:
            malformed += 1
            continue
        if started_at <= occurred <= now:
            timestamps.append((occurred, str(row["source_id"])))
    gaps = [
        (right[0] - left[0]).total_seconds()
        for left, right in zip(timestamps, timestamps[1:])
    ]
    return {
        "count": len(timestamps),
        "first_at": timestamps[0][0].isoformat() if timestamps else None,
        "last_at": timestamps[-1][0].isoformat() if timestamps else None,
        "last_source_id": timestamps[-1][1] if timestamps else None,
        "max_gap_seconds": max(gaps, default=None),
        "malformed_events_ignored": malformed,
        "source_ids": sorted({source for _when, source in timestamps}),
    }


def read_checkpoint(
    database: str | Path,
    manifest: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one honest checkpoint without changing the database."""
    database_path = Path(database).expanduser().resolve()
    current = (now or _utc_now()).astimezone(timezone.utc)
    started = _parse_utc(str(manifest["started_at"]))
    if current < started:
        raise ProvingWindowError("checkpoint time precedes proving-window start")
    required = float(manifest["required_duration_seconds"])
    max_gap_allowed = float(manifest["max_heartbeat_gap_seconds"])
    connection = _connect_read_only(database_path)
    try:
        tables = _table_names(connection)
        required_tables = {"events", "supervisor_state", "schema_migrations"}
        missing_tables = sorted(required_tables - tables)
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        event_head = int(connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()[0]) \
            if "events" in tables else 0
        schema_row = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone() if "schema_migrations" in tables else None
        supervisor_row = connection.execute(
            "SELECT * FROM supervisor_state WHERE singleton=1"
        ).fetchone() if "supervisor_state" in tables else None
        heartbeat = _heartbeat_snapshot(
            connection, started_at=started, now=current) \
            if "events" in tables else {
                "count": 0, "first_at": None, "last_at": None,
                "last_source_id": None, "max_gap_seconds": None,
                "malformed_events_ignored": 0, "source_ids": [],
            }
        stop_rows = connection.execute(
            "SELECT directive_id, command_type, target, active, updated_at "
            "FROM directives WHERE active=1 AND command_type IN "
            "('STOP_EVERYTHING','PAUSE','VETO') ORDER BY updated_at DESC"
        ).fetchall() if "directives" in tables else []
        lease_row = connection.execute(
            "SELECT * FROM leases WHERE lease_key='supervisor:singleton'"
        ).fetchone() if "leases" in tables else None
    finally:
        connection.close()

    elapsed = (current - started).total_seconds()
    remaining = max(0.0, required - elapsed)
    heartbeat_gap = heartbeat["max_gap_seconds"]
    supervisor_status = supervisor_row["status"] if supervisor_row is not None else None
    integrity_ok = quick_check == "ok" and not missing_tables
    heartbeat_ok = (
        heartbeat["count"] > 0 and
        (heartbeat_gap is None or heartbeat_gap <= max_gap_allowed)
    )
    stop_active = bool(stop_rows)
    if not integrity_ok:
        status = "BLOCKED_INTEGRITY"
    elif stop_active:
        status = "WAITING_OWNER_STOP"
    elif supervisor_status != "RUNNING":
        status = "WAITING_SUPERVISOR"
    elif not heartbeat_ok:
        status = "WAITING_HEARTBEAT"
    elif remaining > 0:
        status = "WAITING_ELAPSED"
    else:
        status = "READY_TO_CLOSE"

    return {
        "schema": CHECKPOINT_SCHEMA,
        "recorded_at": current.isoformat(),
        "database": str(database_path),
        "manifest_hash": _canonical_hash(manifest),
        "manifest": manifest,
        "status": status,
        "window": {
            "started_at": started.isoformat(),
            "now": current.isoformat(),
            "elapsed_seconds": round(elapsed, 3),
            "required_duration_seconds": required,
            "remaining_seconds": round(remaining, 3),
            "max_heartbeat_gap_allowed_seconds": max_gap_allowed,
        },
        "runtime": {
            "sqlite_quick_check": quick_check,
            "schema_version": schema_row[0] if schema_row else None,
            "event_head": event_head,
            "supervisor_state": _row_dict(supervisor_row),
            "supervisor_lease": _row_dict(lease_row),
            "active_stop_directives": [dict(row) for row in stop_rows],
            "required_tables_present": not missing_tables,
            "missing_required_tables": missing_tables,
        },
        "heartbeat": heartbeat,
        "evidence_limits": {
            "external_provider_effects": "NOT_TESTED",
            "voice_provider": "NOT_TESTED",
            "physical_host_faults": "NOT_TESTED",
            "powered_off_intervals": manifest.get(
                "powered_off_intervals", "NOT_CAPTURED"),
            "claims_external_effects": False,
        },
    }


def write_checkpoint(path: str | Path, checkpoint: dict[str, Any]) -> Path:
    """Atomically persist one checkpoint so restart/compaction cannot tear it."""
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(checkpoint, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now", help="UTC ISO timestamp for deterministic checks")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        now = _parse_utc(args.now) if args.now else None
        checkpoint = read_checkpoint(args.database, manifest, now=now)
        write_checkpoint(args.output, checkpoint)
    except (OSError, sqlite3.Error, ProvingWindowError) as exc:
        print(json.dumps({"schema": CHECKPOINT_SCHEMA, "status": "BLOCKED_MONITOR", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": checkpoint["status"], "output": str(args.output.resolve()), "event_head": checkpoint["runtime"]["event_head"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
