"""R11-G06: restart-safe cadence plus full quality/protection gates."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Any

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    REPO_ROOT,
    evidence_bytes,
    read_json,
    write_json,
)

EVIDENCE_BUDGET_BYTES = 8_388_608

PRE_EXISTING_BASELINE: tuple[str, ...] = (
    "?? docs/remaster/evidence/r6/",
    "?? docs/remaster/work_packages/R11_WORK_PACKAGE.json",
    "?? docs/remaster/work_packages/R6_WORK_PACKAGE.json",
    "?? scripts/remaster/r6/",
    "?? src/jarvis_foundry/kernel/memory/",
    "?? tests/remaster/r6/",
)

ALLOWED_PREFIXES = (
    "src/jarvis_foundry/evolution_live/",
    "scripts/remaster/r11/",
    "tests/remaster/r11/",
    "docs/remaster/evidence/r11/",
)


def _status_from(name: str) -> bool:
    path = EVIDENCE_DIR / name
    if not path.is_file():
        return False
    payload = read_json(path)
    if name == "R11_FOCUSED_TESTS.json":
        return payload.get("exit_code") == 0
    if name == "R11_FULL_SUITE.json":
        return payload.get("exit_code") == 0
    if name == "R11_STATIC.json":
        return payload.get("passed") is True
    if name == "R11_LIVE_GUARD_COMPARISON.json":
        return payload.get("protected_state_unchanged") is True
    return payload.get("passed") is True


def _confinement() -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    lines = completed.stdout.splitlines()
    missing = [entry for entry in PRE_EXISTING_BASELINE if entry not in lines]
    violations: list[str] = []
    for line in lines:
        if line in PRE_EXISTING_BASELINE:
            continue
        if any(line[3:].startswith(prefix) for prefix in ALLOWED_PREFIXES):
            continue
        violations.append(line)
    return {
        "porcelain_line_count": len(lines),
        "baseline_entries_missing": missing,
        "confinement_violations": violations,
        "confined": not violations and not missing,
    }


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    restart = read_json(EVIDENCE_DIR / "R11_RESTART.json")
    restart_cycle = restart["restart_cycle"]
    waiting = read_json(EVIDENCE_DIR / "R11_WAITING.json")
    restart_checks = {
        "restart_simulated": bool(restart_cycle["simulated"]),
        "resumed_after_isolate": restart_cycle["resumed_at_stage"] == "evaluate",
        "isolate_effect_replayed_not_duplicated": bool(
            restart_cycle.get("clone_effect_status_after_restart") == "replayed"
            and restart_cycle.get("clone_replayed_without_duplicate_effect")
        ),
        "waiting_not_duplicated_after_restart": not bool(waiting["replay_after_restart_created"]),
        "no_duplicate_effect_journal_entries": (
            len(restart["journal_effect_ids"]) == len(set(restart["journal_effect_ids"]))
        ),
    }
    statuses = {
        "static": _status_from("R11_STATIC.json"),
        "focused_tests": _status_from("R11_FOCUSED_TESTS.json"),
        "full_suite": _status_from("R11_FULL_SUITE.json"),
        "secret_scan": _status_from("R11_SECRET_SCAN.json"),
        "privacy": _status_from("R11_PRIVACY.json"),
        "live_guard": _status_from("R11_LIVE_GUARD_COMPARISON.json"),
    }
    size = evidence_bytes()
    confinement = _confinement()
    checks: dict[str, Any] = dict(restart_checks)
    checks.update(statuses)
    checks["evidence_under_budget"] = size < EVIDENCE_BUDGET_BYTES
    checks["artifact_confinement"] = confinement["confined"]
    passed = all(checks.values())
    output = {
        "schema": "jarvis-r11-g06-restart-v1",
        "evidence_utc": EVIDENCE_UTC,
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "checks": checks,
        "restart": restart,
        "confinement": confinement,
        "evidence_bytes": size,
        "evidence_budget_bytes": EVIDENCE_BUDGET_BYTES,
        "evidence": [
            "R11_RESTART.json",
            "R11_WAITING.json",
            "R11_STATIC.json",
            "R11_FOCUSED_TESTS.json",
            "R11_FULL_SUITE.json",
            "R11_SECRET_SCAN.json",
            "R11_PRIVACY.json",
            "R11_LIVE_GUARD_COMPARISON.json",
        ],
    }
    write_json(EVIDENCE_DIR / "R11_G06_RESTART.json", output)
    print(
        f"g06 restart: passed={passed} evidence_bytes={size} "
        f"confined={confinement['confined']} wall_seconds={output['wall_seconds']}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
