"""Git/filesystem/ledger reconciliation and rollback demonstration for R8."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.orchestration.git_ops import porcelain_hash, tree_state_hash
from jarvis_foundry.orchestration.paths import (
    TEMP_BASE,
    clear_within,
    copy_tree,
    tree_hash,
)


def reconcile_mission(
    *,
    clone: Path,
    ledger: Ledger,
    mission_id: str,
    proving_root: Path | None = None,
) -> dict[str, Any]:
    root = proving_root or TEMP_BASE
    git_hash = tree_state_hash(clone, proving_root=root)
    porcelain = porcelain_hash(clone, proving_root=root)
    chain = ledger.verify_chain()
    mission_events = [
        event for event in ledger.replay() if event.payload.get("mission_id") == mission_id
    ]
    return {
        "git_tree_hash": git_hash,
        "git_porcelain_hash": porcelain,
        "ledger_chain_valid": chain.valid,
        "ledger_sequence_count": chain.sequence_count,
        "ledger_head_hash": chain.head_hash,
        "mission_event_count": len(mission_events),
        "mission_event_types": sorted({event.event_type for event in mission_events}),
        "reconciled": (bool(mission_events) and chain.valid and len(mission_events) >= 5),
    }


def demonstrate_rollback(
    *,
    clone: Path,
    baseline_snapshot: Path,
    accepted_snapshot: Path,
    baseline_tree_hash: str,
    proving_root: Path | None = None,
) -> dict[str, Any]:
    """Restore the clone to baseline, verify, then re-apply accepted state."""
    root = proving_root or TEMP_BASE
    clone_root = clone.resolve()
    if not clone_root.is_relative_to(root.resolve()):
        raise RuntimeError(f"rollback target outside proving root: {clone_root}")
    accepted_dir = accepted_snapshot / "tree"
    clear_within(accepted_dir, root)
    copy_tree(clone_root, accepted_dir, exclude=set())
    accepted_hash = tree_hash(accepted_dir)

    clear_within(clone_root, root)
    copy_tree(baseline_snapshot, clone_root, exclude=set())
    restored_hash = tree_hash(clone_root)

    clear_within(clone_root, root)
    copy_tree(accepted_dir, clone_root, exclude=set())
    reapplied_hash = tree_hash(clone_root)
    return {
        "baseline_tree_hash": baseline_tree_hash,
        "restored_tree_hash": restored_hash,
        "restore_matches_baseline": restored_hash == baseline_tree_hash,
        "accepted_tree_hash": accepted_hash,
        "reapplied_tree_hash": reapplied_hash,
        "reapply_matches_accepted": reapplied_hash == accepted_hash,
        "restored": restored_hash == baseline_tree_hash and reapplied_hash == accepted_hash,
    }
