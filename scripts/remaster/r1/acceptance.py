"""Deterministic R1 purpose-contract loader and external-adapter evaluator.

This module is deliberately standard-library only and never imports the
JARVIS Foundry product. Product behavior crosses the versioned JSON subprocess
boundary defined in ``docs/remaster/r1/PROTOCOL_SCHEMA.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


PROTOCOL_VERSION = "jarvis-purpose-protocol-v1"
REQUEST_SCHEMA = "jarvis-purpose-request-v1"
RESPONSE_SCHEMA = "jarvis-purpose-response-v1"
VERDICT_SCHEMA = "jarvis-purpose-verdict-v1"
PURPOSE_SCHEMA = "jarvis-purpose-spec-v1"
CONTRACT_SCHEMA = "jarvis-purpose-journey-v1"
CONTRACT_VERSION = "1.0.0"
JOURNEY_IDS = tuple(f"J{number:02d}" for number in range(1, 11))
ALLOWED_STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_TESTED", "WAITING")
STATUS_PRECEDENCE = ("FAIL", "BLOCKED", "NOT_TESTED", "WAITING", "PASS")
PROOF_SCOPES = ("HARNESS_SELF_TEST", "PRODUCT_LIVE")
FORBIDDEN_EFFECT_CLASSES = frozenset(
    {
        "purchasing",
        "payments",
        "trading",
        "banking",
        "cryptocurrency_transfer",
        "financial_account_change",
        "money_movement",
    }
)
MAX_RESPONSE_BYTES = 2_097_152
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_REQUEST_ID = "r1-purpose-canonical-request-v1"
DEFAULT_CHALLENGE_NONCE = "r1-purpose-evaluator-challenge-v2"
DEFAULT_EVALUATOR_AUTHORITY_ID = "r1-purpose-evaluator-authority-v2"
OBSERVER_REQUEST_SCHEMA = "jarvis-purpose-observer-request-v1"
OBSERVER_RESPONSE_SCHEMA = "jarvis-purpose-observer-response-v1"
OBSERVER_REGISTRY_SCHEMA = "jarvis-purpose-observer-registry-v1"
STATUS_REASON_CODES = {
    "BLOCKED": frozenset(
        {
            "EXTERNAL_DEPENDENCY_UNAVAILABLE",
            "MISSING_CREDENTIAL",
            "REQUIRED_PHYSICAL_ACT",
            "CONTRADICTORY_OWNER_DIRECTIVES",
        }
    ),
    "NOT_TESTED": frozenset(
        {"CRITERIA_NOT_EXECUTED", "FRESHNESS_EXPIRED", "CAPABILITY_NOT_EXERCISED"}
    ),
    "WAITING": frozenset(
        {"DURABLE_EVENT_PENDING", "STOP_OR_VETO_ACTIVE", "BOUNDED_BACKOFF"}
    ),
    "FAIL": frozenset(
        {"CRITERION_FAILED", "IMPLEMENTATION_DEFECT", "PROTOCOL_FAILURE", "CONTROL_VIOLATION"}
    ),
}
_PLACEHOLDER = re.compile(r"\b(?:todo|tbd|placeholder|lorem ipsum|fill[- ]?me)\b", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ValidationError(ValueError):
    """A deterministic fail-closed schema or semantic validation error."""

    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(f"{code} at {path}: {message}")
        self.code = code
        self.path = path
        self.message = message


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("DUPLICATE_JSON_KEY", "$", f"duplicate key {key!r}")
        result[key] = value
    return result


def strict_json_loads(text: str) -> Any:
    """Parse JSON while rejecting duplicate object keys."""

    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except ValidationError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValidationError("MALFORMED_JSON", "$", str(exc)) from exc


def load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("JSON_READ_FAILED", str(path), str(exc)) from exc
    return strict_json_loads(text)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def formatted_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(131_072), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("TYPE_MISMATCH", path, "expected object")
    return value


def _require_list(value: Any, path: str, minimum: int = 0) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError("TYPE_MISMATCH", path, "expected array")
    if len(value) < minimum:
        raise ValidationError("ARRAY_TOO_SHORT", path, f"requires at least {minimum} items")
    return value


def _strict_keys(value: dict[str, Any], required: Iterable[str], path: str) -> None:
    expected = set(required)
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ValidationError("MISSING_FIELD", path, f"missing {missing}")
    if unknown:
        raise ValidationError("UNKNOWN_FIELD", path, f"unknown {unknown}")


def _nonempty_string(value: Any, path: str, minimum: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise ValidationError("EMPTY_OR_INVALID_STRING", path, f"requires at least {minimum} characters")
    if _PLACEHOLDER.search(value):
        raise ValidationError("PLACEHOLDER_CONTENT", path, "placeholder language is forbidden")
    return value


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise ValidationError("TYPE_MISMATCH", path, "expected boolean")
    return value


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValidationError("TYPE_MISMATCH", path, f"expected integer >= {minimum}")
    return value


def _number(value: Any, path: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ValidationError("TYPE_MISMATCH", path, f"expected number >= {minimum}")
    return float(value)


def _unique_strings(value: Any, path: str, minimum: int = 1) -> list[str]:
    items = _require_list(value, path, minimum)
    checked = [_nonempty_string(item, f"{path}[{index}]") for index, item in enumerate(items)]
    if len(set(checked)) != len(checked):
        raise ValidationError("DUPLICATE_VALUE", path, "array values must be unique")
    return checked


def parse_utc_timestamp(value: Any, path: str) -> datetime:
    text = _nonempty_string(value, path)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("INVALID_TIMESTAMP", path, str(exc)) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("INVALID_TIMESTAMP", path, "timezone offset is required")
    return parsed.astimezone(timezone.utc)


def _validate_named_records(
    value: Any,
    path: str,
    minimum: int,
    keys: Sequence[str],
    validator: Callable[[dict[str, Any], str], None],
) -> list[dict[str, Any]]:
    items = _require_list(value, path, minimum)
    records: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        item_path = f"{path}[{index}]"
        record = _require_dict(raw, item_path)
        _strict_keys(record, keys, item_path)
        validator(record, item_path)
        records.append(record)
    return records


def validate_contract(contract: Any, expected_id: str) -> dict[str, Any]:
    path = f"contract[{expected_id}]"
    value = _require_dict(contract, path)
    root_keys = (
        "schema",
        "contract_version",
        "journey_id",
        "name",
        "purpose",
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
        "executable_requirements",
    )
    _strict_keys(value, root_keys, path)
    if value["schema"] != CONTRACT_SCHEMA or value["contract_version"] != CONTRACT_VERSION:
        raise ValidationError("CONTRACT_VERSION_MISMATCH", path, "unexpected contract schema or version")
    if value["journey_id"] != expected_id or expected_id not in JOURNEY_IDS:
        raise ValidationError("UNKNOWN_JOURNEY_ID", f"{path}.journey_id", repr(value["journey_id"]))
    _nonempty_string(value["name"], f"{path}.name", 12)
    _nonempty_string(value["purpose"], f"{path}.purpose", 30)

    def input_validator(item: dict[str, Any], item_path: str) -> None:
        _nonempty_string(item["name"], f"{item_path}.name", 3)
        if item["type"] not in {"string", "integer", "number", "boolean", "object", "array"}:
            raise ValidationError("INVALID_INPUT_TYPE", f"{item_path}.type", repr(item["type"]))
        _bool(item["required"], f"{item_path}.required")
        example = item["example"]
        if example is None or example == "" or example == [] or example == {}:
            raise ValidationError("PLACEHOLDER_CONTENT", f"{item_path}.example", "example must be concrete")

    inputs = _validate_named_records(
        value["inputs"], f"{path}.inputs", 2, ("name", "type", "required", "example"), input_validator
    )
    if len({item["name"] for item in inputs}) != len(inputs):
        raise ValidationError("DUPLICATE_VALUE", f"{path}.inputs", "input names must be unique")

    def precondition_validator(item: dict[str, Any], item_path: str) -> None:
        _nonempty_string(item["id"], f"{item_path}.id", 5)
        _nonempty_string(item["statement"], f"{item_path}.statement", 20)
        _nonempty_string(item["check"], f"{item_path}.check", 8)

    preconditions = _validate_named_records(
        value["preconditions"],
        f"{path}.preconditions",
        2,
        ("id", "statement", "check"),
        precondition_validator,
    )
    if len({item["id"] for item in preconditions}) != len(preconditions):
        raise ValidationError("DUPLICATE_VALUE", f"{path}.preconditions", "precondition IDs must be unique")

    def target_validator(item: dict[str, Any], item_path: str) -> None:
        for key, minimum in (("target_id", 4), ("kind", 4), ("identity", 8), ("authorization_scope", 12)):
            _nonempty_string(item[key], f"{item_path}.{key}", minimum)

    targets = _validate_named_records(
        value["canonical_targets"],
        f"{path}.canonical_targets",
        1,
        ("target_id", "kind", "identity", "authorization_scope"),
        target_validator,
    )
    target_ids = [item["target_id"] for item in targets]
    if len(set(target_ids)) != len(target_ids):
        raise ValidationError("DUPLICATE_VALUE", f"{path}.canonical_targets", "target IDs must be unique")

    budget = _require_dict(value["budget"], f"{path}.budget")
    budget_keys = ("max_elapsed_ms", "max_calls", "max_tokens", "max_effects", "max_cost_usd")
    _strict_keys(budget, budget_keys, f"{path}.budget")
    elapsed = _integer(budget["max_elapsed_ms"], f"{path}.budget.max_elapsed_ms", 1)
    if elapsed > DEFAULT_TIMEOUT_SECONDS * 1000:
        raise ValidationError("BUDGET_EXCEEDS_PROTOCOL", f"{path}.budget.max_elapsed_ms", str(elapsed))
    for key in ("max_calls", "max_tokens", "max_effects"):
        _integer(budget[key], f"{path}.budget.{key}")
    _number(budget["max_cost_usd"], f"{path}.budget.max_cost_usd")

    def success_validator(item: dict[str, Any], item_path: str) -> None:
        _nonempty_string(item["id"], f"{item_path}.id", 5)
        _nonempty_string(item["statement"], f"{item_path}.statement", 20)

    criteria = _validate_named_records(
        value["success_criteria"],
        f"{path}.success_criteria",
        3,
        ("id", "statement"),
        success_validator,
    )
    criterion_ids = [item["id"] for item in criteria]
    if len(set(criterion_ids)) != len(criterion_ids):
        raise ValidationError("DUPLICATE_VALUE", f"{path}.success_criteria", "criterion IDs must be unique")

    def evidence_validator(item: dict[str, Any], item_path: str) -> None:
        _nonempty_string(item["id"], f"{item_path}.id", 5)
        _nonempty_string(item["kind"], f"{item_path}.kind", 4)
        if item["phase"] not in {
            "INPUT", "SOURCE", "BEFORE", "DECISION", "CONTROL", "AFTER", "RECOVERY",
            "ROLLBACK", "COMPENSATION", "VERIFICATION", "LEARNING",
        }:
            raise ValidationError("INVALID_EVIDENCE_PHASE", f"{item_path}.phase", repr(item["phase"]))
        _nonempty_string(item["proof_dimension"], f"{item_path}.proof_dimension", 5)
        linked = _unique_strings(item["criterion_ids"], f"{item_path}.criterion_ids")
        unknown = sorted(set(linked) - set(criterion_ids))
        if unknown:
            raise ValidationError("UNKNOWN_CRITERION_ID", f"{item_path}.criterion_ids", repr(unknown))
        _integer(item["min_count"], f"{item_path}.min_count", 1)

    evidence_requirements = _validate_named_records(
        value["required_evidence"],
        f"{path}.required_evidence",
        5,
        ("id", "kind", "phase", "proof_dimension", "criterion_ids", "min_count"),
        evidence_validator,
    )
    evidence_ids = [item["id"] for item in evidence_requirements]
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValidationError("DUPLICATE_VALUE", f"{path}.required_evidence", "evidence IDs must be unique")
    covered_criteria = {criterion_id for item in evidence_requirements for criterion_id in item["criterion_ids"]}
    if covered_criteria != set(criterion_ids):
        raise ValidationError("CRITERION_WITHOUT_EVIDENCE", path, repr(sorted(set(criterion_ids) - covered_criteria)))

    def unacceptable_validator(item: dict[str, Any], item_path: str) -> None:
        _nonempty_string(item["id"], f"{item_path}.id", 5)
        _nonempty_string(item["statement"], f"{item_path}.statement", 20)
        code = _nonempty_string(item["fail_code"], f"{item_path}.fail_code", 4)
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            raise ValidationError("INVALID_FAIL_CODE", f"{item_path}.fail_code", code)

    _validate_named_records(
        value["unacceptable_outcomes"],
        f"{path}.unacceptable_outcomes",
        3,
        ("id", "statement", "fail_code"),
        unacceptable_validator,
    )

    def intervention_validator(item: dict[str, Any], item_path: str) -> None:
        _nonempty_string(item["trigger"], f"{item_path}.trigger", 12)
        _nonempty_string(item["response"], f"{item_path}.response", 12)
        if item["status"] not in {"FAIL", "BLOCKED", "NOT_TESTED", "WAITING"}:
            raise ValidationError("INVALID_STATUS", f"{item_path}.status", repr(item["status"]))

    _validate_named_records(
        value["intervention_rules"],
        f"{path}.intervention_rules",
        2,
        ("trigger", "response", "status"),
        intervention_validator,
    )

    def injection_validator(item: dict[str, Any], item_path: str) -> None:
        _nonempty_string(item["id"], f"{item_path}.id", 5)
        _nonempty_string(item["inject"], f"{item_path}.inject", 15)
        _nonempty_string(item["expected_response"], f"{item_path}.expected_response", 15)

    injections = _validate_named_records(
        value["failure_injections"],
        f"{path}.failure_injections",
        2,
        ("id", "inject", "expected_response"),
        injection_validator,
    )
    if len({item["id"] for item in injections}) != len(injections):
        raise ValidationError("DUPLICATE_VALUE", f"{path}.failure_injections", "injection IDs must be unique")

    recovery = _require_dict(value["recovery"], f"{path}.recovery")
    _strict_keys(recovery, ("requirements", "evidence_required", "max_elapsed_ms"), f"{path}.recovery")
    recovery_requirements = _unique_strings(recovery["requirements"], f"{path}.recovery.requirements", 2)
    if any(len(item) < 15 for item in recovery_requirements):
        raise ValidationError("INCOMPLETE_RECOVERY", f"{path}.recovery.requirements", "requirements are too short")
    recovery_evidence = _unique_strings(recovery["evidence_required"], f"{path}.recovery.evidence_required")
    if not set(recovery_evidence).issubset(evidence_ids):
        raise ValidationError("UNKNOWN_EVIDENCE_ID", f"{path}.recovery.evidence_required", repr(recovery_evidence))
    _integer(recovery["max_elapsed_ms"], f"{path}.recovery.max_elapsed_ms", 1)

    rollback = _require_dict(value["rollback"], f"{path}.rollback")
    _strict_keys(rollback, ("requirements", "evidence_required", "restoration_target"), f"{path}.rollback")
    rollback_requirements = _unique_strings(rollback["requirements"], f"{path}.rollback.requirements", 2)
    if any(len(item) < 15 for item in rollback_requirements):
        raise ValidationError("INCOMPLETE_ROLLBACK", f"{path}.rollback.requirements", "requirements are too short")
    rollback_evidence = _unique_strings(rollback["evidence_required"], f"{path}.rollback.evidence_required")
    if not set(rollback_evidence).issubset(evidence_ids):
        raise ValidationError("UNKNOWN_EVIDENCE_ID", f"{path}.rollback.evidence_required", repr(rollback_evidence))
    _nonempty_string(rollback["restoration_target"], f"{path}.rollback.restoration_target", 15)

    dimensions = _unique_strings(value["proof_dimensions"], f"{path}.proof_dimensions", 5)
    required_dimensions = {item["proof_dimension"] for item in evidence_requirements}
    if set(dimensions) != required_dimensions:
        raise ValidationError("PROOF_DIMENSION_MISMATCH", f"{path}.proof_dimensions", "must exactly match required evidence dimensions")

    requirements = _require_dict(value["executable_requirements"], f"{path}.executable_requirements")
    requirement_keys = (
        "actor_independence", "stop_veto", "provenance", "freshness", "uncertainty",
        "contradictions_preserved", "usefulness", "idempotency", "after_state",
        "compensation", "recovery", "rollback", "allowed_effect_classes",
    )
    _strict_keys(requirements, requirement_keys, f"{path}.executable_requirements")
    for key in requirement_keys[:-1]:
        _bool(requirements[key], f"{path}.executable_requirements.{key}")
    for key in (
        "actor_independence", "stop_veto", "provenance", "freshness", "uncertainty",
        "usefulness", "idempotency", "after_state", "recovery", "rollback",
    ):
        if requirements[key] is not True:
            raise ValidationError("MANDATORY_REQUIREMENT_DISABLED", f"{path}.executable_requirements.{key}", "must be true")
    allowed_effects = set(_unique_strings(requirements["allowed_effect_classes"], f"{path}.executable_requirements.allowed_effect_classes"))
    forbidden = sorted(allowed_effects & FORBIDDEN_EFFECT_CLASSES)
    if forbidden:
        raise ValidationError("FORBIDDEN_FINANCIAL_EFFECT", f"{path}.executable_requirements.allowed_effect_classes", repr(forbidden))
    return value


def load_purpose_bundle(purpose_root: Path) -> dict[str, Any]:
    root = purpose_root.resolve(strict=True)
    spec_path = root / "PURPOSE_SPEC.json"
    spec = _require_dict(load_json(spec_path), "purpose_spec")
    spec_keys = (
        "schema", "spec_version", "protocol_version", "title", "purpose", "operation_boundary",
        "status_vocabulary", "aggregate_precedence", "proof_scopes", "evidence_identity",
        "subprocess_protocol", "aggregate_rule", "journey_contracts", "reference_adapter_rule",
        "current_prototype_rule", "proof_authority",
    )
    _strict_keys(spec, spec_keys, "purpose_spec")
    if spec["schema"] != PURPOSE_SCHEMA or spec["spec_version"] != CONTRACT_VERSION:
        raise ValidationError("PURPOSE_VERSION_MISMATCH", "purpose_spec", "unexpected purpose schema or version")
    if spec["protocol_version"] != PROTOCOL_VERSION:
        raise ValidationError("PROTOCOL_VERSION_MISMATCH", "purpose_spec.protocol_version", repr(spec["protocol_version"]))
    _nonempty_string(spec["title"], "purpose_spec.title", 12)
    _nonempty_string(spec["purpose"], "purpose_spec.purpose", 30)
    boundary = _require_dict(spec["operation_boundary"], "purpose_spec.operation_boundary")
    _strict_keys(boundary, ("allowed_scope", "deterministically_denied_effect_classes", "owner_control"), "purpose_spec.operation_boundary")
    denied = set(_unique_strings(boundary["deterministically_denied_effect_classes"], "purpose_spec.operation_boundary.deterministically_denied_effect_classes", 7))
    if denied != FORBIDDEN_EFFECT_CLASSES:
        raise ValidationError("FINANCIAL_BOUNDARY_MISMATCH", "purpose_spec.operation_boundary", repr(sorted(denied)))
    if spec["status_vocabulary"] != list(ALLOWED_STATUSES):
        raise ValidationError("STATUS_VOCABULARY_MISMATCH", "purpose_spec.status_vocabulary", repr(spec["status_vocabulary"]))
    if spec["aggregate_precedence"] != list(STATUS_PRECEDENCE):
        raise ValidationError("STATUS_PRECEDENCE_MISMATCH", "purpose_spec.aggregate_precedence", repr(spec["aggregate_precedence"]))
    entries = _require_list(spec["journey_contracts"], "purpose_spec.journey_contracts", 10)
    if len(entries) != 10:
        raise ValidationError("JOURNEY_COUNT_MISMATCH", "purpose_spec.journey_contracts", str(len(entries)))
    contracts: list[dict[str, Any]] = []
    manifest: list[dict[str, str]] = []
    seen: list[str] = []
    for index, raw in enumerate(entries):
        entry_path = f"purpose_spec.journey_contracts[{index}]"
        entry = _require_dict(raw, entry_path)
        _strict_keys(entry, ("journey_id", "path"), entry_path)
        journey_id = _nonempty_string(entry["journey_id"], f"{entry_path}.journey_id")
        relative = Path(_nonempty_string(entry["path"], f"{entry_path}.path"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValidationError("CONTRACT_PATH_ESCAPE", f"{entry_path}.path", str(relative))
        contract_path = (root / relative).resolve(strict=True)
        if not contract_path.is_relative_to(root):
            raise ValidationError("CONTRACT_PATH_ESCAPE", f"{entry_path}.path", str(contract_path))
        contract = validate_contract(load_json(contract_path), journey_id)
        contracts.append(contract)
        manifest.append(
            {
                "journey_id": journey_id,
                "path": relative.as_posix(),
                "sha256": sha256_bytes(canonical_json_bytes(contract)),
            }
        )
        seen.append(journey_id)
    if tuple(seen) != JOURNEY_IDS:
        raise ValidationError("JOURNEY_SET_MISMATCH", "purpose_spec.journey_contracts", repr(seen))
    registry_path = root / "OBSERVER_REGISTRY.json"
    registry = _require_dict(load_json(registry_path), "observer_registry")
    _strict_keys(registry, ("schema", "registry_version", "registrations"), "observer_registry")
    if registry["schema"] != OBSERVER_REGISTRY_SCHEMA or registry["registry_version"] != CONTRACT_VERSION:
        raise ValidationError("OBSERVER_REGISTRY_VERSION_MISMATCH", "observer_registry", "unexpected registry schema or version")
    registrations = _require_list(registry["registrations"], "observer_registry.registrations")
    observer_ids: set[str] = set()
    for index, raw in enumerate(registrations):
        registration_path = f"observer_registry.registrations[{index}]"
        registration = _require_dict(raw, registration_path)
        _strict_keys(
            registration,
            ("observer_id", "artifact_path", "artifact_sha256", "command_prefix", "target_bindings"),
            registration_path,
        )
        observer_id = _nonempty_string(registration["observer_id"], f"{registration_path}.observer_id", 8)
        if observer_id in observer_ids:
            raise ValidationError("DUPLICATE_OBSERVER_ID", registration_path, observer_id)
        observer_ids.add(observer_id)
        artifact_relative = Path(_nonempty_string(registration["artifact_path"], f"{registration_path}.artifact_path", 3))
        if artifact_relative.is_absolute() or ".." in artifact_relative.parts:
            raise ValidationError("OBSERVER_ARTIFACT_PATH_ESCAPE", f"{registration_path}.artifact_path", str(artifact_relative))
        artifact_path = (root / artifact_relative).resolve(strict=True)
        if not artifact_path.is_relative_to(root):
            raise ValidationError("OBSERVER_ARTIFACT_PATH_ESCAPE", f"{registration_path}.artifact_path", str(artifact_path))
        if registration["artifact_sha256"] != sha256_file(artifact_path):
            raise ValidationError("OBSERVER_ARTIFACT_HASH_MISMATCH", f"{registration_path}.artifact_sha256", observer_id)
        command_prefix = _unique_strings(registration["command_prefix"], f"{registration_path}.command_prefix")
        allowed_placeholders = {"{observer_artifact}", "{python_executable}"}
        used_placeholders = {item for item in command_prefix if item.startswith("{") and item.endswith("}")}
        if not used_placeholders.issubset(allowed_placeholders) or command_prefix.count("{observer_artifact}") != 1:
            raise ValidationError(
                "INVALID_OBSERVER_COMMAND",
                f"{registration_path}.command_prefix",
                "exactly one {observer_artifact} and only documented placeholders are allowed",
            )
        bindings = _require_list(registration["target_bindings"], f"{registration_path}.target_bindings", 1)
        seen_targets: set[str] = set()
        for binding_index, binding_raw in enumerate(bindings):
            binding_path = f"{registration_path}.target_bindings[{binding_index}]"
            binding = _require_dict(binding_raw, binding_path)
            _strict_keys(binding, ("target_id", "canonical_identity", "account_or_service_identity"), binding_path)
            target_id = _nonempty_string(binding["target_id"], f"{binding_path}.target_id", 4)
            if target_id in seen_targets:
                raise ValidationError("DUPLICATE_OBSERVER_TARGET", binding_path, target_id)
            seen_targets.add(target_id)
            _nonempty_string(binding["canonical_identity"], f"{binding_path}.canonical_identity", 8)
            _nonempty_string(binding["account_or_service_identity"], f"{binding_path}.account_or_service_identity", 4)
    return {
        "root": root,
        "spec": spec,
        "spec_path": spec_path,
        "spec_sha256": sha256_file(spec_path),
        "contracts": contracts,
        "manifest": manifest,
        "observer_registry": registry,
        "observer_registry_path": registry_path,
        "observer_registry_sha256": sha256_file(registry_path),
    }


def build_request(
    purpose_bundle: dict[str, Any],
    run_root: Path,
    evaluation_time: str,
    request_id: str = DEFAULT_REQUEST_ID,
    requested_proof_scope: str = "HARNESS_SELF_TEST",
    challenge_nonce: str = DEFAULT_CHALLENGE_NONCE,
    evaluator_authority_id: str = DEFAULT_EVALUATOR_AUTHORITY_ID,
    observer_id: str | None = None,
) -> dict[str, Any]:
    parse_utc_timestamp(evaluation_time, "request.evaluation_time")
    _nonempty_string(request_id, "request.request_id", 8)
    if requested_proof_scope not in PROOF_SCOPES:
        raise ValidationError("INVALID_PROOF_SCOPE", "request.requested_proof_scope", repr(requested_proof_scope))
    _nonempty_string(challenge_nonce, "request.challenge_nonce", 16)
    _nonempty_string(evaluator_authority_id, "request.evaluator_authority_id", 8)
    if observer_id is not None:
        _nonempty_string(observer_id, "request.observer_id", 8)
        registered_ids = {
            item["observer_id"] for item in purpose_bundle["observer_registry"]["registrations"]
        }
        if observer_id not in registered_ids:
            raise ValidationError("OBSERVER_NOT_REGISTERED", "request.observer_id", observer_id)
    resolved_run_root = run_root.resolve()
    resolved_run_root.mkdir(parents=True, exist_ok=True)
    contracts = []
    for contract, manifest in zip(purpose_bundle["contracts"], purpose_bundle["manifest"], strict=True):
        contracts.append(
            {
                "journey_id": contract["journey_id"],
                "contract_sha256": manifest["sha256"],
                "contract": contract,
            }
        )
    request = {
        "schema": REQUEST_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "evaluation_time": evaluation_time,
        "run_root": str(resolved_run_root),
        "purpose_spec_sha256": purpose_bundle["spec_sha256"],
        "observer_registry_sha256": purpose_bundle["observer_registry_sha256"],
        "requested_proof_scope": requested_proof_scope,
        "challenge_nonce": challenge_nonce,
        "evaluator_authority_id": evaluator_authority_id,
        "observer_id": observer_id,
        "contracts": contracts,
    }
    validate_request(request)
    return request


def validate_request(request: Any) -> dict[str, Any]:
    value = _require_dict(request, "request")
    keys = (
        "schema", "protocol_version", "request_id", "evaluation_time", "run_root",
        "purpose_spec_sha256", "observer_registry_sha256", "requested_proof_scope",
        "challenge_nonce", "evaluator_authority_id", "observer_id", "contracts",
    )
    _strict_keys(value, keys, "request")
    if value["schema"] != REQUEST_SCHEMA or value["protocol_version"] != PROTOCOL_VERSION:
        raise ValidationError("PROTOCOL_VERSION_MISMATCH", "request", "unexpected request schema or protocol")
    _nonempty_string(value["request_id"], "request.request_id", 8)
    parse_utc_timestamp(value["evaluation_time"], "request.evaluation_time")
    _nonempty_string(value["run_root"], "request.run_root")
    if not isinstance(value["purpose_spec_sha256"], str) or not _SHA256.fullmatch(value["purpose_spec_sha256"]):
        raise ValidationError("INVALID_HASH", "request.purpose_spec_sha256", repr(value["purpose_spec_sha256"]))
    if not isinstance(value["observer_registry_sha256"], str) or not _SHA256.fullmatch(value["observer_registry_sha256"]):
        raise ValidationError("INVALID_HASH", "request.observer_registry_sha256", repr(value["observer_registry_sha256"]))
    if value["requested_proof_scope"] not in PROOF_SCOPES:
        raise ValidationError("INVALID_PROOF_SCOPE", "request.requested_proof_scope", repr(value["requested_proof_scope"]))
    _nonempty_string(value["challenge_nonce"], "request.challenge_nonce", 16)
    _nonempty_string(value["evaluator_authority_id"], "request.evaluator_authority_id", 8)
    if value["observer_id"] is not None:
        _nonempty_string(value["observer_id"], "request.observer_id", 8)
    entries = _require_list(value["contracts"], "request.contracts", 10)
    if len(entries) != 10:
        raise ValidationError("JOURNEY_COUNT_MISMATCH", "request.contracts", str(len(entries)))
    seen: list[str] = []
    for index, raw in enumerate(entries):
        path = f"request.contracts[{index}]"
        entry = _require_dict(raw, path)
        _strict_keys(entry, ("journey_id", "contract_sha256", "contract"), path)
        journey_id = entry["journey_id"]
        if journey_id not in JOURNEY_IDS:
            raise ValidationError("UNKNOWN_JOURNEY_ID", f"{path}.journey_id", repr(journey_id))
        expected_hash = sha256_bytes(canonical_json_bytes(entry["contract"]))
        if entry["contract_sha256"] != expected_hash:
            raise ValidationError("CONTRACT_HASH_MISMATCH", f"{path}.contract_sha256", "embedded contract hash differs")
        validate_contract(entry["contract"], journey_id)
        seen.append(journey_id)
    if tuple(seen) != JOURNEY_IDS:
        raise ValidationError("JOURNEY_SET_MISMATCH", "request.contracts", repr(seen))
    return value


def _finding(code: str, path: str, message: str, evidence_ids: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "code": code,
        "path": path,
        "message": message,
        "evidence_ids": sorted(set(evidence_ids)),
    }


def _all_failed_result(
    request: dict[str, Any],
    manifest: list[dict[str, str]],
    finding: dict[str, Any],
    process: dict[str, Any],
) -> dict[str, Any]:
    journeys = []
    for entry in request["contracts"]:
        contract = entry["contract"]
        criterion_evidence = {
            criterion["id"]: [f"finding:{finding['code']}"]
            for criterion in contract["success_criteria"]
        }
        journeys.append(
            {
                "journey_id": contract["journey_id"],
                "status": "FAIL",
                "criterion_evidence": criterion_evidence,
                "findings": [finding],
            }
        )
    requested_scope = request.get("requested_proof_scope", "NOT_ESTABLISHED")
    return {
        "schema": VERDICT_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request["request_id"],
        "subject": {
            "adapter_id": "UNIDENTIFIED_EXTERNAL_ADAPTER",
            "system_identity": "UNIDENTIFIED",
            "system_release": "UNIDENTIFIED",
        },
        "proof_scope": requested_scope,
        "qualification": "PROTOCOL_REJECTION",
        "product_claim_status": "NOT_TESTED",
        "proof_authority": {
            "scope_source": "EVALUATOR_REQUEST" if requested_scope in PROOF_SCOPES else "NOT_ESTABLISHED",
            "evaluator_authority_id": request.get("evaluator_authority_id", "NOT_ESTABLISHED"),
            "challenge_nonce_sha256": sha256_bytes(str(request.get("challenge_nonce", "")).encode("utf-8")),
            "observer_registry_sha256": request.get("observer_registry_sha256", "NOT_ESTABLISHED"),
            "observer_status": "NOT_EVALUATED",
            "actor_response_sha256": None,
            "actor_artifact_sha256": None,
            "actor_execution_identity": None,
            "observer_id": request.get("observer_id"),
            "observer_artifact_sha256": None,
            "observer_execution_identity": None,
        },
        "status": "FAIL",
        "journeys": journeys,
        "findings": [finding],
        "contract_manifest": manifest,
        "adapter_process": process,
        "run_confinement": {"temporary": False, "cleanup": "caller_owned"},
    }


def _strict_keys_finding(value: Any, expected: Iterable[str], path: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return [_finding("TYPE_MISMATCH", path, "expected object")]
    wanted = set(expected)
    actual = set(value)
    findings = []
    if wanted - actual:
        findings.append(_finding("MISSING_FIELD", path, f"missing {sorted(wanted - actual)}"))
    if actual - wanted:
        findings.append(_finding("UNKNOWN_FIELD", path, f"unknown {sorted(actual - wanted)}"))
    return findings


def _validate_evidence(
    evidence: Any,
    index: int,
    execution_path: str,
    contract: dict[str, Any],
    adapter: dict[str, Any],
    requested_scope: str,
    run_root: Path,
    evaluation_time: datetime,
    artifact_cache: dict[Path, tuple[int, str]],
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
    path = f"{execution_path}.evidence[{index}]"
    findings = _strict_keys_finding(evidence, ("evidence_id", "payload_sha256", "payload"), path)
    if findings or not isinstance(evidence, dict):
        return findings, None, None
    evidence_id = evidence.get("evidence_id")
    payload_hash = evidence.get("payload_sha256")
    payload = evidence.get("payload")
    if not isinstance(payload, dict):
        findings.append(_finding("TYPE_MISMATCH", f"{path}.payload", "expected object"))
        return findings, evidence_id if isinstance(evidence_id, str) else None, None
    actual_payload_hash = sha256_bytes(canonical_json_bytes(payload))
    if payload_hash != actual_payload_hash or evidence_id != f"sha256:{actual_payload_hash}":
        findings.append(_finding("EVIDENCE_HASH_MISMATCH", path, "evidence ID or payload hash does not match canonical payload"))
    payload_keys = (
        "requirement_ids", "criterion_ids", "kind", "phase", "proof_dimension", "source",
        "captured_at", "target_id", "before_state", "after_state", "real_or_fixture",
        "proof_scope", "producer", "freshness", "uncertainty", "contradictions", "content",
    )
    findings.extend(_strict_keys_finding(payload, payload_keys, f"{path}.payload"))
    if findings and set(payload) != set(payload_keys):
        return findings, evidence_id if isinstance(evidence_id, str) else None, payload
    requirement_map = {item["id"]: item for item in contract["required_evidence"]}
    criterion_ids = {item["id"] for item in contract["success_criteria"]}
    requirement_ids = payload.get("requirement_ids")
    linked_criteria = payload.get("criterion_ids")
    if not isinstance(requirement_ids, list) or not requirement_ids or any(not isinstance(item, str) for item in requirement_ids):
        findings.append(_finding("INVALID_REQUIREMENT_LINK", f"{path}.payload.requirement_ids", "nonempty string array required"))
        requirement_ids = []
    if len(set(requirement_ids)) != len(requirement_ids) or not set(requirement_ids).issubset(requirement_map):
        findings.append(_finding("UNKNOWN_EVIDENCE_REQUIREMENT", f"{path}.payload.requirement_ids", repr(requirement_ids)))
    if not isinstance(linked_criteria, list) or not linked_criteria or any(not isinstance(item, str) for item in linked_criteria):
        findings.append(_finding("INVALID_CRITERION_LINK", f"{path}.payload.criterion_ids", "nonempty string array required"))
        linked_criteria = []
    if len(set(linked_criteria)) != len(linked_criteria) or not set(linked_criteria).issubset(criterion_ids):
        findings.append(_finding("UNKNOWN_CRITERION_ID", f"{path}.payload.criterion_ids", repr(linked_criteria)))
    for requirement_id in requirement_ids:
        requirement = requirement_map.get(requirement_id)
        if requirement is None:
            continue
        for key in ("kind", "phase", "proof_dimension"):
            if payload.get(key) != requirement[key]:
                findings.append(_finding("EVIDENCE_REQUIREMENT_MISMATCH", f"{path}.payload.{key}", f"{payload.get(key)!r} != {requirement[key]!r}"))
        if not set(requirement["criterion_ids"]).issubset(linked_criteria):
            findings.append(_finding("CRITERION_LINK_MISSING", f"{path}.payload.criterion_ids", requirement_id))
    if payload.get("target_id") not in {item["target_id"] for item in contract["canonical_targets"]}:
        findings.append(_finding("CANONICAL_TARGET_SUBSTITUTED", f"{path}.payload.target_id", repr(payload.get("target_id"))))
    if payload.get("proof_scope") != requested_scope:
        findings.append(_finding("PROOF_SCOPE_MISMATCH", f"{path}.payload.proof_scope", repr(payload.get("proof_scope"))))
    real_or_fixture = payload.get("real_or_fixture")
    if requested_scope == "HARNESS_SELF_TEST" and real_or_fixture != "REAL_TEMPORARY_EFFECT":
        findings.append(_finding("FIXTURE_AS_LIVE", f"{path}.payload.real_or_fixture", "harness PASS requires actual temporary effects"))
    if requested_scope == "PRODUCT_LIVE" and real_or_fixture != "REAL_EXTERNAL_EFFECT":
        findings.append(_finding("FIXTURE_AS_LIVE", f"{path}.payload.real_or_fixture", "product-live proof requires real external after-state"))
    try:
        captured = parse_utc_timestamp(payload.get("captured_at"), f"{path}.payload.captured_at")
        freshness = _require_dict(payload.get("freshness"), f"{path}.payload.freshness")
        _strict_keys(freshness, ("observed_at", "valid_until"), f"{path}.payload.freshness")
        observed = parse_utc_timestamp(freshness["observed_at"], f"{path}.payload.freshness.observed_at")
        valid_until = parse_utc_timestamp(freshness["valid_until"], f"{path}.payload.freshness.valid_until")
        if captured > evaluation_time or observed > evaluation_time or valid_until < evaluation_time or valid_until < observed:
            findings.append(_finding("STALE_OR_UNIDENTIFIABLE_SOURCE", f"{path}.payload.freshness", "evidence is future-dated, expired, or temporally inconsistent"))
    except ValidationError as exc:
        findings.append(_finding("STALE_OR_UNIDENTIFIABLE_SOURCE", exc.path, exc.message))
    source_findings = _strict_keys_finding(payload.get("source"), ("source_id", "source_type", "locator", "capture_method"), f"{path}.payload.source")
    findings.extend(source_findings)
    if isinstance(payload.get("source"), dict):
        for key in ("source_id", "source_type", "locator", "capture_method"):
            if not isinstance(payload["source"].get(key), str) or len(payload["source"].get(key, "").strip()) < 3:
                findings.append(_finding("STALE_OR_UNIDENTIFIABLE_SOURCE", f"{path}.payload.source.{key}", "source field is missing or too short"))
        if requested_scope == "PRODUCT_LIVE":
            locator = payload["source"].get("locator", "")
            if not isinstance(locator, str) or "://" not in locator or locator.lower().startswith("file://"):
                findings.append(
                    _finding(
                        "UNTRUSTED_LIVE_SOURCE",
                        f"{path}.payload.source.locator",
                        "PRODUCT_LIVE source identity requires an independently observed non-file system reference",
                    )
                )
    producer = payload.get("producer")
    findings.extend(_strict_keys_finding(producer, ("actor_id", "run_id", "role"), f"{path}.payload.producer"))
    if isinstance(producer, dict):
        expected_role = "INDEPENDENT_VERIFIER" if payload.get("phase") == "VERIFICATION" else "ACTOR"
        expected_run = adapter.get("verifier_run_id") if expected_role == "INDEPENDENT_VERIFIER" else adapter.get("actor_run_id")
        if producer.get("role") != expected_role or producer.get("run_id") != expected_run:
            findings.append(_finding("ACTOR_IDENTITY_MISMATCH", f"{path}.payload.producer", "producer role or run identity is not bound to adapter identity"))
        if not isinstance(producer.get("actor_id"), str) or len(producer.get("actor_id", "")) < 4:
            findings.append(_finding("ACTOR_IDENTITY_MISMATCH", f"{path}.payload.producer.actor_id", "actor identity missing"))
    uncertainty = payload.get("uncertainty")
    findings.extend(_strict_keys_finding(uncertainty, ("level", "statement"), f"{path}.payload.uncertainty"))
    if isinstance(uncertainty, dict):
        if uncertainty.get("level") not in {"NONE", "LOW", "MEDIUM", "HIGH"} or not isinstance(uncertainty.get("statement"), str) or len(uncertainty.get("statement", "")) < 8:
            findings.append(_finding("UNCERTAINTY_MISSING", f"{path}.payload.uncertainty", "valid level and explanation required"))
    contradictions = payload.get("contradictions")
    if not isinstance(contradictions, list) or any(not isinstance(item, str) or not item for item in contradictions):
        findings.append(_finding("CONTRADICTION_FIELD_INVALID", f"{path}.payload.contradictions", "string array required"))
    if len(set(contradictions or [])) != len(contradictions or []):
        findings.append(_finding("CONTRADICTION_FIELD_INVALID", f"{path}.payload.contradictions", "duplicates forbidden"))
    if payload.get("phase") == "BEFORE" and not isinstance(payload.get("before_state"), dict):
        findings.append(_finding("BEFORE_STATE_MISSING", f"{path}.payload.before_state", "BEFORE evidence requires object state"))
    if payload.get("phase") in {"AFTER", "RECOVERY", "ROLLBACK", "COMPENSATION", "VERIFICATION", "LEARNING"} and not isinstance(payload.get("after_state"), dict):
        findings.append(_finding("ABSENT_AFTER_STATE", f"{path}.payload.after_state", "phase requires object after-state"))
    content = payload.get("content")
    findings.extend(_strict_keys_finding(content, ("relative_path", "sha256", "size", "media_type", "summary"), f"{path}.payload.content"))
    if isinstance(content, dict):
        relative_text = content.get("relative_path")
        if not isinstance(relative_text, str):
            findings.append(_finding("ARTIFACT_PATH_INVALID", f"{path}.payload.content.relative_path", "string required"))
        else:
            relative = Path(relative_text)
            if relative.is_absolute() or ".." in relative.parts:
                findings.append(_finding("ARTIFACT_PATH_ESCAPE", f"{path}.payload.content.relative_path", relative_text))
            else:
                artifact_path = (run_root / relative).resolve()
                if not artifact_path.is_relative_to(run_root):
                    findings.append(_finding("ARTIFACT_PATH_ESCAPE", f"{path}.payload.content.relative_path", relative_text))
                elif not artifact_path.is_file():
                    findings.append(_finding("EVIDENCE_ARTIFACT_MISSING", f"{path}.payload.content.relative_path", relative_text))
                else:
                    if artifact_path not in artifact_cache:
                        artifact_cache[artifact_path] = (
                            artifact_path.stat().st_size,
                            sha256_file(artifact_path),
                        )
                    actual_size, actual_hash = artifact_cache[artifact_path]
                    if content.get("size") != actual_size or content.get("sha256") != actual_hash:
                        findings.append(_finding("EVIDENCE_ARTIFACT_TAMPER", f"{path}.payload.content", "file size or hash mismatch"))
        if not isinstance(content.get("summary"), str) or len(content.get("summary", "")) < 8:
            findings.append(_finding("EVIDENCE_SUMMARY_MISSING", f"{path}.payload.content.summary", "nonempty summary required"))
    return findings, evidence_id if isinstance(evidence_id, str) else None, payload


def _validate_status_detail(
    status: Any,
    status_detail: Any,
    contract: dict[str, Any],
    criterion_evidence: dict[str, list[str]],
    evidence_by_id: dict[str, dict[str, Any]],
    evaluation_time: datetime,
    path: str,
) -> list[dict[str, Any]]:
    """Validate non-PASS provenance without treating a status word as proof."""

    if status == "PASS":
        if status_detail is not None:
            return [
                _finding(
                    "PASS_STATUS_DETAIL_FORBIDDEN",
                    f"{path}.status_detail",
                    "PASS cannot carry a non-PASS status detail",
                )
            ]
        return []
    if status not in STATUS_REASON_CODES:
        return []
    if status_detail is None:
        return [
            _finding(
                "STATUS_DETAIL_REQUIRED",
                f"{path}.status_detail",
                f"{status} requires status-specific reason and evidence",
            )
        ]
    common_keys = ("status", "reason_code", "explanation", "criterion_ids", "evidence_ids", "detail")
    findings = _strict_keys_finding(status_detail, common_keys, f"{path}.status_detail")
    if not isinstance(status_detail, dict):
        return findings
    if status_detail.get("status") != status:
        findings.append(
            _finding(
                "STATUS_DETAIL_MISMATCH",
                f"{path}.status_detail.status",
                f"{status_detail.get('status')!r} != {status!r}",
            )
        )
    if status_detail.get("reason_code") not in STATUS_REASON_CODES[status]:
        findings.append(
            _finding(
                "INVALID_STATUS_REASON",
                f"{path}.status_detail.reason_code",
                repr(status_detail.get("reason_code")),
            )
        )
    explanation = status_detail.get("explanation")
    if not isinstance(explanation, str) or len(explanation.strip()) < 20:
        findings.append(
            _finding(
                "STATUS_EXPLANATION_MISSING",
                f"{path}.status_detail.explanation",
                "at least 20 non-whitespace characters are required",
            )
        )
    valid_criterion_ids = {item["id"] for item in contract["success_criteria"]}
    criterion_ids = status_detail.get("criterion_ids")
    if (
        not isinstance(criterion_ids, list)
        or not criterion_ids
        or any(not isinstance(item, str) for item in criterion_ids)
        or len(set(criterion_ids)) != len(criterion_ids)
        or not set(criterion_ids).issubset(valid_criterion_ids)
    ):
        findings.append(
            _finding(
                "INVALID_STATUS_CRITERIA",
                f"{path}.status_detail.criterion_ids",
                repr(criterion_ids),
            )
        )
        criterion_ids = []
    evidence_ids = status_detail.get("evidence_ids")
    if (
        not isinstance(evidence_ids, list)
        or not evidence_ids
        or any(not isinstance(item, str) for item in evidence_ids)
        or len(set(evidence_ids)) != len(evidence_ids)
    ):
        findings.append(
            _finding(
                "INVALID_STATUS_EVIDENCE",
                f"{path}.status_detail.evidence_ids",
                "a nonempty unique evidence-ID array is required",
            )
        )
        evidence_ids = []
    dangling = sorted(item for item in evidence_ids if item not in evidence_by_id)
    if dangling:
        findings.append(
            _finding(
                "DANGLING_STATUS_EVIDENCE",
                f"{path}.status_detail.evidence_ids",
                repr(dangling),
                dangling,
            )
        )
    for criterion_id in criterion_ids:
        if not set(evidence_ids).intersection(criterion_evidence.get(criterion_id, [])):
            findings.append(
                _finding(
                    "STATUS_EVIDENCE_NOT_CRITERION_BOUND",
                    f"{path}.status_detail.evidence_ids",
                    criterion_id,
                    evidence_ids,
                )
            )

    detail = status_detail.get("detail")
    detail_path = f"{path}.status_detail.detail"
    observation_predicate: Callable[[dict[str, Any]], bool]
    if status == "BLOCKED":
        detail_keys = (
            "dependency_id",
            "dependency_kind",
            "unavailable_observation_evidence_id",
            "retry_policy",
            "unblock_condition",
        )
        findings.extend(_strict_keys_finding(detail, detail_keys, detail_path))
        if not isinstance(detail, dict):
            return findings
        for key in ("dependency_id", "dependency_kind", "retry_policy", "unblock_condition"):
            minimum = 4 if key in {"dependency_id", "dependency_kind"} else 12
            if not isinstance(detail.get(key), str) or len(detail.get(key, "").strip()) < minimum:
                findings.append(_finding("INVALID_BLOCKED_DETAIL", f"{detail_path}.{key}", "specific nonempty value required"))
        unavailable_id = detail.get("unavailable_observation_evidence_id")
        if unavailable_id not in evidence_ids or unavailable_id not in evidence_by_id:
            findings.append(
                _finding(
                    "DANGLING_STATUS_EVIDENCE",
                    f"{detail_path}.unavailable_observation_evidence_id",
                    repr(unavailable_id),
                    [unavailable_id] if isinstance(unavailable_id, str) else [],
                )
            )
        observation_predicate = lambda observation: (
            observation.get("status") == "BLOCKED"
            and observation.get("dependency_id") == detail.get("dependency_id")
            and observation.get("availability") == "UNAVAILABLE"
        )
        observation_ids = [unavailable_id] if isinstance(unavailable_id, str) else []
    elif status == "WAITING":
        detail_keys = ("wait_event_id", "event_source", "resume_condition", "resumption_mode", "next_check_at")
        findings.extend(_strict_keys_finding(detail, detail_keys, detail_path))
        if not isinstance(detail, dict):
            return findings
        for key in ("wait_event_id", "event_source", "resume_condition"):
            minimum = 4 if key != "resume_condition" else 12
            if not isinstance(detail.get(key), str) or len(detail.get(key, "").strip()) < minimum:
                findings.append(_finding("INVALID_WAITING_DETAIL", f"{detail_path}.{key}", "specific nonempty value required"))
        mode = detail.get("resumption_mode")
        if mode not in {"EVENT_DRIVEN", "BOUNDED_POLL"}:
            findings.append(_finding("INVALID_WAITING_DETAIL", f"{detail_path}.resumption_mode", repr(mode)))
        next_check = detail.get("next_check_at")
        if mode == "EVENT_DRIVEN" and next_check is not None:
            findings.append(_finding("INVALID_WAITING_DETAIL", f"{detail_path}.next_check_at", "event-driven waits require null"))
        if mode == "BOUNDED_POLL":
            try:
                parsed_next_check = parse_utc_timestamp(next_check, f"{detail_path}.next_check_at")
                if parsed_next_check <= evaluation_time:
                    findings.append(_finding("INVALID_WAITING_DETAIL", f"{detail_path}.next_check_at", "must be after evaluation time"))
            except ValidationError as exc:
                findings.append(_finding("INVALID_WAITING_DETAIL", exc.path, exc.message))
        observation_predicate = lambda observation: (
            observation.get("status") == "WAITING"
            and observation.get("wait_event_id") == detail.get("wait_event_id")
            and observation.get("event_state") == "PENDING"
            and observation.get("resume_condition") == detail.get("resume_condition")
        )
        observation_ids = list(evidence_ids)
    elif status == "NOT_TESTED":
        detail_keys = ("unexecuted_criterion_ids", "why_not_exercised")
        findings.extend(_strict_keys_finding(detail, detail_keys, detail_path))
        if not isinstance(detail, dict):
            return findings
        unexecuted = detail.get("unexecuted_criterion_ids")
        if unexecuted != criterion_ids:
            findings.append(
                _finding(
                    "INVALID_NOT_TESTED_DETAIL",
                    f"{detail_path}.unexecuted_criterion_ids",
                    "must exactly match status_detail.criterion_ids",
                )
            )
        if not isinstance(detail.get("why_not_exercised"), str) or len(detail.get("why_not_exercised", "").strip()) < 20:
            findings.append(_finding("INVALID_NOT_TESTED_DETAIL", f"{detail_path}.why_not_exercised", "specific explanation required"))
        observation_predicate = lambda observation: (
            observation.get("status") == "NOT_TESTED"
            and observation.get("criterion_execution") == "NOT_EXECUTED"
            and observation.get("unexecuted_criterion_ids") == criterion_ids
        )
        observation_ids = list(evidence_ids)
    else:
        detail_keys = ("failed_criterion_ids", "failure_kind", "failure_observation_evidence_id")
        findings.extend(_strict_keys_finding(detail, detail_keys, detail_path))
        if not isinstance(detail, dict):
            return findings
        if detail.get("failed_criterion_ids") != criterion_ids:
            findings.append(
                _finding(
                    "INVALID_FAIL_DETAIL",
                    f"{detail_path}.failed_criterion_ids",
                    "must exactly match status_detail.criterion_ids",
                )
            )
        if detail.get("failure_kind") not in {"CRITERION_FAILURE", "IMPLEMENTATION_FAILURE", "PROTOCOL_FAILURE", "CONTROL_FAILURE"}:
            findings.append(_finding("INVALID_FAIL_DETAIL", f"{detail_path}.failure_kind", repr(detail.get("failure_kind"))))
        failure_id = detail.get("failure_observation_evidence_id")
        if failure_id not in evidence_ids or failure_id not in evidence_by_id:
            findings.append(
                _finding(
                    "DANGLING_STATUS_EVIDENCE",
                    f"{detail_path}.failure_observation_evidence_id",
                    repr(failure_id),
                    [failure_id] if isinstance(failure_id, str) else [],
                )
            )
        observation_predicate = lambda observation: (
            observation.get("status") == "FAIL"
            and observation.get("observed_result") == "FAILED"
            and observation.get("failure_kind") == detail.get("failure_kind")
            and observation.get("failed_criterion_ids") == criterion_ids
        )
        observation_ids = [failure_id] if isinstance(failure_id, str) else []

    matched = False
    for evidence_id in observation_ids:
        payload = evidence_by_id.get(evidence_id)
        if not isinstance(payload, dict):
            continue
        after_state = payload.get("after_state")
        observation = after_state.get("status_observation") if isinstance(after_state, dict) else None
        if isinstance(observation, dict) and observation_predicate(observation):
            matched = True
            break
    if not matched:
        findings.append(
            _finding(
                "STATUS_OBSERVATION_MISMATCH",
                f"{path}.status_detail",
                f"no referenced content-addressed evidence proves {status}",
                observation_ids,
            )
        )
    return findings


def _live_proof_findings(path: str, observer_journey: dict[str, Any] | None) -> list[dict[str, Any]]:
    if observer_journey is not None:
        return []
    return [
        _finding("PRODUCT_LIVE_OBSERVER_REQUIRED", path, "no valid evaluator-controlled registered observer attestation exists"),
        _finding("UNTRUSTED_LIVE_SOURCE", f"{path}.evidence", "subject source claims lack independent observation"),
        _finding("UNTRUSTED_LIVE_TARGET_RECEIPT", f"{path}.target_receipts", "subject receipts lack independent target-bound observation"),
        _finding("UNTRUSTED_LIVE_ACTOR_IDENTITY", f"{path}.adapter", "subject actor labels do not establish evaluator-issued execution identity"),
        _finding("UNTRUSTED_LIVE_VERIFIER_IDENTITY", f"{path}.adapter", "subject verifier labels do not establish an independent observer execution"),
        _finding("UNTRUSTED_LIVE_OUTCOME_ASSERTION", f"{path}.outcome", "subject outcome fields lack independent semantic receipts"),
        _finding("UNTRUSTED_LIVE_FAILURE_INJECTION_ASSERTION", f"{path}.failure_injections_exercised", "listed injection IDs lack independent observation receipts"),
    ]


def _validate_execution(
    execution: Any,
    contract: dict[str, Any],
    adapter: dict[str, Any],
    requested_scope: str,
    observer_attestation: dict[str, Any] | None,
    run_root: Path,
    evaluation_time: datetime,
) -> tuple[str, dict[str, list[str]], list[dict[str, Any]]]:
    journey_id = contract["journey_id"]
    path = f"response.journeys[{journey_id}]"
    keys = (
        "journey_id", "status", "target_ids", "budget_usage", "stop_veto", "effect_classes",
        "target_receipts", "evidence", "criterion_evidence", "failure_injections_exercised", "outcome",
        "status_detail",
    )
    findings = _strict_keys_finding(execution, keys, path)
    criterion_ids = [item["id"] for item in contract["success_criteria"]]
    criterion_evidence: dict[str, list[str]] = {item: [] for item in criterion_ids}
    if not isinstance(execution, dict):
        for criterion_id in criterion_ids:
            criterion_evidence[criterion_id] = ["finding:TYPE_MISMATCH"]
        return "FAIL", criterion_evidence, findings
    if execution.get("journey_id") != journey_id:
        findings.append(_finding("UNKNOWN_JOURNEY_ID", f"{path}.journey_id", repr(execution.get("journey_id"))))
    status = execution.get("status")
    if status not in ALLOWED_STATUSES:
        findings.append(_finding("INVALID_STATUS", f"{path}.status", repr(status)))
    targets = execution.get("target_ids")
    expected_targets = [item["target_id"] for item in contract["canonical_targets"]]
    if targets != expected_targets:
        findings.append(_finding("CANONICAL_TARGET_SUBSTITUTED", f"{path}.target_ids", f"{targets!r} != {expected_targets!r}"))
    target_receipts = execution.get("target_receipts")
    if not isinstance(target_receipts, list) or len(target_receipts) != len(contract["canonical_targets"]):
        findings.append(_finding("UNTRUSTED_LIVE_TARGET_RECEIPT", f"{path}.target_receipts", "one strict receipt per canonical target is required"))
        target_receipts = []
    else:
        receipt_keys = (
            "target_id", "canonical_identity", "account_or_service_identity", "before_state_sha256",
            "after_state_sha256", "receipt_id", "idempotency_key",
        )
        for index, (receipt, target) in enumerate(zip(target_receipts, contract["canonical_targets"], strict=True)):
            receipt_path = f"{path}.target_receipts[{index}]"
            findings.extend(_strict_keys_finding(receipt, receipt_keys, receipt_path))
            if not isinstance(receipt, dict):
                continue
            if receipt.get("target_id") != target["target_id"] or receipt.get("canonical_identity") != target["identity"]:
                findings.append(_finding("CANONICAL_TARGET_SUBSTITUTED", receipt_path, "receipt is not bound to the frozen target identity"))
            for key in ("account_or_service_identity", "receipt_id", "idempotency_key"):
                if not isinstance(receipt.get(key), str) or len(receipt.get(key, "").strip()) < 4:
                    findings.append(_finding("UNTRUSTED_LIVE_TARGET_RECEIPT", f"{receipt_path}.{key}", "nonempty bound identity required"))
            for key in ("before_state_sha256", "after_state_sha256"):
                if not isinstance(receipt.get(key), str) or not _SHA256.fullmatch(receipt.get(key, "")):
                    findings.append(_finding("UNTRUSTED_LIVE_TARGET_RECEIPT", f"{receipt_path}.{key}", "canonical state hash required"))
    usage = execution.get("budget_usage")
    budget_keys = ("elapsed_ms", "calls", "tokens", "effects", "cost_usd")
    findings.extend(_strict_keys_finding(usage, budget_keys, f"{path}.budget_usage"))
    if isinstance(usage, dict):
        comparisons = (
            ("elapsed_ms", "max_elapsed_ms"), ("calls", "max_calls"), ("tokens", "max_tokens"),
            ("effects", "max_effects"), ("cost_usd", "max_cost_usd"),
        )
        for used_key, max_key in comparisons:
            used = usage.get(used_key)
            limit = contract["budget"][max_key]
            if isinstance(used, bool) or not isinstance(used, (int, float)) or used < 0 or used > limit:
                findings.append(_finding("BUDGET_OVERFLOW", f"{path}.budget_usage.{used_key}", f"{used!r} exceeds or violates {limit!r}"))
    stop_veto = execution.get("stop_veto")
    stop_keys = ("stop_checked", "veto_checked", "forged_content_ignored", "control_actor_id", "control_source")
    findings.extend(_strict_keys_finding(stop_veto, stop_keys, f"{path}.stop_veto"))
    if isinstance(stop_veto, dict) and contract["executable_requirements"]["stop_veto"]:
        if stop_veto.get("stop_checked") is not True or stop_veto.get("veto_checked") is not True:
            findings.append(_finding("STOP_VETO_NOT_ENFORCED", f"{path}.stop_veto", "STOP and veto checks must both be true"))
        if stop_veto.get("forged_content_ignored") is not True or stop_veto.get("control_source") != "AUTHENTICATED_OWNER_CONTROL":
            findings.append(_finding("FORGED_OWNER_CONTROL", f"{path}.stop_veto", "retrieved content cannot control owner directives"))
        if not isinstance(stop_veto.get("control_actor_id"), str) or len(stop_veto.get("control_actor_id", "")) < 4:
            findings.append(_finding("FORGED_OWNER_CONTROL", f"{path}.stop_veto.control_actor_id", "authenticated actor identity missing"))
    effects = execution.get("effect_classes")
    allowed_effects = set(contract["executable_requirements"]["allowed_effect_classes"])
    if not isinstance(effects, list) or not effects or any(not isinstance(item, str) for item in effects) or len(set(effects)) != len(effects):
        findings.append(_finding("INVALID_EFFECT_CLASSES", f"{path}.effect_classes", "nonempty unique string array required"))
        effects = []
    financial = sorted(set(effects) & FORBIDDEN_EFFECT_CLASSES)
    if financial:
        findings.append(_finding("FORBIDDEN_FINANCIAL_EFFECT", f"{path}.effect_classes", repr(financial)))
    disallowed = sorted(set(effects) - allowed_effects)
    if disallowed:
        findings.append(_finding("DISALLOWED_EFFECT_CLASS", f"{path}.effect_classes", repr(disallowed)))
    evidence_list = execution.get("evidence")
    if not isinstance(evidence_list, list) or not evidence_list:
        findings.append(_finding("MISSING_REQUIRED_EVIDENCE", f"{path}.evidence", "nonempty evidence array required"))
        evidence_list = []
    evidence_by_id: dict[str, dict[str, Any]] = {}
    requirement_counts: dict[str, int] = {item["id"]: 0 for item in contract["required_evidence"]}
    dimension_counts: dict[str, int] = {item: 0 for item in contract["proof_dimensions"]}
    target_counts: dict[str, int] = {item["target_id"]: 0 for item in contract["canonical_targets"]}
    contradiction_count = 0
    artifact_cache: dict[Path, tuple[int, str]] = {}
    for index, evidence in enumerate(evidence_list):
        evidence_findings, evidence_id, payload = _validate_evidence(
            evidence,
            index,
            path,
            contract,
            adapter,
            requested_scope,
            run_root,
            evaluation_time,
            artifact_cache,
        )
        findings.extend(evidence_findings)
        if evidence_id is not None:
            if evidence_id in evidence_by_id:
                findings.append(_finding("DUPLICATE_EVIDENCE_ID", f"{path}.evidence[{index}]", evidence_id))
            elif payload is not None:
                evidence_by_id[evidence_id] = payload
        if payload is not None:
            for requirement_id in payload.get("requirement_ids", []):
                if requirement_id in requirement_counts:
                    requirement_counts[requirement_id] += 1
            dimension = payload.get("proof_dimension")
            if dimension in dimension_counts:
                dimension_counts[dimension] += 1
            target_id = payload.get("target_id")
            if target_id in target_counts:
                target_counts[target_id] += 1
            contradictions = payload.get("contradictions")
            if isinstance(contradictions, list):
                contradiction_count += len(contradictions)
    for index, receipt in enumerate(target_receipts):
        if not isinstance(receipt, dict):
            continue
        receipt_path = f"{path}.target_receipts[{index}]"
        matching_payloads = [
            payload
            for payload in evidence_by_id.values()
            if payload.get("target_id") == receipt.get("target_id")
        ]
        before_hashes = {
            payload["before_state"].get("sha256")
            for payload in matching_payloads
            if isinstance(payload.get("before_state"), dict)
        }
        after_hashes = {
            payload["after_state"].get("sha256")
            for payload in matching_payloads
            if isinstance(payload.get("after_state"), dict)
        }
        if receipt.get("before_state_sha256") not in before_hashes:
            findings.append(_finding("TARGET_RECEIPT_STATE_MISMATCH", f"{receipt_path}.before_state_sha256", "not bound to evidence before-state"))
        if receipt.get("after_state_sha256") not in after_hashes:
            findings.append(_finding("TARGET_RECEIPT_STATE_MISMATCH", f"{receipt_path}.after_state_sha256", "not bound to evidence after-state"))
    for requirement in contract["required_evidence"]:
        if requirement_counts[requirement["id"]] < requirement["min_count"]:
            findings.append(_finding("MISSING_REQUIRED_EVIDENCE", f"{path}.evidence", requirement["id"]))
    for dimension, count in dimension_counts.items():
        if count == 0:
            findings.append(_finding("PROOF_DIMENSION_MISSING", f"{path}.evidence", dimension))
    for target_id, count in target_counts.items():
        if count == 0:
            findings.append(_finding("CANONICAL_TARGET_EVIDENCE_MISSING", f"{path}.evidence", target_id))
    mapping = execution.get("criterion_evidence")
    findings.extend(_strict_keys_finding(mapping, criterion_ids, f"{path}.criterion_evidence"))
    if isinstance(mapping, dict):
        for criterion_id in criterion_ids:
            linked = mapping.get(criterion_id)
            if not isinstance(linked, list) or not linked or any(not isinstance(item, str) for item in linked):
                findings.append(_finding("CRITERION_WITHOUT_EVIDENCE", f"{path}.criterion_evidence.{criterion_id}", "nonempty evidence ID array required"))
                continue
            if len(set(linked)) != len(linked):
                findings.append(_finding("DUPLICATE_VALUE", f"{path}.criterion_evidence.{criterion_id}", "evidence IDs must be unique"))
            criterion_evidence[criterion_id] = list(linked)
            for evidence_id in linked:
                payload = evidence_by_id.get(evidence_id)
                if payload is None or criterion_id not in payload.get("criterion_ids", []):
                    findings.append(_finding("CRITERION_EVIDENCE_INVALID", f"{path}.criterion_evidence.{criterion_id}", evidence_id, [evidence_id]))
    findings.extend(
        _validate_status_detail(
            status,
            execution.get("status_detail"),
            contract,
            criterion_evidence,
            evidence_by_id,
            evaluation_time,
            path,
        )
    )
    expected_injections = [item["id"] for item in contract["failure_injections"]]
    if execution.get("failure_injections_exercised") != expected_injections:
        findings.append(_finding("FAILURE_INJECTION_MISSING", f"{path}.failure_injections_exercised", f"expected {expected_injections!r}"))
    outcome = execution.get("outcome")
    outcome_keys = (
        "useful", "after_state_verified", "idempotent", "duplicate_effect_count",
        "contradictions_preserved", "recovery_verified", "rollback_verified",
        "compensation_verified", "result_summary",
    )
    findings.extend(_strict_keys_finding(outcome, outcome_keys, f"{path}.outcome"))
    requirements = contract["executable_requirements"]
    if isinstance(outcome, dict):
        boolean_requirements = (
            ("usefulness", "useful", "USEFULNESS_NOT_PROVEN"),
            ("after_state", "after_state_verified", "ABSENT_AFTER_STATE"),
            ("idempotency", "idempotent", "DUPLICATE_NON_IDEMPOTENT_EFFECT"),
            ("contradictions_preserved", "contradictions_preserved", "CONTRADICTION_ERASED"),
            ("recovery", "recovery_verified", "RECOVERY_FAILED"),
            ("rollback", "rollback_verified", "ROLLBACK_FAILED"),
            ("compensation", "compensation_verified", "COMPENSATION_FAILED"),
        )
        for _, outcome_key, _ in boolean_requirements:
            if not isinstance(outcome.get(outcome_key), bool):
                findings.append(_finding("TYPE_MISMATCH", f"{path}.outcome.{outcome_key}", "boolean required"))
        if status == "PASS":
            for requirement_key, outcome_key, code in boolean_requirements:
                if requirements[requirement_key] and outcome.get(outcome_key) is not True:
                    findings.append(_finding(code, f"{path}.outcome.{outcome_key}", "required executable outcome is not true"))
        duplicate_count = outcome.get("duplicate_effect_count")
        if type(duplicate_count) is not int or duplicate_count < 0:
            findings.append(_finding("TYPE_MISMATCH", f"{path}.outcome.duplicate_effect_count", "nonnegative integer required"))
        elif status == "PASS" and requirements["idempotency"] and duplicate_count != 0:
            findings.append(_finding("DUPLICATE_NON_IDEMPOTENT_EFFECT", f"{path}.outcome.duplicate_effect_count", repr(duplicate_count)))
        if status == "PASS" and requirements["contradictions_preserved"] and contradiction_count == 0:
            findings.append(_finding("CONTRADICTION_ERASED", f"{path}.evidence", "no contradicting evidence remains"))
        if not isinstance(outcome.get("result_summary"), str) or len(outcome.get("result_summary", "")) < 20:
            findings.append(_finding("OUTCOME_SUMMARY_MISSING", f"{path}.outcome.result_summary", "nonempty useful result summary required"))
        if status in STATUS_REASON_CODES:
            required_outcomes = [
                outcome.get(outcome_key)
                for requirement_key, outcome_key, _ in boolean_requirements
                if requirements[requirement_key]
            ]
            if required_outcomes and all(value is True for value in required_outcomes) and duplicate_count == 0:
                findings.append(
                    _finding(
                        "NONPASS_CONTRADICTS_SUCCESS_OUTCOME",
                        f"{path}.outcome",
                        f"{status} cannot retain an unchanged all-success outcome",
                    )
                )
    if adapter.get("actor_run_id") == adapter.get("verifier_run_id") and requirements["actor_independence"]:
        findings.append(_finding("SELF_VERIFICATION", path, "builder/actor and verifier run identities are identical"))
    if requested_scope == "PRODUCT_LIVE":
        findings.extend(_live_proof_findings(path, observer_attestation))
    if findings:
        finding_refs = [f"finding:{item['code']}" for item in findings]
        for criterion_id in criterion_ids:
            criterion_evidence[criterion_id] = sorted(set(criterion_evidence[criterion_id] + finding_refs))
        return "FAIL", criterion_evidence, findings
    return str(status), criterion_evidence, findings


def _aggregate(statuses: Iterable[str]) -> str:
    values = set(statuses)
    for status in STATUS_PRECEDENCE:
        if status in values:
            return status
    return "FAIL"


def _issued_execution_identity(
    authority_id: str,
    challenge_nonce: str,
    role: str,
    artifact_sha256: str,
    actor_response_sha256: str,
) -> str:
    payload = {
        "authority_id": authority_id,
        "challenge_nonce": challenge_nonce,
        "role": role,
        "artifact_sha256": artifact_sha256,
        "actor_response_sha256": actor_response_sha256,
    }
    return f"sha256:{sha256_bytes(canonical_json_bytes(payload))}"


def _source_identity_sha256(execution: dict[str, Any], target_id: str) -> str:
    sources = sorted(
        (
            record["payload"]["source"]
            for record in execution.get("evidence", [])
            if isinstance(record, dict)
            and isinstance(record.get("payload"), dict)
            and record["payload"].get("target_id") == target_id
            and isinstance(record["payload"].get("source"), dict)
        ),
        key=lambda item: canonical_json_bytes(item),
    )
    return sha256_bytes(canonical_json_bytes(sources))


def _validate_observer_context(
    request: dict[str, Any],
    response: dict[str, Any],
    proof_context: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return only observer journeys backed by evaluator-run registered code."""

    response_sha256 = sha256_bytes(canonical_json_bytes(response))
    empty_authority = {
        "scope_source": "EVALUATOR_REQUEST",
        "evaluator_authority_id": request["evaluator_authority_id"],
        "challenge_nonce_sha256": sha256_bytes(request["challenge_nonce"].encode("utf-8")),
        "observer_registry_sha256": request["observer_registry_sha256"],
        "observer_status": "NOT_REQUESTED" if request["requested_proof_scope"] == "HARNESS_SELF_TEST" else "ABSENT",
        "actor_response_sha256": response_sha256,
        "actor_artifact_sha256": None,
        "actor_execution_identity": None,
        "observer_id": request["observer_id"],
        "observer_artifact_sha256": None,
        "observer_execution_identity": None,
    }
    if proof_context is None:
        return {}, [], empty_authority
    context_keys = (
        "observer_registry_sha256",
        "actor_artifact_sha256",
        "actor_execution_identity",
        "observer_artifact_sha256",
        "observer_execution_identity",
        "registration",
        "attestation",
        "observer_process",
    )
    findings = _strict_keys_finding(proof_context, context_keys, "proof_context")
    if not isinstance(proof_context, dict):
        return {}, findings, empty_authority
    actor_artifact_sha256 = proof_context.get("actor_artifact_sha256")
    actor_execution_identity = proof_context.get("actor_execution_identity")
    authority = dict(empty_authority)
    authority.update(
        {
            "actor_artifact_sha256": actor_artifact_sha256,
            "actor_execution_identity": actor_execution_identity,
            "observer_artifact_sha256": proof_context.get("observer_artifact_sha256"),
            "observer_execution_identity": proof_context.get("observer_execution_identity"),
        }
    )
    if proof_context.get("observer_registry_sha256") != request["observer_registry_sha256"]:
        findings.append(_finding("OBSERVER_REGISTRY_MISMATCH", "proof_context.observer_registry_sha256", "context is not bound to the frozen request registry"))
    if not isinstance(actor_artifact_sha256, str) or not _SHA256.fullmatch(actor_artifact_sha256):
        findings.append(_finding("UNTRUSTED_LIVE_ACTOR_IDENTITY", "proof_context.actor_artifact_sha256", "evaluator-hashed actor command artifacts are required"))
    else:
        expected_actor_identity = _issued_execution_identity(
            request["evaluator_authority_id"],
            request["challenge_nonce"],
            "ACTOR",
            actor_artifact_sha256,
            response_sha256,
        )
        if actor_execution_identity != expected_actor_identity:
            findings.append(_finding("UNTRUSTED_LIVE_ACTOR_IDENTITY", "proof_context.actor_execution_identity", "identity was not issued from the frozen actor artifact and response"))
    if request["requested_proof_scope"] != "PRODUCT_LIVE":
        authority["observer_status"] = "NOT_REQUESTED"
        return {}, findings, authority
    observer_id = request.get("observer_id")
    if observer_id is None:
        authority["observer_status"] = "ABSENT"
        return {}, findings, authority
    registration = proof_context.get("registration")
    registration_findings = _strict_keys_finding(
        registration,
        ("observer_id", "artifact_path", "artifact_sha256", "command_prefix", "target_bindings"),
        "proof_context.registration",
    )
    findings.extend(registration_findings)
    if not isinstance(registration, dict) or registration.get("observer_id") != observer_id:
        findings.append(_finding("OBSERVER_NOT_REGISTERED", "proof_context.registration.observer_id", repr(observer_id)))
        authority["observer_status"] = "REJECTED"
        return {}, findings, authority
    observer_artifact_sha256 = proof_context.get("observer_artifact_sha256")
    if observer_artifact_sha256 != registration.get("artifact_sha256"):
        findings.append(_finding("OBSERVER_ARTIFACT_HASH_MISMATCH", "proof_context.observer_artifact_sha256", repr(observer_artifact_sha256)))
    expected_observer_identity = None
    if isinstance(observer_artifact_sha256, str) and _SHA256.fullmatch(observer_artifact_sha256):
        expected_observer_identity = _issued_execution_identity(
            request["evaluator_authority_id"],
            request["challenge_nonce"],
            "INDEPENDENT_OBSERVER",
            observer_artifact_sha256,
            response_sha256,
        )
    if proof_context.get("observer_execution_identity") != expected_observer_identity:
        findings.append(_finding("UNTRUSTED_LIVE_VERIFIER_IDENTITY", "proof_context.observer_execution_identity", "identity was not evaluator-issued for the registered observer artifact"))
    if expected_observer_identity == actor_execution_identity:
        findings.append(_finding("UNTRUSTED_LIVE_VERIFIER_IDENTITY", "proof_context", "actor and observer execution identities are not separate"))
    observer_process = proof_context.get("observer_process")
    if not isinstance(observer_process, dict) or observer_process.get("exit_code") != 0 or observer_process.get("timed_out") is not False:
        findings.append(_finding("PRODUCT_LIVE_OBSERVER_INVALID", "proof_context.observer_process", "registered observer did not complete successfully"))
    attestation = proof_context.get("attestation")
    attestation_keys = (
        "schema",
        "protocol_version",
        "request_id",
        "observer_id",
        "observer_artifact_sha256",
        "evaluator_authority_id",
        "challenge_nonce",
        "actor_artifact_sha256",
        "actor_response_sha256",
        "actor_execution_identity",
        "observer_execution_identity",
        "journeys",
    )
    findings.extend(_strict_keys_finding(attestation, attestation_keys, "observer_attestation"))
    if not isinstance(attestation, dict):
        authority["observer_status"] = "REJECTED"
        return {}, findings, authority
    expected_values = {
        "schema": OBSERVER_RESPONSE_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request["request_id"],
        "observer_id": observer_id,
        "observer_artifact_sha256": observer_artifact_sha256,
        "evaluator_authority_id": request["evaluator_authority_id"],
        "challenge_nonce": request["challenge_nonce"],
        "actor_artifact_sha256": actor_artifact_sha256,
        "actor_response_sha256": response_sha256,
        "actor_execution_identity": actor_execution_identity,
        "observer_execution_identity": expected_observer_identity,
    }
    for key, expected in expected_values.items():
        if attestation.get(key) != expected:
            findings.append(_finding("OBSERVER_ATTESTATION_BINDING_MISMATCH", f"observer_attestation.{key}", f"{attestation.get(key)!r} != {expected!r}"))
    execution_map = {
        item.get("journey_id"): item
        for item in response.get("journeys", [])
        if isinstance(item, dict) and item.get("journey_id") in JOURNEY_IDS
    }
    contract_map = {item["journey_id"]: item["contract"] for item in request["contracts"]}
    binding_map = {
        item.get("target_id"): item
        for item in registration.get("target_bindings", [])
        if isinstance(item, dict)
    }
    raw_journeys = attestation.get("journeys")
    if not isinstance(raw_journeys, list) or len(raw_journeys) != len(JOURNEY_IDS):
        findings.append(_finding("PRODUCT_LIVE_OBSERVER_INVALID", "observer_attestation.journeys", "exactly ten observed journeys are required"))
        raw_journeys = []
    observed_map: dict[str, dict[str, Any]] = {}
    for index, observed in enumerate(raw_journeys):
        observed_path = f"observer_attestation.journeys[{index}]"
        findings.extend(_strict_keys_finding(observed, ("journey_id", "target_observations", "semantic_observations"), observed_path))
        if not isinstance(observed, dict):
            continue
        journey_id = observed.get("journey_id")
        if journey_id not in JOURNEY_IDS or journey_id in observed_map:
            findings.append(_finding("PRODUCT_LIVE_OBSERVER_INVALID", f"{observed_path}.journey_id", repr(journey_id)))
            continue
        execution = execution_map.get(journey_id, {})
        contract = contract_map[journey_id]
        receipts = {
            item.get("target_id"): item
            for item in execution.get("target_receipts", [])
            if isinstance(item, dict)
        }
        target_observations = observed.get("target_observations")
        if not isinstance(target_observations, list) or len(target_observations) != len(contract["canonical_targets"]):
            findings.append(_finding("UNTRUSTED_LIVE_TARGET_RECEIPT", f"{observed_path}.target_observations", "one observation per canonical target is required"))
            target_observations = []
        for target_index, target in enumerate(contract["canonical_targets"]):
            target_path = f"{observed_path}.target_observations[{target_index}]"
            observation = target_observations[target_index] if target_index < len(target_observations) else None
            target_keys = (
                "target_id",
                "canonical_identity",
                "account_or_service_identity",
                "source_identity_sha256",
                "external_target_locator",
                "before_state_sha256",
                "after_state_sha256",
                "effect_or_provider_receipt_id",
                "idempotency_key",
            )
            findings.extend(_strict_keys_finding(observation, target_keys, target_path))
            if not isinstance(observation, dict):
                continue
            receipt = receipts.get(target["target_id"], {})
            binding = binding_map.get(target["target_id"], {})
            expected_target_values = {
                "target_id": target["target_id"],
                "canonical_identity": target["identity"],
                "account_or_service_identity": binding.get("account_or_service_identity"),
                "source_identity_sha256": _source_identity_sha256(execution, target["target_id"]),
                "before_state_sha256": receipt.get("before_state_sha256"),
                "after_state_sha256": receipt.get("after_state_sha256"),
                "effect_or_provider_receipt_id": receipt.get("receipt_id"),
                "idempotency_key": receipt.get("idempotency_key"),
            }
            if binding.get("canonical_identity") != target["identity"]:
                findings.append(_finding("UNTRUSTED_LIVE_TARGET_RECEIPT", target_path, "registry lacks the frozen target binding"))
            for key, expected in expected_target_values.items():
                if expected is None or observation.get(key) != expected:
                    findings.append(_finding("UNTRUSTED_LIVE_TARGET_RECEIPT", f"{target_path}.{key}", f"{observation.get(key)!r} != {expected!r}"))
            locator = observation.get("external_target_locator")
            if not isinstance(locator, str) or "://" not in locator or locator.lower().startswith("file://"):
                findings.append(_finding("UNTRUSTED_LIVE_TARGET_RECEIPT", f"{target_path}.external_target_locator", "non-file external target identity required"))
        semantic = observed.get("semantic_observations")
        semantic_keys = (
            "failure_injection_receipts",
            "restart_receipt_id",
            "duplicate_delivery_receipt_id",
            "recovery_receipt_id",
            "rollback_receipt_id",
            "compensation_receipt_id",
            "usefulness_receipt_id",
            "after_state_receipt_id",
            "stop_veto_receipt_id",
        )
        findings.extend(_strict_keys_finding(semantic, semantic_keys, f"{observed_path}.semantic_observations"))
        if isinstance(semantic, dict):
            failure_receipts = semantic.get("failure_injection_receipts")
            expected_injections = [item["id"] for item in contract["failure_injections"]]
            if not isinstance(failure_receipts, list) or [item.get("injection_id") for item in failure_receipts if isinstance(item, dict)] != expected_injections:
                findings.append(_finding("UNTRUSTED_LIVE_FAILURE_INJECTION_ASSERTION", f"{observed_path}.semantic_observations.failure_injection_receipts", repr(expected_injections)))
            else:
                for receipt_index, failure_receipt in enumerate(failure_receipts):
                    receipt_path = f"{observed_path}.semantic_observations.failure_injection_receipts[{receipt_index}]"
                    findings.extend(_strict_keys_finding(failure_receipt, ("injection_id", "observation_receipt_id"), receipt_path))
                    if not isinstance(failure_receipt.get("observation_receipt_id"), str) or len(failure_receipt["observation_receipt_id"].strip()) < 4:
                        findings.append(_finding("UNTRUSTED_LIVE_FAILURE_INJECTION_ASSERTION", receipt_path, "independent observation receipt required"))
            requirements = contract["executable_requirements"]
            injection_text = " ".join(item["inject"].lower() for item in contract["failure_injections"])
            required_receipts = {
                "restart_receipt_id": bool(re.search(r"restart|terminate|crash", injection_text)),
                "duplicate_delivery_receipt_id": requirements["idempotency"],
                "recovery_receipt_id": requirements["recovery"],
                "rollback_receipt_id": requirements["rollback"],
                "compensation_receipt_id": requirements["compensation"],
                "usefulness_receipt_id": requirements["usefulness"],
                "after_state_receipt_id": requirements["after_state"],
                "stop_veto_receipt_id": requirements["stop_veto"],
            }
            for key, required in required_receipts.items():
                value = semantic.get(key)
                if required and (not isinstance(value, str) or len(value.strip()) < 4):
                    code = "UNTRUSTED_LIVE_OUTCOME_ASSERTION"
                    if key in {"restart_receipt_id", "duplicate_delivery_receipt_id"}:
                        code = "UNTRUSTED_LIVE_FAILURE_INJECTION_ASSERTION"
                    findings.append(_finding(code, f"{observed_path}.semantic_observations.{key}", "independent semantic receipt required"))
                if not required and value is not None and (not isinstance(value, str) or len(value.strip()) < 4):
                    findings.append(_finding("PRODUCT_LIVE_OBSERVER_INVALID", f"{observed_path}.semantic_observations.{key}", "must be null or a nonempty receipt"))
        observed_map[journey_id] = observed
    if tuple(observed_map) != JOURNEY_IDS:
        findings.append(_finding("PRODUCT_LIVE_OBSERVER_INVALID", "observer_attestation.journeys", "ordered J01-J10 observations required"))
    if findings:
        authority["observer_status"] = "REJECTED"
        return {}, findings, authority
    authority["observer_status"] = "VERIFIED"
    return observed_map, [], authority


def evaluate_response(
    request: dict[str, Any],
    response: Any,
    manifest: list[dict[str, str]],
    run_root: Path,
    process: dict[str, Any],
    proof_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        request = validate_request(request)
        response_dict = _require_dict(response, "response")
        _strict_keys(response_dict, ("schema", "protocol_version", "request_id", "adapter", "journeys"), "response")
        if response_dict["schema"] != RESPONSE_SCHEMA or response_dict["protocol_version"] != PROTOCOL_VERSION:
            raise ValidationError("PROTOCOL_VERSION_MISMATCH", "response", "unexpected response schema or protocol")
        if response_dict["request_id"] != request["request_id"]:
            raise ValidationError("REQUEST_ID_MISMATCH", "response.request_id", repr(response_dict["request_id"]))
        adapter = _require_dict(response_dict["adapter"], "response.adapter")
        adapter_keys = (
            "adapter_id", "adapter_version", "system_identity", "system_release",
            "actor_run_id", "verifier_run_id", "proof_scope",
        )
        _strict_keys(adapter, adapter_keys, "response.adapter")
        for key in adapter_keys[:-1]:
            _nonempty_string(adapter[key], f"response.adapter.{key}", 4)
        if adapter["proof_scope"] not in PROOF_SCOPES:
            raise ValidationError("INVALID_PROOF_SCOPE", "response.adapter.proof_scope", repr(adapter["proof_scope"]))
        executions = _require_list(response_dict["journeys"], "response.journeys", 10)
        if len(executions) != 10:
            raise ValidationError("JOURNEY_COUNT_MISMATCH", "response.journeys", str(len(executions)))
        execution_map: dict[str, Any] = {}
        for index, execution in enumerate(executions):
            if not isinstance(execution, dict):
                raise ValidationError("TYPE_MISMATCH", f"response.journeys[{index}]", "expected object")
            journey_id = execution.get("journey_id")
            if journey_id not in JOURNEY_IDS:
                raise ValidationError("UNKNOWN_JOURNEY_ID", f"response.journeys[{index}].journey_id", repr(journey_id))
            if journey_id in execution_map:
                raise ValidationError("DUPLICATE_JOURNEY_ID", "response.journeys", journey_id)
            execution_map[journey_id] = execution
        if tuple(execution_map) != JOURNEY_IDS:
            raise ValidationError("JOURNEY_SET_MISMATCH", "response.journeys", repr(list(execution_map)))
    except ValidationError as exc:
        return _all_failed_result(
            request,
            manifest,
            _finding(exc.code, exc.path, exc.message),
            process,
        )
    evaluation_time = parse_utc_timestamp(request["evaluation_time"], "request.evaluation_time")
    resolved_run_root = run_root.resolve(strict=True)
    observer_journeys, authority_findings, proof_authority = _validate_observer_context(
        request, response_dict, proof_context
    )
    journeys = []
    all_findings: list[dict[str, Any]] = []
    statuses = []
    for entry in request["contracts"]:
        contract = entry["contract"]
        status, criterion_evidence, findings = _validate_execution(
            execution_map[contract["journey_id"]],
            contract,
            adapter,
            request["requested_proof_scope"],
            observer_journeys.get(contract["journey_id"]),
            resolved_run_root,
            evaluation_time,
        )
        if adapter["proof_scope"] != request["requested_proof_scope"]:
            findings.append(
                _finding(
                    "SUBJECT_PROOF_SCOPE_MISMATCH",
                    "response.adapter.proof_scope",
                    f"{adapter['proof_scope']!r} != evaluator-requested {request['requested_proof_scope']!r}",
                )
            )
        findings.extend(authority_findings)
        if findings and status != "FAIL":
            status = "FAIL"
            finding_refs = [f"finding:{item['code']}" for item in findings]
            criterion_evidence = {
                criterion_id: sorted(set(evidence_ids + finding_refs))
                for criterion_id, evidence_ids in criterion_evidence.items()
            }
        journeys.append(
            {
                "journey_id": contract["journey_id"],
                "status": status,
                "criterion_evidence": criterion_evidence,
                "findings": findings,
            }
        )
        statuses.append(status)
        all_findings.extend(findings)
    aggregate = _aggregate(statuses)
    proof_scope = request["requested_proof_scope"]
    product_status = "NOT_TESTED" if proof_scope == "HARNESS_SELF_TEST" else aggregate
    return {
        "schema": VERDICT_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request["request_id"],
        "subject": {
            "adapter_id": adapter["adapter_id"],
            "system_identity": adapter["system_identity"],
            "system_release": adapter["system_release"],
        },
        "proof_scope": proof_scope,
        "qualification": "HARNESS_SELF_TEST_ONLY" if proof_scope == "HARNESS_SELF_TEST" else "PRODUCT_PURPOSE_EVALUATION",
        "product_claim_status": product_status,
        "proof_authority": proof_authority,
        "status": aggregate,
        "journeys": journeys,
        "findings": all_findings,
        "contract_manifest": manifest,
        "adapter_process": process,
        "run_confinement": {"temporary": False, "cleanup": "caller_owned"},
    }


def _sanitized_environment() -> dict[str, str]:
    sensitive_fragments = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "API_KEY", "AUTH", "COOKIE", "CREDENTIAL")
    allowed: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(fragment in upper for fragment in sensitive_fragments):
            continue
        allowed[key] = value
    allowed["PYTHONIOENCODING"] = "utf-8"
    allowed["PYTHONDONTWRITEBYTECODE"] = "1"
    return allowed


def _process_record(exit_code: int | None, stderr: bytes, timed_out: bool = False) -> dict[str, Any]:
    return {
        "exit_code": exit_code,
        "stderr_sha256": sha256_bytes(stderr),
        "stderr_present": bool(stderr),
        "timed_out": timed_out,
    }


def _command_artifact_sha256(adapter_command: Sequence[str], run_root: Path) -> str | None:
    identities = []
    for index, token in enumerate(adapter_command):
        candidate: Path | None = None
        raw = Path(token)
        if raw.is_file():
            candidate = raw.resolve(strict=True)
        elif index == 0:
            located = shutil.which(token)
            if located is not None and Path(located).is_file():
                candidate = Path(located).resolve(strict=True)
        if candidate is None:
            continue
        if candidate.is_relative_to(run_root):
            return None
        identities.append(
            {
                "command_index": index,
                "resolved_path": str(candidate),
                "sha256": sha256_file(candidate),
                "size": candidate.stat().st_size,
            }
        )
    if not identities:
        return None
    return sha256_bytes(canonical_json_bytes({"command": list(adapter_command), "artifacts": identities}))


def _observer_command(registration: dict[str, Any], purpose_bundle: dict[str, Any]) -> list[str]:
    artifact_path = (purpose_bundle["root"] / registration["artifact_path"]).resolve(strict=True)
    replacements = {
        "{observer_artifact}": str(artifact_path),
        "{python_executable}": str(Path(sys.executable).resolve(strict=True)),
    }
    return [replacements.get(token, token) for token in registration["command_prefix"]]


def _observer_request(
    request: dict[str, Any],
    response: dict[str, Any],
    registration: dict[str, Any],
    actor_artifact_sha256: str,
    actor_execution_identity: str,
    observer_execution_identity: str,
) -> dict[str, Any]:
    return {
        "schema": OBSERVER_REQUEST_SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request["request_id"],
        "requested_proof_scope": request["requested_proof_scope"],
        "evaluator_authority_id": request["evaluator_authority_id"],
        "challenge_nonce": request["challenge_nonce"],
        "observer_id": registration["observer_id"],
        "observer_artifact_sha256": registration["artifact_sha256"],
        "actor_artifact_sha256": actor_artifact_sha256,
        "actor_response_sha256": sha256_bytes(canonical_json_bytes(response)),
        "actor_execution_identity": actor_execution_identity,
        "observer_execution_identity": observer_execution_identity,
        "adapter_claim": response["adapter"],
        "journey_claims": response["journeys"],
    }


def _run_observer(
    request: dict[str, Any],
    response: dict[str, Any],
    purpose_bundle: dict[str, Any],
    actor_artifact_sha256: str | None,
    timeout_seconds: int,
    run_root: Path,
) -> dict[str, Any]:
    registration = next(
        (
            item
            for item in purpose_bundle["observer_registry"]["registrations"]
            if item["observer_id"] == request.get("observer_id")
        ),
        None,
    )
    response_sha256 = sha256_bytes(canonical_json_bytes(response))
    actor_execution_identity = None
    if actor_artifact_sha256 is not None:
        actor_execution_identity = _issued_execution_identity(
            request["evaluator_authority_id"],
            request["challenge_nonce"],
            "ACTOR",
            actor_artifact_sha256,
            response_sha256,
        )
    context: dict[str, Any] = {
        "observer_registry_sha256": purpose_bundle["observer_registry_sha256"],
        "actor_artifact_sha256": actor_artifact_sha256,
        "actor_execution_identity": actor_execution_identity,
        "observer_artifact_sha256": None,
        "observer_execution_identity": None,
        "registration": registration,
        "attestation": None,
        "observer_process": _process_record(None, b""),
    }
    observer_artifact_path = None
    if registration is not None:
        observer_artifact_path = (purpose_bundle["root"] / registration["artifact_path"]).resolve(strict=True)
    if (
        request["requested_proof_scope"] != "PRODUCT_LIVE"
        or registration is None
        or actor_artifact_sha256 is None
        or observer_artifact_path is None
        or observer_artifact_path.is_relative_to(run_root)
    ):
        return context
    observer_artifact_sha256 = registration["artifact_sha256"]
    observer_execution_identity = _issued_execution_identity(
        request["evaluator_authority_id"],
        request["challenge_nonce"],
        "INDEPENDENT_OBSERVER",
        observer_artifact_sha256,
        response_sha256,
    )
    context["observer_artifact_sha256"] = observer_artifact_sha256
    context["observer_execution_identity"] = observer_execution_identity
    observer_request = _observer_request(
        request,
        response,
        registration,
        actor_artifact_sha256,
        actor_execution_identity,
        observer_execution_identity,
    )
    try:
        completed = subprocess.run(
            _observer_command(registration, purpose_bundle),
            input=canonical_json_bytes(observer_request) + b"\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
            env=_sanitized_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
        context["observer_process"] = _process_record(None, stderr, timed_out=True)
        return context
    except OSError as exc:
        context["observer_process"] = _process_record(None, str(exc).encode("utf-8", errors="replace"))
        return context
    context["observer_process"] = _process_record(completed.returncode, completed.stderr)
    if completed.returncode != 0 or len(completed.stdout) > MAX_RESPONSE_BYTES:
        return context
    try:
        context["attestation"] = strict_json_loads(completed.stdout.decode("utf-8", errors="strict"))
    except (UnicodeError, ValidationError):
        return context
    return context


def _run_with_root(
    adapter_command: Sequence[str],
    purpose_bundle: dict[str, Any],
    run_root: Path,
    evaluation_time: str,
    request_id: str,
    timeout_seconds: int,
    requested_proof_scope: str,
    challenge_nonce: str,
    evaluator_authority_id: str,
    observer_id: str | None,
) -> dict[str, Any]:
    request = build_request(
        purpose_bundle,
        run_root,
        evaluation_time,
        request_id,
        requested_proof_scope=requested_proof_scope,
        challenge_nonce=challenge_nonce,
        evaluator_authority_id=evaluator_authority_id,
        observer_id=observer_id,
    )
    if not adapter_command or any(not isinstance(item, str) or not item for item in adapter_command):
        process = _process_record(None, b"")
        return _all_failed_result(request, purpose_bundle["manifest"], _finding("ADAPTER_COMMAND_MISSING", "adapter_command", "nonempty command required"), process)
    request_bytes = canonical_json_bytes(request) + b"\n"
    try:
        completed = subprocess.run(
            list(adapter_command),
            input=request_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
            env=_sanitized_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
        process = _process_record(None, stderr, timed_out=True)
        return _all_failed_result(request, purpose_bundle["manifest"], _finding("ADAPTER_TIMEOUT", "adapter_process", f"exceeded {timeout_seconds} seconds"), process)
    except OSError as exc:
        process = _process_record(None, str(exc).encode("utf-8", errors="replace"))
        return _all_failed_result(request, purpose_bundle["manifest"], _finding("ADAPTER_UNAVAILABLE", "adapter_process", str(exc)), process)
    process = _process_record(completed.returncode, completed.stderr)
    if completed.returncode != 0:
        return _all_failed_result(request, purpose_bundle["manifest"], _finding("ADAPTER_NONZERO_EXIT", "adapter_process.exit_code", str(completed.returncode)), process)
    if len(completed.stdout) > MAX_RESPONSE_BYTES:
        return _all_failed_result(request, purpose_bundle["manifest"], _finding("ADAPTER_RESPONSE_TOO_LARGE", "adapter_process.stdout", str(len(completed.stdout))), process)
    try:
        response_text = completed.stdout.decode("utf-8", errors="strict")
        response = strict_json_loads(response_text)
    except (UnicodeError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            finding = _finding(exc.code, "adapter_process.stdout", exc.message)
        else:
            finding = _finding("MALFORMED_JSON", "adapter_process.stdout", str(exc))
        return _all_failed_result(request, purpose_bundle["manifest"], finding, process)
    actor_artifact_sha256 = _command_artifact_sha256(adapter_command, run_root)
    proof_context = _run_observer(
        request,
        response,
        purpose_bundle,
        actor_artifact_sha256,
        timeout_seconds,
        run_root,
    )
    return evaluate_response(
        request,
        response,
        purpose_bundle["manifest"],
        run_root,
        process,
        proof_context,
    )


def run_external_adapter(
    adapter_command: Sequence[str],
    purpose_root: Path,
    evaluation_time: str,
    run_root: Path | None = None,
    request_id: str = DEFAULT_REQUEST_ID,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    temporary_parent: Path | None = None,
    requested_proof_scope: str = "HARNESS_SELF_TEST",
    challenge_nonce: str = DEFAULT_CHALLENGE_NONCE,
    evaluator_authority_id: str = DEFAULT_EVALUATOR_AUTHORITY_ID,
    observer_id: str | None = None,
) -> dict[str, Any]:
    """Run one external adapter and return a canonical, fail-closed verdict."""

    purpose_bundle = load_purpose_bundle(purpose_root)
    if timeout_seconds < 1 or timeout_seconds > DEFAULT_TIMEOUT_SECONDS:
        raise ValidationError("INVALID_TIMEOUT", "timeout_seconds", f"must be 1..{DEFAULT_TIMEOUT_SECONDS}")
    if run_root is not None:
        resolved = run_root.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return _run_with_root(
            adapter_command,
            purpose_bundle,
            resolved,
            evaluation_time,
            request_id,
            timeout_seconds,
            requested_proof_scope,
            challenge_nonce,
            evaluator_authority_id,
            observer_id,
        )
    parent = temporary_parent.resolve() if temporary_parent is not None else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jarvis-r1-", dir=parent) as temporary:
        result = _run_with_root(
            adapter_command,
            purpose_bundle,
            Path(temporary),
            evaluation_time,
            request_id,
            timeout_seconds,
            requested_proof_scope,
            challenge_nonce,
            evaluator_authority_id,
            observer_id,
        )
    result["run_confinement"] = {"temporary": True, "cleanup": "completed"}
    return result


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(formatted_json(result), encoding="utf-8", newline="\n")


__all__ = [
    "ALLOWED_STATUSES",
    "CONTRACT_VERSION",
    "DEFAULT_CHALLENGE_NONCE",
    "DEFAULT_EVALUATOR_AUTHORITY_ID",
    "DEFAULT_REQUEST_ID",
    "DEFAULT_TIMEOUT_SECONDS",
    "FORBIDDEN_EFFECT_CLASSES",
    "JOURNEY_IDS",
    "OBSERVER_REGISTRY_SCHEMA",
    "OBSERVER_REQUEST_SCHEMA",
    "OBSERVER_RESPONSE_SCHEMA",
    "PROTOCOL_VERSION",
    "PROOF_SCOPES",
    "STATUS_REASON_CODES",
    "ValidationError",
    "build_request",
    "canonical_json_bytes",
    "evaluate_response",
    "formatted_json",
    "load_purpose_bundle",
    "parse_utc_timestamp",
    "run_external_adapter",
    "sha256_bytes",
    "sha256_file",
    "strict_json_loads",
    "validate_contract",
    "validate_request",
    "write_result",
]
