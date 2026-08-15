"""R8-G01: isolated clone/branch machinery, dirty preservation, no source mutation."""

from __future__ import annotations

import sys
import time

from common import EVIDENCE_DIR, TEMP_BASE, read_json, write_json

from jarvis_foundry.orchestration.git_ops import clone_project, inspect_project
from jarvis_foundry.orchestration.paths import clear_within

ALPHA_PROJECT = TEMP_BASE / "projects" / "alpha_ledger"


def main() -> int:
    started = time.monotonic()
    asset = read_json(EVIDENCE_DIR / "R8_MISSION_ASSET_A.json")
    repair = read_json(EVIDENCE_DIR / "R8_MISSION_REPAIR_B.json")

    source_before = asset["source_before"]
    alpha_dirty_preserved = "NOTES.md" in source_before["dirty_report"]["untracked"]
    asset_isolated = (
        asset["status"] == "ACCEPTED"
        and asset["source_after_unchanged"] is True
        and alpha_dirty_preserved
        and source_before["is_git_repo"] is True
    )
    repair_isolated = (
        repair["status"] == "ACCEPTED"
        and repair["source_after_unchanged"] is True
        and repair["source_before"]["is_git_repo"] is True
    )

    clone_dir = TEMP_BASE / "gates" / "g01_clone"
    if clone_dir.exists():
        clear_within(clone_dir, TEMP_BASE)
    before = inspect_project(ALPHA_PROJECT)
    info = clone_project(ALPHA_PROJECT, clone_dir, branch="r8/g01", proving_root=TEMP_BASE)
    after = inspect_project(ALPHA_PROJECT)
    source_unchanged = (
        before["tree_hash"] == after["tree_hash"]
        and before["porcelain"] == after["porcelain"]
        and before["head"] == after["head"]
    )
    clone_head = (
        __import__("jarvis_foundry.orchestration.git_ops", fromlist=["run_git"])
        .run_git(["rev-parse", "HEAD"], clone_dir)
        .stdout.strip()
    )
    passed = bool(
        asset_isolated
        and repair_isolated
        and source_unchanged
        and info["branch"] == "r8/g01"
        and clone_head == before["head"]
    )
    payload = {
        "schema": "jarvis-r8-g01-isolation-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "asset_mission_isolated": asset_isolated,
        "repair_mission_isolated": repair_isolated,
        "alpha_dirty_untracked_preserved": alpha_dirty_preserved,
        "alpha_dirty_report": source_before["dirty_report"],
        "independent_clone": {
            "branch": info["branch"],
            "source_head_before": before["head"],
            "clone_head": clone_head,
            "source_tree_hash_before": before["tree_hash"],
            "source_tree_hash_after": after["tree_hash"],
            "source_porcelain_unchanged": before["porcelain"] == after["porcelain"],
            "source_unchanged": source_unchanged,
        },
    }
    write_json(EVIDENCE_DIR / "R8_G01_ISOLATION.json", payload)
    print(f"g01 isolation: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
