from __future__ import annotations

import copy
from pathlib import Path

import pytest

from acceptance import (
    build_request,
    canonical_json_bytes,
    evaluate_response,
    load_purpose_bundle,
    sha256_bytes,
)
from mutant_adapter import ROUND2_MUTANTS, apply_mutant
from reference_adapter import build_response


EVALUATION_TIME = "2030-01-01T00:00:00Z"
CHALLENGE_NONCE = "r1-round2-fixed-challenge-nonce"
AUTHORITY_ID = "r1-round2-evaluator-authority"
PROCESS = {
    "exit_code": 0,
    "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "stderr_present": False,
    "timed_out": False,
}


@pytest.fixture(scope="module")
def round2_cases(purpose_root: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    bundle = load_purpose_bundle(purpose_root)
    case_root = tmp_path_factory.mktemp("round2-shared")
    cases = {}
    for scope in ("HARNESS_SELF_TEST", "PRODUCT_LIVE"):
        run_root = case_root / scope.lower()
        request = _request(bundle, run_root, scope)
        cases[scope] = {
            "run_root": run_root,
            "request": request,
            "response": build_response(request, compact_artifacts=True),
        }
    return {"bundle": bundle, "cases": cases}


def _request(bundle: dict, run_root: Path, proof_scope: str = "HARNESS_SELF_TEST") -> dict:
    return build_request(
        bundle,
        run_root,
        EVALUATION_TIME,
        requested_proof_scope=proof_scope,
        challenge_nonce=CHALLENGE_NONCE,
        evaluator_authority_id=AUTHORITY_ID,
    )


def _rehash_record(execution: dict, record: dict) -> None:
    old_id = record["evidence_id"]
    digest = sha256_bytes(canonical_json_bytes(record["payload"]))
    new_id = f"sha256:{digest}"
    record["payload_sha256"] = digest
    record["evidence_id"] = new_id
    for linked in execution["criterion_evidence"].values():
        for index, evidence_id in enumerate(linked):
            if evidence_id == old_id:
                linked[index] = new_id


def _relabel_product_live(response: dict) -> dict:
    response["adapter"]["proof_scope"] = "PRODUCT_LIVE"
    response["adapter"]["actor_run_id"] = "self-asserted-live-actor"
    response["adapter"]["verifier_run_id"] = "self-asserted-live-verifier"
    for execution in response["journeys"]:
        for record in execution["evidence"]:
            record["payload"]["proof_scope"] = "PRODUCT_LIVE"
            record["payload"]["real_or_fixture"] = "REAL_EXTERNAL_EFFECT"
            record["payload"]["producer"]["run_id"] = (
                response["adapter"]["verifier_run_id"]
                if record["payload"]["phase"] == "VERIFICATION"
                else response["adapter"]["actor_run_id"]
            )
            _rehash_record(execution, record)
    return response


def _status_detail(status: str, execution: dict) -> dict:
    criterion_id = next(iter(execution["criterion_evidence"]))
    record = next(
        item
        for item in execution["evidence"]
        if criterion_id in item["payload"]["criterion_ids"]
    )
    observation = {"status": status}
    if status == "BLOCKED":
        observation.update(
            {
                "dependency_id": "external-observer-service",
                "availability": "UNAVAILABLE",
            }
        )
    elif status == "WAITING":
        observation.update(
            {
                "wait_event_id": "event-provider-recovered",
                "event_state": "PENDING",
                "resume_condition": "Resume only after the recorded provider-recovered event is observed.",
            }
        )
    elif status == "NOT_TESTED":
        observation.update(
            {
                "criterion_execution": "NOT_EXECUTED",
                "unexecuted_criterion_ids": [criterion_id],
            }
        )
    else:
        observation.update(
            {
                "observed_result": "FAILED",
                "failure_kind": "CRITERION_FAILURE",
                "failed_criterion_ids": [criterion_id],
            }
        )
    record["payload"]["after_state"] = dict(record["payload"]["after_state"])
    record["payload"]["after_state"]["status_observation"] = observation
    record["payload"]["content"]["summary"] = (
        f"Controlled content-addressed {status} observation for {criterion_id}."
    )
    _rehash_record(execution, record)
    evidence_id = record["evidence_id"]
    common = {
        "status": status,
        "reason_code": {
            "BLOCKED": "EXTERNAL_DEPENDENCY_UNAVAILABLE",
            "NOT_TESTED": "CRITERIA_NOT_EXECUTED",
            "WAITING": "DURABLE_EVENT_PENDING",
            "FAIL": "CRITERION_FAILED",
        }[status],
        "explanation": f"Controlled {status} example with criterion-specific durable evidence.",
        "criterion_ids": [criterion_id],
        "evidence_ids": [evidence_id],
    }
    if status == "BLOCKED":
        common["detail"] = {
            "dependency_id": "external-observer-service",
            "dependency_kind": "external_service",
            "unavailable_observation_evidence_id": evidence_id,
            "retry_policy": "Retry on a recorded provider-availability event with bounded backoff.",
            "unblock_condition": "The identified external observer service answers a fresh bounded probe.",
        }
    elif status == "WAITING":
        common["detail"] = {
            "wait_event_id": "event-provider-recovered",
            "event_source": "durable-provider-monitor",
            "resume_condition": "Resume only after the recorded provider-recovered event is observed.",
            "resumption_mode": "EVENT_DRIVEN",
            "next_check_at": None,
        }
    elif status == "NOT_TESTED":
        common["detail"] = {
            "unexecuted_criterion_ids": [criterion_id],
            "why_not_exercised": "The named criterion was intentionally not executed in this controlled example.",
        }
    else:
        common["detail"] = {
            "failed_criterion_ids": [criterion_id],
            "failure_kind": "CRITERION_FAILURE",
            "failure_observation_evidence_id": evidence_id,
        }
    return common


def test_relabelled_rehashed_reference_cannot_establish_product_live(
    round2_cases: dict,
) -> None:
    bundle = round2_cases["bundle"]
    case = round2_cases["cases"]["PRODUCT_LIVE"]
    run_root = case["run_root"]
    request = case["request"]
    response = _relabel_product_live(copy.deepcopy(case["response"]))
    result = evaluate_response(request, response, bundle["manifest"], run_root, PROCESS)
    codes = {finding["code"] for finding in result["findings"]}
    assert result["status"] == "FAIL"
    assert result["product_claim_status"] == "FAIL"
    assert result["proof_scope"] == "PRODUCT_LIVE"
    assert "PRODUCT_LIVE_OBSERVER_REQUIRED" in codes
    assert result["proof_authority"]["scope_source"] == "EVALUATOR_REQUEST"
    assert result["proof_authority"]["observer_status"] == "ABSENT"

    harness_case = round2_cases["cases"]["HARNESS_SELF_TEST"]
    promoted = _relabel_product_live(copy.deepcopy(harness_case["response"]))
    promoted_result = evaluate_response(
        harness_case["request"],
        promoted,
        bundle["manifest"],
        harness_case["run_root"],
        PROCESS,
    )
    assert promoted_result["status"] == "FAIL"
    assert promoted_result["proof_scope"] == "HARNESS_SELF_TEST"
    assert promoted_result["product_claim_status"] == "NOT_TESTED"
    assert "SUBJECT_PROOF_SCOPE_MISMATCH" in {
        finding["code"] for finding in promoted_result["findings"]
    }


def test_product_live_self_assertion_categories_each_lack_independent_proof(
    round2_cases: dict,
) -> None:
    bundle = round2_cases["bundle"]
    case = round2_cases["cases"]["PRODUCT_LIVE"]
    run_root = case["run_root"]
    request = case["request"]
    baseline = case["response"]
    expected = {
        "live_source_locator_spoof": "UNTRUSTED_LIVE_SOURCE",
        "live_target_receipt_spoof": "UNTRUSTED_LIVE_TARGET_RECEIPT",
        "live_actor_identity_spoof": "UNTRUSTED_LIVE_ACTOR_IDENTITY",
        "live_verifier_identity_spoof": "UNTRUSTED_LIVE_VERIFIER_IDENTITY",
        "live_outcome_boolean_spoof": "UNTRUSTED_LIVE_OUTCOME_ASSERTION",
        "live_failure_injection_list_spoof": "UNTRUSTED_LIVE_FAILURE_INJECTION_ASSERTION",
    }
    for mutant, expected_code in expected.items():
        response = apply_mutant(copy.deepcopy(baseline), mutant)
        result = evaluate_response(request, response, bundle["manifest"], run_root, PROCESS)
        codes = {finding["code"] for finding in result["findings"]}
        assert expected_code in codes
        assert all(journey["status"] == "FAIL" for journey in result["journeys"])
    assert set(expected).issubset(ROUND2_MUTANTS)


def test_subject_cannot_self_register_an_observer(
    round2_cases: dict,
) -> None:
    bundle = round2_cases["bundle"]
    case = round2_cases["cases"]["PRODUCT_LIVE"]
    run_root = case["run_root"]
    request = case["request"]
    response = _relabel_product_live(copy.deepcopy(case["response"]))
    assert bundle["observer_registry"]["registrations"] == []
    assert request["observer_id"] is None
    assert request["observer_registry_sha256"] == bundle["observer_registry_sha256"]
    response["observer_registration"] = {"observer_id": "subject-selected"}
    result = evaluate_response(request, response, bundle["manifest"], run_root, PROCESS)
    assert result["status"] == "FAIL"
    assert result["findings"][0]["code"] == "UNKNOWN_FIELD"


def test_blind_nonpass_status_flips_are_rejected(
    round2_cases: dict,
) -> None:
    bundle = round2_cases["bundle"]
    case = round2_cases["cases"]["HARNESS_SELF_TEST"]
    run_root = case["run_root"]
    request = case["request"]
    baseline = case["response"]
    for status in ("BLOCKED", "NOT_TESTED", "WAITING", "FAIL"):
        response = apply_mutant(copy.deepcopy(baseline), f"blind_status_{status.lower()}")
        result = evaluate_response(request, response, bundle["manifest"], run_root, PROCESS)
        assert result["status"] == "FAIL"
        assert result["journeys"][0]["status"] == "FAIL"
        assert "STATUS_DETAIL_REQUIRED" in {item["code"] for item in result["journeys"][0]["findings"]}


def test_valid_controlled_nonpass_statuses_retain_exact_status(
    round2_cases: dict,
) -> None:
    bundle = round2_cases["bundle"]
    case = round2_cases["cases"]["HARNESS_SELF_TEST"]
    run_root = case["run_root"]
    request = case["request"]
    baseline = case["response"]
    for status in ("BLOCKED", "NOT_TESTED", "WAITING", "FAIL"):
        response = copy.deepcopy(baseline)
        execution = response["journeys"][0]
        execution["status"] = status
        execution["status_detail"] = _status_detail(status, execution)
        execution["outcome"]["useful"] = False
        result = evaluate_response(request, response, bundle["manifest"], run_root, PROCESS)
        assert result["journeys"][0]["status"] == status
        assert result["journeys"][0]["findings"] == []
        assert result["status"] == status


def test_status_detail_rejects_unknown_fields_and_dangling_evidence(
    round2_cases: dict,
) -> None:
    bundle = round2_cases["bundle"]
    case = round2_cases["cases"]["HARNESS_SELF_TEST"]
    run_root = case["run_root"]
    request = case["request"]
    baseline = case["response"]
    for corruption, expected_code in (("unknown", "UNKNOWN_FIELD"), ("dangling", "DANGLING_STATUS_EVIDENCE")):
        response = copy.deepcopy(baseline)
        execution = response["journeys"][0]
        execution["status"] = "BLOCKED"
        execution["status_detail"] = _status_detail("BLOCKED", execution)
        execution["outcome"]["useful"] = False
        if corruption == "unknown":
            execution["status_detail"]["surprise"] = True
        else:
            execution["status_detail"]["evidence_ids"] = ["sha256:" + "0" * 64]
        result = evaluate_response(request, response, bundle["manifest"], run_root, PROCESS)
        assert result["journeys"][0]["status"] == "FAIL"
        assert expected_code in {item["code"] for item in result["journeys"][0]["findings"]}


def test_product_live_rejection_is_byte_deterministic_with_fixed_authority(
    round2_cases: dict,
) -> None:
    bundle = round2_cases["bundle"]
    case = round2_cases["cases"]["PRODUCT_LIVE"]
    verdicts = []
    for _ in range(2):
        response = _relabel_product_live(copy.deepcopy(case["response"]))
        verdicts.append(
            evaluate_response(
                case["request"], response, bundle["manifest"], case["run_root"], PROCESS
            )
        )
    assert canonical_json_bytes(verdicts[0]) == canonical_json_bytes(verdicts[1])


def test_reference_receipts_bind_every_canonical_target(
    round2_cases: dict,
) -> None:
    case = round2_cases["cases"]["HARNESS_SELF_TEST"]
    request = case["request"]
    response = case["response"]
    contracts = {entry["journey_id"]: entry["contract"] for entry in request["contracts"]}
    for execution in response["journeys"]:
        contract = contracts[execution["journey_id"]]
        assert [item["target_id"] for item in execution["target_receipts"]] == [
            item["target_id"] for item in contract["canonical_targets"]
        ]
        assert all(item["canonical_identity"] for item in execution["target_receipts"])
        assert all(item["account_or_service_identity"] for item in execution["target_receipts"])
        assert all(item["receipt_id"] and item["idempotency_key"] for item in execution["target_receipts"])
