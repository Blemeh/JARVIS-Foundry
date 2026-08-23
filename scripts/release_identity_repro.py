"""Build, validate, start, and roll back releases in disposable roots."""

from __future__ import annotations

import gc
import json
import os
import subprocess
import tempfile
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.layout import default_live_foundry_home
from jarvis_foundry.deployment.release import (
    build_release,
    rollback_current,
    set_current,
    validate_release,
)
from jarvis_foundry.migrations import SCHEMA_VERSION


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        timeout=60, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _run_release_python(python_exe: Path, *, root: Path, home: Path) -> dict[str, object]:
    environment = os.environ.copy()
    environment["JARVIS_INSTALL_ROOT"] = str(root)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    result = subprocess.run(
        [str(python_exe), "-m", "jarvis_foundry.deployment.launcher",
         "--home", str(home), "--once"],
        capture_output=True, text=True, timeout=120, check=False,
        env=environment, cwd=python_exe.parent.parent.parent,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"controlled release start failed ({result.returncode}): "
            f"{result.stdout[-1200:]} {result.stderr[-1200:]}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else {"stdout": result.stdout}


def _run(source_repo: Path, root: Path, home: Path) -> dict[str, object]:
    root = root.resolve()
    home = home.resolve()
    if root == default_live_foundry_home().resolve() or home == default_live_foundry_home().resolve():
        raise RuntimeError("refusing to run the release harness on the live Foundry home")
    root.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    FoundryDatabase(home / "foundry.db").initialize()

    first_sha = _git("rev-parse", "HEAD", cwd=source_repo)
    first = build_release(source_repo=source_repo, source_sha=first_sha, root=root)
    first_identity = validate_release(
        first_sha, root=root, foundry_home=home, require_schema_match=True)
    set_current(first_sha, root=root)
    first_start = _run_release_python(Path(first["python_exe"]), root=root, home=home)

    # Create a second immutable identity in the disposable clone without
    # changing the lane checkout or its source content.
    _git("commit", "--allow-empty", "-m", "disposable release identity step",
         cwd=source_repo)
    second_sha = _git("rev-parse", "HEAD", cwd=source_repo)
    second = build_release(source_repo=source_repo, source_sha=second_sha, root=root)
    second_identity = validate_release(
        second_sha, root=root, foundry_home=home, require_schema_match=True)
    set_current(second_sha, root=root)
    second_start = _run_release_python(Path(second["python_exe"]), root=root, home=home)

    rolled_back = rollback_current(root=root, foundry_home=home)
    rollback_identity = validate_release(
        first_sha, root=root, foundry_home=home, require_schema_match=True)
    rollback_start = _run_release_python(
        Path(first["python_exe"]), root=root, home=home)
    if rolled_back["current"] != first_sha or rolled_back["previous"] != second_sha:
        raise AssertionError(f"rollback pointers are not exact: {rolled_back}")
    if rollback_identity["runtime_identity"]["git_commit"] != first_sha:
        raise AssertionError("rollback identity did not match first commit")

    return {
        "harness": "release-identity",
        "verdict": "PASS",
        "schema": SCHEMA_VERSION,
        "first": first_identity["runtime_identity"],
        "second": second_identity["runtime_identity"],
        "first_start": first_start,
        "second_start": second_start,
        "rollback": rolled_back,
        "rollback_start": rollback_start,
    }


def main() -> int:
    lane = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="jarvis-release-repro-") as path:
        workspace = Path(path)
        clone = workspace / "source"
        root = workspace / "install"
        home = workspace / "foundry"
        result = subprocess.run(
            ["git", "clone", "--local", "--no-hardlinks", str(lane), str(clone)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "disposable clone failed")
        _git("config", "user.email", "jarvis-harness@example.invalid", cwd=clone)
        _git("config", "user.name", "JARVIS disposable harness", cwd=clone)
        try:
            output = _run(clone, root, home)
        finally:
            gc.collect()
        print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
