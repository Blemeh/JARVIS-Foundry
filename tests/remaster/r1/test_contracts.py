from __future__ import annotations

import hashlib
import json
from pathlib import Path

from acceptance import (
    FORBIDDEN_EFFECT_CLASSES,
    JOURNEY_IDS,
    build_request,
    canonical_json_bytes,
    load_purpose_bundle,
    sha256_bytes,
    validate_request,
)


FROZEN_PACKAGE_SHA256 = "281b1f86c45b92a561a29ebb41e02be1cebe1ee291d7ee4fbff2035c6d21e0ab"


def test_exactly_ten_machine_readable_journey_contracts(purpose_root: Path) -> None:
    bundle = load_purpose_bundle(purpose_root)
    files = sorted((purpose_root / "journeys").glob("J*.json"))
    assert len(files) == 10
    assert tuple(item["journey_id"] for item in bundle["contracts"]) == JOURNEY_IDS
    assert [path.name for path in files] == [f"{journey_id}.json" for journey_id in JOURNEY_IDS]
    assert [item["journey_id"] for item in bundle["spec"]["journey_contracts"]] == list(JOURNEY_IDS)


def test_every_contract_has_complete_journey_specific_semantics(purpose_root: Path) -> None:
    bundle = load_purpose_bundle(purpose_root)
    purposes = set()
    target_sets = set()
    for contract in bundle["contracts"]:
        required = {
            "inputs",
            "preconditions",
            "canonical_targets",
            "budget",
            "required_evidence",
            "success_criteria",
            "unacceptable_outcomes",
            "intervention_rules",
            "failure_injections",
            "recovery",
            "rollback",
            "proof_dimensions",
        }
        assert required.issubset(contract)
        assert len(contract["inputs"]) >= 2
        assert len(contract["preconditions"]) >= 2
        assert len(contract["required_evidence"]) >= 5
        assert len(contract["success_criteria"]) >= 3
        assert len(contract["unacceptable_outcomes"]) >= 3
        assert len(contract["failure_injections"]) >= 2
        assert contract["purpose"] not in purposes
        purposes.add(contract["purpose"])
        targets = tuple(item["target_id"] for item in contract["canonical_targets"])
        assert targets not in target_sets
        target_sets.add(targets)


def test_executable_requirements_cannot_be_prose_only(purpose_root: Path) -> None:
    contracts = load_purpose_bundle(purpose_root)["contracts"]
    mandatory_true = {
        "actor_independence",
        "stop_veto",
        "provenance",
        "freshness",
        "uncertainty",
        "usefulness",
        "idempotency",
        "after_state",
        "recovery",
        "rollback",
    }
    for contract in contracts:
        requirements = contract["executable_requirements"]
        assert all(requirements[key] is True for key in mandatory_true)
        assert contract["budget"]["max_elapsed_ms"] <= 15_000
        assert contract["budget"]["max_cost_usd"] == 0
        assert not (set(requirements["allowed_effect_classes"]) & FORBIDDEN_EFFECT_CLASSES)
        evidence_dimensions = {item["proof_dimension"] for item in contract["required_evidence"]}
        assert evidence_dimensions == set(contract["proof_dimensions"])
    by_id = {item["journey_id"]: item for item in contracts}
    assert by_id["J02"]["executable_requirements"]["contradictions_preserved"] is True
    assert by_id["J03"]["executable_requirements"]["contradictions_preserved"] is True
    assert by_id["J05"]["executable_requirements"]["contradictions_preserved"] is True
    assert by_id["J09"]["executable_requirements"]["compensation"] is True


def test_financial_boundary_is_exact_and_deterministic(purpose_root: Path) -> None:
    spec = load_purpose_bundle(purpose_root)["spec"]
    assert set(spec["operation_boundary"]["deterministically_denied_effect_classes"]) == FORBIDDEN_EFFECT_CLASSES
    assert spec["operation_boundary"]["allowed_scope"].startswith("Governed digital work")


def test_contract_manifest_hashes_canonical_embedded_objects(purpose_root: Path, tmp_path: Path) -> None:
    bundle = load_purpose_bundle(purpose_root)
    request = build_request(bundle, tmp_path / "run", "2030-01-01T00:00:00Z")
    validate_request(request)
    for entry in request["contracts"]:
        assert entry["contract_sha256"] == sha256_bytes(canonical_json_bytes(entry["contract"]))
    assert [item["sha256"] for item in bundle["manifest"]] == [
        item["contract_sha256"] for item in request["contracts"]
    ]


def test_public_schemas_are_strict_at_every_named_object(purpose_root: Path) -> None:
    contract_schema = json.loads((purpose_root / "JOURNEY_CONTRACT_SCHEMA.json").read_text(encoding="utf-8"))
    protocol_schema = json.loads((purpose_root / "PROTOCOL_SCHEMA.json").read_text(encoding="utf-8"))
    assert contract_schema["additionalProperties"] is False
    assert all(definition.get("additionalProperties") is False for definition in contract_schema["$defs"].values())
    object_defs = [value for value in protocol_schema["$defs"].values() if value.get("type") == "object"]
    assert object_defs
    assert all(definition.get("additionalProperties") is False for definition in object_defs)


def test_frozen_work_package_remains_exact(repo_root: Path) -> None:
    path = repo_root / "docs" / "remaster" / "work_packages" / "R1_WORK_PACKAGE.json"
    assert path.stat().st_size == 10_577
    assert hashlib.sha256(path.read_bytes()).hexdigest() == FROZEN_PACKAGE_SHA256
