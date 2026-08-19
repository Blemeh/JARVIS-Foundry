"""Run a disposable worker-loop deployment and restore rehearsal.

The command is deliberately narrower than live deployment.  It opens the
source database through SQLite's read-only URI, backs it up into a new canary
home, migrates only that copy, runs the canonical worker and Supervisor smoke
paths, and restores the canary into another new home.  Existing targets and
paths overlapping the source database home are rejected.  No scheduler task,
provider, credential, or live database write is reachable from this command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _logical_sha256(path: Path) -> str:
    """Hash a consistent SQLite snapshot rather than a WAL-sensitive file."""
    source = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    snapshot = sqlite3.connect(":memory:")
    try:
        source.execute("PRAGMA query_only=ON")
        source.backup(snapshot)
        return hashlib.sha256(snapshot.serialize()).hexdigest().upper()
    finally:
        snapshot.close()
        source.close()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _overlaps(left: Path, right: Path) -> bool:
    return _inside(left, right) or _inside(right, left)


def _safe_target(path: Path, live_home: Path, label: str) -> None:
    if _overlaps(path, live_home):
        raise ValueError(f"{label} overlaps the live database home: {path}")
    if path.exists():
        raise FileExistsError(f"{label} already exists; refusing overwrite: {path}")


@contextmanager
def _target_lock(canary_home: Path) -> Iterator[dict[str, Any]]:
    """Serialize attempts to use the same disposable canary target.

    The lock is created beside (not inside) the target so two processes racing
    before target creation cannot both mutate the same backup.  A stale lock
    fails closed; the caller can choose a fresh target rather than deleting
    another process's evidence.
    """
    lock_path = canary_home.with_name(f".{canary_home.name}.lock")
    descriptor: int | None = None
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
    except FileExistsError as error:
        raise RuntimeError(f"canary target is busy: {lock_path}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    lock = {"path": str(lock_path), "acquired": True}
    try:
        yield lock
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _sanitize_supervisor_lease(database: Any) -> dict[str, Any]:
    """Remove only the copied Supervisor singleton lease from the canary.

    A live snapshot legitimately contains this ephemeral lease.  Releasing it
    through the canonical LeaseManager on the disposable database lets the
    smoke Supervisor acquire its own lease without touching live state.
    """
    from jarvis_foundry.supervisor.leases import LeaseManager

    lease_key = "supervisor:singleton"
    row = database.row("leases", "lease_key", lease_key)
    if not row:
        return {"lease_key": lease_key, "present": False, "removed": False}
    owner_id = str(row["owner_id"])
    removed = LeaseManager(database).release(lease_key, owner_id)
    return {
        "lease_key": lease_key,
        "present": True,
        "owner_id": owner_id,
        "removed": removed,
        "scope": "canary_only",
    }


def _git_head(source_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _backup_read_only(source: Path, target: Path) -> dict[str, Any]:
    source_connection = sqlite3.connect(
        f"file:{source.as_posix()}?mode=ro", uri=True, timeout=10)
    source_connection.row_factory = sqlite3.Row
    source_connection.execute("PRAGMA query_only=ON")
    destination = sqlite3.connect(target)
    try:
        source_connection.backup(destination)
        destination.commit()
        quick_check = destination.execute("PRAGMA quick_check").fetchone()[0]
        schema = destination.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]
        event_count = destination.execute(
            "SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        destination.close()
        source_connection.close()
    return {
        "quick_check": quick_check,
        "schema": int(schema),
        "event_count": int(event_count),
    }


def _table_present(database: Any, table: str) -> bool:
    with database.connect(readonly=True) as connection:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None


def _event_count(database: Any) -> int:
    with database.connect(readonly=True) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])


def _supervisor_smoke(source_root: Path, canary_home: Path,
                      python_executable: Path, timeout: float) -> dict[str, Any]:
    command = [
        str(python_executable), "-m", "jarvis_foundry.supervisor.run",
        "--home", str(canary_home), "--once",
    ]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(source_root / "src")
    process = subprocess.run(
        command, cwd=source_root, env=environment,
        capture_output=True, text=True, timeout=timeout,
    )
    stdout = process.stdout.strip()
    stderr = process.stderr.strip()
    return {
        "command": " ".join(command),
        "exit": process.returncode,
        "start_state": "RUNNING" if '"supervisor": "RUNNING"' in stdout else "UNKNOWN",
        "heartbeat_state": "RUNNING" if '"heartbeat": "RUNNING"' in stdout else "UNKNOWN",
        "clean_stop": process.returncode == 0,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    """Acquire the disposable target lease before any target is created."""
    source = arguments.live_database.expanduser().resolve()
    source_root = arguments.source_root.expanduser().resolve()
    canary_home = arguments.canary_home.expanduser().resolve()
    restore_home = arguments.restore_home.expanduser().resolve()
    output = arguments.output.expanduser().resolve()
    live_home = source.parent
    if not source.is_file():
        raise FileNotFoundError(f"live database does not exist: {source}")
    if not (source_root / "src" / "jarvis_foundry").is_dir():
        raise FileNotFoundError(f"source root is not a JARVIS checkout: {source_root}")
    if _overlaps(canary_home, live_home) or _overlaps(restore_home, live_home):
        raise ValueError("canary and restore homes must not overlap the live database home")
    if _overlaps(canary_home, restore_home):
        raise ValueError("canary and restore homes must not overlap each other")
    if _overlaps(output, live_home):
        raise ValueError(f"output overlaps the live database home: {output}")
    with _target_lock(canary_home) as lock:
        return _run_unlocked(arguments, lock)


def _run_unlocked(arguments: argparse.Namespace, lock: dict[str, Any]) -> dict[str, Any]:
    source = arguments.live_database.expanduser().resolve()
    source_root = arguments.source_root.expanduser().resolve()
    canary_home = arguments.canary_home.expanduser().resolve()
    restore_home = arguments.restore_home.expanduser().resolve()
    output = arguments.output.expanduser().resolve()
    live_home = source.parent

    if not source.is_file():
        raise FileNotFoundError(f"live database does not exist: {source}")
    if not (source_root / "src" / "jarvis_foundry").is_dir():
        raise FileNotFoundError(f"source root is not a JARVIS checkout: {source_root}")
    _safe_target(canary_home, live_home, "canary home")
    _safe_target(restore_home, live_home, "restore home")
    if _overlaps(output, live_home):
        raise ValueError(f"output overlaps the live database home: {output}")
    if output.exists():
        raise FileExistsError(f"output already exists; refusing overwrite: {output}")

    canary_home.mkdir(parents=True)
    restore_home.mkdir(parents=True)
    canary_database_path = canary_home / "foundry.db"
    restore_database_path = restore_home / "foundry.db"
    backup = _backup_read_only(source, canary_database_path)
    backup_hash = _sha256(canary_database_path)

    sys.path.insert(0, str(source_root / "src"))
    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.paths import FoundryPaths
    from jarvis_foundry.reconcile import reconcile
    from jarvis_foundry.runtime.config import WorkerRuntimeConfig
    from jarvis_foundry.runtime.worker_loop import run_cycle

    canary_database = FoundryDatabase(canary_database_path)
    migrated_version = canary_database.initialize()
    canary_quick_check = canary_database.quick_check()
    contracts_present = _table_present(canary_database, "mission_step_contracts")
    migrated_reconciliation = reconcile(canary_database).to_json()

    worker_report = run_cycle(
        canary_database,
        FoundryPaths(canary_home),
        WorkerRuntimeConfig(
            enabled=True, runtime_state_root=str(canary_home / "worker-runtime")
        ),
        loop_id="deployment-canary-wait-1",
        instance_id="deployment-canary-worker-1",
    )
    worker_reconciliation = reconcile(canary_database).to_json()
    lease_sanitization = _sanitize_supervisor_lease(canary_database)
    supervisor = _supervisor_smoke(
        source_root, canary_home, arguments.python_executable, arguments.timeout)
    final_reconciliation = reconcile(canary_database).to_json()

    canary_hash_before_restore = _logical_sha256(canary_database_path)
    canary_file_hash_before_restore = _sha256(canary_database_path)
    restored_database = FoundryDatabase(restore_database_path)
    restored_database.restore_from(canary_database_path)
    canary_hash_after_restore = _logical_sha256(canary_database_path)
    canary_file_hash_after_restore = _sha256(canary_database_path)
    restored_reconciliation = reconcile(restored_database).to_json()

    checks = {
        "backup_quick_check": backup["quick_check"] == "ok",
        "schema34": migrated_version == 34 and contracts_present,
        "migration_reconciled": bool(migrated_reconciliation["consistent"]),
        "worker_waiting": worker_report.get("outcome") == "WAITING",
        "worker_reconciled": bool(worker_reconciliation["consistent"]),
        "supervisor_lease_sanitized": (
            not lease_sanitization["present"] or lease_sanitization["removed"]
        ),
        "supervisor_started": supervisor["start_state"] == "RUNNING",
        "supervisor_heartbeat": supervisor["heartbeat_state"] == "RUNNING",
        "supervisor_clean_stop": supervisor["clean_stop"],
        "final_reconciled": bool(final_reconciliation["consistent"]),
        "restore_source_unchanged": canary_hash_before_restore == canary_hash_after_restore,
        "restore_reconciled": bool(restored_reconciliation["consistent"]),
    }
    return {
        "schema": "jarvis-worker-loop-deployment-canary-v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS_DISPOSABLE" if all(checks.values()) else "FAIL_DISPOSABLE",
        "source": {
            "repository": str(source_root),
            "head": _git_head(source_root),
            "python": str(arguments.python_executable),
            "pythonpath": str(source_root / "src"),
        },
        "method": {
            "live_database": str(source),
            "live_database_open": "SQLite mode=ro",
            "canary_home": str(canary_home),
            "restore_home": str(restore_home),
            "live_database_touched": False,
            "live_supervisor_restarted": False,
            "worker_task_enabled": False,
            "provider_dispatch": "NOT_TESTED",
            "target_lock": lock,
        },
        "backup": {
            **backup,
            "sha256": backup_hash,
        },
        "migration": {
            "migrated_version": migrated_version,
            "quick_check": canary_quick_check,
            "mission_step_contracts_present": contracts_present,
            "event_count_final": _event_count(canary_database),
            "reconciliation": final_reconciliation,
        },
        "worker_loop": {
            "report": worker_report,
            "reconciliation": worker_reconciliation,
            "mission_invented": bool(worker_report.get("mission_id")),
        },
        "lease_sanitization": lease_sanitization,
        "supervisor": supervisor,
        "restore": {
            "database": str(restore_database_path),
            "source_sha256_before": canary_hash_before_restore,
            "source_sha256_after": canary_hash_after_restore,
            "source_file_sha256_before": canary_file_hash_before_restore,
            "source_file_sha256_after": canary_file_hash_after_restore,
            "source_unchanged": canary_hash_before_restore == canary_hash_after_restore,
            "schema": 34,
            "quick_check": restored_database.quick_check(),
            "event_count": _event_count(restored_database),
            "reconciliation": restored_reconciliation,
        },
        "checks": checks,
        "boundary": "Disposable rehearsal only; live migration, scheduler activation, provider dispatch, and external effects are not performed.",
    }


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-database", type=Path, required=True)
    parser.add_argument("--canary-home", type=Path, required=True)
    parser.add_argument("--restore-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=root)
    parser.add_argument("--python", dest="python_executable", type=Path,
                        default=Path(sys.executable))
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = _run(arguments)
    except Exception as error:  # noqa: BLE001 - structured boundary result
        result = {
            "schema": "jarvis-worker-loop-deployment-canary-v1",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "status": "FAIL_DISPOSABLE",
            "error_class": type(error).__name__,
            "error": str(error)[:1000],
            "live_systems_touched": False,
        }
    output = arguments.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(output)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "PASS_DISPOSABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
