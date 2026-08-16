"""R12-G03: release freeze and restore proof gate."""

from __future__ import annotations

import sys
import time

from common import EVIDENCE_DIR, read_json, write_json

REQUIRED_FREEZE_FIELDS = (
    "executable",
    "source",
    "dependencies",
    "configuration",
    "db_wal",
    "scheduled_tasks",
    "ports",
    "capabilities",
    "credential_metadata",
    "queues",
    "leases",
    "health",
    "evidence",
    "release_pointer",
    "rollback_package",
)


def main() -> int:
    started = time.monotonic()
    freeze = read_json(EVIDENCE_DIR / "R12_RELEASE_FREEZE.json")
    restore = read_json(EVIDENCE_DIR / "R12_RESTORE_PROOF.json")
    missing = [field for field in REQUIRED_FREEZE_FIELDS if field not in freeze]
    freeze_complete = not missing
    restore_passed = bool(restore.get("passed") and restore.get("restored_exactly"))
    passed = freeze_complete and restore_passed
    payload = {
        "schema": "jarvis-r12-g03-freeze-restore-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "freeze_complete": freeze_complete,
        "missing_fields": missing,
        "restore_proof_passed": restore_passed,
        "restored_exactly": restore.get("restored_exactly"),
        "rollback_package_sha256": freeze.get("rollback_package", {}).get(
            "rollback_package_sha256"
        ),
    }
    write_json(EVIDENCE_DIR / "R12_G03_FREEZE_RESTORE.json", payload)
    print(f"g03 freeze/restore: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
