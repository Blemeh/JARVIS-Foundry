"""R8-G05: Git/filesystem/ledger reconciliation and rollback demonstration."""

from __future__ import annotations

import sys
import time

from common import EVIDENCE_DIR, TEMP_BASE, read_json, write_json

from jarvis_foundry.orchestration.paths import clear_within, copy_tree, tree_hash


def main() -> int:
    started = time.monotonic()
    asset = read_json(EVIDENCE_DIR / "R8_MISSION_ASSET_A.json")
    repair = read_json(EVIDENCE_DIR / "R8_MISSION_REPAIR_B.json")
    asset_ok = (
        asset["reconciliation"]["reconciled"] is True
        and asset["rollback"]["restored"] is True
        and asset["ledger"]["chain_valid"] is True
    )
    repair_ok = (
        repair["reconciliation"]["reconciled"] is True
        and repair["rollback"]["restored"] is True
        and repair["ledger"]["chain_valid"] is True
    )

    baseline = TEMP_BASE / "snapshots" / "mission-r8a-asset" / "baseline"
    restore_target = TEMP_BASE / "gates" / "g05_restore"
    if restore_target.exists():
        clear_within(restore_target, TEMP_BASE)
    copy_tree(baseline, restore_target, exclude=set())
    restored_hash = tree_hash(restore_target)
    baseline_hash = tree_hash(baseline)
    independent_restore = restored_hash == baseline_hash
    passed = bool(
        asset_ok
        and repair_ok
        and independent_restore
        and asset["rollback"]["restore_matches_baseline"] is True
        and asset["rollback"]["reapply_matches_accepted"] is True
        and repair["rollback"]["restore_matches_baseline"] is True
        and repair["rollback"]["reapply_matches_accepted"] is True
    )
    payload = {
        "schema": "jarvis-r8-g05-reconciliation-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "asset_reconciliation": asset["reconciliation"],
        "asset_rollback": asset["rollback"],
        "repair_reconciliation": repair["reconciliation"],
        "repair_rollback": repair["rollback"],
        "independent_restore": {
            "baseline_hash": baseline_hash,
            "restored_hash": restored_hash,
            "matches": independent_restore,
        },
        "ledger_truth": {
            "asset_chain_valid": asset["ledger"]["chain_valid"],
            "repair_chain_valid": repair["ledger"]["chain_valid"],
            "asset_event_count": len(asset["ledger"]["events"]),
            "repair_event_count": len(repair["ledger"]["events"]),
        },
    }
    write_json(EVIDENCE_DIR / "R8_G05_RECONCILIATION.json", payload)
    print(f"g05 reconciliation: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
