"""Read-only live guard for R12: protected main, live daemon, tasks, vault."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    LIVE_DB_SNAPSHOT,
    LIVE_DIR,
    PROTECTED_MAIN,
    VAULT_ROOT,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json,
)

POWERSHELL_CAPTURE = r"""
$ErrorActionPreference = 'SilentlyContinue'
$taskNames = @('JARVIS-Foundry-Supervisor', 'Hermes_Gateway')
$taskRows = @()
foreach ($name in $taskNames) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        $xml = Export-ScheduledTask -TaskName $name | Out-String
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($xml)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLower()
        $taskRows += [pscustomobject]@{
            task_name = $name
            exists = $true
            status = $task.State.ToString()
            xml_sha256 = $hash
        }
    } else {
        $taskRows += [pscustomobject]@{ task_name = $name; exists = $false }
    }
}
$sha = [System.Security.Cryptography.SHA256]::Create()
$procs = Get-CimInstance Win32_Process | Where-Object {
    ($_.ExecutablePath -like '*python*') -and
    (($_.CommandLine -like '*jarvis_foundry*') -or
     ($_.CommandLine -like '*JARVIS-Foundry*'))
}
$procRows = @()
foreach ($p in $procs) {
    $cmd = [string]$p.CommandLine
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($cmd)
    $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLower()
    $procRows += [pscustomobject]@{
        pid = $p.ProcessId
        creation_date = [string]$p.CreationDate
        executable = [string]$p.ExecutablePath
        command_sha256 = $hash
    }
}
[pscustomobject]@{
    captured_utc = (Get-Date).ToUniversalTime().ToString('o')
    protected_tasks = @($taskRows)
    protected_supervisor_processes = @($procRows)
} | ConvertTo-Json -Depth 5 -Compress
"""


def _capture() -> dict[str, Any]:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            POWERSHELL_CAPTURE,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"live guard capture failed: {completed.stderr[:500]}")
    payload: dict[str, Any] = json.loads(completed.stdout.strip())
    payload["files"] = {}
    for name in ("foundry.db", "foundry.db-wal", "foundry.db-shm", "foundry-profile.yaml"):
        path = LIVE_DIR / name
        if path.is_file():
            payload["files"][name] = {
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
    main_head = subprocess.run(
        ["git", "-C", str(PROTECTED_MAIN), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    main_status = subprocess.run(
        ["git", "-C", str(PROTECTED_MAIN), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    payload["protected_main"] = {
        "path": str(PROTECTED_MAIN),
        "head": main_head.stdout.strip() if main_head.returncode == 0 else None,
        "status_sha256": sha256_bytes(
            main_status.stdout.encode("utf-8") if main_status.returncode == 0 else b""
        ),
    }
    vault_entries: list[str] = []
    vault_total = 0
    vault_count = 0
    if VAULT_ROOT.is_dir():
        for path in sorted(VAULT_ROOT.rglob("*")):
            if not path.is_file():
                continue
            if any(part.startswith(".") for part in path.relative_to(VAULT_ROOT).parts):
                continue
            stat = path.stat()
            vault_entries.append(
                f"{path.relative_to(VAULT_ROOT).as_posix()}:{stat.st_size}:{stat.st_mtime_ns}:{sha256_file(path)}"
            )
            vault_total += stat.st_size
            vault_count += 1
    payload["vault"] = {
        "path": str(VAULT_ROOT),
        "file_count": vault_count,
        "total_bytes": vault_total,
        "manifest_sha256": sha256_bytes("|".join(vault_entries).encode("utf-8")),
    }
    return payload


def _live_db_provenance(before: dict[str, Any]) -> dict[str, Any]:
    if not LIVE_DB_SNAPSHOT.is_file():
        return {"testable": False, "detail": "start snapshot missing"}
    import sqlite3
    import tempfile

    from jarvis_foundry.kernel.compat.readers import LegacyEventsReader

    before_rows = LegacyEventsReader(LIVE_DB_SNAPSHOT).read_rows()
    after_path = Path(tempfile.mkdtemp(prefix="jarvis-r12-live-guard-")) / "after.db"
    source = sqlite3.connect(f"file:{LIVE_DIR / 'foundry.db'}?mode=ro", uri=True)
    destination = sqlite3.connect(after_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    after_rows = LegacyEventsReader(after_path).read_rows()
    before_count = len(before_rows)
    after_count = len(after_rows)
    common_count = min(before_count, after_count)
    common_identical = all(
        before_rows[index].event_id == after_rows[index].event_id
        and before_rows[index].payload_hash == after_rows[index].payload_hash
        and before_rows[index].event_type == after_rows[index].event_type
        for index in range(common_count)
    )
    extra = after_rows[common_count:]
    extra_types = sorted({row.event_type for row in extra})
    supervisor_only = all(row.event_type.startswith("supervisor.") for row in extra)
    result = {
        "testable": True,
        "before_event_count": before_count,
        "after_event_count": after_count,
        "event_delta": after_count - before_count,
        "historical_rows_preserved_unchanged": common_identical,
        "extra_rows_supervisor_only": supervisor_only,
        "extra_event_types": extra_types,
        "r12_attributable_mutation": not (
            common_identical and supervisor_only and after_count >= before_count
        ),
    }
    after_path.unlink(missing_ok=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("before", "after", "compare"), required=True)
    parser.add_argument("--before", type=Path, default=EVIDENCE_DIR / "R12_LIVE_GUARD_BEFORE.json")
    parser.add_argument("--after", type=Path, default=EVIDENCE_DIR / "R12_LIVE_GUARD_AFTER.json")
    parser.add_argument(
        "--comparison", type=Path, default=EVIDENCE_DIR / "R12_LIVE_GUARD_COMPARISON.json"
    )
    args = parser.parse_args(argv)

    if args.mode in {"before", "after"}:
        payload = _capture()
        if args.mode == "before" and LIVE_DIR.joinpath("foundry.db").is_file():
            import sqlite3

            source = sqlite3.connect(f"file:{LIVE_DIR / 'foundry.db'}?mode=ro", uri=True)
            destination = sqlite3.connect(LIVE_DB_SNAPSHOT)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
        output = args.before if args.mode == "before" else args.after
        write_json(output, payload)
        print(
            f"live guard {args.mode}: files={sorted(payload['files'])} "
            f"tasks={len(payload['protected_tasks'])} "
            f"processes={len(payload['protected_supervisor_processes'])} "
            f"vault_files={payload['vault']['file_count']}"
        )
        return 0

    before = read_json(args.before)
    after = read_json(args.after)
    protected_files = ("foundry.db", "foundry-profile.yaml")
    file_unchanged = {
        name: before["files"].get(name) == after["files"].get(name) for name in protected_files
    }
    tasks_unchanged = before.get("protected_tasks") == after.get("protected_tasks")
    before_commands = sorted(
        str(item.get("command_sha256", ""))
        for item in before.get("protected_supervisor_processes", [])
    )
    after_commands = sorted(
        str(item.get("command_sha256", ""))
        for item in after.get("protected_supervisor_processes", [])
    )
    processes_unchanged = before_commands == after_commands
    main_unchanged = before.get("protected_main") == after.get("protected_main")
    vault_unchanged = before.get("vault") == after.get("vault")
    provenance = _live_db_provenance(before)
    protected_state_unchanged = (
        file_unchanged.get("foundry-profile.yaml", False)
        and tasks_unchanged
        and processes_unchanged
        and main_unchanged
        and vault_unchanged
        and not provenance["r12_attributable_mutation"]
    )
    comparison = {
        "schema": "jarvis-r12-live-guard-comparison-v1",
        "protected_state_unchanged": protected_state_unchanged,
        "file_unchanged": file_unchanged,
        "tasks_unchanged": tasks_unchanged,
        "processes_unchanged": processes_unchanged,
        "process_command_multiset_unchanged": processes_unchanged,
        "process_restart_observation": (
            "same command hashes observed; a live supervisor may recycle PIDs while "
            "the protected command set remains unchanged"
            if processes_unchanged
            else "protected command set changed"
        ),
        "protected_main_unchanged": main_unchanged,
        "vault_unchanged": vault_unchanged,
        "live_db_provenance": provenance,
        "live_wal_activity_expected": {
            name: {
                "before_sha256": before["files"].get(name, {}).get("sha256"),
                "after_sha256": after["files"].get(name, {}).get("sha256"),
            }
            for name in ("foundry.db-wal", "foundry.db-shm")
        },
        "before": before["captured_utc"],
        "after": after["captured_utc"],
    }
    write_json(args.comparison, comparison)
    print(
        f"live guard compare: protected_state_unchanged={protected_state_unchanged} "
        f"main={main_unchanged} vault={vault_unchanged} tasks={tasks_unchanged} "
        f"processes={processes_unchanged} "
        f"r12_attributable_mutation={provenance['r12_attributable_mutation']}"
    )
    return 0 if protected_state_unchanged else 1


if __name__ == "__main__":
    sys.exit(main())
