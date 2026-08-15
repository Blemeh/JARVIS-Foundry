"""Actual diff/artifact inspection for accepted-or-rejected R8 missions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.orchestration.git_ops import (
    diff_evidence,
    untracked_and_changed_files,
)
from jarvis_foundry.orchestration.paths import TEMP_BASE, resolve_within
from jarvis_foundry.orchestration.schemas import WorkerResult


def inspect_mission(
    clone: Path, result: WorkerResult, *, proving_root: Path | None = None
) -> dict[str, Any]:
    """Inspect the real working tree and compare it with worker claims."""
    clone_root = resolve_within(proving_root or TEMP_BASE, Path(clone))
    diff = diff_evidence(clone_root, proving_root=proving_root or TEMP_BASE)
    actual = untracked_and_changed_files(clone_root, proving_root=proving_root or TEMP_BASE)
    actual = {path: entry for path, entry in actual.items() if path not in set(result.evidence)}
    claims = {change.path: change for change in result.files_changed}
    claimed_missing = [
        path for path, change in claims.items() if change.action != "deleted" and path not in actual
    ]
    unclaimed_changes = [
        path for path, change in actual.items() if path not in claims and change["sha256"] != ""
    ]
    hash_mismatches: list[str] = []
    for path, change in claims.items():
        if (
            path in actual
            and change.action != "deleted"
            and actual[path]["sha256"] != change.sha256
        ):
            hash_mismatches.append(path)
    integrity_issues = {
        "claimed_missing": claimed_missing,
        "unclaimed_changes": unclaimed_changes,
        "hash_mismatches": hash_mismatches,
    }
    return {
        "clone": str(clone_root),
        "diff": diff,
        "actual_files": {path: change for path, change in actual.items()},
        "claimed_files": {path: change.to_dict() for path, change in claims.items()},
        "integrity_issues": integrity_issues,
        "integrity_clean": not any(integrity_issues.values()),
        "diff_summary_hash": canonical_hash(diff),
    }
