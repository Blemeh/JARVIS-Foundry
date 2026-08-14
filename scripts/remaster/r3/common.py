"""Shared helpers for the R3 gate scripts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = REPO_ROOT / "docs" / "remaster" / "evidence" / "r3"


def repo_root() -> Path:
    return REPO_ROOT


def evidence_path(name: str) -> Path:
    return EVIDENCE_DIR / name


def record_enabled() -> bool:
    """Evidence files are written only in builder/local-record mode."""
    return os.environ.get("R3_RECORD_EVIDENCE", "") == "1"


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
    timeout_seconds: int = 1200,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a command and capture timing plus output for honest evidence."""
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


def record_command_result(
    evidence_name: str,
    result: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
    raw_name: str | None = None,
) -> dict[str, Any]:
    """Write a JSON evidence record plus optional raw output file."""
    payload: dict[str, Any] = {
        "command": result["command"],
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
    }
    if extra:
        payload.update(extra)
    if raw_name:
        raw = evidence_path(raw_name)
        raw.write_text(result["output"], encoding="utf-8")
        payload["raw_output"] = raw_name
        payload["raw_output_sha256"] = sha256_file(raw)
    write_json(evidence_path(evidence_name), payload)
    return payload
