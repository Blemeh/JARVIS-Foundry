"""R12-G06: final audit package readiness gate.

The builder cannot self-certify the fresh independent final audit. This
gate records package readiness and keeps the independent verdict
NOT_TESTED/WAITING for the fresh verifier.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from common import EVIDENCE_DIR, EVIDENCE_UTC, read_json, write_json


def main() -> int:
    started = time.monotonic()
    prerequisites = {
        "comparison": read_json(EVIDENCE_DIR / "R12_G01_COMPARISON.json").get("passed"),
        "adoption": read_json(EVIDENCE_DIR / "R12_G02_ADOPTION.json").get("passed"),
        "freeze_restore": read_json(EVIDENCE_DIR / "R12_G03_FREEZE_RESTORE.json").get("passed"),
        "faults": read_json(EVIDENCE_DIR / "R12_G04_FAULTS.json").get("passed"),
        "proving": read_json(EVIDENCE_DIR / "R12_G05_PROVING.json").get("passed"),
    }
    package: dict[str, Any] = {
        "schema": "jarvis-r12-final-audit-package-v1",
        "fixed_utc": EVIDENCE_UTC,
        "prerequisites_ready": all(prerequisites.values()),
        "prerequisite_gates": prerequisites,
        "independent_audit_status": "PENDING",
        "verdict": "NOT_TESTED",
        "recommended_audit_scope": (
            "fresh independent context must reproduce R12_G01..G05 evidence, "
            "verify live/protected state unchanged, and return exactly one of "
            "SERVES_ORIGINAL_PURPOSE / SERVES_PURPOSE_WITH_EXTERNAL_BLOCKERS / "
            "DOES_NOT_YET_SERVE_PURPOSE"
        ),
    }
    write_json(EVIDENCE_DIR / "R12_FINAL_AUDIT_PACKAGE.json", package)
    payload = {
        "schema": "jarvis-r12-g06-audit-v1",
        "exit_code": 0,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": package["prerequisites_ready"],
        "independent_audit_status": "PENDING",
        "verdict_status": "WAITING",
        "verdict": "NOT_TESTED",
        "package": str(EVIDENCE_DIR / "R12_FINAL_AUDIT_PACKAGE.json"),
    }
    write_json(EVIDENCE_DIR / "R12_G06_AUDIT.json", payload)
    print(f"g06 audit: package_ready={payload['passed']} verdict=NOT_TESTED (WAITING)")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
