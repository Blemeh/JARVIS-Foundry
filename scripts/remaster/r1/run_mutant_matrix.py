"""Execute the preserved twelve plus Round-2 fail-closed R1 mutants."""

from __future__ import annotations

import argparse
from pathlib import Path

from acceptance import (
    canonical_json_bytes,
    formatted_json,
    run_external_adapter,
    sha256_bytes,
    sha256_file,
    write_result,
)
from mutant_adapter import FROZEN_MUTANTS, ROUND2_MUTANTS


EXPECTED_CODES = {
    "missing_evidence": "MISSING_REQUIRED_EVIDENCE",
    "fixture_as_live": "FIXTURE_AS_LIVE",
    "stale_source": "STALE_OR_UNIDENTIFIABLE_SOURCE",
    "target_substitution": "CANONICAL_TARGET_SUBSTITUTED",
    "self_verification": "SELF_VERIFICATION",
    "budget_overflow": "BUDGET_OVERFLOW",
    "duplicate_non_idempotent_effect": "DUPLICATE_NON_IDEMPOTENT_EFFECT",
    "absent_after_state": "ABSENT_AFTER_STATE",
    "contradiction_erasure": "CONTRADICTION_ERASED",
    "failed_rollback": "ROLLBACK_FAILED",
    "forged_owner_control": "FORGED_OWNER_CONTROL",
    "forbidden_financial_effect": "FORBIDDEN_FINANCIAL_EFFECT",
    "product_live_relabel_rehash": "PRODUCT_LIVE_OBSERVER_REQUIRED",
    "live_source_locator_spoof": "UNTRUSTED_LIVE_SOURCE",
    "live_target_receipt_spoof": "UNTRUSTED_LIVE_TARGET_RECEIPT",
    "live_actor_identity_spoof": "UNTRUSTED_LIVE_ACTOR_IDENTITY",
    "live_verifier_identity_spoof": "UNTRUSTED_LIVE_VERIFIER_IDENTITY",
    "live_outcome_boolean_spoof": "UNTRUSTED_LIVE_OUTCOME_ASSERTION",
    "live_failure_injection_list_spoof": "UNTRUSTED_LIVE_FAILURE_INJECTION_ASSERTION",
    "blind_status_fail": "STATUS_DETAIL_REQUIRED",
    "blind_status_blocked": "STATUS_DETAIL_REQUIRED",
    "blind_status_not_tested": "STATUS_DETAIL_REQUIRED",
    "blind_status_waiting": "STATUS_DETAIL_REQUIRED",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the frozen R1 adversarial mutant matrix")
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--purpose-root", type=Path, required=True)
    parser.add_argument("--script-root", type=Path, required=True)
    parser.add_argument("--evaluation-time", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--matrix-output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    temporary_parent = output_root / "temporary-runs"
    rows = []
    all_mutants = FROZEN_MUTANTS + ROUND2_MUTANTS
    product_live_mutants = {
        mutant for mutant in ROUND2_MUTANTS if mutant.startswith("live_") or mutant.startswith("product_live_")
    }
    for mutant in all_mutants:
        command = [
            str(args.python.resolve(strict=True)),
            "-B",
            str((args.script_root / "mutant_adapter.py").resolve(strict=True)),
            "--mutant",
            mutant,
        ]
        result = run_external_adapter(
            command,
            args.purpose_root,
            args.evaluation_time,
            run_root=None,
            temporary_parent=temporary_parent,
            requested_proof_scope=(
                "PRODUCT_LIVE" if mutant in product_live_mutants else "HARNESS_SELF_TEST"
            ),
        )
        result_path = output_root / f"{mutant}_REPAIR_ROUND2.json"
        codes = sorted({finding["code"] for finding in result["findings"]})
        expected_code = EXPECTED_CODES[mutant]
        failed_journey_ids = [
            journey["journey_id"] for journey in result["journeys"] if journey["status"] == "FAIL"
        ]
        result_record = {
            "schema": "jarvis-r1-mutant-result-repair-round2-v1",
            "mutant": mutant,
            "expected_code": expected_code,
            "observed_codes": codes,
            "status": result["status"],
            "proof_scope": result["proof_scope"],
            "product_claim_status": result["product_claim_status"],
            "observer_status": result.get("proof_authority", {}).get("observer_status"),
            "failed_journey_ids": failed_journey_ids,
            "criterion_evidence_nonempty": all(
                evidence_ids
                for journey in result["journeys"]
                for evidence_ids in journey["criterion_evidence"].values()
            ),
            "detected": result["status"] == "FAIL" and expected_code in codes,
            "canonical_verdict_sha256": sha256_bytes(canonical_json_bytes(result)),
        }
        write_result(result_path, result_record)
        rows.append(
            {
                "mutant": mutant,
                "expected_code": expected_code,
                "observed_codes": codes,
                "status": result["status"],
                "detected": result["status"] == "FAIL" and expected_code in codes,
                "failed_journey_ids": failed_journey_ids,
                "result_path": f"{output_root.name}/{result_path.name}",
                "result_sha256": sha256_file(result_path),
                "result_size": result_path.stat().st_size,
                "canonical_verdict_sha256": result_record["canonical_verdict_sha256"],
            }
        )
    if temporary_parent.exists() and not any(temporary_parent.iterdir()):
        temporary_parent.rmdir()
    matrix = {
        "schema": "jarvis-r1-mutant-matrix-v2",
        "program": "POST_V7_JARVIS_REMASTER",
        "stage": "R1",
        "evaluation_time": args.evaluation_time,
        "preserved_frozen_mutants": len(FROZEN_MUTANTS),
        "round2_mutants": len(ROUND2_MUTANTS),
        "required_mutants": len(all_mutants),
        "rejected_mutants": sum(1 for row in rows if row["detected"]),
        "all_rejected": all(row["detected"] for row in rows),
        "mutants": rows,
    }
    args.matrix_output.parent.mkdir(parents=True, exist_ok=True)
    args.matrix_output.write_text(formatted_json(matrix), encoding="utf-8", newline="\n")
    return 0 if matrix["all_rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
