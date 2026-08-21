from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _cleanup_orphan_gate_tasks() -> list[str]:
    """Remove leftover WP7 staging tasks that can flash console windows."""
    if os.name != "nt":
        return []
    result = subprocess.run(
        ["schtasks", "/Query", "/FO", "LIST"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False,
    )
    removed: list[str] = []
    if result.returncode != 0:
        return removed
    current: str | None = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("TaskName:"):
            current = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Task To Run:") and current:
            task = current
            current = None
            if not task.startswith("\\JF-WP7Gate-"):
                continue
            if "pytest-of" not in stripped and "foundry-home" not in stripped:
                continue
            delete = subprocess.run(
                ["schtasks", "/Delete", "/TN", task, "/F"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=60, check=False,
            )
            if delete.returncode == 0:
                removed.append(task)
    return removed


@pytest.fixture(scope="session", autouse=True)
def _purge_orphan_installer_tasks():
  yield
  _cleanup_orphan_gate_tasks()

