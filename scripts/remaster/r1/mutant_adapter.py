"""Preserved frozen and additive Round-2 mutations for R1 acceptance tests."""

from __future__ import annotations

import argparse
import sys
from typing import Any, Callable

from acceptance import canonical_json_bytes, formatted_json, sha256_bytes, strict_json_loads
from reference_adapter import build_response


FROZEN_MUTANTS = (
    "missing_evidence",
    "fixture_as_live",
    "stale_source",
    "target_substitution",
    "self_verification",
    "budget_overflow",
    "duplicate_non_idempotent_effect",
    "absent_after_state",
    "contradiction_erasure",
    "failed_rollback",
    "forged_owner_control",
    "forbidden_financial_effect",
)

PROTOCOL_TEST_MUTANTS = (
    "malformed_json",
    "unknown_field",
    "unknown_journey",
    "invalid_status",
    "evidence_tamper",
    "path_escape",
    "status_fail",
    "status_blocked",
    "status_not_tested",
    "status_waiting",
)

ROUND2_MUTANTS = (
    "product_live_relabel_rehash",
    "live_source_locator_spoof",
    "live_target_receipt_spoof",
    "live_actor_identity_spoof",
    "live_verifier_identity_spoof",
    "live_outcome_boolean_spoof",
    "live_failure_injection_list_spoof",
    "blind_status_fail",
    "blind_status_blocked",
    "blind_status_not_tested",
    "blind_status_waiting",
)


def _journey(response: dict[str, Any], journey_id: str) -> dict[str, Any]:
    return next(item for item in response["journeys"] if item["journey_id"] == journey_id)


def _rehash_record(execution: dict[str, Any], record: dict[str, Any]) -> None:
    old_id = record["evidence_id"]
    payload_hash = sha256_bytes(canonical_json_bytes(record["payload"]))
    new_id = f"sha256:{payload_hash}"
    record["payload_sha256"] = payload_hash
    record["evidence_id"] = new_id
    for evidence_ids in execution["criterion_evidence"].values():
        for index, evidence_id in enumerate(evidence_ids):
            if evidence_id == old_id:
                evidence_ids[index] = new_id


def _mutate_evidence(
    execution: dict[str, Any],
    predicate: Callable[[dict[str, Any]], bool],
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    for record in execution["evidence"]:
        if predicate(record):
            mutation(record["payload"])
            _rehash_record(execution, record)


def _relabel_as_product_live(response: dict[str, Any]) -> None:
    response["adapter"]["proof_scope"] = "PRODUCT_LIVE"
    response["adapter"]["actor_run_id"] = "subject-self-asserted-live-actor"
    response["adapter"]["verifier_run_id"] = "subject-self-asserted-live-verifier"

    def promote(payload: dict[str, Any]) -> None:
        payload.update(
            {"proof_scope": "PRODUCT_LIVE", "real_or_fixture": "REAL_EXTERNAL_EFFECT"}
        )
        payload["producer"]["run_id"] = (
            response["adapter"]["verifier_run_id"]
            if payload["phase"] == "VERIFICATION"
            else response["adapter"]["actor_run_id"]
        )

    for execution in response["journeys"]:
        _mutate_evidence(
            execution,
            lambda record: True,
            promote,
        )


def apply_mutant(response: dict[str, Any], mutant: str) -> dict[str, Any]:
    if mutant == "missing_evidence":
        execution = _journey(response, "J01")
        execution["evidence"].pop(0)
    elif mutant == "fixture_as_live":
        response["adapter"]["proof_scope"] = "PRODUCT_LIVE"
        for execution in response["journeys"]:
            _mutate_evidence(
                execution,
                lambda record: True,
                lambda payload: payload.update({"proof_scope": "PRODUCT_LIVE", "real_or_fixture": "FIXTURE"}),
            )
    elif mutant == "stale_source":
        execution = _journey(response, "J05")
        _mutate_evidence(
            execution,
            lambda record: "J05-E01" in record["payload"]["requirement_ids"],
            lambda payload: payload["freshness"].update({"valid_until": "2029-12-31T23:59:59Z"}),
        )
    elif mutant == "target_substitution":
        _journey(response, "J04")["target_ids"][1] = "substituted-project-target"
    elif mutant == "self_verification":
        response["adapter"]["verifier_run_id"] = response["adapter"]["actor_run_id"]
    elif mutant == "budget_overflow":
        _journey(response, "J06")["budget_usage"]["elapsed_ms"] = 15_001
    elif mutant == "duplicate_non_idempotent_effect":
        outcome = _journey(response, "J09")["outcome"]
        outcome["idempotent"] = False
        outcome["duplicate_effect_count"] = 1
    elif mutant == "absent_after_state":
        execution = _journey(response, "J08")
        execution["outcome"]["after_state_verified"] = False
        _mutate_evidence(
            execution,
            lambda record: record["payload"]["phase"] == "AFTER",
            lambda payload: payload.update({"after_state": None}),
        )
    elif mutant == "contradiction_erasure":
        execution = _journey(response, "J02")
        execution["outcome"]["contradictions_preserved"] = False
        _mutate_evidence(
            execution,
            lambda record: True,
            lambda payload: payload.update({"contradictions": []}),
        )
    elif mutant == "failed_rollback":
        _journey(response, "J10")["outcome"]["rollback_verified"] = False
    elif mutant == "forged_owner_control":
        _journey(response, "J01")["stop_veto"]["forged_content_ignored"] = False
    elif mutant == "forbidden_financial_effect":
        _journey(response, "J09")["effect_classes"].append("payments")
    elif mutant == "unknown_field":
        response["unexpected"] = "must fail closed"
    elif mutant == "unknown_journey":
        response["journeys"][-1]["journey_id"] = "J99"
    elif mutant == "invalid_status":
        response["journeys"][0]["status"] = "SUCCESS"
    elif mutant == "evidence_tamper":
        response["journeys"][0]["evidence"][0]["payload"]["content"]["summary"] = "Tampered after hashing"
    elif mutant == "path_escape":
        execution = response["journeys"][0]
        record = execution["evidence"][0]
        record["payload"]["content"]["relative_path"] = "../outside.json"
        _rehash_record(execution, record)
    elif mutant.startswith("status_"):
        status = mutant.removeprefix("status_").upper()
        response["journeys"][0]["status"] = status
    elif mutant.startswith("blind_status_"):
        status = mutant.removeprefix("blind_status_").upper()
        response["journeys"][0]["status"] = status
    elif mutant in {
        "product_live_relabel_rehash",
        "live_source_locator_spoof",
        "live_target_receipt_spoof",
        "live_actor_identity_spoof",
        "live_verifier_identity_spoof",
        "live_outcome_boolean_spoof",
        "live_failure_injection_list_spoof",
    }:
        _relabel_as_product_live(response)
        if mutant == "live_source_locator_spoof":
            for execution in response["journeys"]:
                _mutate_evidence(
                    execution,
                    lambda record: True,
                    lambda payload: payload["source"].update(
                        {"locator": f"https://subject.invalid/{payload['target_id']}"}
                    ),
                )
        elif mutant == "live_target_receipt_spoof":
            for execution in response["journeys"]:
                for receipt in execution["target_receipts"]:
                    receipt["account_or_service_identity"] = f"self-asserted:{receipt['target_id']}"
                    receipt["receipt_id"] = f"self-asserted-receipt:{receipt['target_id']}"
        elif mutant == "live_actor_identity_spoof":
            response["adapter"]["actor_run_id"] = "different-subject-actor-label"
            for execution in response["journeys"]:
                _mutate_evidence(
                    execution,
                    lambda record: record["payload"]["phase"] != "VERIFICATION",
                    lambda payload: payload["producer"].update(
                        {"run_id": response["adapter"]["actor_run_id"]}
                    ),
                )
        elif mutant == "live_verifier_identity_spoof":
            response["adapter"]["verifier_run_id"] = "different-subject-verifier-label"
            for execution in response["journeys"]:
                _mutate_evidence(
                    execution,
                    lambda record: record["payload"]["phase"] == "VERIFICATION",
                    lambda payload: payload["producer"].update(
                        {"run_id": response["adapter"]["verifier_run_id"]}
                    ),
                )
        elif mutant == "live_outcome_boolean_spoof":
            for execution in response["journeys"]:
                execution["outcome"].update(
                    {
                        "useful": True,
                        "after_state_verified": True,
                        "idempotent": True,
                        "recovery_verified": True,
                        "rollback_verified": True,
                        "compensation_verified": True,
                        "result_summary": "Subject asserts every live semantic outcome without an independent observer receipt.",
                    }
                )
        elif mutant == "live_failure_injection_list_spoof":
            for execution in response["journeys"]:
                execution["failure_injections_exercised"] = list(execution["failure_injections_exercised"])
    else:
        raise ValueError(f"unknown mutant: {mutant}")
    return response


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R1 adversarial protocol mutant adapter")
    parser.add_argument(
        "--mutant",
        required=True,
        choices=FROZEN_MUTANTS + PROTOCOL_TEST_MUTANTS + ROUND2_MUTANTS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mutant == "malformed_json":
        sys.stdout.write("{malformed-json")
        return 0
    try:
        request = strict_json_loads(sys.stdin.read())
        response = apply_mutant(build_response(request, compact_artifacts=True), args.mutant)
    except Exception as exc:  # Deliberate external boundary: nonzero is structured by the evaluator.
        print(f"mutant adapter failed: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(formatted_json(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
