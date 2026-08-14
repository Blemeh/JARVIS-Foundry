"""Deterministic satisfiability adapter for the R1 purpose harness.

The adapter performs actual digital file mutations only beneath the supplied
temporary run root. Its evidence is permanently ``HARNESS_SELF_TEST`` and may
never establish product or live capability.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from acceptance import (
    PROTOCOL_VERSION,
    RESPONSE_SCHEMA,
    canonical_json_bytes,
    formatted_json,
    parse_utc_timestamp,
    sha256_bytes,
    sha256_file,
    strict_json_loads,
    validate_request,
)


ADAPTER_IDENTITY = {
    "adapter_id": "r1-deterministic-reference-adapter",
    "adapter_version": "1.0.0",
    "system_identity": "R1_REFERENCE_FILE_SYSTEM",
    "system_release": "reference-file-effects-v1",
    "actor_run_id": "reference-actor-run-v1",
    "verifier_run_id": "reference-independent-verifier-run-v1",
    "proof_scope": "HARNESS_SELF_TEST",
}


def _timestamp(value) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(path: Path, value: Any) -> bytes:
    data = formatted_json(value).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _effect_states(journey_root: Path, journey_id: str, target_ids: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    target = journey_root / "target-state.json"
    before_value = {
        "schema": "jarvis-reference-target-state-v1",
        "journey_id": journey_id,
        "state": "BEFORE",
        "target_ids": target_ids,
        "sequence": 0,
    }
    before_bytes = _write_json(target, before_value)
    before = {"sha256": sha256_bytes(before_bytes), "size": len(before_bytes), "state": "BEFORE"}
    after_value = {
        "schema": "jarvis-reference-target-state-v1",
        "journey_id": journey_id,
        "state": "USEFUL_EFFECT_VERIFIED",
        "target_ids": target_ids,
        "sequence": 1,
    }
    after_bytes = _write_json(target, after_value)
    after = {"sha256": sha256_bytes(after_bytes), "size": len(after_bytes), "state": "USEFUL_EFFECT_VERIFIED"}
    corrupt_value = dict(after_value)
    corrupt_value["state"] = "INJECTED_FAILURE"
    corrupt_value["sequence"] = 2
    _write_json(target, corrupt_value)
    restored_bytes = _write_json(target, after_value)
    if sha256_bytes(restored_bytes) != after["sha256"]:
        raise RuntimeError(f"{journey_id}: deterministic restoration hash mismatch")
    return before, after


def _evidence_record(
    run_root: Path,
    journey_root: Path,
    contract: dict[str, Any],
    requirement: dict[str, Any],
    requirement_index: int,
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    observed_at: str,
    valid_until: str,
    compact_content: tuple[Path, bytes] | None = None,
) -> dict[str, Any]:
    journey_id = contract["journey_id"]
    target_ids = [item["target_id"] for item in contract["canonical_targets"]]
    target_id = target_ids[requirement_index % len(target_ids)]
    if compact_content is None:
        relative_path = Path(journey_id) / f"evidence-{requirement['id']}.json"
        artifact_path = run_root / relative_path
        artifact_value = {
            "schema": "jarvis-reference-evidence-artifact-v1",
            "journey_id": journey_id,
            "requirement_id": requirement["id"],
            "criterion_ids": requirement["criterion_ids"],
            "phase": requirement["phase"],
            "proof_dimension": requirement["proof_dimension"],
            "target_id": target_id,
            "before_state": before_state,
            "after_state": after_state,
            "operation": "actual temporary-directory digital file effect",
            "restoration_verified": True,
        }
        artifact_bytes = _write_json(artifact_path, artifact_value)
    else:
        relative_path, artifact_bytes = compact_content
    is_verification = requirement["phase"] == "VERIFICATION"
    contradictions = []
    if contract["executable_requirements"]["contradictions_preserved"] and requirement["phase"] in {"SOURCE", "AFTER"}:
        contradictions = [f"{journey_id}-supporting-source", f"{journey_id}-contradicting-source"]
    payload = {
        "requirement_ids": [requirement["id"]],
        "criterion_ids": list(requirement["criterion_ids"]),
        "kind": requirement["kind"],
        "phase": requirement["phase"],
        "proof_dimension": requirement["proof_dimension"],
        "source": {
            "source_id": f"reference-source-{journey_id}-{requirement['id']}",
            "source_type": "temporary_file_effect",
            "locator": relative_path.as_posix(),
            "capture_method": "write_then_sha256_readback",
        },
        "captured_at": observed_at,
        "target_id": target_id,
        "before_state": before_state,
        "after_state": after_state,
        "real_or_fixture": "REAL_TEMPORARY_EFFECT",
        "proof_scope": "HARNESS_SELF_TEST",
        "producer": {
            "actor_id": "reference-independent-verifier" if is_verification else "reference-effect-actor",
            "run_id": ADAPTER_IDENTITY["verifier_run_id"] if is_verification else ADAPTER_IDENTITY["actor_run_id"],
            "role": "INDEPENDENT_VERIFIER" if is_verification else "ACTOR",
        },
        "freshness": {"observed_at": observed_at, "valid_until": valid_until},
        "uncertainty": {
            "level": "MEDIUM" if contradictions else "LOW",
            "statement": "Harness evidence is bounded to deterministic temporary file effects only.",
        },
        "contradictions": contradictions,
        "content": {
            "relative_path": relative_path.as_posix(),
            "sha256": sha256_bytes(artifact_bytes),
            "size": len(artifact_bytes),
            "media_type": "application/json",
            "summary": f"Actual temporary evidence for {journey_id} requirement {requirement['id']}.",
        },
    }
    payload_hash = sha256_bytes(canonical_json_bytes(payload))
    return {
        "evidence_id": f"sha256:{payload_hash}",
        "payload_sha256": payload_hash,
        "payload": payload,
    }


def build_response(request: dict[str, Any], compact_artifacts: bool = False) -> dict[str, Any]:
    validate_request(request)
    run_root = Path(request["run_root"]).resolve(strict=True)
    evaluation = parse_utc_timestamp(request["evaluation_time"], "request.evaluation_time")
    observed_at = _timestamp(evaluation)
    valid_until = _timestamp(evaluation + timedelta(hours=1))
    executions = []
    for journey_index, entry in enumerate(request["contracts"], start=1):
        contract = entry["contract"]
        journey_id = contract["journey_id"]
        journey_root = (run_root / journey_id).resolve()
        if not journey_root.is_relative_to(run_root):
            raise RuntimeError(f"{journey_id}: target escapes run root")
        journey_root.mkdir(parents=True, exist_ok=False)
        target_ids = [item["target_id"] for item in contract["canonical_targets"]]
        before_state, after_state = _effect_states(journey_root, journey_id, target_ids)
        compact_content = None
        if compact_artifacts:
            compact_relative = Path(journey_id) / "evidence-shared.json"
            compact_bytes = _write_json(
                run_root / compact_relative,
                {
                    "schema": "jarvis-reference-evidence-artifact-v1",
                    "journey_id": journey_id,
                    "operation": "actual temporary-directory digital file effect",
                    "restoration_verified": True,
                },
            )
            compact_content = (compact_relative, compact_bytes)
        target_receipts = []
        for target in contract["canonical_targets"]:
            receipt_seed = {
                "journey_id": journey_id,
                "target_id": target["target_id"],
                "before_state_sha256": before_state["sha256"],
                "after_state_sha256": after_state["sha256"],
            }
            receipt_digest = sha256_bytes(canonical_json_bytes(receipt_seed))
            target_receipts.append(
                {
                    "target_id": target["target_id"],
                    "canonical_identity": target["identity"],
                    "account_or_service_identity": f"temporary-harness:{target['target_id']}",
                    "before_state_sha256": before_state["sha256"],
                    "after_state_sha256": after_state["sha256"],
                    "receipt_id": f"temporary-receipt:{receipt_digest}",
                    "idempotency_key": f"temporary-idempotency:{journey_id}:{target['target_id']}",
                }
            )
        evidence = [
            _evidence_record(
                run_root,
                journey_root,
                contract,
                requirement,
                requirement_index,
                before_state,
                after_state,
                observed_at,
                valid_until,
                compact_content,
            )
            for requirement_index, requirement in enumerate(contract["required_evidence"])
        ]
        criterion_evidence = {item["id"]: [] for item in contract["success_criteria"]}
        for record in evidence:
            for criterion_id in record["payload"]["criterion_ids"]:
                criterion_evidence[criterion_id].append(record["evidence_id"])
        executions.append(
            {
                "journey_id": journey_id,
                "status": "PASS",
                "target_ids": target_ids,
                "budget_usage": {
                    "elapsed_ms": 100 + journey_index,
                    "calls": 0,
                    "tokens": 0,
                    "effects": min(3, contract["budget"]["max_effects"]),
                    "cost_usd": 0,
                },
                "stop_veto": {
                    "stop_checked": True,
                    "veto_checked": True,
                    "forged_content_ignored": True,
                    "control_actor_id": "reference-authenticated-owner-control",
                    "control_source": "AUTHENTICATED_OWNER_CONTROL",
                },
                "effect_classes": list(contract["executable_requirements"]["allowed_effect_classes"]),
                "target_receipts": target_receipts,
                "evidence": evidence,
                "criterion_evidence": criterion_evidence,
                "failure_injections_exercised": [item["id"] for item in contract["failure_injections"]],
                "outcome": {
                    "useful": True,
                    "after_state_verified": True,
                    "idempotent": True,
                    "duplicate_effect_count": 0,
                    "contradictions_preserved": True,
                    "recovery_verified": True,
                    "rollback_verified": True,
                    "compensation_verified": True,
                    "result_summary": f"{journey_id} completed deterministic file effects, recovery, restoration, and independent harness verification.",
                },
                "status_detail": None,
            }
        )
    return {
        "schema": RESPONSE_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request["request_id"],
        "adapter": dict(ADAPTER_IDENTITY),
        "journeys": executions,
    }


def main() -> int:
    try:
        request = strict_json_loads(sys.stdin.read())
        response = build_response(request)
    except Exception as exc:  # External protocol boundary must return nonzero, never a fake response.
        print(f"reference adapter rejected request: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(formatted_json(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
