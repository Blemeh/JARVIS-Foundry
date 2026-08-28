"""Authoritative identity contract for the production Supervisor.

Process names are not an identity.  The production task may launch the
Supervisor through a ``.cmd`` file, a ``.vbs`` headless wrapper, or directly
through ``python -m``.  This module normalizes those forms into one
machine-checkable contract used by status, restart diagnostics, and tests.

The contract is deliberately read-only: it never starts, stops, or adopts a
process.  A process is authoritative only when its command line contains one
of the canonical Supervisor modules and its executable/release can be
correlated when that evidence is available.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..platform.windows_subprocess import headless_run_kwargs

SUPERVISOR_MODULES: tuple[str, ...] = (
    "jarvis_foundry.supervisor.run",
    "jarvis_foundry.deployment.launcher",
    "jarvis_foundry.deployment.headless_launcher",
)
_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$", re.IGNORECASE)
_RELEASE_PATH_RE = re.compile(
    r"[\\/]releases[\\/]([0-9a-f]{40,64})(?:[\\/]|$)", re.IGNORECASE)


def _powershell_executable() -> str:
    """Prefer Windows PowerShell for CIM compatibility.

    The bundled PowerShell Core can be present without its CIM module.  The
    classic Windows executable is the supported process-query provider on
    Windows; callers still receive an honest empty list if neither is usable.
    """
    candidates = (
        os.environ.get("WINDIR", r"C:\Windows") + r"\System32\WindowsPowerShell\v1.0\powershell.exe",
        "powershell.exe",
        "powershell",
    )
    for candidate in candidates:
        if Path(candidate).is_file() or candidate.startswith("powershell"):
            return candidate
    return candidates[0]


def query_processes() -> list[dict[str, Any]]:
    """Return raw process identity records from the host without name hacks."""
    if os.name != "nt":
        # Linux/macOS test lanes can provide an explicit query function; the
        # production Windows path below is the only source of live truth.
        return []
    script = (
        "$ErrorActionPreference='Stop'; "
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            [_powershell_executable(), "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=30, check=False,
            **headless_run_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode == 0 and (result.stdout or "").strip():
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, Mapping):
            return [dict(payload)]
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, Mapping)]

    # Some Windows images expose CIM as a discoverable command but cannot
    # load the CimCmdlets module (the exact failure seen on the recovery host).
    # WMIC is deprecated but still provides the same immutable process fields;
    # use it only as a read-only compatibility provider.
    wmic = shutil.which("wmic.exe") or shutil.which("wmic")
    if not wmic:
        return []
    try:
        legacy = subprocess.run(
            [wmic, "process", "get", "ProcessId,ParentProcessId,ExecutablePath,CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=30, check=False,
            **headless_run_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if legacy.returncode != 0:
        return []
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    last_key: str | None = None
    known_keys = {"CommandLine", "ExecutablePath", "ParentProcessId", "ProcessId"}
    for raw_line in (legacy.stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, separator, value = line.partition("=")
        if separator and key.strip() in known_keys:
            key = key.strip()
            value = value.strip()
            if key == "ProcessId" and current.get("ProcessId") is not None:
                # Defensive boundary for a provider that repeats a property
                # before the next record; normal WMIC output places PID last.
                records.append(current)
                current = {}
            current[key] = value
            if key == "ProcessId":
                # WMIC's list formatter emits properties in an unstable order
                # and can put several blank lines between records.  PID is the
                # only reliable record terminator, so append immediately here.
                records.append(current)
                current = {}
                last_key = None
            else:
                last_key = key
        elif last_key == "CommandLine":
            # Command lines may wrap and may themselves contain '='.  Only
            # known property names above can start a new field.
            current[last_key] = f"{current.get(last_key, '')} {line}".strip()
    if current:
        records.append(current)
    return records


def _module_for(command_line: str) -> str | None:
    text = str(command_line or "")
    # Match an actual Python module invocation, rather than an arbitrary
    # occurrence of the module name in ``-c`` source, a log line, or a prompt.
    # Longest module first avoids the launcher substring being selected for a
    # headless launcher command.  The release launcher always uses ``-m``;
    # accepting a bare module token would reintroduce the old false-positive
    # process-name heuristic.
    for module in sorted(SUPERVISOR_MODULES, key=len, reverse=True):
        pattern = rf"(?:^|\s)-m\s+[\"']?{re.escape(module)}(?:[\"']?)(?=\s|$)"
        if re.search(pattern, text, re.IGNORECASE):
            return module
    return None


def _release_sha(executable: str | None, command_line: str = "") -> str | None:
    values = [str(executable or ""), str(command_line or "")]
    for value in values:
        for part in re.split(r"[\\/\s\"']+", value):
            if _SHA_RE.fullmatch(part):
                return part.lower()
    return None


def normalize_process(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize one raw CIM record into the shared identity shape."""
    command_line = str(record.get("CommandLine") or record.get("command_line") or "")
    module = _module_for(command_line)
    if module is None:
        return None
    raw_pid = record.get("ProcessId", record.get("pid"))
    try:
        pid = int(raw_pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    executable = str(record.get("ExecutablePath", record.get("executable_path")) or "")
    parent_raw = record.get("ParentProcessId", record.get("parent_pid"))
    try:
        parent_pid = int(parent_raw) if parent_raw not in (None, "") else None
    except (TypeError, ValueError):
        parent_pid = None
    release_sha = _release_sha(executable, command_line)
    identity = {
        "pid": pid,
        "parent_pid": parent_pid,
        "module": module,
        "executable": executable or None,
        "command_line": command_line,
        "release_sha": release_sha,
        # PID + executable + command module is stable enough for diagnostics;
        # creation time is added by ``with_creation_identity`` when Windows
        # can query it, preventing PID reuse from looking healthy.
        "identity_key": f"{pid}:{module}:{release_sha or 'unknown'}",
    }
    return identity


def with_creation_identity(record: dict[str, Any]) -> dict[str, Any]:
    """Attach the same creation identity used by Supervisor leases."""
    try:
        from ..supervisor.worker import process_creation_identity

        creation = process_creation_identity(int(record["pid"]))
    except (ImportError, KeyError, TypeError, ValueError):
        creation = {"pid": record.get("pid")}
    record = dict(record)
    record["creation_identity"] = creation
    create_time = creation.get("create_time") if isinstance(creation, Mapping) else None
    expected_exe = str(record.get("executable") or "")
    observed_exe = str(creation.get("exe") or "") if isinstance(creation, Mapping) else ""
    def _executable_matches(left: str, right: str) -> bool:
        if not left or not right:
            return True
        if os.path.normcase(left) == os.path.normcase(right):
            return True
        # CPython's Windows venv redirector can report the base interpreter
        # from QueryFullProcessImageNameW while WMIC retains the venv launcher
        # path.  Treat Python console/GUI interpreters as equivalent; a
        # non-Python executable remains a definitive PID-reuse signal.
        left_path, right_path = Path(left), Path(right)
        if {left_path.name.casefold(), right_path.name.casefold()} <= {
            "python.exe", "pythonw.exe"
        }:
            return True
        return (
            left_path.parent == right_path.parent
            and {left_path.name.casefold(), right_path.name.casefold()}
            == {"python.exe", "pythonw.exe"}
        )

    executable_matches = _executable_matches(expected_exe, observed_exe)
    if create_time is None:
        record["identity_status"] = "UNAVAILABLE"
    elif not executable_matches:
        # WMIC/CIM can return a command-line row after the PID has already
        # been reused.  A different executable is definitive stale evidence;
        # never let that row count as a live production Supervisor.
        record["identity_status"] = "PID_REUSED"
    else:
        record["identity_status"] = "VALID"
    try:
        record["live"] = bool(
            create_time is not None
            and executable_matches
            and _pid_alive(int(record["pid"])))
    except (KeyError, TypeError, ValueError):
        record["live"] = False
    record["identity_key"] = (
        f"{record['pid']}:{create_time}:{record['module']}:{record.get('release_sha') or 'unknown'}"
    )
    return record


def _pid_alive(pid: int) -> bool:
    try:
        from ..platform.windows_subprocess import pid_alive

        return bool(pid_alive(pid))
    except (ImportError, TypeError, ValueError):
        return False


def _python_executable(path: str | None) -> bool:
    return Path(str(path or "")).name.casefold() in {"python.exe", "pythonw.exe"}


def _venv_redirector(path: str | None) -> bool:
    value = str(path or "").replace("/", "\\").casefold()
    return _python_executable(path) and "\\scripts\\" in value


def _same_logical_command(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Return whether two rows are the Windows venv redirector pair.

    ``venv\\Scripts\\python[w].exe`` can be a tiny redirector that starts the
    base interpreter.  WMIC then reports two PIDs with the same module/home and
    nearly identical creation times.  They are one logical Supervisor, not a
    duplicate authority.  Independent launches are never collapsed because
    they lack the parent-child and redirector evidence below.
    """
    if left.get("module") != right.get("module"):
        return False
    if process_home(left) != process_home(right):
        return False
    if left.get("release_sha") != right.get("release_sha"):
        return False
    if not (_python_executable(left.get("executable"))
            and _python_executable(right.get("executable"))):
        return False
    if _venv_redirector(left.get("executable")) == _venv_redirector(right.get("executable")):
        return False
    left_parent, right_parent = left.get("parent_pid"), right.get("parent_pid")
    left_pid, right_pid = left.get("pid"), right.get("pid")
    if left_parent != right_pid and right_parent != left_pid:
        return False
    left_time = (left.get("creation_identity") or {}).get("create_time")
    right_time = (right.get("creation_identity") or {}).get("create_time")
    if left_time is None or right_time is None:
        # Some CIM providers omit CreationDate for the venv redirector even
        # while retaining its executable, command, and parent-child fields.
        # Those strict path/parent checks above still distinguish this wrapper
        # handoff from an independent Supervisor launch.
        return True
    # FILETIME units are 100 ns.  A redirector handoff is effectively
    # immediate; a wider window could hide a genuine duplicate launch.
    try:
        return abs(int(left_time) - int(right_time)) <= 5 * 10_000_000
    except (TypeError, ValueError):
        # CIM's CreationDate is second-resolution text while the native
        # identity probe returns FILETIME.  The census deliberately avoids a
        # second probe for every process; equal CIM timestamps plus the
        # parent/redirector evidence are sufficient to collapse that pair.
        return str(left_time).strip() == str(right_time).strip()


def _collapse_interpreter_wrappers(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse only proven venv-wrapper/child pairs into one authority row."""
    consumed: set[int] = set()
    result: list[dict[str, Any]] = []
    for index, left in enumerate(records):
        if index in consumed:
            continue
        pair_index: int | None = None
        for candidate_index in range(index + 1, len(records)):
            if candidate_index in consumed:
                continue
            if _same_logical_command(left, records[candidate_index]):
                pair_index = candidate_index
                break
        if pair_index is None:
            result.append(left)
            continue
        right = records[pair_index]
        consumed.add(pair_index)
        # Keep the child/base-interpreter row as the executable that actually
        # owns the running Python code; retain wrapper provenance for status
        # and restart diagnostics instead of silently dropping evidence.
        if left.get("parent_pid") == right.get("pid"):
            child, wrapper = left, right
        else:
            child, wrapper = right, left
        merged = dict(child)
        merged["interpreter_wrapper_pid"] = wrapper.get("pid")
        merged["interpreter_wrapper_identity"] = wrapper.get("identity_key")
        merged["logical_identity_key"] = (
            f"{child.get('identity_key')}|wrapper:{wrapper.get('identity_key')}"
        )
        result.append(merged)
    return result


def process_home(record: Mapping[str, Any]) -> str | None:
    """Extract the explicit ``--home`` value from a Supervisor command."""
    command = str(record.get("command_line") or record.get("CommandLine") or "")
    match = re.search(r"(?:^|\s)--home\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))", command, re.IGNORECASE)
    if not match:
        return None
    return next((group for group in match.groups() if group), None)


def process_matches_home(record: Mapping[str, Any], home: str | Path) -> bool:
    observed = process_home(record)
    if observed is None:
        return False
    try:
        return Path(observed).expanduser().resolve() == Path(home).expanduser().resolve()
    except OSError:
        return str(observed).lower() == str(home).lower()


def supervisor_processes(
    *, query: Callable[[], Iterable[Mapping[str, Any]]] | None = None,
    attach_creation_identity: bool = True,
) -> list[dict[str, Any]]:
    """Return all canonical Supervisor process identities, sorted by PID.

    ``query`` is injectable for deterministic tests and for hosts where CIM
    is unavailable.  No process is considered a Supervisor merely because
    its executable is named ``python``.
    """
    source = query or query_processes
    records: list[dict[str, Any]] = []
    try:
        raw_records = source()
    except Exception:  # noqa: BLE001 - status must fail closed, not crash
        raw_records = ()
    for raw in raw_records:
        normalized = normalize_process(raw)
        if normalized is not None:
            records.append(
                with_creation_identity(normalized)
                if attach_creation_identity else normalized)
    return sorted(
        _collapse_interpreter_wrappers(records),
        key=lambda item: int(item["pid"]),
    )


def supervisor_process_census(
    *, expected_sha: str | None = None, active_pid: int | None = None,
    expected_root: Path | None = None,
    active_creation_identity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Classify observed Supervisor rows against one durable owner.

    This is the production process-truth authority used by the status and
    Command Center projections. It is deliberately read-only: the database
    lease remains the owner authority, while this census detects stale release
    rows, PID reuse, duplicates, and unidentified launchers.
    """
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match "
        "'jarvis_foundry\\.(supervisor\\.run|deployment\\.(headless_launcher|launcher))' } | "
        "ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t"
        "$([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$_.ExecutablePath)))`t"
        "$([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$_.CommandLine)))`t"
        "$($_.CreationDate)\" }"
    )
    command = [_powershell_executable(), "-NoProfile", "-Command", script]
    try:
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=30, check=False,
            **headless_run_kwargs(),
        )
        if result.returncode != 0 and not (result.stdout or "").strip():
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = (result.stdout or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
        rows = [payload] if isinstance(payload, dict) else list(payload)
    except (TypeError, ValueError):
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
    census_rows: list[dict[str, Any]] = []
    for raw_row in rows:
        if not isinstance(raw_row, Mapping):
            continue
        command_line = str(raw_row.get("CommandLine") or "")
        executable = str(raw_row.get("ExecutablePath") or "")
        if root_needle is not None:
            process_text = f"{command_line} {executable}".replace("/", "\\").lower()
            if root_needle not in process_text:
                continue
        normalized = normalize_process(raw_row)
        if normalized is None:
            continue
        # Keep the provider's timestamp as an internal collapse key.  The
        # durable owner identity is still probed below only for active_pid;
        # an unprobed row must never be presented as authenticated evidence.
        normalized["creation_time"] = raw_row.get("CreationDate")
        normalized["creation_identity"] = {
            "pid": normalized["pid"],
            "create_time": raw_row.get("CreationDate"),
            "exe": normalized.get("executable"),
        }
        census_rows.append(normalized)

    # A Windows venv launch normally appears as a redirector plus its base
    # interpreter.  Collapse only the proven parent/child pair; independent
    # launches remain separate duplicate evidence.
    census_rows = _collapse_interpreter_wrappers(census_rows)

    observed: list[dict[str, Any]] = []
    from ..supervisor.worker import process_creation_identity
    for row in census_rows:
        command_line = str(row.get("command_line") or "")
        executable = str(row.get("executable") or "")
        release_sha = row.get("release_sha")
        try:
            pid = int(row.get("pid"))
        except (TypeError, ValueError):
            pid = None
        try:
            parent_pid = int(row.get("parent_pid"))
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
        if row.get("interpreter_wrapper_pid") is not None:
            record["interpreter_wrapper_pid"] = row["interpreter_wrapper_pid"]
            record["interpreter_wrapper_identity"] = row.get(
                "interpreter_wrapper_identity")
            record["logical_identity_key"] = row.get("logical_identity_key")
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
    return sorted(observed, key=lambda item: int(item["pid"] or 0))


def release_sha_for_id(release_id: str | None, *, root: str | Path | None = None) -> str | None:
    """Resolve a durable ``release_id`` back to its immutable source SHA.

    The immutable launcher records ``JARVIS_RELEASE_ID`` in the Supervisor
    ledger, while Windows process inspection cannot read a process's
    environment and the command line may not contain a SHA.  Status therefore
    needs a read-only bridge from the recorded release id to the release
    manifest.  Only manifests whose directory, ``source_sha`` and
    ``release_id`` all agree are accepted; an unknown or forged mapping stays
    ``None`` and parity remains degraded.
    """
    wanted = str(release_id or "").strip()
    if not wanted:
        return None
    try:
        from .layout import install_root

        releases_root = install_root(root) / "releases"
        if not releases_root.is_dir():
            return None
        # Current/previous are the common case.  Include every immutable
        # release directory as a fallback so a restarted process on a
        # historical release remains diagnosable after a pointer moved.
        candidates: list[Path] = []
        for directory in sorted(releases_root.iterdir(), key=lambda item: item.name):
            if directory.is_dir() and _SHA_RE.fullmatch(directory.name):
                candidates.append(directory)
        for directory in candidates:
            manifest_path = directory / "RELEASE.json"
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, Mapping):
                continue
            source_sha = str(payload.get("source_sha") or "").lower()
            if (str(payload.get("release_id") or "") == wanted
                    and source_sha == directory.name.lower()
                    and _SHA_RE.fullmatch(source_sha)):
                return source_sha
    except (OSError, TypeError, ValueError):
        return None
    return None


def parity_state(
    *,
    canonical_sha: str | None,
    installed_sha: str | None,
    runtime_shas: Iterable[str | None],
    supervisor_count: int,
) -> str:
    """Classify source/installed/runtime identity without a healthy default."""
    runtime = {str(value).lower() for value in runtime_shas if value}
    canonical = str(canonical_sha or "").lower()
    installed = str(installed_sha or "").lower()
    if supervisor_count == 0:
        return "MISSING_RUNTIME"
    if supervisor_count > 1:
        return "DUPLICATE_SUPERVISOR"
    if not installed:
        return "INSTALLED_RELEASE_UNKNOWN"
    if not runtime:
        return "RUNTIME_RELEASE_UNKNOWN"
    if installed not in runtime:
        return "RUNTIME_MISMATCH"
    if not canonical:
        return "CANONICAL_SOURCE_UNKNOWN"
    if canonical != installed:
        return "SOURCE_AHEAD"
    return "MATCH"


__all__ = [
    "SUPERVISOR_MODULES", "normalize_process", "parity_state",
    "process_home", "process_matches_home",
    "query_processes", "release_sha_for_id", "supervisor_processes",
    "supervisor_process_census",
    "with_creation_identity",
]
