"""Shared helpers for the R4 gate scripts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = REPO_ROOT / "docs" / "remaster" / "evidence" / "r4"
KERNEL_ROOT = REPO_ROOT / "src" / "jarvis_foundry" / "kernel"
TESTS_ROOT = REPO_ROOT / "tests" / "remaster" / "r4"
SCRIPTS_ROOT = REPO_ROOT / "scripts" / "remaster" / "r4"


def repo_root() -> Path:
    return REPO_ROOT


def evidence_path(name: str) -> Path:
    return EVIDENCE_DIR / name


def record_enabled() -> bool:
    return os.environ.get("R4_RECORD_EVIDENCE", "") == "1"


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
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


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
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "output": completed.stdout + completed.stderr,
    }


def kernel_modules() -> list[str]:
    return sorted(
        path.relative_to(KERNEL_ROOT).with_suffix("").as_posix().replace("/", ".")
        for path in KERNEL_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and path.name != "ports.py"
    )


def kernel_files(root: Path) -> list[str]:
    return sorted(
        str(path.relative_to(root).as_posix())
        for path in (root / "src" / "jarvis_foundry" / "kernel").rglob("*.py")
        if "__pycache__" not in path.parts
    )


def r4_owned_files(root: Path) -> list[str]:
    scripts = sorted((root / "scripts" / "remaster" / "r4").glob("*.py"))
    tests = sorted((root / "tests" / "remaster" / "r4").glob("*.py"))
    return [
        *kernel_files(root),
        *[str(path.relative_to(root).as_posix()) for path in scripts],
        *[str(path.relative_to(root).as_posix()) for path in tests],
    ]
