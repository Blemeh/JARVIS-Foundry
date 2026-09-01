"""Independent verification helpers for the V10-C local Golden Journey 3.

This module is intentionally a verifier, not another mission loop.  It reads
the disposable Git worktree and release slot through fresh subprocesses and
pointer/manifest reads, then returns a small evidence object that can be
attached to the existing mission/effect receipt.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .deployment import ArtifactIdentity, DisposableDeploymentSlot


class GoldenJourneyVerificationError(ValueError):
    """The independent verifier received an invalid local target."""


def _git(worktree: Path, *args: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    output = (result.stdout if result.returncode == 0 else result.stderr).strip()
    return result.returncode == 0, output


def independent_verify_release(
    worktree: str | Path,
    *,
    expected_head: str,
    artifact: ArtifactIdentity,
    slot: DisposableDeploymentSlot,
) -> dict[str, Any]:
    """Verify source identity, clean tree, artifact identity, and slot state.

    No result from the worker, Fabric, or deployment switch is trusted as the
    source of these checks.  A failed or malformed check is reported as a
    failed verification rather than raising after the target has been
    mutated, which lets the caller preserve the evidence and decide whether
    to roll back.
    """

    root = Path(worktree).expanduser().resolve(strict=False)
    if not root.is_dir() or not (root / ".git").exists():
        raise GoldenJourneyVerificationError(f"not a Git worktree: {root}")
    if not isinstance(expected_head, str) or len(expected_head) != 40:
        raise GoldenJourneyVerificationError("expected_head must be a full Git object id")

    head_ok, actual_head = _git(root, "rev-parse", "HEAD")
    tree_ok, tree_hash = _git(root, "rev-parse", "HEAD^{tree}")
    status_ok, status = _git(root, "status", "--porcelain")
    manifest_path = Path(artifact.manifest_path).expanduser().resolve(strict=False)
    manifest: dict[str, Any] | None = None
    if manifest_path.is_file():
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                manifest = value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            manifest = None
    slot_state = slot.reconcile()
    checks = {
        "head_matches": head_ok and actual_head == expected_head,
        "tree_observed": tree_ok and bool(tree_hash),
        "worktree_clean": status_ok and not status,
        "artifact_manifest_present": manifest is not None,
        "artifact_id_matches": bool(manifest and manifest.get("artifact_id") == artifact.artifact_id),
        "artifact_hash_matches": bool(manifest and manifest.get("content_hash") == artifact.content_hash),
        "artifact_source_ref_matches": bool(manifest and manifest.get("source_ref") == artifact.source_ref),
        "current_points_to_artifact": slot_state.get("current") == artifact.artifact_id,
        "slot_consistent": bool(slot_state.get("consistent")),
    }
    return {
        "verdict": "VERIFIED" if all(checks.values()) else "MISMATCH",
        "checks": checks,
        "runtime_identity": {
            "head": actual_head if head_ok else None,
            "tree": tree_hash if tree_ok else None,
            "worktree": str(root),
        },
        "release_identity": {
            "artifact_id": artifact.artifact_id,
            "content_hash": artifact.content_hash,
            "source_ref": artifact.source_ref,
            "manifest_path": str(manifest_path),
        },
        "slot": slot_state,
    }


__all__ = ["GoldenJourneyVerificationError", "independent_verify_release"]
