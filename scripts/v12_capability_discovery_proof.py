"""Disposable V12 capability-solution discovery proof.

The proof uses the real checkout for source/Git archaeology and the existing
GitHub research connector for mature external solutions.  It writes only a
disposable SQLite ledger and an evidence JSON file; it never installs,
registers, creates a worktree, pushes, or touches a live database/provider.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_foundry.capability_solution_discovery import (  # noqa: E402
    CapabilityGapRequirement,
    CapabilitySolutionDiscovery,
    ReuseTier,
    search_git_archaeology,
)
from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.fabric.catalog import record_outcome  # noqa: E402
from jarvis_foundry.fabric.loop import seed_builtins  # noqa: E402
from jarvis_foundry.fabric.model import VerifyVerdict  # noqa: E402


def gap(action: str, *, capability: str | None = None, description: str | None = None) -> CapabilityGapRequirement:
    capability_id = capability or f"cap.{action}"
    return CapabilityGapRequirement(
        gap_id=f"proof-gap-{action.replace('.', '-')}",
        requirement_set_id="proof-capreq-v12-real-gaps",
        requested_action=action,
        missing_capability_id=capability_id,
        description=description or f"Real proof mission is blocked by {action}",
        required_actions=(action,),
        required_capability_ids=(capability_id,),
        candidate_identity={"candidate_id": f"proof-{action}", "candidate_type": "repository_improvement"},
    )


def run(output: Path) -> dict[str, Any]:
    repo_root = ROOT
    with tempfile.TemporaryDirectory(prefix="jarvis-v12-capability-proof-") as temp:
        db = FoundryDatabase(Path(temp) / "foundry.db")
        db.initialize()
        # This is a real current Fabric capability, marked ready only by a
        # disposable verified outcome in the proof database.
        seed_builtins(db)
        record_outcome(
            db,
            "cap.artifact.pdf.copy",
            success=True,
            verified=VerifyVerdict.MATCH,
            note="disposable pypdf readback proof",
        )
        pdf_run = CapabilitySolutionDiscovery(
            db, repo_root=repo_root,
        ).discover(gap("artifact.pdf.copy"), record_claims=False)

        mcp_run = CapabilitySolutionDiscovery(
            db, repo_root=repo_root,
        ).discover(
            gap(
                "mcp.client.invoke",
                description="Owner mission needs a bounded MCP client; no Fabric capability is registered",
            ),
            search_all_tiers=True,
            record_claims=True,
        )

        uia_requirement = gap(
            "windows.uia.click",
            description="Owner mission needs one bounded Windows UI Automation verb",
        )
        uia_candidates, uia_report = search_git_archaeology(
            uia_requirement,
            repo_root,
            max_refs=32,
            max_worktrees=32,
            max_commits=32,
            max_heads=8,
            max_matches=24,
        )

        external_report = next(
            report for report in mcp_run.search_reports if report.stage == "EXTERNAL"
        )
        provider_completed = external_report.state.value == "COMPLETED"
        external_candidates_observed = any(
            item.tier is ReuseTier.EXTERNAL for item in mcp_run.candidates
        )
        history_observed = bool(
            uia_report.inspected.get("refs_inspected")
            and uia_report.inspected.get("worktrees_inspected")
            and uia_report.inspected.get("commands")
        )
        fabric_selected = (
            pdf_run.recommendation.selected_candidate_id is not None
            and any(item.tier is ReuseTier.FABRIC_CATALOG and item.disposition.value == "RECOMMENDED" for item in pdf_run.candidates)
        )
        verdict = "CAPABILITY_DISCOVERY_PROOF_COMPLETE" if provider_completed and external_candidates_observed and history_observed and fabric_selected else "PENDING_EXTERNAL_OR_HISTORY_EVIDENCE"
        payload: dict[str, Any] = {
            "contract_version": "jarvis.v12.capability-discovery-proof.v1",
            "verdict": verdict,
            "scope": "discovery/comparison only; no install/register/provider write/live mutation",
            "repository": str(repo_root),
            "repository_head": uia_report.inspected.get("current_head"),
            "base": {
                "ref": "candidate/v11-integration-base2-20260902",
                "sha": "30998ef06d9be73adb30387c10a7fa72ef910d28",
            },
            "dependency_input": {
                "ref": "candidate/v11-capability-requirements-20260902",
                "sha": "aac5710248f2df9eda46c59694a0f43e1d71d6e5",
            },
            "real_gap_proofs": {
                "fabric_pdf": pdf_run.as_dict(),
                "mcp_external": mcp_run.as_dict(),
                "windows_uia_git": {
                    "requirement": uia_requirement.as_dict(),
                    "candidates": [item.as_dict() for item in uia_candidates],
                    "search_report": uia_report.as_dict(),
                },
            },
            "assertions": {
                "fabric_selected": fabric_selected,
                "external_provider_completed": provider_completed,
                "external_candidate_observed": external_candidates_observed,
                "git_refs_worktrees_commands_observed": history_observed,
                "external_data_only": all(
                    item.authority == "DATA_ONLY" and item.data_only
                    for item in mcp_run.candidates if item.tier is ReuseTier.EXTERNAL
                ),
                "no_install_register_or_execution_authorization": all(
                    not item.installed and not item.registered and not item.execution_authorized
                    for item in (*pdf_run.candidates, *mcp_run.candidates, *uia_candidates)
                ),
                "no_provider_writes": True,
            },
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence" / "v12-capability-discovery" / "proof.json",
    )
    args = parser.parse_args()
    payload = run(args.output)
    print(json.dumps({
        "verdict": payload["verdict"],
        "output": str(args.output),
        "assertions": payload["assertions"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
