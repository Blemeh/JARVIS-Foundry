from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import v13_final_personal_executive_convergence_proof as proof
from scripts.v13_final_personal_executive_verifier import (
    RECEIPT_KEYS,
    verify_proof,
)

from test_v13_final_personal_executive_convergence import _receipts


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _proof_fixture(tmp_path: Path) -> Path:
    source_sha = "1" * 40
    spine = proof._run_spine_proof(
        tmp_path / "runtime",
        _receipts(),
    )
    output = tmp_path / "output"
    receipt_root = output / "receipts"
    receipt_root.mkdir(parents=True)
    receipt_identities = {}
    for key in RECEIPT_KEYS:
        path = receipt_root / f"{key}.json"
        path.write_text(
            json.dumps({"source_sha": source_sha}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        receipt_identities[key] = {
            "path": str(path),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
    report = proof.build_report(
        source={
            "source_sha": source_sha,
            "source_tree_sha": "2" * 40,
        },
        spine=spine,
        receipt_checks={key: True for key in RECEIPT_KEYS},
        copied_receipts=receipt_identities,
    )
    report_path = output / "proof.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report_path


def test_final_verifier_recomputes_database_truth_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    proof_path = _proof_fixture(tmp_path)

    verified = verify_proof(
        proof_path,
        enforce_source=False,
        require_reports=False,
    )

    assert verified["verdict"] == "PASS"
    assert verified["critical"] == []
    assert verified["high"] == []
    assert all(verified["checks"].values())
    assert len(verified["database_truth"]["effects"]) == 3
    assert verified["database_truth"]["starts_during_stop"] == []

    tampered = json.loads(proof_path.read_text(encoding="utf-8"))
    tampered["duplicate_counters"]["effects"] = 1
    tampered_path = proof_path.with_name("proof-tampered.json")
    tampered_path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    rejected = verify_proof(
        tampered_path,
        enforce_source=False,
        require_reports=False,
    )

    assert rejected["verdict"] == "FAIL"
    assert (
        rejected["checks"][
            "database.duplicate_claims_match_recomputation"
        ]
        is False
    )
    assert "database.duplicate_claims_match_recomputation" in rejected["high"]
