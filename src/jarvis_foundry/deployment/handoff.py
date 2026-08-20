"""Exact-process Supervisor handoff for immutable release activation.

Task Scheduler owns the ``cmd`` wrapper for the Supervisor task.  On this
host ``schtasks /End`` can therefore end the wrapper while the immutable
release Python child keeps running and retaining ``supervisor:singleton``.
This module keeps the existing task request, then falls back to the exact
Supervisor process tree only after bounded waiting.  It never selects a
process by executable name.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..clock import parse, utc_now
from ..database import FoundryDatabase
from ..supervisor.worker import (
    list_process_tree,
    pid_alive,
    process_creation_identity,
    terminate_owned_tree,
)


class SupervisorHandoffError(RuntimeError):
    """Raised when an exact, single Supervisor handoff cannot be proven."""


@dataclass(frozen=True, slots=True)
class SupervisorProcess:
    pid: int
    parent_pid: int
    executable: str | None
    command_line: str
    creation_identity: dict[str, Any]


def _norm(value: str | Path | None) -> str:
    if value is None:
        return ""
    return os.path.normcase(os.path.normpath(str(value)))


def _process_inventory() -> list[dict[str, Any]]:
    """Read the Windows process inventory without selecting anything by name."""
    if os.name != "nt":
        return []
    script = r"""
$items = @(Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -and $_.CommandLine -match 'jarvis_foundry\.(supervisor\.run|deployment\.(launcher|headless_launcher))' } |
  Select-Object ProcessId, ParentProcessId, ExecutablePath, CommandLine)
if ($items.Count -eq 0) { '[]' } else { $items | ConvertTo-Json -Compress }
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode != 0:
        raise SupervisorHandoffError(
            result.stderr.strip() or "Supervisor process inventory failed")
    raw = (result.stdout or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SupervisorHandoffError("Supervisor process inventory was malformed") from error
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return payload
    return []


def discover_supervisor_processes(
    home: Path,
    *,
    release_python: str | Path | None = None,
) -> list[SupervisorProcess]:
    """Return Supervisor processes for exactly one Foundry home/release.

    The executable path and explicit ``--home`` command-line argument bind
    the process to the canonical runtime.  Creation identity is captured
    before any termination so a reused PID is never treated as the old
    Supervisor.
    """
    home_text = _norm(Path(home).expanduser().resolve())
    release_text = _norm(release_python)
    observed: list[SupervisorProcess] = []
    for raw in _process_inventory():
        try:
            pid = int(raw.get("ProcessId") or 0)
            parent_pid = int(raw.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        executable = raw.get("ExecutablePath")
        executable_text = str(executable) if executable else None
        command_line = str(raw.get("CommandLine") or "")
        if home_text not in _norm(command_line):
            continue
        if release_text and _norm(executable_text) != release_text:
            continue
        observed.append(SupervisorProcess(
            pid=pid,
            parent_pid=parent_pid,
            executable=executable_text,
            command_line=command_line,
            creation_identity=process_creation_identity(pid),
        ))
    return observed


def supervisor_roots(processes: list[SupervisorProcess]) -> list[SupervisorProcess]:
    """Reduce matching rows to root processes, excluding same-tree children."""
    pids = {item.pid for item in processes}
    return [item for item in processes if item.parent_pid not in pids]


def resolve_handoff_release_python(
    home: Path,
    configured_release_python: str | Path,
) -> str:
    """Bind handoff to the one exact Supervisor root currently owning home.

    The release pointer can be stale after an interrupted activation. A
    running process inventory is authoritative for termination, but only when
    it resolves to exactly one Supervisor root. Ambiguity fails closed so a
    release handoff can never broaden into an executable-name kill.
    """
    roots = supervisor_roots(discover_supervisor_processes(home))
    if len(roots) > 1:
        raise SupervisorHandoffError(
            "cannot resolve handoff release with multiple Supervisor roots: "
            f"{[item.pid for item in roots]}"
        )
    if not roots:
        return str(Path(configured_release_python).resolve())
    executable = roots[0].executable
    if not executable:
        raise SupervisorHandoffError(
            f"running Supervisor root {roots[0].pid} has no executable identity"
        )
    return str(Path(executable).resolve())


def _filetime_datetime(value: Any) -> datetime | None:
    """Convert a Windows FILETIME identity to UTC for ledger correlation."""
    try:
        ticks = int(value)
    except (TypeError, ValueError):
        return None
    if ticks <= 0:
        return None
    return datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks / 10)


def select_authoritative_root(
    roots: list[SupervisorProcess],
    *,
    started_at: datetime | None,
    authority_window_seconds: float = 300.0,
) -> SupervisorProcess:
    """Select the root that created the durable singleton instance.

    A repeating Task Scheduler trigger may leave a short-lived second launch
    attempt while the old process still owns the singleton.  Only the root
    whose process-creation identity precedes the durable ``supervisor.started``
    event for the current lease owner is authoritative.  Ambiguity fails
    closed rather than guessing a PID.
    """
    if not roots:
        raise SupervisorHandoffError("no Supervisor root matches the current release")
    if len(roots) == 1:
        return roots[0]
    if started_at is None:
        raise SupervisorHandoffError(
            f"multiple Supervisor roots and no durable started identity: {[item.pid for item in roots]}")
    candidates: list[SupervisorProcess] = []
    for process in roots:
        created_at = _filetime_datetime(process.creation_identity.get("create_time"))
        if created_at is None:
            continue
        age = (started_at - created_at).total_seconds()
        if 0 <= age <= authority_window_seconds:
            candidates.append(process)
    if len(candidates) != 1:
        raise SupervisorHandoffError(
            "durable Supervisor identity did not select exactly one root: "
            f"roots={[item.pid for item in roots]}, candidates={[item.pid for item in candidates]}, "
            f"started_at={started_at.isoformat()}")
    return candidates[0]


def _same_process(process: SupervisorProcess) -> bool:
    """Confirm that the captured PID still denotes the captured process."""
    if not pid_alive(process.pid):
        return False
    current = process_creation_identity(process.pid)
    expected_exe = _norm(process.executable)
    observed_exe = _norm(current.get("exe"))
    if expected_exe and observed_exe and expected_exe != observed_exe:
        return False
    expected_time = process.creation_identity.get("create_time")
    observed_time = current.get("create_time")
    if expected_time is not None and observed_time is not None:
        return expected_time == observed_time
    return True


def _same_pid(pid: int, identity: dict[str, Any]) -> bool:
    """Confirm a captured descendant PID was not reused."""
    if not pid_alive(pid):
        return False
    current = process_creation_identity(pid)
    expected_exe = _norm(identity.get("exe"))
    observed_exe = _norm(current.get("exe"))
    if expected_exe and observed_exe and expected_exe != observed_exe:
        return False
    expected_time = identity.get("create_time")
    observed_time = current.get("create_time")
    if expected_time is not None and observed_time is not None:
        return expected_time == observed_time
    return True


def _wait_for_process_exit(process: SupervisorProcess, deadline: float, poll: float) -> bool:
    while time.monotonic() < deadline:
        if not _same_process(process):
            return True
        time.sleep(poll)
    return not _same_process(process)


def _singleton_lease(home: Path) -> dict[str, Any] | None:
    database = FoundryDatabase(Path(home).resolve() / "foundry.db")
    if not database.path.is_file():
        return None
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT lease_key, owner_id, expires_at, heartbeat_at "
            "FROM leases WHERE lease_key = 'supervisor:singleton'"
        ).fetchone()
    return dict(row) if row is not None else None


def _authoritative_started_at(home: Path) -> tuple[str | None, datetime | None]:
    """Read the current singleton owner and its first durable start event."""
    lease = _singleton_lease(home)
    if lease is None or not lease.get("owner_id"):
        return None, None
    database = FoundryDatabase(Path(home).resolve() / "foundry.db")
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT occurred_at FROM events "
            "WHERE event_type = 'supervisor.started' AND source_id = ? "
            "ORDER BY sequence LIMIT 1",
            (str(lease["owner_id"]),),
        ).fetchone()
    if row is None:
        return str(lease["owner_id"]), None
    try:
        return str(lease["owner_id"]), parse(str(row["occurred_at"]))
    except (KeyError, TypeError, ValueError):
        raise SupervisorHandoffError("authoritative Supervisor start event is malformed")


def _wait_for_no_supervisor_roots(
    home: Path,
    release_python: str | Path,
    deadline: float,
    poll: float,
) -> bool:
    while time.monotonic() < deadline:
        roots = supervisor_roots(discover_supervisor_processes(
            home, release_python=release_python))
        if not roots:
            return True
        time.sleep(poll)
    return not supervisor_roots(discover_supervisor_processes(
        home, release_python=release_python))


def _wait_for_lease_reconciliation(home: Path, deadline: float, poll: float) -> str:
    while time.monotonic() < deadline:
        row = _singleton_lease(home)
        if row is None:
            return "released"
        try:
            if parse(str(row["expires_at"])) <= utc_now():
                return "expired"
        except (KeyError, TypeError, ValueError):
            raise SupervisorHandoffError("singleton lease row is malformed")
        time.sleep(poll)
    row = _singleton_lease(home)
    if row is None:
        return "released"
    try:
        if parse(str(row["expires_at"])) <= utc_now():
            return "expired"
    except (KeyError, TypeError, ValueError):
        raise SupervisorHandoffError("singleton lease row is malformed")
    raise SupervisorHandoffError(
        f"singleton lease still held by {row.get('owner_id', 'unknown')}")


def handoff_running_supervisor(
    *,
    home: Path,
    release_python: str | Path,
    request_shutdown: Callable[[], Any],
    wait_seconds: float = 15.0,
    lease_wait_seconds: float = 120.0,
    poll_seconds: float = 0.25,
) -> dict[str, Any]:
    """Stop the current Supervisor and prove its lease is safe to replace.

    ``request_shutdown`` is the existing canonical task ``/End`` request.
    If the task wrapper leaves the Python child alive, only the exact root
    PID captured for this home/release is passed to ``terminate_owned_tree``.
    """
    all_matching = discover_supervisor_processes(home, release_python=release_python)
    roots = supervisor_roots(all_matching)
    authority_owner, authority_started_at = _authoritative_started_at(home) if len(roots) > 1 else (None, None)
    process = select_authoritative_root(roots, started_at=authority_started_at) if roots else None
    old_pid = process.pid if process is not None else None
    had_process = process is not None
    owned_identities = (
        {pid: process_creation_identity(pid) for pid in list_process_tree(process.pid)}
        if process is not None else {}
    )
    request_result: Any = None
    request_error: str | None = None
    terminated_pids: list[int] = []
    forced = False

    if process is not None:
        try:
            request_result = request_shutdown()
        except Exception as error:  # noqa: BLE001 — exact fallback remains safe
            request_error = f"{type(error).__name__}: {error}"
        graceful_deadline = time.monotonic() + max(0.1, wait_seconds)
        graceful = _wait_for_process_exit(process, graceful_deadline, poll_seconds)
        survivors = [
            pid for pid, identity in owned_identities.items()
            if _same_pid(pid, identity)
        ]
        if (not graceful) or survivors:
            forced = True
            # If the root is still present, taskkill its exact tree so newly
            # attached descendants are included.  If only a descendant
            # survived the wrapper, target each originally captured PID after
            # its own creation-identity check.
            if _same_process(process):
                terminated_pids = list(terminate_owned_tree(
                    process.pid, grace_seconds=min(2.0, max(0.1, wait_seconds))))
            else:
                for pid in survivors:
                    terminated_pids.extend(terminate_owned_tree(
                        pid, grace_seconds=min(2.0, max(0.1, wait_seconds))))
            leftover = [
                pid for pid, identity in owned_identities.items()
                if _same_pid(pid, identity)
            ]
            if leftover:
                raise SupervisorHandoffError(
                    f"exact Supervisor tree did not terminate: {leftover}")
            process = None
        else:
            process = None

    if not _wait_for_no_supervisor_roots(
            home, release_python,
            time.monotonic() + max(0.1, wait_seconds), poll_seconds):
        remaining = supervisor_roots(discover_supervisor_processes(
            home, release_python=release_python))
        raise SupervisorHandoffError(
            f"old release Supervisor roots remain after handoff: {[item.pid for item in remaining]}")

    lease_state = _wait_for_lease_reconciliation(
        home, time.monotonic() + max(0.1, lease_wait_seconds), poll_seconds)
    return {
        "running": had_process,
        "old_pid": old_pid,
        "old_release_python": str(Path(release_python).resolve()),
        "authority_owner_id": authority_owner,
        "authority_started_at": authority_started_at.isoformat() if authority_started_at else None,
        "request_result": request_result,
        "request_error": request_error,
        "forced_exact_tree": forced,
        "terminated_pids": terminated_pids,
        "singleton_lease": lease_state,
    }


def wait_for_single_supervisor(
    *,
    home: Path,
    release_python: str | Path,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.25,
) -> SupervisorProcess:
    """Wait until exactly one Supervisor root owns ``home`` and the release."""
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    while time.monotonic() < deadline:
        all_processes = discover_supervisor_processes(home)
        roots = supervisor_roots(all_processes)
        matching = [item for item in roots if _norm(item.executable) == _norm(release_python)]
        if len(roots) == 1 and len(matching) == 1:
            return matching[0]
        time.sleep(poll_seconds)
    roots = supervisor_roots(discover_supervisor_processes(home))
    raise SupervisorHandoffError(
        "new release Supervisor was not the single authoritative process: "
        f"roots={[item.pid for item in roots]}, "
        f"expected={Path(release_python).resolve()}")
