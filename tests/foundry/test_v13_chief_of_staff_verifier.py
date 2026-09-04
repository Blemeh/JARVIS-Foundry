from __future__ import annotations

import json
from pathlib import Path

from scripts.v13_chief_of_staff_independent_verifier import verify_proof
from scripts.v13_chief_of_staff_proof import run_proof


def test_independent_verifier_recomputes_truth_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    report = run_proof(
        tmp_path / "runtime",
        enforce_source=False,
    )
    proof_path = tmp_path / "proof.json"
    proof_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verified = verify_proof(
        proof_path,
        require_full_suite=False,
        enforce_source=False,
    )

    assert report["passed"] is True
    assert verified["verdict"] == "PASS"
    assert all(verified["checks"].values())
    assert verified["replay_on_backup"]["event_delta"] == 0
    assert verified["runtime_change_id"].startswith("cos-source-")
    assert len(verified["runtime_lifecycle_change_ids"]) == 3
    assert len(verified["runtime_lifecycle_world_event_ids"]) == 3
    assert verified["runtime_lifecycle_wait_id"].startswith("cos-wait-")
    assert verified["authenticated_owner_factor_event_id"]
    assert verified["late_stop_on_backup"]["guard_calls"] == 2
    assert verified["late_stop_on_backup"]["status"] == (
        "BLOCKED_BY_OWNER_CONTROL"
    )
    assert verified["recomputed_counts"]["decision_queue_open_matches"] == 1
    assert (
        verified["recomputed_counts"]["decision_queue_resolved_matches"]
        == 1
    )
    assert verified["recomputed_counts"]["item_state_changes"] == 3
    assert verified["recomputed_counts"]["world_observations"] == 4

    tampered = json.loads(proof_path.read_text(encoding="utf-8"))
    tampered["counts"]["candidates"] = 2
    tampered_path = tmp_path / "proof-tampered.json"
    tampered_path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    rejected = verify_proof(
        tampered_path,
        require_full_suite=False,
        enforce_source=False,
    )

    assert rejected["verdict"] == "FAIL"
    assert rejected["checks"]["proof_claimed_counts_match"] is False
    assert "proof_claimed_counts_match" in rejected["findings"]
