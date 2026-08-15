"""Shared helpers for the R7 evidence gates."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = REPO_ROOT / "docs" / "remaster" / "evidence" / "r7"
SCRIPTS_ROOT = REPO_ROOT / "scripts" / "remaster" / "r7"
TESTS_ROOT = REPO_ROOT / "tests" / "remaster" / "r7"
SOURCE_ROOT = REPO_ROOT / "src" / "jarvis_foundry" / "kernel" / "portfolio"

TEMP_BASE = Path(
    os.environ.get("JARVIS_R7_TEMP", "C:/Users/b/AppData/Local/Temp/jarvis-r7-builder")
)

# Fixed clock for deterministic gate evidence; wall time is recorded separately.
EVIDENCE_UTC = "2026-08-15T16:30:00Z"

STAGED_ROOT = TEMP_BASE / "staged"
WORK_DIR = TEMP_BASE / "work"
PROVING_ROOT = TEMP_BASE / "proving"
SNAPSHOT_DIR = WORK_DIR / "snapshots"
VAULT_ROOT = Path("C:/Users/b/Blendi")

PROJECT_ROOTS: dict[str, Path] = {
    "leadgen": Path("C:/Users/b/Documents/LeadGen"),
    "operators": Path("C:/Users/b/Documents/OperatorOS"),
    "pivotlens": Path("C:/Users/b/Claude/pivotlens"),
    "jarvis-foundry": Path(
        "C:/Users/b/Documents/ChatGPT/Autonomous AI powered by Hoowen/JARVIS-Foundry"
    ),
}

EXCLUDED_VAULT_CATEGORIES: tuple[str, ...] = (
    "00 - Inbox",
    "01 - Identity",
    "05 - Personal",
    "08 - Archive",
    "90 - Logs",
    ".obsidian",
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


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


def record_enabled() -> bool:
    return os.environ.get("R7_RECORD_EVIDENCE", "") == "1"


def tool_versions() -> dict[str, str]:
    completed = subprocess.run(
        [
            "C:/Users/b/AppData/Local/Temp/jarvis-r3-venv/Scripts/python.exe",
            "-m",
            "pip",
            "list",
            "--format=json",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        return {}
    packages = json.loads(completed.stdout)
    wanted = {
        "pytest",
        "pytest-cov",
        "coverage",
        "mypy",
        "ruff",
        "bandit",
        "hypothesis",
        "pip-audit",
    }
    return {item["name"]: item["version"] for item in packages if item["name"] in wanted}


def evidence_bytes() -> int:
    return sum(path.stat().st_size for path in EVIDENCE_DIR.glob("*") if path.is_file())


def owned_r7_files(root: Path) -> list[str]:
    """Every R7-owned file bound by the artifact manifest."""
    roots = [
        root / "src" / "jarvis_foundry" / "kernel" / "portfolio",
        root / "scripts" / "remaster" / "r7",
        root / "tests" / "remaster" / "r7",
        root / "docs" / "remaster" / "evidence" / "r7",
    ]
    paths: list[str] = []
    for base in roots:
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                paths.append(str(path.relative_to(root).as_posix()))
    return sorted(paths)


def safe_clear(path: Path) -> None:
    """Clear an isolated temp path only; never a protected path."""
    resolved = path.resolve()
    if not resolved.is_relative_to(TEMP_BASE.resolve()):
        raise RuntimeError(f"refusing to clear outside R7 temp: {resolved}")
    import shutil

    if resolved.is_dir():
        shutil.rmtree(resolved)
    elif resolved.is_file():
        resolved.unlink()
