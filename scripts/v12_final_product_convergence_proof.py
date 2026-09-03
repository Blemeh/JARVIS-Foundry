"""Run the source-bound disposable V12 final product convergence proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_foundry.migrations import SCHEMA_VERSION

BRANCH = "candidate/v12-final-product-convergence-20260903"
BASE_V11_SHA = "afddb8d5ca3d5c3459a4f5394e84affb92d13738"
ENDURANCE_FINAL_SHA = "49b2c2337a0579519d0b782a5a260673079eb398"
ENDURANCE_IMPLEMENTATION_SHA = "43da572ca0fb24ae82222cdd02c2e23a76bf95f6"
SELF_EXPANSION_FINAL_SHA = "e159437daeca4f5c5008bf0345adfeafae469c66"
SELF_EXPANSION_SOURCE_SHA = "ceede0eeb766216765add95525f840f9ac382224"
MCP_EFFECTS_FINAL_SHA = "1d72dbfe7af40e35d37ce20edacc6bfaeb8badd6"
MCP_EFFECTS_IMPLEMENTATION_SHA = "b730b708451b0399165e8adbdb037150126cc6c1"


class ProofFailure(RuntimeError):
    """Raised when a disposable proof or convergence invariant fails."""


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        raise ProofFailure(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProofFailure(message)


def _run(command: list[str], *, timeout: int) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise ProofFailure(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout[-4000:]}\n"
            f"stderr:\n{result.stderr[-4000:]}"
        )
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:].strip(),
        "stderr_tail": result.stderr[-2000:].strip(),
    }


def _source_identity() -> dict[str, Any]:
    head = _git("rev-parse", "HEAD")
    merge_base = _git("merge-base", BASE_V11_SHA, head)
    return {
        "branch": _git("branch", "--show-current"),
        "worktree": str(ROOT),
        "source_sha": head,
        "v11_base_sha": BASE_V11_SHA,
        "merge_base_with_v11": merge_base,
        "schema_version": SCHEMA_VERSION,
        "worktree_clean_before_proof": not bool(_git("status", "--short")),
        "main_sha_before": _git("rev-parse", "main"),
        "origin_main_sha_before": _git("rev-parse", "origin/main"),
    }


def _copy_receipt(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    _require(_sha256(source) == _sha256(destination), f"copy mismatch: {destination}")


def _receipt_identity(source: Path, destination: Path) -> dict[str, Any]:
    return {
        "path": str(destination.relative_to(ROOT)).replace("\\", "/"),
        "sha256": _sha256(source),
    }


def _build_report(
    *,
    source: dict[str, Any],
    a5: dict[str, Any],
    selected_mcp: dict[str, Any],
    a6: dict[str, Any],
    mcp: dict[str, Any],
    receipts: dict[str, Any],
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    duplicate_missions = (
        int(a5.get("duplicate_missions", 0))
        + int(a6["duplicates"]["missions"])
    )
    duplicate_effects = (
        int(a6["duplicates"]["effects"])
        + int(mcp["duplicate_effects"])
    )
    duplicate_registrations = (
        int(a5["duplicate_registrations"])
        + int(selected_mcp["duplicate_registrations"])
    )
    duplicate_wakes = (
        int(a5["duplicate_wakes"])
        + int(selected_mcp["duplicate_wakes"])
    )
    duplicate_resumes = (
        int(a5["duplicate_resumes"])
        + int(selected_mcp["duplicate_resumes"])
        + int(a6["duplicates"]["continuations"])
    )
    false_successes = (
        int(a5.get("false_successes", 0))
        + int(a6["integrity"]["false_successes"])
    )
    unreconciled_accepted_effects = (
        int(a5.get("unreconciled_accepted_effects", 0))
        + int(a6["integrity"]["unreconciled_accepted_effects"])
        + int(bool(mcp["accepted_effect_unverified"]))
    )
    integrity = {
        "duplicate_missions": duplicate_missions,
        "duplicate_effects": duplicate_effects,
        "duplicate_registrations": duplicate_registrations,
        "duplicate_wakes": duplicate_wakes,
        "duplicate_resumes": duplicate_resumes,
        "false_successes": false_successes,
        "unreconciled_accepted_effects": unreconciled_accepted_effects,
    }
    _require(all(value == 0 for value in integrity.values()), str(integrity))

    return {
        "contract_version": "jarvis.v12.final-product-convergence-proof.v1",
        "verdict": "PASS",
        "final_status": "V12_FINAL_SOURCE_CANDIDATE",
        "source": source,
        "inputs": {
            "endurance": {
                "final_sha": ENDURANCE_FINAL_SHA,
                "implementation_sha": ENDURANCE_IMPLEMENTATION_SHA,
            },
            "self_expansion": {
                "final_sha": SELF_EXPANSION_FINAL_SHA,
                "source_sha": SELF_EXPANSION_SOURCE_SHA,
            },
            "mcp_effects": {
                "final_sha": MCP_EFFECTS_FINAL_SHA,
                "implementation_sha": MCP_EFFECTS_IMPLEMENTATION_SHA,
            },
        },
        "authority_spine": {
            "executive": "jarvis_foundry.executive.autonomy/campaigns",
            "mission_loop": "jarvis_foundry.mission.loop.MissionLoop",
            "scheduler_wake": "jarvis_foundry.fabric.gap_bridge",
            "fabric_catalog": "jarvis_foundry.fabric.catalog",
            "provider": (
                "jarvis_foundry.roles.router + runtime.provider_registry"
            ),
            "effect": "jarvis_foundry.effects + fabric.execute_action",
            "mcp_runtime": "jarvis_foundry.mcp_backend.FabricMcpBackend",
        },
        "a5": {
            "passed": a5["passed"],
            "objective_status": a5["objective_final_status"],
            "mission_state": a5["mission_final_state"],
            "gap_count": a5["gap_count"],
            "failed_first_trial": bool(
                a5["discovery"]["first_failed_candidate_id"]
            ),
            "trial_count": a5["trial_count"],
            "probation": a5["probation"],
            "wake_count": a5["wake_count"],
            "resume_count": a5["resume_count"],
            "restart_identity_preserved": (
                a5["attempt_id_before_stop"] == a5["attempt_id_after_restart"]
            ),
            "rollback": a5["rollback"],
        },
        "selected_mcp_adoption": {
            "passed": selected_mcp["passed"],
            "runtime": selected_mcp["runtime"],
            "second_mcp_runtime": selected_mcp["second_mcp_runtime"],
            "capability_id": selected_mcp["capability_id"],
            "trial_mode": selected_mcp["trial_mode"],
            "probation": selected_mcp["probation"],
            "restart_replayed_same_receipt": (
                selected_mcp["restart_replayed_same_receipt"]
            ),
            "worker_dispatch": selected_mcp["worker_dispatch"],
        },
        "a6": {
            "passed": a6["verdict"] == "PASS",
            "campaign": a6["campaign"],
            "provider_fallback": a6["fallback"],
            "all_down_wait": a6["all_down_wait"],
            "recovery": a6["recovery"],
            "deadlines": a6["deadlines"],
            "ambiguity": a6["ambiguous_effect"],
            "stop": a6["stop"],
            "restart": a6["startup_resume"],
            "joins": a6["joins"],
        },
        "mcp": {
            "passed": mcp["passed"],
            "transport": mcp["transport"],
            "projected_capability_count": len(mcp["projected_capabilities"]),
            "stable_identity_after_reconnect": (
                mcp["stable_identities_before"]
                == mcp["stable_identities_after"]
            ),
            "external_effect": {
                "success": (
                    mcp["mutating_invocation"]["claimed"] == "COMPLETED"
                    and mcp["mutating_invocation"]["verified"] == "MATCH"
                ),
                "receipt_state": mcp["mutating_invocation"]["receipt"]["state"],
                "replay_performed": mcp["mutating_replay"]["claimed_effect"][
                    "performed"
                ],
            },
            "timeout_after_effect": {
                "journal_state": mcp["timeout_after_effect"]["claimed_effect"][
                    "journal_state"
                ],
                "replay_performed": mcp[
                    "timeout_after_effect_replay_under_stop"
                ]["claimed_effect"]["performed"],
            },
        },
        "integrity": integrity,
        "gmail": {
            "read": "DEFERRED_BY_OWNER",
            "send": "DEFERRED_BY_OWNER",
            "observation": "DISABLED",
            "proof_executed": False,
        },
        "v11": {
            "exit": "PENDING",
            "a3": "BLOCKED_BY_PROVIDER",
            "a4": "NOT_STARTED",
            "state_48h": "NOT_STARTED",
        },
        "receipts": receipts,
        "commands": commands,
        "live_mutated": False,
        "main_or_origin_mutated": False,
        "activation_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "evidence" / "v12-final-product-convergence",
    )
    parser.add_argument("--work-root", type=Path, default=None)
    args = parser.parse_args(argv)

    source = _source_identity()
    _require(source["branch"] == BRANCH, f"unexpected branch: {source['branch']}")
    _require(
        source["merge_base_with_v11"] == BASE_V11_SHA,
        f"candidate is not based exactly on {BASE_V11_SHA}",
    )
    _require(SCHEMA_VERSION == 40, f"expected schema 40, got {SCHEMA_VERSION}")
    _require(
        source["worktree_clean_before_proof"],
        "worktree must be clean before source-bound proof",
    )

    work_root = (
        args.work_root.expanduser().resolve()
        if args.work_root is not None
        else Path(tempfile.mkdtemp(prefix="jarvis-v12-final-product-proof-"))
    )
    if args.work_root is not None:
        _require(not work_root.exists(), f"work root already exists: {work_root}")
        work_root.mkdir(parents=True)

    a5_path = work_root / "a5-self-expansion.json"
    selected_mcp_path = work_root / "selected-mcp-adoption.json"
    a6_path = work_root / "a6-endurance.json"
    mcp_root = work_root / "mcp-backend"
    commands = [
        _run(
            [
                sys.executable,
                str(
                    ROOT
                    / "scripts"
                    / "v12_self_expansion_convergence_proof.py"
                ),
                "--output",
                str(a5_path),
            ],
            timeout=900,
        ),
        _run(
            [
                sys.executable,
                str(ROOT / "scripts" / "v12_endurance_convergence_proof.py"),
                "--mode",
                "full",
                "--root",
                str(work_root / "a6-runtime"),
                "--output",
                str(a6_path),
            ],
            timeout=900,
        ),
        _run(
            [
                sys.executable,
                str(ROOT / "scripts" / "v12_mcp_backend_proof.py"),
                "--output-root",
                str(mcp_root),
            ],
            timeout=900,
        ),
    ]
    mcp_path = mcp_root / "V12_MCP_BACKEND_REAL_PROOF.json"
    a5 = _load(a5_path)
    selected_mcp = _load(selected_mcp_path)
    a6 = _load(a6_path)
    mcp = _load(mcp_path)

    _require(a5.get("passed") is True, "A5 self-expansion proof failed")
    _require(selected_mcp.get("passed") is True, "selected MCP adoption failed")
    _require(a6.get("verdict") == "PASS", "A6 endurance proof failed")
    _require(mcp.get("passed") is True, "MCP backend proof failed")
    _require(
        a6["source"]["base_v11_sha"] == BASE_V11_SHA,
        "A6 receipt named the wrong V11 base",
    )
    _require(
        a6["source"]["implementation_sha"] == source["source_sha"],
        "A6 receipt was not produced from the source proof SHA",
    )

    source["worktree_clean_after_disposable_proofs"] = not bool(
        _git("status", "--short")
    )
    source["main_sha_after"] = _git("rev-parse", "main")
    source["origin_main_sha_after"] = _git("rev-parse", "origin/main")
    _require(
        source["worktree_clean_after_disposable_proofs"],
        "disposable proofs modified the candidate worktree",
    )
    _require(
        source["main_sha_before"] == source["main_sha_after"]
        and source["origin_main_sha_before"] == source["origin_main_sha_after"],
        "main or origin/main moved during proof",
    )

    output_dir = args.output_dir.expanduser().resolve()
    a5_destination = output_dir / "a5-self-expansion.json"
    selected_mcp_destination = output_dir / "selected-mcp-adoption.json"
    a6_destination = output_dir / "a6-endurance.json"
    mcp_destination = output_dir / "mcp-backend.json"
    receipt_paths = {
        "a5": _receipt_identity(a5_path, a5_destination),
        "selected_mcp": _receipt_identity(
            selected_mcp_path, selected_mcp_destination
        ),
        "a6": _receipt_identity(a6_path, a6_destination),
        "mcp": _receipt_identity(mcp_path, mcp_destination),
    }
    report = _build_report(
        source=source,
        a5=a5,
        selected_mcp=selected_mcp,
        a6=a6,
        mcp=mcp,
        receipts=receipt_paths,
        commands=commands,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _copy_receipt(a5_path, a5_destination)
    _copy_receipt(selected_mcp_path, selected_mcp_destination)
    _copy_receipt(a6_path, a6_destination)
    _copy_receipt(mcp_path, mcp_destination)
    destination = output_dir / "proof.json"
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "passed": True,
        "evidence": str(destination),
        "source_sha": source["source_sha"],
        "sha256": _sha256(destination),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProofFailure as error:
        print(
            json.dumps(
                {"passed": False, "error": str(error)},
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from error
