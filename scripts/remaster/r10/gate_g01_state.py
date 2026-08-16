"""R10-G01: Command Center renders real durable state with drill-down and timeline."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, EVIDENCE_UTC, REPO_ROOT, sha256_file, write_json

from jarvis_foundry.command_center.proving import ProvingLayout
from jarvis_foundry.command_center.seeding import seed_proving_stores
from jarvis_foundry.command_center.state import (
    build_r10_snapshot,
    evidence_for_record,
    record_lookup,
)


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    layout = ProvingLayout(Path("C:/Users/b/AppData/Local/Temp/jarvis-r10-builder"))
    seed_proving_stores(layout, REPO_ROOT / "docs" / "remaster" / "evidence")
    snapshot = build_r10_snapshot(layout, now=EVIDENCE_UTC)
    checks: dict[str, bool] = {
        "projects": bool(snapshot["projects"]),
        "missions": bool(snapshot["missions"]),
        "candidate_decisions": bool(snapshot["candidate_decisions"]),
        "rejected_alternatives": bool(snapshot["rejected_alternatives"]),
        "workers": bool(snapshot["workers"]),
        "verification": bool(snapshot["verification"]),
        "findings": bool(snapshot["findings"]),
        "releases": bool(snapshot["releases"]),
        "capabilities": bool(snapshot["capabilities"]),
        "costs_section": bool(snapshot["costs"]),
        "resources": bool(snapshot["resources"]),
        "evidence_index": snapshot["evidence"]["count"] > 0,
        "health": bool(snapshot["health"]),
        "incidents": bool(snapshot["incidents"]),
        "waiting_blockers": bool(snapshot["waiting_blockers"]),
        "next_actions": bool(snapshot["next_actions"]),
        "timeline": bool(snapshot["timeline"]),
        "decision_effect_timeline": any(
            event["event_type"] in {"portfolio.decision", "owner.directive.recorded"}
            for event in snapshot["timeline"]
        ),
    }
    drilldown: dict[str, Any] = {}
    sample = snapshot["missions"][0]
    entry = record_lookup(snapshot, sample["record_id"])
    if entry is not None:
        evidence = evidence_for_record(entry)
        drilldown = {
            "record_id": evidence["record_id"],
            "kind": evidence["kind"],
            "json_hash_length": len(evidence["json_hash"]),
            "json_hash": evidence["json_hash"],
            "provenance_count": len(evidence["provenance"]),
            "evidence_refs_count": len(evidence["evidence_refs"]),
        }
    checks["evidence_drilldown"] = (
        drilldown.get("json_hash_length") == 64
        and drilldown.get("provenance_count", 0) > 0
        and drilldown.get("evidence_refs_count", 0) > 0
    )
    checks["source_is_isolated_proving"] = (
        snapshot.get("source_of_truth", {}).get("kind") == "isolated_proving_stores"
    )
    passed = all(checks.values())
    snapshot_path = EVIDENCE_DIR / "R10_STATE_SNAPSHOT.json"
    write_json(snapshot_path, snapshot)
    payload = {
        "schema": "jarvis-r10-g01-state-v1",
        "evidence_utc": EVIDENCE_UTC,
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "checks": checks,
        "drilldown": drilldown,
        "snapshot_output": "R10_STATE_SNAPSHOT.json",
        "snapshot_sha256": sha256_file(snapshot_path),
        "snapshot_size": snapshot_path.stat().st_size,
        "evidence": ["R10_STATE_SNAPSHOT.json"],
    }
    write_json(EVIDENCE_DIR / "R10_G01_STATE.json", payload)
    print(
        f"g01 state: passed={passed} checks={sum(checks.values())}/{len(checks)} "
        f"wall_seconds={payload['wall_seconds']}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
