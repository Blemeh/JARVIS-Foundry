"""R8-G02: real asset A inspected, verified, and accepted end-to-end."""

from __future__ import annotations

import os
import subprocess
import sys
import time

from common import EVIDENCE_DIR, PYTHON, TEMP_BASE, read_json, sha256_file, write_json

from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.portfolio.store import PortfolioStore
from jarvis_foundry.orchestration.ledger_events import r8_event_registry


def _link_binds_accepted_event(mission_id: str) -> bool:
    """The store MissionLink must bind the accepted event's ledger header."""
    ledger = Ledger(EVIDENCE_DIR / "machinery" / "r8_ledger.db", registry=r8_event_registry())
    store = PortfolioStore(EVIDENCE_DIR / "machinery" / "r8_store")
    links = [link for link in store.mission_links() if link.mission_id == mission_id]
    accepted = [
        event
        for event in ledger.replay()
        if event.event_type == "mission.accepted" and event.payload.get("mission_id") == mission_id
    ]
    return bool(links and accepted and links[0].ledger_header_hash == accepted[0].header_hash)


def main() -> int:
    started = time.monotonic()
    report = read_json(EVIDENCE_DIR / "R8_MISSION_ASSET_A.json")
    clone = TEMP_BASE / "clones" / "mission-r8a-asset"
    test_file = clone / "tests" / "test_regression_edges.py"
    health_file = clone / "docs" / "health.md"
    files_exist = test_file.is_file() and health_file.is_file()
    claimed = {change["path"]: change for change in report["worker_result"]["files_changed"]}
    hashes_match = all(
        sha256_file(clone / path) == change["sha256"] for path, change in claimed.items()
    )
    verdict = report["verdict"]
    tests_passed = all(item["exit_code"] == 0 for item in verdict["test_evidence"].values())
    lifecycle = [event["event_type"] for event in report["ledger"]["events"]]
    lifecycle_complete = all(
        item in lifecycle
        for item in (
            "mission.dispatched",
            "mission.result_ingested",
            "mission.inspected",
            "mission.findings_recorded",
            "mission.reverified",
            "mission.accepted",
            "mission.reconciled",
            "mission.rollback_demonstrated",
        )
    )
    link_binds_accepted = _link_binds_accepted_event("mission-r8a-asset")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(clone / "src")
    independent = subprocess.run(
        [PYTHON, "-m", "pytest", "tests", "-q"],
        cwd=str(clone),
        capture_output=True,
        text=True,
        timeout=1200,
        env=env,
        check=False,
    )
    passed = bool(
        report["status"] == "ACCEPTED"
        and report["worker_result"]["status"] == "PASS"
        and verdict["verdict"] == "PASS"
        and report["inspection"]["integrity_clean"] is True
        and files_exist
        and hashes_match
        and tests_passed
        and lifecycle_complete
        and link_binds_accepted
        and report["rollback"]["restored"] is True
        and independent.returncode == 0
    )
    payload = {
        "schema": "jarvis-r8-g02-asset-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "asset_files": sorted(claimed),
        "asset_file_hashes_match": hashes_match,
        "tests_passed": tests_passed,
        "lifecycle_complete": lifecycle_complete,
        "mission_link_binds_accepted_event": link_binds_accepted,
        "independent_pytest_exit_code": independent.returncode,
        "independent_pytest_summary": (
            independent.stdout.strip().splitlines()[-1] if independent.stdout.strip() else ""
        ),
        "diff_name_only": report["inspection"]["diff"]["diff_name_only"],
        "not_a_toy": {
            "files_created": len(claimed),
            "test_count_claimed": report["worker_result"]["tests"][0]["summary"],
        },
    }
    write_json(EVIDENCE_DIR / "R8_G02_ASSET.json", payload)
    print(f"g02 asset: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
