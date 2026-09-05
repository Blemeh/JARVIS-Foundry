from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jarvis_foundry.autonomy_projection import (
    RESEARCH_DOSSIER_CONTRACT,
)
from jarvis_foundry.research_dossier import DOSSIER_CONTRACT


def test_four_real_decision_proofs_use_canonical_boundaries(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    output = tmp_path / "proof.json"
    project_python = repo_root / ".venv" / "Scripts" / "python.exe"
    python = project_python if project_python.is_file() else Path(sys.executable)
    result = subprocess.run(
        [
            str(python),
            str(repo_root / "scripts" / "v13_world_intelligence_proof.py"),
            "--repo-root",
            str(repo_root),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    proof = json.loads(output.read_text(encoding="utf-8"))

    assert proof["contract"] == "jarvis.v13.world-intelligence-proof.v1"
    assert proof["schema"] == 40
    assert set(proof["proofs"]) == {"A", "B", "C", "D"}
    assert RESEARCH_DOSSIER_CONTRACT == DOSSIER_CONTRACT

    technical = proof["proofs"]["A"]
    assert technical["preliminary"]["recommendation"][
        "selected_option_id"
    ] == "transaction-begin-immediate"
    assert technical["preliminary"]["stop_assessment"][
        "disposition"
    ] == "CONTINUE"
    assert technical["final"]["recommendation"][
        "selected_option_id"
    ] == "read-snapshot-mode-ro"
    assert technical["final"]["recommendation"]["changed_mind"] is True
    assert technical["final"]["stop_assessment"][
        "disposition"
    ] == "STOP_ENOUGH_EVIDENCE"
    assert technical["changed_evidence"] is True
    assert technical["executive_bridge"]["consumed_by_executive"] is True
    assert technical["a3_admission"]["status"] == "ADMITTED"
    assert technical["bounded_mission"]["mission_id"]

    library = proof["proofs"]["B"]
    assert library["recommendation"][
        "selected_option_id"
    ] == "reuse-jsonschema"
    assert library["actual_validation"] == "PASSED"
    assert library["syndicated_inputs"] == 3
    assert library["syndicated_records_retained"] == 1

    project = proof["proofs"]["C"]
    assert project["recommendation"][
        "selected_option_id"
    ] == "bounded-decision-contracts"
    owner_ref = project["recommendation"][
        "owner_context_advisory_refs"
    ][0]
    assert owner_ref["context_id"] == "v13-owner-context-input"
    assert owner_ref["provenance_authority"] == \
        "OWNER_CONTEXT_CANDIDATE_INPUT"
    assert project["ancestry"]["semantic_consumption_only"] is True

    non_code = proof["proofs"]["D"]
    assert non_code["recommendation"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert non_code["recommendation"]["selected_option_id"] is None
    assert non_code["stop_assessment"][
        "disposition"
    ] == "STOP_INSUFFICIENT_EVIDENCE"
    assert non_code["action"] == "WAIT_AND_RESEARCH_LATER"
    assert non_code["gmail_policy"]["authenticated"] is False
    assert non_code["gmail_policy"]["probed"] is False

    assert proof["adversarial"]["prompt_injection_records"] >= 1
    assert proof["adversarial"][
        "prompt_injection_authorities"
    ] == ["DATA_ONLY"]
    assert proof["adversarial"]["stale_source_ids"]
    assert proof["adversarial"][
        "source_outage_disposition"
    ] == "STOP_INSUFFICIENT_EVIDENCE"
    assert proof["projection"]["contract"] == DOSSIER_CONTRACT
    assert proof["projection"]["count"] == 4
    assert all(
        item["source_quality"]
        for item in proof["projection"]["dossiers"]
    )
    assert proof["calibration"]["observations"] == 1
    assert proof["calibration"]["self_training"] is False
    assert proof["event_counts"]["mission.created"] == 1
    assert proof["event_counts"]["executive.objective_created"] == 1
    assert proof["event_counts"]["research.decision_result_recorded"] == 1
    assert proof["boundaries"]["live_mutated"] is False
    assert proof["boundaries"]["main_mutated"] is False
    assert proof["boundaries"]["origin_main_mutated"] is False
