"""Disposable release-candidate factory for the overnight R2/R3 gate.

This module is deliberately a factory, not a deployment command.  It creates
one run below the explicitly named overnight lane, builds immutable releases,
opens a disposable schema generation, exercises the headless Supervisor and
worker lease contracts, then proves rollback and database restoration.  It
never chooses a live default and it never installs or changes a Task Scheduler
task.

The output is a machine-readable ``run.json`` plus bounded command logs.  A
second invocation gets a new run id and leaves the first run untouched.  The
``--dry-run`` mode only validates the root and emits the planned paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

# The factory is also a directly runnable command. Make its source identity
# explicit before importing the package so an installed package from another
# worktree cannot silently perform the disposable build.
_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from jarvis_foundry.clock import isoformat, utc_now
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.layout import headless_task_command
from jarvis_foundry.deployment.release import (
    ReleaseError,
    build_release,
    migrate_clone,
    read_current,
    read_previous,
    release_authority_report,
    restore_database,
    rollback_current,
    schema_version,
    set_current,
    validate_release,
)
from jarvis_foundry.events import Event
from jarvis_foundry.installer import Installer
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.supervisor.leases import LeaseUnavailable
from jarvis_foundry.supervisor.run import _heartbeat_loop
from jarvis_foundry.supervisor.service import Supervisor
from jarvis_foundry.supervisor.worker import (
    WorkerController,
    load_adapter_pids,
    process_creation_identity,
    terminate_owned_tree,
    update_adapter_pid,
)


LANE_NAME = "JARVIS-Overnight-Release-Candidate"
DEFAULT_LANE_ROOT = Path(r"C:\temp") / LANE_NAME
ROOT_MARKER = "OVERNIGHT_FACTORY_ROOT.json"
RUN_MARKER = "RUN.json"
_HEX_RE = re.compile(r"^[0-9a-f]{40,64}$")
_SECRET_ENV = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "OPENCODE_GO_API_KEY", "LLM_API_KEY",
)


class FactoryGuardError(RuntimeError):
    """Raised when a factory action would leave the disposable lane."""


@dataclass(frozen=True)
class FactoryRoots:
    """All mutable paths owned by exactly one disposable factory run."""

    lane_root: Path
    run_id: str
    run_root: Path
    install_root: Path
    foundry_home: Path
    hermes_home: Path
    config_root: Path
    db_root: Path
    logs_root: Path
    evidence_root: Path

    @classmethod
    def for_run(cls, lane_root: Path, run_id: str) -> "FactoryRoots":
        lane = lane_root.resolve()
        run = lane / "var" / "overnight" / "release-candidate" / "runs" / run_id
        return cls(
            lane_root=lane,
            run_id=run_id,
            run_root=run,
            install_root=lane / "var" / "overnight" / "release-candidate" / "releases" / run_id,
            foundry_home=run / "state" / "foundry",
            hermes_home=run / "state" / "hermes",
            config_root=run / "state" / "config",
            db_root=run / "db",
            logs_root=run / "logs",
            evidence_root=lane / "evidence" / "release" / "overnight" / run_id,
        )

    def paths(self) -> dict[str, str]:
        return {
            key: str(value)
            for key in self.__dataclass_fields__
            if isinstance((value := getattr(self, key)), Path)
        }


@dataclass
class Check:
    id: str
    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=isoformat)
    finished_at: str | None = None


@dataclass
class FaultResult:
    id: str
    status: str
    expected: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class FactoryRecord:
    """Durable run shape written after each unit and at finalization."""

    run_id: str
    status: str
    source_repo: str
    source_sha: str
    previous_sha: str | None
    roots: dict[str, str]
    checks: list[dict[str, Any]] = field(default_factory=list)
    faults: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    cleanup: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=isoformat)
    finished_at: str | None = None


def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _git(repo: Path, *args: str, timeout: int = 30) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if result.returncode != 0:
        raise FactoryGuardError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def guard_lane_root(
    root: str | Path,
    *,
    expected_lane: str | Path | None = DEFAULT_LANE_ROOT,
    source_repo: str | Path | None = None,
) -> Path:
    """Fail closed before any directory creation or destructive operation."""
    lane = _resolve(root)
    if expected_lane is not None and lane != _resolve(expected_lane):
        raise FactoryGuardError(
            f"factory root must be the named lane {_resolve(expected_lane)}, got {lane}")
    if lane.name != LANE_NAME:
        raise FactoryGuardError(f"unexpected factory lane name: {lane.name}")
    if lane == Path(lane.anchor):
        raise FactoryGuardError("filesystem root is not a factory lane")

    live_values = {
        _resolve(r"C:\Users\b\AppData\Local\JARVIS"),
        _resolve(r"C:\Users\b\AppData\Local\hermes"),
    }
    for variable in ("JARVIS_HOME", "HERMES_HOME", "JARVIS_INSTALL_ROOT"):
        value = os.environ.get(variable)
        if value:
            live_values.add(_resolve(value))
    for live in live_values:
        if lane == live or _is_within(lane, live) or _is_within(live, lane):
            raise FactoryGuardError(f"factory lane intersects protected root: {live}")

    broad_roots = {_resolve(Path("C:/")), _resolve(r"C:\temp"), _resolve(r"C:\Users")}
    if lane in broad_roots:
        raise FactoryGuardError(f"refusing broad mutable root: {lane}")

    canonical = _resolve(source_repo) if source_repo is not None else None
    if canonical is not None:
        if canonical == lane:
            # The named lane is itself the isolated worktree.  This is the
            # expected source/worktree arrangement for this factory.
            pass
        elif _is_within(lane, canonical) or _is_within(canonical, lane):
            raise FactoryGuardError(
                f"factory lane and source repo overlap unexpectedly: {lane} / {canonical}")
    return lane


def validate_run_roots(roots: FactoryRoots) -> None:
    lane = roots.lane_root
    for name in (
        "run_root", "install_root", "foundry_home", "hermes_home",
        "config_root", "db_root", "logs_root", "evidence_root",
    ):
        path = getattr(roots, name)
        if not _is_within(path, lane):
            raise FactoryGuardError(f"{name} escapes factory lane: {path}")
    if roots.run_root.exists() and any(roots.run_root.iterdir()):
        raise FactoryGuardError(
            f"run id already contains data; choose a new run id: {roots.run_root}")


def runtime_environment(roots: FactoryRoots) -> dict[str, str]:
    """Create a provider-free environment with no inherited path authority."""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    for variable in _SECRET_ENV:
        env.pop(variable, None)
    env.update({
        "JARVIS_HOME": str(roots.foundry_home),
        "HERMES_HOME": str(roots.hermes_home),
        "JARVIS_INSTALL_ROOT": str(roots.install_root),
        "JARVIS_FACTORY_RUN_ID": roots.run_id,
        "JARVIS_FACTORY_PROVIDER_MODE": "DISABLED",
    })
    return env


class FactoryRunner:
    def __init__(self, roots: FactoryRoots, source_repo: Path, source_sha: str) -> None:
        self.roots = roots
        self.source_repo = source_repo.resolve()
        self.source_sha = source_sha
        self.previous_sha: str | None = None
        self.record = FactoryRecord(
            run_id=roots.run_id,
            status="RUNNING",
            source_repo=str(self.source_repo),
            source_sha=source_sha,
            previous_sha=None,
            roots=roots.paths(),
        )
        self._previous_worktree: Path | None = None
        self._primary_process: subprocess.Popen[Any] | None = None
        self._worker_controller: WorkerController | None = None
        self._write_record()

    @property
    def record_path(self) -> Path:
        return self.roots.evidence_root / RUN_MARKER

    def _write_record(self) -> None:
        _write_json(self.record_path, asdict(self.record))

    def check(self, check_id: str, fn: Callable[[], tuple[str, dict[str, Any]]]) -> Any:
        started = isoformat()
        try:
            detail, evidence = fn()
            item = Check(check_id, "PASS", detail, evidence, started, isoformat())
            self.record.checks.append(asdict(item))
            self._write_record()
            return evidence
        except Exception as error:  # noqa: BLE001 - record then fail closed
            item = Check(
                check_id, "FAIL", f"{type(error).__name__}: {error}",
                {"traceback": traceback.format_exc(limit=8)}, started, isoformat())
            self.record.checks.append(asdict(item))
            self._write_record()
            raise

    def fault(self, fault_id: str, fn: Callable[[], tuple[bool, str, dict[str, Any]]]) -> None:
        try:
            passed, detail, evidence = fn()
        except Exception as error:  # noqa: BLE001 - a fault probe must be durable
            passed, detail, evidence = False, f"{type(error).__name__}: {error}", {}
        item = FaultResult(
            fault_id, "PASS" if passed else "FAIL", "controlled refusal/repair", detail, evidence)
        self.record.faults.append(asdict(item))
        self._write_record()
        if not passed:
            raise FactoryGuardError(f"controlled fault did not behave as expected: {fault_id}: {detail}")

    def command(
        self,
        command: list[str],
        *,
        name: str,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(cwd or self.roots.run_root),
            env=env,
        )
        log = self.roots.logs_root / f"{name}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            json.dumps({
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "log": str(log),
        }

    def _create_previous_worktree(self) -> Path:
        assert self.previous_sha
        worktree = self.roots.run_root / "previous-source"
        result = subprocess.run(
            ["git", "-C", str(self.source_repo), "worktree", "add", "--detach",
             str(worktree), self.previous_sha],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if result.returncode != 0:
            raise FactoryGuardError(result.stderr.strip() or "previous worktree creation failed")
        self._previous_worktree = worktree
        return worktree

    def _remove_previous_worktree(self) -> None:
        if self._previous_worktree is None:
            return
        subprocess.run(
            ["git", "-C", str(self.source_repo), "worktree", "remove", "--force",
             str(self._previous_worktree)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        self._previous_worktree = None

    def build_releases(self) -> tuple[dict[str, Any], dict[str, Any]]:
        self.previous_sha = _git(self.source_repo, "rev-parse", f"{self.source_sha}^")
        self.record.previous_sha = self.previous_sha
        self._write_record()
        previous_repo = self._create_previous_worktree()
        try:
            previous = build_release(
                source_repo=previous_repo, source_sha=self.previous_sha,
                python_exe=sys.executable, root=self.roots.install_root,
            )
        finally:
            self._remove_previous_worktree()
        # Build the candidate against the validated previous release so its
        # immutable ``previous_runtime`` identity is captured in RELEASE.json.
        # This pointer is disposable factory authority and is replaced by the
        # normal CURRENT/PREVIOUS activation check later in the run.
        set_current(self.previous_sha, root=self.roots.install_root)
        candidate = build_release(
            source_repo=self.source_repo, source_sha=self.source_sha,
            python_exe=sys.executable, root=self.roots.install_root,
        )
        self.record.artifacts["previous_release"] = previous
        self.record.artifacts["candidate_release"] = candidate
        self._write_record()
        return previous, candidate

    def _candidate_python(self) -> Path:
        return self.roots.install_root / "releases" / self.source_sha / ".venv" / "Scripts" / "python.exe"

    def _previous_python(self) -> Path:
        if not self.previous_sha:
            raise FactoryGuardError("previous SHA is not known")
        return self.roots.install_root / "releases" / self.previous_sha / ".venv" / "Scripts" / "python.exe"

    def validate_release_identity(self) -> None:
        candidate_manifest = validate_release(self.source_sha, root=self.roots.install_root, require_schema_match=False)
        previous_manifest = validate_release(self.previous_sha or "", root=self.roots.install_root, require_schema_match=False)
        expected_tree = _git(self.source_repo, "rev-parse", f"{self.source_sha}^{{tree}}")
        if candidate_manifest["manifest"].get("git_tree") != expected_tree:
            raise ReleaseError("candidate manifest tree does not match Git")
        identity = self.command(
            [str(self._candidate_python()), "-c",
             "import json,jarvis_foundry,sys; print(json.dumps({'python':sys.executable,'package':jarvis_foundry.__file__}))"],
            name="candidate-import-identity", env=runtime_environment(self.roots), cwd=self.roots.foundry_home,
        )
        if identity["returncode"] != 0:
            raise FactoryGuardError("candidate import identity command failed")
        payload = json.loads(identity["stdout"].strip().splitlines()[-1])
        package_path = _resolve(payload["package"])
        candidate_dir = _resolve(self.roots.install_root / "releases" / self.source_sha)
        source_package = self.source_repo / "src"
        if not _is_within(package_path, candidate_dir) or _is_within(package_path, source_package):
            raise FactoryGuardError(f"candidate imported from unexpected path: {package_path}")
        self.record.artifacts["identity"] = {
            "candidate": candidate_manifest,
            "previous": previous_manifest,
            "expected_tree": expected_tree,
            "import": payload,
        }
        self._write_record()

    def _validate_candidate_manifest_against_git(self) -> None:
        """Validate the manifest field that ``validate_release`` cannot
        resolve without a source repository: the exact Git tree object."""
        manifest = validate_release(
            self.source_sha, root=self.roots.install_root, require_schema_match=False,
        )["manifest"]
        expected_tree = _git(self.source_repo, "rev-parse", f"{self.source_sha}^{{tree}}")
        if manifest.get("git_tree") != expected_tree:
            raise ReleaseError("RELEASE.json git_tree does not match source Git tree")

    def set_authority(self) -> None:
        if not self.previous_sha:
            raise FactoryGuardError("previous release is missing")
        set_current(self.previous_sha, root=self.roots.install_root)
        set_current(self.source_sha, root=self.roots.install_root)
        if read_current(self.roots.install_root) != self.source_sha:
            raise FactoryGuardError("CURRENT did not point to candidate")
        if read_previous(self.roots.install_root) != self.previous_sha:
            raise FactoryGuardError("PREVIOUS did not point to prior release")
        self.record.artifacts["authority_before_runtime"] = {
            "current": read_current(self.roots.install_root),
            "previous": read_previous(self.roots.install_root),
        }
        self._write_record()

    def prepare_database(self) -> None:
        database = FoundryDatabase(self.roots.foundry_home / "foundry.db")
        version = database.initialize(target=34)
        database.append(Event.create(
            "factory.baseline",
            {"run_id": self.roots.run_id, "trust_state": "LAB", "schema": version},
            source_type="factory", source_id=self.roots.run_id,
            idempotency_key=f"factory.baseline:{self.roots.run_id}",
        ))
        backup = self.roots.db_root / "foundry-v34-backup.db"
        database.backup_to(backup)
        if schema_version(database.path) != 34:
            raise FactoryGuardError("baseline database is not schema v34")

        interruption = self.roots.db_root / "migration-interruption.db"
        database.backup_to(interruption)
        probe = FoundryDatabase(interruption)
        if probe.initialize(target=35) != 35:
            raise FactoryGuardError("migration checkpoint v35 was not durable")
        if probe.initialize(target=36) != 36:
            raise FactoryGuardError("migration checkpoint v36 was not durable")
        if probe.quick_check() != "ok":
            raise FactoryGuardError("interruption probe failed quick_check")

        if database.initialize(target=35) != 35:
            raise FactoryGuardError("runtime database did not reach v35")
        if database.initialize(target=SCHEMA_VERSION) != SCHEMA_VERSION:
            raise FactoryGuardError("runtime database did not reach package schema")
        with database.connect(readonly=True) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        required = {"knowledge_claim_evidence", "candidate_outcomes", "discovery_portfolio"}
        if not required.issubset(tables):
            raise FactoryGuardError(f"migration markers missing: {sorted(required - tables)}")
        self.record.artifacts["database"] = {
            "path": str(database.path),
            "v34_backup": str(backup),
            "v34_backup_sha256": _sha256(backup),
            "schema": schema_version(database.path),
            "event_count": database.event_count(readonly=True),
            "required_tables": sorted(required),
            "migration_interruption_probe": str(interruption),
        }
        self._write_record()

    def _runtime_command(self, python: Path, *, once: bool = True) -> list[str]:
        command = [str(python), "-m", "jarvis_foundry.supervisor.run", "--home",
                   str(self.roots.foundry_home), "--interval", "0.2"]
        if once:
            command.append("--once")
        return command

    def run_supervisor_once(self, label: str, python: Path | None = None) -> dict[str, Any]:
        result = self.command(
            self._runtime_command(python or self._candidate_python()),
            name=label, env=runtime_environment(self.roots), cwd=self.roots.foundry_home,
            timeout=60,
        )
        if result["returncode"] != 0:
            raise FactoryGuardError(f"Supervisor once failed: {label}")
        if '"heartbeat"' not in result["stdout"]:
            raise FactoryGuardError(f"Supervisor once did not emit a heartbeat: {label}")
        return result

    def _wait_for_lease(self, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        path = self.roots.foundry_home / "foundry.db"
        while time.monotonic() < deadline:
            try:
                connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=2)
                try:
                    row = connection.execute(
                        "SELECT owner_id FROM leases WHERE lease_key = ?",
                        (Supervisor.LEASE_KEY,),
                    ).fetchone()
                finally:
                    connection.close()
                if row:
                    return True
            except sqlite3.Error:
                pass
            time.sleep(0.1)
        return False

    def supervisor_process_contract(self) -> None:
        log_path = self.roots.logs_root / "supervisor-primary.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = log_path.open("w", encoding="utf-8")
        self._primary_process = subprocess.Popen(
            self._runtime_command(self._candidate_python(), once=False),
            cwd=str(self.roots.foundry_home), env=runtime_environment(self.roots),
            stdout=stream, stderr=subprocess.STDOUT, text=True,
        )
        if not self._wait_for_lease():
            self._primary_process.poll()
            stream.close()
            raise FactoryGuardError("primary Supervisor did not acquire its lease")
        duplicate = self.command(
            self._runtime_command(self._candidate_python()),
            name="supervisor-duplicate", env=runtime_environment(self.roots),
            cwd=self.roots.foundry_home, timeout=30,
        )
        if duplicate["returncode"] != 2 or "LEASE_UNAVAILABLE" not in duplicate["stdout"]:
            raise FactoryGuardError("duplicate Supervisor did not fail closed with exit 2")
        database = FoundryDatabase(self.roots.foundry_home / "foundry.db")
        heartbeat_count = database.event_count(readonly=True, event_type="supervisor.heartbeat")
        if heartbeat_count < 1:
            raise FactoryGuardError("primary Supervisor did not record a heartbeat")
        self.record.artifacts["supervisor"] = {
            "pid": self._primary_process.pid,
            "pid_identity": process_creation_identity(self._primary_process.pid),
            "duplicate_returncode": duplicate["returncode"],
            "heartbeat_count": heartbeat_count,
            "primary_log": str(log_path),
        }
        self._write_record()
        terminate_owned_tree(self._primary_process.pid, process=self._primary_process)
        self._primary_process.wait(timeout=15)
        stream.close()
        self._primary_process = None
        # ``taskkill /F`` is a controlled crash injection.  It cannot execute
        # the Supervisor finally block, so expire only this disposable lease
        # before proving startup takeover.  Production leases are untouched.
        database = FoundryDatabase(self.roots.foundry_home / "foundry.db")
        with database.transaction() as connection:
            connection.execute(
                "UPDATE leases SET expires_at=?, heartbeat_at=? WHERE lease_key=?",
                (isoformat(utc_now() - timedelta(seconds=1)),
                 isoformat(utc_now() - timedelta(seconds=1)), Supervisor.LEASE_KEY),
            )
        self.record.artifacts["supervisor"]["crash_recovery_lease_expired"] = True
        self._write_record()
        self.run_supervisor_once("supervisor-restart")

    def worker_process_contract(self) -> None:
        database = FoundryDatabase(self.roots.foundry_home / "foundry.db")
        controller = WorkerController(database, lease_seconds=30)
        self._worker_controller = controller
        worker_command = [sys.executable, "-c", "import time; time.sleep(30)"]
        first = controller.spawn(
            "factory-worker", worker_command, cwd=self.roots.foundry_home,
            worker_type="factory-script", stop_path=self.roots.foundry_home / "EMERGENCY_STOP",
        )
        if not first.alive:
            raise FactoryGuardError("worker did not start")
        duplicate_controller = WorkerController(database, lease_seconds=30)
        try:
            duplicate_controller.spawn("factory-worker", worker_command, cwd=self.roots.foundry_home)
        except LeaseUnavailable:
            duplicate_ok = True
        else:
            duplicate_ok = False
        if not duplicate_ok:
            raise FactoryGuardError("duplicate worker was not blocked by its lease")

        record = load_adapter_pids(controller.pid_table_path())["factory-worker"]
        identity = dict(record.get("creation_identity") or {})
        if identity.get("create_time") is not None:
            identity["create_time"] = int(identity["create_time"]) + 1
        else:
            identity["exe"] = "reused-pid-sentinel"
        update_adapter_pid(controller.pid_table_path(), "factory-worker", creation_identity=identity)
        duplicate_controller.reap()
        if controller.status("factory-worker").alive and database.row(
                "leases", "lease_key", "worker:factory-worker") is not None:
            raise FactoryGuardError("PID reuse guard did not release the stale worker lease")
        controller.terminate("factory-worker")
        self._worker_controller = None
        self.record.artifacts["worker"] = {
            "run_id": "factory-worker",
            "started_pid": first.process.pid if first.process else None,
            "duplicate_blocked": duplicate_ok,
            "reused_pid_identity_reaped": True,
        }
        self._write_record()

    def heartbeat_fault_contract(self) -> None:
        database = FoundryDatabase(self.roots.foundry_home / "foundry.db")
        supervisor = Supervisor(database, self.roots.foundry_home / "FAULT_STOP", instance_id=f"fault-{self.roots.run_id}")
        if supervisor.start() != "RUNNING":
            raise FactoryGuardError("fault Supervisor did not start")
        original = supervisor.heartbeat
        calls = {"count": 0}

        def failing_heartbeat() -> str:
            calls["count"] += 1
            raise OSError("controlled heartbeat fault")

        supervisor.heartbeat = failing_heartbeat  # type: ignore[method-assign]
        try:
            exit_code = _heartbeat_loop(supervisor, 0.01, max_failures=2, stop_after=3)
        finally:
            supervisor.heartbeat = original  # type: ignore[method-assign]
            supervisor.stop()
        if exit_code != 1 or calls["count"] != 2:
            raise FactoryGuardError(f"heartbeat fault was not bounded: {exit_code}/{calls}")
        self.record.artifacts["heartbeat_fault"] = {"exit_code": exit_code, "calls": calls["count"]}
        self._write_record()

    def scheduler_and_canary(self) -> None:
        database = FoundryDatabase(self.roots.foundry_home / "foundry.db")
        installer = Installer(
            database,
            task_name=f"JARVIS-Overnight-{self.roots.run_id}",
            foundry_home=self.roots.foundry_home,
            hermes_home=self.roots.hermes_home,
            python_exe=self._candidate_python(),
        )
        xml = installer._task_xml()
        xml_path = self.roots.evidence_root / "disposable-task.xml"
        xml_path.write_text(xml, encoding="utf-8")
        command, prefix = headless_task_command(self.roots.install_root)
        if str(self.roots.foundry_home) not in xml or str(self._candidate_python()) not in xml:
            raise FactoryGuardError("headless XML omitted disposable absolute identity")
        if "MultipleInstancesPolicy" not in xml or "IgnoreNew" not in xml:
            raise FactoryGuardError("headless XML omitted duplicate-instance guard")
        if installer.task_name.startswith("JARVIS-Foundry"):
            raise FactoryGuardError("factory attempted a production task name")
        canary_key = f"factory.canary:{self.roots.run_id}"
        database.append(Event.create(
            "factory.canary", {"run_id": self.roots.run_id, "trust_state": "LAB"},
            source_type="factory", source_id=self.roots.run_id, idempotency_key=canary_key,
        ))
        before = database.event_count(readonly=True)
        self.run_supervisor_once("canary-supervisor")
        after = database.event_count(readonly=True)
        if after <= before:
            raise FactoryGuardError("canary did not advance the append-only ledger")
        self.record.artifacts["scheduler_canary"] = {
            "task_installation": "NOT_RUN",
            "task_name": installer.task_name,
            "task_xml": str(xml_path),
            "headless_command": command,
            "headless_prefix": prefix,
            "canary_event_key": canary_key,
            "event_count_before": before,
            "event_count_after": after,
        }
        self._write_record()

    def rollback_and_restore(self) -> None:
        if not self.previous_sha:
            raise FactoryGuardError("cannot roll back without previous SHA")
        rollback = rollback_current(
            root=self.roots.install_root, foundry_home=self.roots.foundry_home,
        )
        if rollback["current"] != self.previous_sha:
            raise FactoryGuardError("rollback did not select PREVIOUS")
        previous_identity = self.command(
            [str(self._previous_python()), "-c",
             "import json,jarvis_foundry,sys; print(json.dumps({'python':sys.executable,'package':jarvis_foundry.__file__}))"],
            name="previous-import-identity", env=runtime_environment(self.roots), cwd=self.roots.foundry_home,
        )
        if previous_identity["returncode"] != 0:
            raise FactoryGuardError("previous release import failed after rollback")
        backup = self.roots.db_root / "foundry-v34-backup.db"
        live_db = self.roots.foundry_home / "foundry.db"
        restore_database(live_db, backup)
        restored_hash = _sha256(live_db)
        if schema_version(live_db) != 34 or FoundryDatabase(live_db).quick_check() != "ok":
            raise FactoryGuardError("database restore did not return to v34")
        # A previous release is schema-v36 code.  Its first health start is
        # intentionally allowed to migrate this disposable v34 generation;
        # the v34 bytes and hash above remain the rollback evidence.
        health = self.run_supervisor_once("previous-supervisor-after-restore", self._previous_python())
        if schema_version(live_db) != SCHEMA_VERSION:
            raise FactoryGuardError("previous runtime did not reopen at package schema")
        self.record.artifacts["rollback"] = {
            "pointers": {"current": read_current(self.roots.install_root), "previous": read_previous(self.roots.install_root)},
            "rollback": rollback,
            "previous_import": json.loads(previous_identity["stdout"].strip().splitlines()[-1]),
            "v34_restored_sha256": restored_hash,
            "v34_backup_sha256": _sha256(backup),
            "previous_health_log": health["log"],
            "post_health_schema": schema_version(live_db),
        }
        self._write_record()

    def exercise_faults(self) -> None:
        manifest_path = self.roots.install_root / "releases" / self.source_sha / "RELEASE.json"
        original_manifest = manifest_path.read_text(encoding="utf-8")

        def malformed() -> tuple[bool, str, dict[str, Any]]:
            manifest_path.write_text("{not-json", encoding="utf-8")
            try:
                validate_release(self.source_sha, root=self.roots.install_root, require_schema_match=False)
            except (ReleaseError, json.JSONDecodeError):
                return True, "malformed RELEASE.json refused", {}
            finally:
                manifest_path.write_text(original_manifest, encoding="utf-8")
            return False, "malformed manifest was accepted", {}

        def tampered_tree() -> tuple[bool, str, dict[str, Any]]:
            data = json.loads(original_manifest)
            data["git_tree"] = "0" * 40
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            try:
                try:
                    self._validate_candidate_manifest_against_git()
                except ReleaseError:
                    return True, "tampered git_tree refused", {}
                return False, "tampered git_tree was accepted", {}
            finally:
                manifest_path.write_text(original_manifest, encoding="utf-8")

        def missing_commit() -> tuple[bool, str, dict[str, Any]]:
            data = json.loads(original_manifest)
            data.pop("git_commit", None)
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            try:
                try:
                    validate_release(self.source_sha, root=self.roots.install_root, require_schema_match=False)
                except ReleaseError:
                    return True, "legacy manifest without git_commit refused", {}
                return False, "legacy manifest was accepted", {}
            finally:
                manifest_path.write_text(original_manifest, encoding="utf-8")

        def build_sha_mismatch() -> tuple[bool, str, dict[str, Any]]:
            try:
                build_release(
                    source_repo=self.source_repo, source_sha="0" * 40,
                    python_exe=sys.executable,
                    root=self.roots.run_root / "build-fault",
                )
            except ReleaseError:
                return True, "wrong requested commit refused before packaging", {}
            return False, "wrong requested commit was accepted", {}

        def migration_target_refusal() -> tuple[bool, str, dict[str, Any]]:
            fault_path = self.roots.db_root / "invalid-target.db"
            try:
                FoundryDatabase(fault_path).initialize(target=SCHEMA_VERSION + 1)
            except (KeyError, ValueError):
                return True, "undefined migration target refused", {}
            return False, "undefined migration target was accepted", {}

        def startup_duplicate() -> tuple[bool, str, dict[str, Any]]:
            evidence = self.record.artifacts.get("supervisor", {})
            if evidence.get("duplicate_returncode") == 2:
                return True, "duplicate Supervisor launch exited 2", evidence
            return False, "duplicate Supervisor launch evidence missing", evidence

        def shutdown_orphan() -> tuple[bool, str, dict[str, Any]]:
            evidence = self.record.artifacts.get("worker", {})
            if evidence.get("reused_pid_identity_reaped"):
                return True, "stale/reused worker PID was reaped without broad kill", evidence
            return False, "worker shutdown evidence missing", evidence

        def rollback_without_previous() -> tuple[bool, str, dict[str, Any]]:
            empty_root = self.roots.run_root / "rollback-fault"
            empty_root.mkdir(parents=True, exist_ok=True)
            try:
                rollback_current(root=empty_root)
            except ReleaseError:
                return True, "rollback without PREVIOUS refused", {}
            return False, "rollback without PREVIOUS was accepted", {}

        self.fault("build_sha_mismatch", build_sha_mismatch)
        self.fault("manifest_malformed", malformed)
        self.fault("manifest_tree_tamper", tampered_tree)
        self.fault("manifest_legacy_identity", missing_commit)
        self.fault("migration_target_refusal", migration_target_refusal)
        self.fault("startup_duplicate", startup_duplicate)
        self.fault("shutdown_orphan", shutdown_orphan)
        self.fault("rollback_without_previous", rollback_without_previous)

        def current_tamper() -> tuple[bool, str, dict[str, Any]]:
            current_path = self.roots.install_root / "CURRENT"
            original = current_path.read_text(encoding="utf-8")
            current_path.write_text("0" * 40 + "\n", encoding="utf-8")
            try:
                try:
                    validate_release(read_current(self.roots.install_root) or "", root=self.roots.install_root, require_schema_match=False)
                except ReleaseError:
                    return True, "invalid CURRENT release refused", {}
                return False, "invalid CURRENT release was accepted", {}
            finally:
                current_path.write_text(original, encoding="utf-8")

        self.fault("current_pointer_tamper", current_tamper)

    def cleanup(self) -> None:
        errors: list[str] = []
        process_cleanup_ok = True
        if self._primary_process is not None:
            try:
                terminate_owned_tree(self._primary_process.pid, process=self._primary_process)
                self._primary_process.wait(timeout=15)
            except Exception as error:  # noqa: BLE001
                errors.append(f"supervisor: {error}")
                process_cleanup_ok = False
            self._primary_process = None
        if self._worker_controller is not None:
            try:
                self._worker_controller.terminate_all()
            except Exception as error:  # noqa: BLE001
                errors.append(f"workers: {error}")
                process_cleanup_ok = False
            self._worker_controller = None
        try:
            EmergencyStop(self.roots.foundry_home / "EMERGENCY_STOP").clear()
            EmergencyStop(self.roots.foundry_home / "FAULT_STOP").clear()
        except Exception as error:  # noqa: BLE001
            errors.append(f"sentinel: {error}")
        self._remove_previous_worktree()
        self.record.cleanup = {
            "processes_terminated": process_cleanup_ok,
            "scheduler_tasks_installed": [],
            "errors": errors,
            "scope": str(self.roots.run_root),
        }
        self._write_record()

    def run(self) -> FactoryRecord:
        try:
            self.check("source_provenance", lambda: self._source_provenance())
            self.check("build_packaging", lambda: self._build_check())
            self.check("release_identity", lambda: self._identity_check())
            self.check("authority_pointers", lambda: self._authority_check())
            self.check("migration_lineage", lambda: self._migration_check())
            self.check("supervisor_process", lambda: self._supervisor_check())
            self.check("worker_process", lambda: self._worker_check())
            self.check("scheduler_canary", lambda: self._scheduler_check())
            self.check("controlled_faults", lambda: self._fault_check())
            self.check("rollback_restore", lambda: self._rollback_check())
            self.record.status = "PASS"
        except Exception:
            self.record.status = "FAIL"
        finally:
            self.cleanup()
            self.record.finished_at = isoformat()
            self._write_record()
        return self.record

    def _source_provenance(self) -> tuple[str, dict[str, Any]]:
        head = _git(self.source_repo, "rev-parse", "HEAD")
        dirty = _git(self.source_repo, "status", "--porcelain")
        if head != self.source_sha:
            raise FactoryGuardError(f"HEAD {head} != requested {self.source_sha}")
        tracked = [line for line in dirty.splitlines() if line and not line.startswith("??")]
        if tracked:
            raise FactoryGuardError(f"tracked source dirty: {tracked[:3]}")
        tree = _git(self.source_repo, "rev-parse", f"{self.source_sha}^{{tree}}")
        if not _HEX_RE.fullmatch(tree):
            raise FactoryGuardError("source tree is not a Git object")
        return "clean source worktree and exact Git identity", {"head": head, "tree": tree, "tracked_dirty": tracked}

    def _build_check(self) -> tuple[str, dict[str, Any]]:
        previous, candidate = self.build_releases()
        return "previous and candidate immutable releases built", {"previous": previous, "candidate": candidate}

    def _identity_check(self) -> tuple[str, dict[str, Any]]:
        self.validate_release_identity()
        return "manifest, Git tree, and installed import identity agree", self.record.artifacts["identity"]

    def _authority_check(self) -> tuple[str, dict[str, Any]]:
        self.set_authority()
        return "CURRENT and PREVIOUS are explicit and point to validated releases", self.record.artifacts["authority_before_runtime"]

    def _migration_check(self) -> tuple[str, dict[str, Any]]:
        self.prepare_database()
        return "v34 baseline backed up and v35/v36 migration checkpoints verified", self.record.artifacts["database"]

    def _supervisor_check(self) -> tuple[str, dict[str, Any]]:
        self.run_supervisor_once("supervisor-once")
        self.supervisor_process_contract()
        return "headless Supervisor started, heartbeated, rejected duplicate launch, and restarted", self.record.artifacts["supervisor"]

    def _worker_check(self) -> tuple[str, dict[str, Any]]:
        self.worker_process_contract()
        return "worker lease, duplicate launch, creation identity, and cleanup verified", self.record.artifacts["worker"]

    def _scheduler_check(self) -> tuple[str, dict[str, Any]]:
        self.scheduler_and_canary()
        return "scheduler XML was generated only and bounded canary advanced the ledger", self.record.artifacts["scheduler_canary"]

    def _fault_check(self) -> tuple[str, dict[str, Any]]:
        self.heartbeat_fault_contract()
        self.exercise_faults()
        return "controlled heartbeat, manifest, and CURRENT faults failed closed", {"faults": self.record.faults}

    def _rollback_check(self) -> tuple[str, dict[str, Any]]:
        self.rollback_and_restore()
        return "CURRENT rollback, v34 restore, and previous-runtime health completed", self.record.artifacts["rollback"]


def plan(
    root: str | Path,
    *,
    source_repo: str | Path,
    source_sha: str | None,
    run_id: str,
    expected_lane: str | Path | None = DEFAULT_LANE_ROOT,
) -> dict[str, Any]:
    lane = guard_lane_root(root, expected_lane=expected_lane, source_repo=source_repo)
    roots = FactoryRoots.for_run(lane, run_id)
    validate_run_roots(roots)
    repo = _resolve(source_repo)
    sha = source_sha or _git(repo, "rev-parse", "HEAD")
    return {
        "status": "PLAN",
        "source_repo": str(repo),
        "source_sha": sha,
        "roots": roots.paths(),
        "provider_mode": "DISABLED",
        "task_scheduler": "NOT_RUN",
        "production_mutations": [],
        "planned_steps": [
            "build previous and candidate immutable releases",
            "set disposable CURRENT/PREVIOUS authority",
            "backup v34 and migrate disposable database through v35/v36",
            "start/restart Supervisor and test duplicate lease refusal",
            "test worker lease and process creation identity",
            "generate headless XML without registering a task",
            "run bounded canary and controlled faults",
            "rollback pointers and restore v34 database",
        ],
    }


def _prepare_roots(lane: Path, run_id: str, source_repo: Path) -> FactoryRoots:
    roots = FactoryRoots.for_run(lane, run_id)
    validate_run_roots(roots)
    marker = lane / ROOT_MARKER
    if marker.exists():
        data = _read_json(marker)
        if data.get("lane_root") != str(lane):
            raise FactoryGuardError("lane marker belongs to another root")
    else:
        _write_json(marker, {
            "lane_root": str(lane),
            "source_repo": str(source_repo),
            "factory": "overnight-release-candidate",
            "schema": SCHEMA_VERSION,
            "created_at": isoformat(),
        })
    roots.run_root.parent.mkdir(parents=True, exist_ok=True)
    roots.run_root.mkdir(parents=True, exist_ok=False)
    for path in (roots.install_root, roots.foundry_home, roots.hermes_home,
                 roots.config_root, roots.db_root, roots.logs_root, roots.evidence_root):
        path.mkdir(parents=True, exist_ok=True)
    _write_json(roots.run_root / "ROOTS.json", roots.paths())
    roots.config_root.joinpath("worker_routing.yaml").write_text(
        "enabled: false\nstatus: DISABLED\nprovider: opencode_go\nmodel: deepseek-v4-flash\n",
        encoding="utf-8",
    )
    return roots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="overnight-release-factory")
    parser.add_argument("--root", type=Path, required=True, help="exact isolated lane root")
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--sha", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    source_repo = _resolve(args.source_repo)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    try:
        if args.dry_run:
            result = plan(args.root, source_repo=source_repo, source_sha=args.sha, run_id=run_id)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        lane = guard_lane_root(args.root, source_repo=source_repo)
        # Normalize an abbreviated user argument before immutable build and
        # manifest checks.  The recorded identity is always the full object.
        sha = _git(source_repo, "rev-parse", args.sha or "HEAD")
        roots = _prepare_roots(lane, run_id, source_repo)
        record = FactoryRunner(roots, source_repo, sha).run()
        print(json.dumps(asdict(record), indent=2, sort_keys=True))
        return 0 if record.status == "PASS" else 1
    except (FactoryGuardError, OSError, ValueError) as error:
        payload = {"status": "BLOCKED", "error": f"{type(error).__name__}: {error}", "run_id": run_id}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
