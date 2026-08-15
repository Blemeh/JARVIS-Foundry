"""Shared helpers for the R5 gate scripts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = REPO_ROOT / "docs" / "remaster" / "evidence" / "r5"
KERNEL_COMPAT_ROOT = REPO_ROOT / "src" / "jarvis_foundry" / "kernel" / "compat"
TESTS_ROOT = REPO_ROOT / "tests" / "remaster" / "r5"
SCRIPTS_ROOT = REPO_ROOT / "scripts" / "remaster" / "r5"
TEMP_WORK = Path(os.environ.get("JARVIS_R5_TEMP", "C:/Users/b/AppData/Local/Temp/jarvis-r5-work"))

LIVE_COPY = TEMP_WORK / "live-foundry-r5.db"
LIVE_R0_COPY = TEMP_WORK / "live-foundry-r0.db"
ISOLATED_V7_COPY = TEMP_WORK / "isolated-v7-r0.db"


def repo_root() -> Path:
    return REPO_ROOT


def evidence_path(name: str) -> Path:
    return EVIDENCE_DIR / name


def record_enabled() -> bool:
    return os.environ.get("R5_RECORD_EVIDENCE", "") == "1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: int = 3600,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        timeout=timeout_seconds,
        capture_output=True,
        text=True,
        env=merged_env,
        check=False,
    )
    wall_seconds = round(time.monotonic() - started, 3)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "wall_seconds": wall_seconds,
        "output": completed.stdout + completed.stderr,
    }


def sample_specs() -> dict[str, Path]:
    return {
        "live_snapshot": LIVE_COPY,
        "live_r0_frozen": LIVE_R0_COPY,
        "isolated_v7_frozen": ISOLATED_V7_COPY,
    }


def owned_r5_files(root: Path) -> list[str]:
    """Every R5-owned file bound by the artifact manifest."""
    paths: list[str] = []
    roots = [
        root / "src" / "jarvis_foundry" / "kernel" / "compat",
        root / "scripts" / "remaster" / "r5",
        root / "tests" / "remaster" / "r5",
        root / "docs" / "remaster" / "evidence" / "r5",
    ]
    for base in roots:
        for path in base.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                paths.append(str(path.relative_to(root).as_posix()))
    return sorted(paths)
