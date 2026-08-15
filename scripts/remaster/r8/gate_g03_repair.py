"""R8-G03: real defect reproduced and repaired by a fresh worker, reverified."""

from __future__ import annotations

import sys
import time

from common import EVIDENCE_DIR, TEMP_BASE, read_json, sha256_file, write_json

from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.portfolio.store import PortfolioStore
from jarvis_foundry.orchestration.ledger_events import r8_event_registry
from jarvis_foundry.orchestration.repair import FIXED_METHOD

CANDIDATE_PROJECT = TEMP_BASE / "projects" / "r8_orchestration_candidate"


def main() -> int:
    started = time.monotonic()
    report = read_json(EVIDENCE_DIR / "R8_MISSION_REPAIR_B.json")
    asset = read_json(EVIDENCE_DIR / "R8_MISSION_ASSET_A.json")
    clone = TEMP_BASE / "clones" / "mission-r8b-repair"
    target = clone / "src" / "jarvis_foundry" / "orchestration" / "verify.py"
    fixed_present = target.is_file() and FIXED_METHOD in target.read_text(encoding="utf-8")
    before_sha = sha256_file(
        CANDIDATE_PROJECT / "src" / "jarvis_foundry" / "orchestration" / "verify.py"
    )
    after_sha = report["worker_result"]["files_changed"][0]["sha256"]
    real_change = before_sha != after_sha
    fresh_worker = report["worker_id"] != asset["worker_id"]
    repro_command = report["worker_result"]["tests"][0]["name"]
    regression = report["worker_result"]["tests"][1]
    lifecycle = [event["event_type"] for event in report["ledger"]["events"]]
    repair_dispatched = any(
        event["event_type"] == "mission.repair_dispatched" for event in report["ledger"]["events"]
    )
    ledger = Ledger(EVIDENCE_DIR / "machinery" / "r8_ledger.db", registry=r8_event_registry())
    store = PortfolioStore(EVIDENCE_DIR / "machinery" / "r8_store")
    repair_links = [
        link for link in store.mission_links() if link.mission_id == "mission-r8b-repair"
    ]
    repair_accepted = [
        event
        for event in ledger.replay()
        if event.event_type == "mission.accepted"
        and event.payload.get("mission_id") == "mission-r8b-repair"
    ]
    link_binds_accepted = bool(
        repair_links
        and repair_accepted
        and repair_links[0].ledger_header_hash == repair_accepted[0].header_hash
    )
    passed = bool(
        report["status"] == "ACCEPTED"
        and report["worker_result"]["status"] == "PASS"
        and report["worker_result"]["claims"].get("defect_reproduced") is True
        and report["verdict"]["verdict"] == "PASS"
        and fixed_present
        and real_change
        and fresh_worker
        and regression["passed"] is True
        and repair_dispatched
        and link_binds_accepted
        and report["rollback"]["restored"] is True
        and report["source_after_unchanged"] is True
    )
    payload = {
        "schema": "jarvis-r8-g03-repair-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "defect_reproduced": report["worker_result"]["claims"].get("defect_reproduced"),
        "reproduced_test_command": repro_command,
        "regression_test_passed": regression["passed"],
        "regression_exit_code": regression["exit_code"],
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "real_content_change": real_change,
        "fresh_worker_distinct": fresh_worker,
        "producing_builder_worker_id": asset["worker_id"],
        "repair_worker_id": report["worker_id"],
        "fixed_method_present": fixed_present,
        "lifecycle_has_repair_dispatched": repair_dispatched,
        "lifecycle_has_reverified": "mission.reverified" in lifecycle,
        "lifecycle_has_accepted": "mission.accepted" in lifecycle,
        "mission_link_binds_accepted_event": link_binds_accepted,
    }
    write_json(EVIDENCE_DIR / "R8_G03_REPAIR.json", payload)
    print(f"g03 repair: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
