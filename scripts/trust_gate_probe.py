"""Read-only trust-gate probe for live or disposable Foundry homes.

The probe deliberately does not record evidence or request a trust
transition.  It combines the canonical production-status snapshot with the
event-derived trust assessment so future proving runs can report exactly
which gate is eligible and which dimensions remain unproven.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.status import production_status
from jarvis_foundry.trust import trust_status


def _git_head(repo: Path | None) -> str | None:
    if repo is None or not (repo / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _gate_summary(assessment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    limited_missing = list(assessment.get("limited_missing") or [])
    full_missing = list(assessment.get("missing") or [])
    return {
        "LIMITED_AUTONOMY": {
            "eligible": not limited_missing,
            "status": "PASS" if not limited_missing else "WAITING",
            "missing": limited_missing,
        },
        "TRUSTED_AUTONOMOUS": {
            "eligible": not full_missing,
            "status": "PASS" if not full_missing else "WAITING",
            "missing": full_missing,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trust_gate_probe")
    parser.add_argument("--home", type=Path, default=None,
                        help="Foundry home to inspect")
    parser.add_argument("--install-root", type=Path, default=None,
                        help="JARVIS install root containing CURRENT")
    parser.add_argument("--source-repo", type=Path, default=None,
                        help="optional source checkout for canonical SHA")
    parser.add_argument("--expected-sha", default=None,
                        help="optional source/install SHA parity check")
    parser.add_argument(
        "--require-gate",
        choices=("LIMITED_AUTONOMY", "TRUSTED_AUTONOMOUS"),
        default=None,
        help="return exit 2 unless this gate is eligible",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.install_root is not None:
        os.environ["JARVIS_INSTALL_ROOT"] = str(args.install_root.resolve())

    status = production_status(home=args.home, source_repo=args.source_repo)
    database_path = Path(str(status["live_db"]))
    assessment = trust_status(FoundryDatabase(database_path))
    source_sha = _git_head(args.source_repo)
    expected = str(args.expected_sha).strip() if args.expected_sha else None
    installed_sha = status.get("current_release")
    known = [value for value in (expected, source_sha, installed_sha) if value]
    parity = (
        "NOT_TESTED" if expected is None else
        "PASS" if len(known) == 3 and len(set(known)) == 1 else "FAIL"
    )
    result: dict[str, Any] = {
        "scope": "read-only production status + event-derived trust assessment",
        "production_mutation": False,
        "sha_preflight": {
            "expected_sha": expected,
            "canonical_source_sha": source_sha,
            "installed_release_sha": installed_sha,
            "status": parity,
        },
        "trust_gate": {
            "state": assessment.get("state"),
            "recommended_state": assessment.get("recommended_state"),
            "evidence_digest": assessment.get("evidence_digest"),
            "limited_autonomy_evidence": assessment.get(
                "limited_autonomy_evidence", []),
            "required_evidence": assessment.get("required_evidence", []),
            "checks": assessment.get("checks", {}),
            "gates": _gate_summary(assessment),
        },
        "runtime": {
            "health": status.get("health"),
            "parity": status.get("parity"),
            "reconciliation": status.get("reconciliation"),
            "release_authority": status.get("release_authority"),
            "scheduler": status.get("scheduler"),
            "process_truth": status.get("process_truth"),
        },
    }
    encoded = json.dumps(result, indent=2, sort_keys=True, default=str)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    if args.require_gate is not None:
        gate = result["trust_gate"]["gates"][args.require_gate]
        if gate["status"] != "PASS":
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
