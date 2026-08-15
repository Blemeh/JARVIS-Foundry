"""Create real proving projects for R8 machinery missions under TEMP."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from common import REPO_ROOT, TEMP_BASE, safe_clear, write_json

from jarvis_foundry.orchestration.paths import copy_tree
from jarvis_foundry.orchestration.proving_project_data import (
    ALPHA_PYPROJECT_SOURCE,
    ALPHA_README_SOURCE,
    ALPHA_STORE_SOURCE,
    ALPHA_TEST_SOURCE,
    REPRO_TEST_SOURCE,
)
from jarvis_foundry.orchestration.repair import revert_repair_transform

ALPHA_ROOT = TEMP_BASE / "projects" / "alpha_ledger"
CANDIDATE_ROOT = TEMP_BASE / "projects" / "r8_orchestration_candidate"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, args: list[str]) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed in {root}: {completed.stderr[:500]}")


def create_alpha_ledger(*, force: bool = False) -> dict[str, object]:
    if ALPHA_ROOT.is_dir() and not force:
        return {"status": "EXISTS", "path": str(ALPHA_ROOT)}
    if ALPHA_ROOT.is_dir():
        safe_clear(ALPHA_ROOT)
    _write(ALPHA_ROOT / "pyproject.toml", ALPHA_PYPROJECT_SOURCE)
    _write(ALPHA_ROOT / "README.md", ALPHA_README_SOURCE)
    _write(ALPHA_ROOT / "src" / "alpha_ledger" / "__init__.py", '"""alpha_ledger event store."""\n')
    _write(ALPHA_ROOT / "src" / "alpha_ledger" / "store.py", ALPHA_STORE_SOURCE)
    _write(ALPHA_ROOT / "tests" / "test_store.py", ALPHA_TEST_SOURCE)
    _git(ALPHA_ROOT, ["init", "-q"])
    _git(ALPHA_ROOT, ["config", "user.email", "r8-proving@localhost"])
    _git(ALPHA_ROOT, ["config", "user.name", "R8 Proving"])
    _git(ALPHA_ROOT, ["add", "."])
    _git(ALPHA_ROOT, ["commit", "-q", "-m", "alpha_ledger baseline"])
    _write(ALPHA_ROOT / "NOTES.md", "# owner notes\nUntracked dirty work preserved by R8.\n")
    return {"status": "CREATED", "path": str(ALPHA_ROOT)}


def create_orchestration_candidate(*, force: bool = False) -> dict[str, object]:
    """Snapshot the current orchestration source plus a real failing repro test."""
    if CANDIDATE_ROOT.is_dir() and not force:
        return {"status": "EXISTS", "path": str(CANDIDATE_ROOT)}
    if CANDIDATE_ROOT.is_dir():
        safe_clear(CANDIDATE_ROOT)

    copy_tree(
        REPO_ROOT / "src",
        CANDIDATE_ROOT / "src",
        exclude={".git", "__pycache__", "jarvis_foundry.egg-info"},
    )
    verify_path = CANDIDATE_ROOT / "src" / "jarvis_foundry" / "orchestration" / "verify.py"
    verify_source = verify_path.read_text(encoding="utf-8")
    verify_path.write_text(
        revert_repair_transform(verify_source, "verifier_recompute"),
        encoding="utf-8",
        newline="\n",
    )
    _write(CANDIDATE_ROOT / "pyproject.toml", ALPHA_PYPROJECT_SOURCE)
    _write(CANDIDATE_ROOT / "README.md", "# R8 orchestration candidate\n")
    _write(CANDIDATE_ROOT / "tests" / "test_verify_repro.py", REPRO_TEST_SOURCE)
    _git(CANDIDATE_ROOT, ["init", "-q"])
    _git(CANDIDATE_ROOT, ["config", "user.email", "r8-proving@localhost"])
    _git(CANDIDATE_ROOT, ["config", "user.name", "R8 Proving"])
    _git(CANDIDATE_ROOT, ["add", "."])
    _git(CANDIDATE_ROOT, ["commit", "-q", "-m", "r8 orchestration candidate baseline"])
    return {"status": "CREATED", "path": str(CANDIDATE_ROOT)}


def main() -> int:
    alpha = create_alpha_ledger()
    candidate = create_orchestration_candidate()
    summary = {"alpha_ledger": alpha, "orchestration_candidate": candidate}
    write_json(TEMP_BASE / "proving_projects.json", summary)
    print(f"proving projects: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
