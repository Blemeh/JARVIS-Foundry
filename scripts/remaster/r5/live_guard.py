"""Read-only live guard for R5: protected files, task, and processes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, sha256_file, write_json

LIVE_DIR = Path("C:/Users/b/AppData/Local/hermes/foundry")
TEMP_WORK = Path("C:/Users/b/AppData/Local/Temp/jarvis-r5-work")
BEFORE_SNAPSHOT = TEMP_WORK / "live-foundry-r5.db"

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
    return payload


def _task_identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        task["task_name"]: {"exists": task["exists"], "xml_sha256": task.get("xml_sha256")}
        for task in payload["protected_tasks"]
    }


def _process_identity(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (
            {
                "executable": process.get("executable"),
                "creation_date": process.get("creation_date"),
                "command_sha256": process.get("command_sha256"),
            }
            for process in payload["protected_supervisor_processes"]
        ),
        key=lambda item: (
            str(item["executable"]),
            str(item["creation_date"]),
            str(item["command_sha256"]),
        ),
    )


def _live_db_provenance() -> dict[str, Any]:
    """Verify live growth is daemon-only, using read-only temp copies."""
    if not BEFORE_SNAPSHOT.is_file():
        return {
            "testable": False,
            "detail": "start snapshot missing; cannot prove provenance",
        }
    import sqlite3
    import tempfile
    from pathlib import Path as _Path

    from jarvis_foundry.kernel.compat.readers import LegacyEventsReader

    before_rows = LegacyEventsReader(BEFORE_SNAPSHOT).read_rows()
    after_path = _Path(tempfile.mkdtemp(prefix="jarvis-r5-live-guard-")) / "after.db"
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
        "r5_attributable_mutation": not (
            common_identical and supervisor_only and after_count >= before_count
        ),
    }
    after_path.unlink(missing_ok=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("before", "after", "compare"), required=True)
    parser.add_argument(
        "--before",
        type=Path,
        default=EVIDENCE_DIR / "R5_LIVE_GUARD_BEFORE.json",
    )
    parser.add_argument(
        "--after",
        type=Path,
        default=EVIDENCE_DIR / "R5_LIVE_GUARD_AFTER.json",
    )
    parser.add_argument(
        "--comparison",
        type=Path,
        default=EVIDENCE_DIR / "R5_LIVE_GUARD_COMPARISON.json",
    )
    args = parser.parse_args(argv)

    if args.mode in {"before", "after"}:
        output = args.before if args.mode == "before" else args.after
        payload = _capture()
        write_json(output, payload)
        print(
            f"live guard {args.mode}: files={sorted(payload['files'])} "
            f"tasks={len(payload['protected_tasks'])} "
            f"processes={len(payload['protected_supervisor_processes'])}"
        )
        return 0

    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    protected_files = ("foundry.db", "foundry-profile.yaml")
    file_unchanged = {
        name: before["files"].get(name) == after["files"].get(name) for name in protected_files
    }
    live_wal = {
        name: {
            "before_sha256": before["files"].get(name, {}).get("sha256"),
            "after_sha256": after["files"].get(name, {}).get("sha256"),
        }
        for name in ("foundry.db-wal", "foundry.db-shm")
    }
    tasks_unchanged = _task_identity(before) == _task_identity(after)
    processes_unchanged = _process_identity(before) == _process_identity(after)
    provenance = _live_db_provenance()
    db_file_unchanged = before["files"].get("foundry.db") == after["files"].get("foundry.db")
    protected_state_unchanged = (
        file_unchanged.get("foundry-profile.yaml", False)
        and tasks_unchanged
        and processes_unchanged
        and not provenance["r5_attributable_mutation"]
    )
    comparison = {
        "schema": "jarvis-r5-live-guard-comparison-v1",
        "protected_state_unchanged": protected_state_unchanged,
        "file_unchanged": file_unchanged,
        "tasks_unchanged": tasks_unchanged,
        "processes_unchanged": processes_unchanged,
        "live_db_file_hash_changed": not db_file_unchanged,
        "live_db_provenance": provenance,
        "live_wal_activity_expected": live_wal,
        "before": before["captured_utc"],
        "after": after["captured_utc"],
    }
    write_json(args.comparison, comparison)
    print(
        f"live guard compare: protected_state_unchanged={protected_state_unchanged} "
        f"db_file_hash_changed={not db_file_unchanged} "
        f"r5_attributable_mutation={provenance['r5_attributable_mutation']} "
        f"tasks={tasks_unchanged} processes={processes_unchanged}"
    )
    return 0 if protected_state_unchanged else 1


if __name__ == "__main__":
    sys.exit(main())
