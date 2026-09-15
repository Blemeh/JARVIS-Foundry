"""Run the disposable Lane 6 campaign/self-expansion/MCP continuity proof."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


class DemoFailure(RuntimeError):
    pass


def _run(command: list[str], *, timeout: int, env: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    receipt = {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:].strip(),
        "stderr_tail": result.stderr[-2000:].strip(),
    }
    if result.returncode != 0:
        raise DemoFailure(json.dumps(receipt, indent=2, sort_keys=True))
    return receipt


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "var" / "lane6-demo" / "run",
    )
    parser.add_argument(
        "--reuse-child-receipts",
        action="store_true",
        help="rebuild the compact report from already-passed disposable receipts",
    )
    args = parser.parse_args(argv)
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    local_bin = Path.home() / ".local" / "bin"
    env["PATH"] = str(local_bin) + os.pathsep + env.get("PATH", "")

    self_expansion_path = output_root / "self-expansion.json"
    endurance_path = output_root / "multi-campaign.json"
    mcp_root = output_root / "mcp"
    commands = [] if args.reuse_child_receipts else [
        _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                (
                    "tests/foundry/test_v12_self_expansion_convergence.py::"
                    "test_callable_expansion_entrypoint_resumes_and_replays_without_duplicate_trial"
                ),
                (
                    "tests/foundry/test_v12_campaign_runtime.py::"
                    "test_two_campaigns_wait_and_wake_once_on_shared_resource_capacity"
                ),
                (
                    "tests/foundry/test_v12_campaign_runtime.py::"
                    "test_startup_repairs_partial_terminal_parent_receipts"
                ),
            ],
            timeout=300,
            env=env,
        ),
        _run(
            [
                sys.executable,
                str(ROOT / "scripts" / "v12_self_expansion_convergence_proof.py"),
                "--output",
                str(self_expansion_path),
            ],
            timeout=1200,
            env=env,
        ),
        _run(
            [
                sys.executable,
                str(ROOT / "scripts" / "v12_endurance_convergence_proof.py"),
                "--mode",
                "full",
                "--root",
                str(output_root / "campaign-state"),
                "--output",
                str(endurance_path),
            ],
            timeout=1200,
            env=env,
        ),
        _run(
            [
                sys.executable,
                str(ROOT / "scripts" / "v12_mcp_backend_proof.py"),
                "--output-root",
                str(mcp_root),
            ],
            timeout=1200,
            env=env,
        ),
    ]

    expansion = _load(self_expansion_path)
    selected_mcp = _load(self_expansion_path.with_name("selected-mcp-adoption.json"))
    endurance = _load(endurance_path)
    mcp = _load(mcp_root / "V12_MCP_BACKEND_REAL_PROOF.json")
    passed = (
        expansion.get("passed") is True
        and selected_mcp.get("passed") is True
        and endurance.get("verdict") == "PASS"
        and mcp.get("passed") is True
    )
    if not passed:
        raise DemoFailure("one or more canonical child proofs did not pass")

    proof = {
        "contract_version": "jarvis.v16.lane6-continuity-demo.v1",
        "verdict": "PASS",
        "source_only": True,
        "live_mutated": False,
        "main_merged": False,
        "origin_pushed": False,
        "continuity": {
            "gap_count": expansion.get("gap_count"),
            "failed_first_candidate": bool(
                (expansion.get("discovery") or {}).get("first_failed_candidate_id")
            ),
            "trial_count": expansion.get("trial_count"),
            "probation": expansion.get("probation"),
            "objective_final_status": expansion.get("objective_final_status"),
            "mission_final_state": expansion.get("mission_final_state"),
            "selected_mcp_runtime": selected_mcp.get("runtime"),
            "selected_mcp_trial_mode": selected_mcp.get("trial_mode"),
            "mcp_restart_same_receipt": selected_mcp.get(
                "restart_replayed_same_receipt"
            ),
        },
        "campaigns": {
            "campaign_id": (endurance.get("campaign") or {}).get("campaign_id"),
            "children": (endurance.get("campaign") or {}).get("child_statuses"),
            "provider_fallback": endurance.get("fallback"),
            "capacity_wait_wake": "PASS_BY_FOCUSED_BEHAVIOR_TEST",
            "deadline": endurance.get("deadlines"),
            "child_failure_repair": "PASS_BY_FOCUSED_BEHAVIOR_TEST",
            "restart": endurance.get("startup_resume"),
            "joins": endurance.get("joins"),
        },
        "mcp": {
            "transport": mcp.get("transport"),
            "projected_capabilities": mcp.get("projected_capabilities"),
            "read_only": mcp.get("read_invocation"),
            "bounded_mutation": mcp.get("mutating_invocation"),
            "timeout_after_effect": mcp.get("timeout_after_effect_state"),
        },
        "integrity": {
            "duplicate_registrations": expansion.get("duplicate_registrations"),
            "duplicate_wakes": expansion.get("duplicate_wakes"),
            "duplicate_resumes": expansion.get("duplicate_resumes"),
            "duplicate_effects": mcp.get("duplicate_effects"),
            "false_successes": expansion.get("false_successes"),
            "unreconciled_accepted_effects": expansion.get(
                "unreconciled_accepted_effects"
            ),
        },
        "commands": commands,
        "child_receipts": {
            "self_expansion": str(self_expansion_path),
            "selected_mcp": str(self_expansion_path.with_name("selected-mcp-adoption.json")),
            "multi_campaign": str(endurance_path),
            "mcp": str(mcp_root / "V12_MCP_BACKEND_REAL_PROOF.json"),
        },
    }
    destination = output_root / "LANE6_CONTINUITY_PROOF.json"
    destination.write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"passed": True, "proof": str(destination)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DemoFailure as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2), file=sys.stderr)
        raise SystemExit(1) from error
