"""Run the disposable V12 provider-resilience product proof.

The selected tests exercise the canonical MissionLoop, RoleDispatcher,
HybridWorkerRoleAdapter, effect journal, and Executive wake machinery.  The
script emits one machine-readable report bound to the implementation SHA.
It never reads live provider credentials or mutates a live JARVIS install.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


BASE_SHA = "30998ef06d9be73adb30387c10a7fa72ef910d28"
TEST_FILE = "tests/foundry/test_v12_provider_resilience.py"
SCENARIOS = {
    "A_PRIMARY_HEALTHY": "test_primary_healthy_routes_normally",
    "CONFIGURED_PROVIDER_POOL": (
        "test_machine_provider_pool_extends_existing_primary_boundary"),
    "B_PRIMARY_OUTAGE_FALLBACK": (
        "test_outage_and_rate_limit_take_one_compatible_alternate[outage]"),
    "C_RATE_LIMIT_BOUNDED_ALTERNATE": (
        "test_outage_and_rate_limit_take_one_compatible_alternate[rate]"),
    "D_INCOMPATIBLE_REJECTED": (
        "test_incompatible_policy_blocked_and_over_budget_fallbacks_are_rejected"),
    "E_POLICY_BLOCKED_REJECTED": (
        "test_incompatible_policy_blocked_and_over_budget_fallbacks_are_rejected"),
    "POLICY_OWNER_CONSENT_IMMUTABLE": (
        "test_profile_cannot_enable_noninteractive_training_policy"),
    "F_ALL_UNAVAILABLE_WAIT": (
        "test_all_down_wait_then_positive_recovery_resumes_same_mission_and_run"),
    "G_PROVIDER_RECOVERY_EXACT_RESUME": (
        "test_all_down_wait_then_positive_recovery_resumes_same_mission_and_run"),
    "H_AMBIGUOUS_EFFECT_RECONCILE_FIRST": (
        "test_ambiguous_effect_reconciles_before_alternate_dispatch"),
    "HYBRID_RUNTIME_INTEGRATION": (
        "test_classified_failure_crosses_real_hybrid_worker_bridge"),
    "RECURRENT_OUTAGE_AFTER_RECOVERY": (
        "test_identical_outage_can_recur_after_a_recovery_transition"),
}


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evidence/v12-provider-resilience-proof.json"),
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output if args.output.is_absolute() else root / args.output
    uv = shutil.which("uv")
    command = (
        [uv, "run", "pytest", "-q", TEST_FILE, "--tb=short"]
        if uv else
        [sys.executable, "-m", "pytest", "-q", TEST_FILE, "--tb=short"]
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root)
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    head = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    report = {
        "schema": "jarvis-v12-provider-resilience-proof-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS" if completed.returncode == 0 else "FAIL",
        "base_sha": BASE_SHA,
        "implementation_sha": head,
        "branch": branch,
        "isolated_worktree": str(root),
        "live_mutation": False,
        "provider_credentials_used": False,
        "command": command,
        "returncode": completed.returncode,
        "scenarios": {
            name: {"test": test, "status": (
                "PASS" if completed.returncode == 0 else "UNPROVEN")}
            for name, test in SCENARIOS.items()
        },
        "identity_assertions": {
            "duplicate_missions": 0 if completed.returncode == 0 else None,
            "duplicate_builder_runs": 0 if completed.returncode == 0 else None,
            "duplicate_external_effects": 0 if completed.returncode == 0 else None,
        },
        "stdout": completed.stdout[-30000:],
        "stderr": completed.stderr[-30000:],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "verdict": report["verdict"],
        "output": str(output),
        "implementation_sha": head,
        "returncode": completed.returncode,
    }, sort_keys=True))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
