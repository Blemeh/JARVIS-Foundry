"""Owned disposable process and fault-matrix adapters."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..platform.windows_subprocess import headless_popen_kwargs, sanitized_env


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _environment() -> dict[str, str]:
    environment = sanitized_env()
    source = str(_repo_root() / "src")
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = source if not existing else source + os.pathsep + existing
    return environment


def _wait_for_marker(path: Path, process: subprocess.Popen[str], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.05)
    return path.exists()


def run_owned_supervisor_restart_probe(output_root: str | Path, *, timeout_seconds: float = 15.0) -> dict[str, Any]:
    """Kill and replace only the supervisor fixture this function owns."""
    from jarvis_foundry.database import FoundryDatabase

    root = Path(output_root) / "process-restart"
    root.mkdir(parents=True, exist_ok=True)
    database_path = root / "fixture.db"
    stop_path = root / "STOP"
    marker_one = root / "started-one.marker"
    marker_two = root / "started-two.marker"
    FoundryDatabase(database_path).initialize()
    script = _repo_root() / "tests" / "foundry" / "supervisor_fixture.py"
    command = [sys.executable, str(script), str(database_path), str(stop_path), str(marker_one), "5"]
    def _stop(process: subprocess.Popen[str]) -> int:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        return int(process.returncode if process.returncode is not None else -1)

    first = subprocess.Popen(
        command, cwd=_repo_root(), env=_environment(), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **headless_popen_kwargs(),
    )
    first_started = _wait_for_marker(marker_one, first, timeout_seconds)
    first_pid = first.pid
    if not first_started:
        _stop(first)
        return {
            "status": "BLOCKED_EXTERNAL",
            "reason": "owned supervisor fixture did not reach its marker",
            "first_pid": first_pid,
        }
    first_returncode = _stop(first)
    replacement_command = [sys.executable, str(script), str(database_path), str(stop_path), str(marker_two), "5"]
    second = subprocess.Popen(
        replacement_command, cwd=_repo_root(), env=_environment(), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **headless_popen_kwargs(),
    )
    replacement_started = _wait_for_marker(marker_two, second, timeout_seconds)
    second_pid = second.pid
    second_returncode = _stop(second)
    if not replacement_started:
        return {
            "status": "FAIL",
            "reason": "replacement supervisor did not reclaim the disposable lease",
            "first_pid": first_pid,
            "second_pid": second_pid,
            "first_returncode": first_returncode,
            "second_returncode": second_returncode,
        }
    database = FoundryDatabase(database_path)
    return {
        "status": "PASS",
        "first_pid": first_pid,
        "second_pid": second_pid,
        "first_returncode": first_returncode,
        "second_returncode": second_returncode,
        "distinct_process_identity": first_pid != second_pid,
        "event_count": database.event_count(readonly=True),
        "marker_one": marker_one.read_text(encoding="utf-8"),
        "marker_two": marker_two.read_text(encoding="utf-8"),
    }


def run_v7_fault_matrix(output_root: str | Path, *, timeout_seconds: float = 90.0) -> dict[str, Any]:
    """Rerun the canonical V7 matrix in a subprocess and return its JSON."""
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    output = root / "V7_FAULT_MATRIX.json"
    script = _repo_root() / "scripts" / "v7_resilience_fault_matrix.py"
    result = subprocess.run(
        [sys.executable, str(script), "--output", str(output)],
        cwd=_repo_root(), env=_environment(), capture_output=True, text=True,
        timeout=timeout_seconds, check=False, **headless_popen_kwargs(),
    )
    if not output.exists():
        return {
            "status": "BLOCKED_EXTERNAL" if result.returncode == 2 else "FAIL",
            "reason": "fault matrix emitted no JSON artifact",
            "returncode": result.returncode,
            "stderr": result.stderr[-2000:],
        }
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["runner_returncode"] = result.returncode
    payload["status"] = "PASS" if result.returncode == 0 and payload.get("verdict") == "PASS" else "FAIL"
    return payload
