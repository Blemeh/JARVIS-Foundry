"""Real subprocess dispatch of R8 workers against the isolated venv."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - bounded subprocess dispatch with fixed explicit paths
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.orchestration.mission_context import MissionContext
from jarvis_foundry.orchestration.paths import TEMP_BASE, resolve_within


@dataclass(frozen=True)
class DispatchRun:
    command: tuple[str, ...]
    exit_code: int
    wall_seconds: float
    output: str
    result_path: str
    worker_id: str


def dispatch(
    *,
    mission: MissionContext,
    worker_module: str,
    worker_id: str,
    python: Path,
    clone: Path,
    context_path: Path,
    result_path: Path,
    timeout_seconds: int = 1200,
    proving_root: Path | None = None,
) -> DispatchRun:
    """Run a real worker subprocess that mutates only the isolated clone."""
    clone_root = resolve_within(proving_root or TEMP_BASE, Path(clone))
    context = resolve_within(context_path.parent, context_path)
    result = resolve_within(result_path.parent, result_path)
    command = (
        str(python),
        "-m",
        worker_module,
        "--mission",
        str(context),
        "--clone",
        str(clone_root),
        "--result",
        str(result),
    )
    started = time.monotonic()
    env = dict(os.environ)
    env["JARVIS_R8_WORKER"] = "1"
    completed = subprocess.run(  # nosec B603 B607 - no shell, fixed python path
        command,
        cwd=str(clone_root),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
        check=False,
    )
    return DispatchRun(
        command=tuple(command),
        exit_code=completed.returncode,
        wall_seconds=round(time.monotonic() - started, 3),
        output=completed.stdout + completed.stderr,
        result_path=str(result),
        worker_id=worker_id,
    )


def run_tests(
    *,
    command: list[str],
    cwd: Path,
    python: Path,
    source_root: Path,
    timeout_seconds: int = 1200,
    proving_root: Path | None = None,
) -> dict[str, Any]:
    """Run a required test command inside the isolated clone and bind evidence."""
    clone_root = resolve_within(proving_root or TEMP_BASE, Path(cwd))
    started = time.monotonic()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(source_root)
    completed = subprocess.run(  # nosec B603 B607 - no shell, fixed python path
        [str(python), *command],
        cwd=str(clone_root),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
        check=False,
    )
    return {
        "command": [str(python), *command],
        "exit_code": completed.returncode,
        "wall_seconds": round(time.monotonic() - started, 3),
        "output": completed.stdout + completed.stderr,
    }
