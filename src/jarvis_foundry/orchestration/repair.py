"""Repair transformation helpers for deterministic fresh repair workers."""

from __future__ import annotations

BUGGY_METHOD = """\
    def _check_completeness(self, clone: Path, result: WorkerResult) -> list[VerifierIssue]:
        # R8 repair B target: initial implementation trusts worker claims.
        return []
"""

FIXED_METHOD = """\
    def _check_completeness(self, clone: Path, result: WorkerResult) -> list[VerifierIssue]:
        actual = untracked_and_changed_files(clone, proving_root=self._proving_root)
        actual = {path: entry for path, entry in actual.items() if path not in set(result.evidence)}
        claims = {change.path: change for change in result.files_changed}
        unclaimed = [path for path in actual if path not in claims]
        missing = [
            path
            for path, change in claims.items()
            if change.action != "deleted" and path not in actual
        ]
        issues = [
            VerifierIssue("completeness", f"unclaimed change: {path}") for path in sorted(unclaimed)
        ]
        issues += [
            VerifierIssue("completeness", f"claimed file missing: {path}")
            for path in sorted(missing)
        ]
        return issues
"""


def apply_repair_transform(source: str, transform_id: str) -> str:
    """Apply a bounded, tested source transformation for a known defect."""
    if transform_id != "verifier_recompute":
        raise ValueError(f"unknown repair transform: {transform_id}")
    if BUGGY_METHOD not in source:
        raise ValueError("repair target not found in source")
    if FIXED_METHOD in source:
        return source
    return source.replace(BUGGY_METHOD, FIXED_METHOD)


def revert_repair_transform(source: str, transform_id: str) -> str:
    """Revert a known repair to the documented before-state (for regression fixtures)."""
    if transform_id != "verifier_recompute":
        raise ValueError(f"unknown repair transform: {transform_id}")
    if FIXED_METHOD not in source:
        raise ValueError("fixed repair target not found in source")
    if BUGGY_METHOD in source:
        return source
    return source.replace(FIXED_METHOD, BUGGY_METHOD)
