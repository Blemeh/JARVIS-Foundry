"""Read-only live guard for protected V7 state (R4)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, write_json

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


def capture() -> dict[str, Any]:
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
    payload["captured_utc"] = datetime.now(UTC).isoformat(timespec="milliseconds")
    return payload


def task_identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        task["task_name"]: {
            "exists": task["exists"],
            "xml_sha256": task.get("xml_sha256"),
        }
        for task in payload["protected_tasks"]
    }


def process_identity(payload: dict[str, Any]) -> list[dict[str, Any]]:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("before", "after", "compare"), required=True)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R4_LIVE_GUARD.json")
    parser.add_argument("--before", type=Path, default=EVIDENCE_DIR / "R4_LIVE_GUARD_BEFORE.json")
    parser.add_argument("--after", type=Path, default=EVIDENCE_DIR / "R4_LIVE_GUARD_AFTER.json")
    args = parser.parse_args(argv)

    if args.mode in {"before", "after"}:
        payload = capture()
        write_json(args.output, payload)
        print(
            f"live guard {args.mode}: tasks={len(payload['protected_tasks'])} "
            f"processes={len(payload['protected_supervisor_processes'])}"
        )
        return 0

    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    tasks_unchanged = task_identity(before) == task_identity(after)
    processes_unchanged = process_identity(before) == process_identity(after)
    comparison = {
        "schema": "jarvis-r4-live-guard-comparison-v1",
        "tasks_unchanged": tasks_unchanged,
        "processes_unchanged": processes_unchanged,
        "protected_state_unchanged": tasks_unchanged and processes_unchanged,
        "before": before["captured_utc"],
        "after": after["captured_utc"],
    }
    write_json(args.output, comparison)
    print(
        f"live guard compare: tasks_unchanged={tasks_unchanged} "
        f"processes_unchanged={processes_unchanged}"
    )
    return 0 if comparison["protected_state_unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
