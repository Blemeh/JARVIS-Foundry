"""Read-only operator truth for canonical source vs live release."""
from __future__ import annotations

import os
import base64
import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..diagnostics.runtime import diagnostic_snapshot, derive_health
from ..installer import V1_TASK_NAME, Installer
from ..migrations import SCHEMA_VERSION
from ..paths import FoundryPaths
from ..reconcile import inspect as inspect_ledger
from .layout import install_root
from .runtime_identity import (
    parity_state, process_matches_home, release_sha_for_id, supervisor_processes,
)
from .release import (
    ReleaseError,
    read_current,
    read_previous,
    read_release_json,
    release_dir,
    release_authority_report,
    schema_version,
    validate_release,
)


_RELEASE_PATH_RE = re.compile(
    r"[\\/]releases[\\/]([0-9a-f]{40,64})(?:[\\/]|$)", re.IGNORECASE)


def _supervisor_processes(
    *, expected_sha: str | None = None, active_pid: int | None = None,
    expected_root: Path | None = None,
    active_creation_identity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return a bounded process census with identity classifications.

    The database lease remains the owner authority.  This census is an
    independent observation used to detect duplicate launchers, stale release
    processes, and PID reuse; it never kills or adopts a process.
    """
    from ..platform.windows_subprocess import headless_run_kwargs

    # Avoid Select-Object/ConvertTo-Json: on some repaired Windows PowerShell
    # installations those Microsoft.PowerShell.Utility cmdlets fail to
    # autoload under a hidden child.  CIM + a tab-delimited/base64 record is
    # equally read-only and works in both Windows PowerShell and pwsh.
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match "
        "'jarvis_foundry\\.(supervisor\\.run|deployment\\.(headless_launcher|launcher))' } | "
        "ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t"
        "$([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$_.ExecutablePath)))`t"
        "$([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$_.CommandLine)))`t"
        "$($_.CreationDate)\" }"
    )
    command = ["powershell", "-NoProfile", "-Command", script]
    try:
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=30, check=False,
            **headless_run_kwargs(),
        )
        # Some Windows hosts have a broken PowerShell module autoload path
        # only when STARTUPINFO is supplied for hidden execution.  Retry the
        # same read-only census plainly rather than turning an available
        # process observation into a false empty result.
        if result.returncode != 0 and not (result.stdout or "").strip():
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=30, check=False)
    except OSError:
        return []
    raw = (result.stdout or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
        rows = [payload] if isinstance(payload, dict) else list(payload)
    except json.JSONDecodeError:
        rows = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) != 5:
                continue
            try:
                executable = base64.b64decode(parts[2]).decode("utf-8")
                command_line = base64.b64decode(parts[3]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
            rows.append({
                "ProcessId": parts[0],
                "ParentProcessId": parts[1],
                "ExecutablePath": executable,
                "CommandLine": command_line,
                "CreationDate": parts[4],
            })
    root_needle = None
    if expected_root is not None:
        root_needle = (
            str(Path(expected_root).expanduser().resolve())
            .replace("/", "\\")
            .rstrip("\\")
            + "\\releases\\"
        ).lower()
    observed: list[dict[str, Any]] = []
    from ..supervisor.worker import process_creation_identity
    for row in rows:
        if not isinstance(row, dict):
            continue
        command_line = str(row.get("CommandLine") or "")
        executable = str(row.get("ExecutablePath") or "")
        if root_needle is not None:
            process_text = f"{command_line} {executable}".replace("/", "\\").lower()
            # A status probe for a disposable or explicitly selected install
            # must not count an unrelated production process on the same
            # machine as a duplicate/stale owner.
            if root_needle not in process_text:
                continue
        match = _RELEASE_PATH_RE.search(f"{command_line} {executable}")
        release_sha = match.group(1).lower() if match else None
        try:
            pid = int(row.get("ProcessId"))
        except (TypeError, ValueError):
            pid = None
        try:
            parent_pid = int(row.get("ParentProcessId"))
        except (TypeError, ValueError):
            parent_pid = None
        pid_match = (
            active_pid is not None and pid is not None and pid == active_pid
        )
        observed_identity = None
        identity_match = pid_match
        if pid_match and active_creation_identity is not None:
            try:
                observed_identity = process_creation_identity(pid)
            except (OSError, ValueError):
                observed_identity = {"pid": pid, "error": "identity probe failed"}
            expected_create_time = active_creation_identity.get("create_time")
            expected_exe = active_creation_identity.get("exe")
            observed_create_time = observed_identity.get("create_time")
            observed_exe = observed_identity.get("exe")
            identity_match = bool(
                expected_create_time is not None
                and expected_exe
                and observed_create_time == expected_create_time
                and observed_exe
                and str(observed_exe).lower() == str(expected_exe).lower()
            )
        record: dict[str, Any] = {
            "pid": pid,
            "parent_pid": parent_pid,
            "executable": executable or None,
            "command_line": command_line or None,
            "creation_time": row.get("CreationDate"),
            "release_sha": release_sha,
            "matches_installed_release": (
                release_sha == str(expected_sha).lower()
                if expected_sha and release_sha else False
            ),
            "is_database_owner": identity_match,
            "identity_match": identity_match,
            "observed_creation_identity": observed_identity,
        }
        if record["is_database_owner"] and record["matches_installed_release"]:
            record["classification"] = "VALID_OWNER"
        elif pid_match and active_creation_identity is not None:
            record["classification"] = "STALE_PROCESS_IDENTITY"
        elif expected_sha and release_sha and release_sha != str(expected_sha).lower():
            record["classification"] = "STALE_RELEASE"
        elif record["matches_installed_release"]:
            record["classification"] = "DUPLICATE_OR_UNOWNED"
        else:
            record["classification"] = "UNIDENTIFIED"
        observed.append(record)
    return observed


def _latest_runtime_payload(database: FoundryDatabase) -> dict[str, Any] | None:
    """Read the newest Supervisor/STOP ledger payload without mutating state."""

    connection = database.connect(readonly=True)
    try:
        row = connection.execute(
            "SELECT event_id, event_type, sequence, recorded_at, payload_json "
            "FROM events WHERE event_type IN ("
            "'supervisor.started','supervisor.heartbeat','supervisor.degraded',"
            "'supervisor.stopped','emergency_stop.requested','emergency_stop.frozen',"
            "'emergency_stop.reconciled') ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    return {
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "sequence": int(row["sequence"]),
        "recorded_at": row["recorded_at"],
        "payload": payload if isinstance(payload, dict) else {},
    }


def _trust_projection_report(
    database: FoundryDatabase, supervisor: dict[str, Any],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    latest = _latest_runtime_payload(database)
    if latest is None:
        return {"status": "NOT_TESTED", "reason": "no runtime event recorded"}
    payload = latest["payload"]
    expected = payload.get("trust_state")
    actual = supervisor.get("trust_state")
    consistent = bool(reconciliation.get("consistent")) and (
        expected in (None, actual)
    )
    return {
        "status": "PASS" if consistent else "FAIL",
        "consistent": consistent,
        "projected_trust_state": actual,
        "latest_ledger_trust_state": expected,
        "latest_runtime_event_id": latest["event_id"],
        "latest_runtime_event_type": latest["event_type"],
    }


def _idle_event_growth(database: FoundryDatabase) -> dict[str, Any]:
    """Report legacy idle-event history separately from current coalescing."""

    idle_types = (
        "worker_loop.waiting", "worker_loop.portfolio_waiting",
        "worker_loop.executive_waiting",
    )
    placeholders = ",".join("?" for _ in idle_types)
    connection = database.connect(readonly=True)
    try:
        head = int(connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()[0])
        rows = connection.execute(
            "SELECT event_type, sequence, payload_json FROM events "
            f"WHERE event_type IN ({placeholders}) ORDER BY sequence DESC",
            idle_types,
        ).fetchall()
    finally:
        connection.close()
    totals = {event_type: 0 for event_type in idle_types}
    legacy = {event_type: 0 for event_type in idle_types}
    recent_legacy = {event_type: 0 for event_type in idle_types}
    recent_floor = max(0, head - 1000)
    recent_total = 0
    recent_coalesced = 0
    for row in rows:
        event_type = str(row["event_type"])
        totals[event_type] += 1
        is_recent = int(row["sequence"]) > recent_floor
        if is_recent:
            recent_total += 1
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        is_legacy = not isinstance(payload, dict) or not payload.get("_coalesced_state")
        if is_legacy:
            legacy[event_type] += 1
            if is_recent:
                recent_legacy[event_type] += 1
        elif is_recent:
            recent_coalesced += 1
    legacy_total = sum(legacy.values())
    recent_legacy_total = sum(recent_legacy.values())
    return {
        "status": "PASS" if recent_legacy_total == 0 else "DEGRADED",
        "event_types": list(idle_types),
        "totals": totals,
        "legacy_uncoalesced_totals": legacy,
        "legacy_uncoalesced_recent_window": recent_legacy,
        "legacy_uncoalesced_total": legacy_total,
        "recent_window_events": recent_total,
        "recent_window_coalesced_events": recent_coalesced,
        "recent_window_size_events": 1000,
        "reason": (
            "current idle states are coalesced"
            if recent_legacy_total == 0 else
            "legacy or uncoalesced idle events remain in the recent ledger window"
        ),
    }


def _task_owned_for_home(task: dict[str, Any], home: Path) -> bool:
    """Prove the named task's JARVIS profile belongs to this Foundry home."""

    if not task.get("installed") or not task.get("profile_present"):
        return False
    profile_path = Path(str(task.get("profile_path") or ""))
    try:
        for line in profile_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("foundry_home:"):
                configured = Path(line.split(":", 1)[1].strip()).expanduser().resolve()
                return configured == home.expanduser().resolve()
    except (OSError, ValueError):
        return False
    return False


def _scheduler_contract(
    install: Path, home: Path, task: dict[str, Any],
) -> dict[str, Any]:
    """Check the installed task action against the stable launcher contract."""

    stable_path = install / "bin" / "jarvis-supervisor-headless.vbs"
    stable_text = None
    try:
        stable_text = stable_path.read_text(encoding="utf-8")
    except OSError:
        pass
    launcher_contract = bool(
        stable_text
        and "shell.Run(cmd, 0, True)" in stable_text
        and "WScript.Quit exitCode" in stable_text
        and 'environment("JARVIS_RELEASE_SHA") = sha' in stable_text
    )
    action_queried = False
    action_contract = False
    action_error = None
    if task.get("installed"):
        from ..platform.windows_subprocess import headless_run_kwargs

        try:
            result = subprocess.run(
                ["schtasks", "/query", "/tn", str(task.get("task_name") or ""),
                 "/xml"],
                capture_output=True, text=True, timeout=30, check=False,
                **headless_run_kwargs(),
            )
            action_queried = result.returncode == 0
            if action_queried:
                # Preserve the forward slashes in WScript's ``//B //Nologo``
                # switches.  Normalize only the expected path so a task
                # emitted with either slash style is accepted.
                xml = (result.stdout or "").lower()
                expected = str(stable_path.resolve()).replace("/", "\\").lower()
                action_markers = (
                    "wscript.exe", "//b //nologo",
                    "<multipleinstancespolicy>ignorenew",
                    "<interval>pt1m", "<duration>p30d",
                )
                expected_path = expected in xml or expected.replace("\\", "/") in xml
                action_contract = expected_path and all(
                    marker in xml for marker in action_markers
                )
            else:
                action_error = (result.stderr or result.stdout or "query failed")[-300:]
        except (OSError, subprocess.SubprocessError) as error:
            action_error = f"{type(error).__name__}: {error}"
    owned = bool(task.get("owned_for_home"))
    if not task.get("installed"):
        status = "NOT_TESTED"
    elif owned and launcher_contract and action_queried and action_contract:
        status = "PASS"
    else:
        status = "FAIL"
    return {
        "status": status,
        "task_name": task.get("task_name"),
        "owned_for_home": owned,
        "stable_launcher": str(stable_path),
        "launcher_contract": launcher_contract,
        "action_queried": action_queried,
        "action_contract": action_contract,
        "action_error": action_error,
        "expected_home": str(home.resolve()),
    }


def production_status(*, home: Path | None = None, source_repo: Path | None = None) -> dict[str, Any]:
    paths = FoundryPaths.discover(home)
    database = FoundryDatabase(paths.database)
    install = install_root()
    installer = Installer(
        database, task_name=V1_TASK_NAME, foundry_home=paths.home,
        hermes_home=FoundryPaths.hermes_home(),
    )
    live_schema = schema_version(paths.database) if paths.database.is_file() else 0
    current = read_current(install)
    previous = read_previous(install)
    current_manifest = None
    release_identity = {
        "status": "UNKNOWN",
        "reason": "CURRENT pointer missing",
    }
    if current:
        manifest_path = release_dir(current, install) / "RELEASE.json"
        if manifest_path.is_file():
            try:
                current_manifest = read_release_json(manifest_path)
                validated = validate_release(
                    current, foundry_home=paths.home, require_schema_match=True)
                release_identity = {
                    "status": "VALID",
                    "runtime_identity": validated["runtime_identity"],
                }
            except (OSError, ReleaseError, ValueError) as error:
                release_identity = {
                    "status": "DEGRADED",
                    "reason": str(error),
                }
        else:
            release_identity = {
                "status": "DEGRADED",
                "reason": "CURRENT release manifest missing",
            }
    source_sha = None
    if source_repo and (Path(source_repo) / ".git").exists():
        import subprocess

        from ..platform.windows_subprocess import headless_run_kwargs

        result = subprocess.run(
            ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15, check=False,
            **headless_run_kwargs(),
        )
        source_sha = result.stdout.strip() or None
    supervisor = {}
    runtime_diagnostic = {
        "runtime_control": {
            "status": "UNKNOWN",
            "reason": "database missing",
        },
        "correlation": {
            "release_ids": [], "process_instances": [], "pids": [],
            "lease_owners": [], "request_ids": [], "mission_ids": [],
        },
        "events": [],
    }
    reconciliation = {"consistent": False, "reason": "database missing"}
    trust_projection = {"status": "NOT_TESTED", "reason": "database missing"}
    idle_event_growth = {"status": "NOT_TESTED", "reason": "database missing"}
    if paths.database.is_file():
        try:
            with database.connect(readonly=True) as connection:
                row = connection.execute(
                    "SELECT * FROM supervisor_state WHERE singleton = 1").fetchone()
                supervisor = dict(row) if row else {}
            runtime_diagnostic = diagnostic_snapshot(
                database, stop_path=str(paths.emergency_stop))
            reconciliation = inspect_ledger(database).to_json()
            trust_projection = _trust_projection_report(
                database, supervisor, reconciliation)
            idle_event_growth = _idle_event_growth(database)
        except Exception as error:  # noqa: BLE001
            supervisor = {"error": str(error)}
            runtime_diagnostic["runtime_control"] = {
                "status": "UNKNOWN",
                "reason": f"diagnostic read failed: {type(error).__name__}",
            }
            trust_projection = {
                "status": "UNKNOWN",
                "reason": f"diagnostic read failed: {type(error).__name__}",
            }
            idle_event_growth = {
                "status": "UNKNOWN",
                "reason": f"diagnostic read failed: {type(error).__name__}",
            }
    from .release import release_authority_report
    authority = release_authority_report(root=install, foundry_home=paths.home)
    runtime_control = runtime_diagnostic.get("runtime_control") or {}
    try:
        active_pid = int(runtime_control.get("pid"))
    except (TypeError, ValueError):
        active_pid = None
    processes = _supervisor_processes(
        expected_sha=current,
        active_pid=active_pid,
        expected_root=install,
        active_creation_identity=(
            runtime_control.get("creation_identity")
            if isinstance(runtime_control.get("creation_identity"), dict)
            else ({} if active_pid is not None else None)
        ),
    )
    valid_owners = [
        process for process in processes
        if process.get("classification") == "VALID_OWNER"
    ]
    stale_processes = [
        process for process in processes
        if process.get("classification") in {
            "STALE_RELEASE", "STALE_PROCESS_IDENTITY",
        }
    ]
    duplicate_processes = [
        process for process in processes
        if process.get("classification") in {"DUPLICATE_OR_UNOWNED", "UNIDENTIFIED"}
    ]
    if active_pid is not None or str(runtime_control.get("status")) == "RUNNING":
        process_status = "PASS" if len(valid_owners) == 1 and not stale_processes and not duplicate_processes else "FAIL"
    elif processes:
        process_status = "FAIL"
    else:
        process_status = "STOPPED"
    process_truth = {
        "status": process_status,
        "exactly_one_valid_supervisor": len(valid_owners) == 1,
        "valid_owner_count": len(valid_owners),
        "duplicate_count": len(duplicate_processes),
        "stale_count": len(stale_processes),
        "active_database_pid": active_pid,
        "duplicates": duplicate_processes,
        "stale_processes": stale_processes,
    }
    try:
        latest_runtime = (
            _latest_runtime_payload(database) if paths.database.is_file() else None)
    except (OSError, sqlite3.Error):
        latest_runtime = None
    # A process whose executable resides in the immutable release directory
    # is the strongest running-identity observation.  A process census is the
    # only observation that can support the word
    # "running".  Keep the ledger-derived identity separately as a last-known
    # value for restart diagnostics; it must never complete source/install/
    # runtime parity while no currently valid owner is observed.
    runtime_sha = (
        valid_owners[0].get("release_sha") if len(valid_owners) == 1 else None
    )
    runtime_release_id = None
    last_runtime_sha = None
    if latest_runtime is not None:
        value = latest_runtime["payload"].get("release_id")
        runtime_release_id = str(value) if value not in (None, "") else None
        if runtime_release_id:
            if runtime_release_id == current:
                last_runtime_sha = current
            elif current_manifest and runtime_release_id == current_manifest.get("release_id"):
                last_runtime_sha = current
            elif len(runtime_release_id) in {40, 64} and _RELEASE_PATH_RE.fullmatch(
                    f"/releases/{runtime_release_id}"):
                last_runtime_sha = runtime_release_id.lower()
    parity_values = {
        "canonical_source_sha": source_sha,
        "installed_release_sha": current,
        "database_active_source_sha": authority.get("database_active_source_sha"),
        "running_runtime_sha": runtime_sha,
    }
    known_parity = [value for value in parity_values.values() if value]
    parity_mismatch = len(set(known_parity)) > 1
    parity = {
        **parity_values,
        "running_runtime_release_id": runtime_release_id,
        "last_known_runtime_sha": last_runtime_sha,
        "running_runtime_verified": runtime_sha is not None,
        "status": (
            "FAIL" if parity_mismatch else
            "PASS" if len(known_parity) == len(parity_values) else
            "NOT_TESTED"
        ),
        "complete": len(known_parity) == len(parity_values),
        "reason": (
            "all source/install/database/runtime identities match"
            if not parity_mismatch and len(known_parity) == len(parity_values)
            else "one or more identity observations are unavailable"
            if not parity_mismatch else
            "source/install/database/runtime identities disagree"
        ),
    }
    installed_sha = current
    runtime_release_sha = runtime_sha
    active_processes = valid_owners
    release_identity["authority"] = authority
    transition_marker = install / "RELEASE_TRANSITION.json"
    transition = None
    if transition_marker.is_file():
        try:
            transition = json.loads(transition_marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            transition = {"status": "UNREADABLE"}
    task = installer.status()
    task["owned_for_home"] = _task_owned_for_home(task, paths.home)
    scheduler = _scheduler_contract(install, paths.home, task)
    release_status = (
        "VALID" if release_identity.get("status") == "VALID"
        and authority.get("status") == "CONSISTENT"
        and authority.get("database_authority_mode") == "DATABASE_LIVE_SLOT"
        else str(authority.get("status") or "UNKNOWN")
    )
    health = derive_health(
        runtime_status=str(
            runtime_diagnostic.get("runtime_control", {}).get("status") or "UNKNOWN"),
        reconciliation_consistent=bool(reconciliation.get("consistent")),
        release_status=release_status,
        stop_active=paths.emergency_stop.exists(),
        supervisor_count=len(active_processes),
        parity=parity,
        trust_state=str(supervisor.get("trust_state") or "UNKNOWN"),
    )
    truth_failures: list[str] = []
    if process_truth["status"] == "FAIL":
        truth_failures.append("Supervisor process census did not prove exactly one owner")
    if scheduler["status"] == "FAIL":
        truth_failures.append("Windows Scheduler action is not the stable identity-bound launcher")
    if trust_projection.get("status") == "FAIL":
        truth_failures.append("trust projection disagrees with the runtime ledger")
    if idle_event_growth.get("status") == "DEGRADED":
        truth_failures.append("legacy idle events are still growing in the recent window")
    if parity.get("status") == "FAIL":
        truth_failures.append("source/install/database/runtime SHA parity failed")
    if transition is not None:
        truth_failures.append("release transition marker requires recovery")
    if truth_failures and health["status"] not in {"BLOCKED", "STOPPED"}:
        health = {
            "status": "DEGRADED",
            "reason": "; ".join(
                [str(health.get("reason") or "operational truth failed"),
                 *truth_failures]
            ),
        }
    if health["status"] == "RUNNING" and not task.get("owned_for_home"):
        health = {
            "status": "DEGRADED",
            "reason": "Supervisor Task Scheduler ownership is not proven",
        }
    return {
        "status": health["status"],
        "canonical_source": source_sha,
        "canonical_source_sha": source_sha,
        "current_release": current,
        "installed_release_sha": installed_sha,
        "runtime_release_sha": runtime_release_sha,
        "parity_state": parity,
        "release_authority": authority,
        "previous_release": previous,
        "current_manifest": current_manifest,
        "release_identity": release_identity,
        "install_root": str(install),
        "live_db": str(paths.database),
        "live_schema": live_schema,
        "package_schema": SCHEMA_VERSION,
        "stop": paths.emergency_stop.exists(),
        "stop_path": str(paths.emergency_stop),
        "supervisor_state": supervisor,
        "task": task,
        "scheduler": scheduler,
        "health": health,
        "reconciliation": reconciliation,
        "trust_projection": trust_projection,
        "idle_event_growth": idle_event_growth,
        "release_authority": authority,
        "release_transition": transition,
        "process_truth": process_truth,
        "parity": parity,
        "runtime_diagnostic": runtime_diagnostic,
        "supervisor_processes": processes,
        "active_supervisor_processes": active_processes,
        "stale_supervisor_processes": stale_processes,
        "pid": os.getpid(),
    }
