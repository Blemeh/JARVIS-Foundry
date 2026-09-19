"""Durable executable contracts for existing mission steps.

Mission acceptance text is not executable authority.  This module defines the
small immutable record that binds a mission step to its capability, bounded
resource target, worker instructions, verification scope, and rollback plan.
The record is event-derived; corrections use a higher version and explicit
supersession rather than mutating history.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ..database import FoundryDatabase
from ..events import Event, canonical_json

SUPERVISOR_CONTROLLER = "supervisor"
CONTRACT_EVENT = "mission.step_contract_recorded"
DISPATCHABLE_STATES = frozenset({
    "ACTION_SELECTION", "REPAIRING", "READY", "VERIFYING",
})
RISK_CLASSES = frozenset({"C0", "C1", "C2", "C3"})


class MissionStepContractError(ValueError):
    """Raised when a durable step contract is malformed or unsafe."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MissionStepContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _text_tuple(value: object, field_name: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise MissionStepContractError(f"{field_name} must be a list of strings")
    result = tuple(_text(item, f"{field_name} item") for item in value)
    if required and not result:
        raise MissionStepContractError(f"{field_name} must not be empty")
    return result


def _relative_target(value: object) -> str:
    raw = _text(value, "target_relative_path").replace("\\", "/")
    path = PurePosixPath(raw)
    first = path.parts[0] if path.parts else ""
    if path.is_absolute() or raw.startswith("//") or ":" in first:
        raise MissionStepContractError("target_relative_path must be relative")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts or ".." in parts:
        raise MissionStepContractError(
            "target_relative_path must remain below the registered resource root")
    return "/".join(parts)


def _mapping(value: object, field_name: str, *, required: bool = False) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MissionStepContractError(f"{field_name} must be an object")
    result = json.loads(canonical_json(dict(value)))
    if required and not result:
        raise MissionStepContractError(f"{field_name} must not be empty")
    return result


@dataclass(frozen=True, slots=True)
class MissionStepContract:
    """Immutable, canonical executable authority for one mission step."""

    mission_id: str
    step_id: str
    version: int
    resource_id: str
    capability: str
    target_relative_path: str
    instructions: str
    expected_effects: tuple[str, ...]
    rollback: dict[str, Any]
    risk_class: str = "C1"
    verification_criteria: tuple[str, ...] = field(default_factory=tuple)
    expected_artifacts: tuple[str, ...] = field(default_factory=tuple)
    forbidden_paths: tuple[str, ...] = field(default_factory=tuple)
    #: Fabric capabilities (``cap.*``) the plan step this contract executes
    #: DECLARED as required, resolved from the selected work item's metadata -
    #: the runtime's own carrier of ``PlanStep.required_capabilities``, written
    #: by ``generate_missions_for_objective`` BEFORE the mission exists.
    #:
    #: Deliberately NOT part of ``canonical_payload()``: the payload is a
    #: hash anchor, so adding a key there would invalidate the recorded
    #: ``contract_hash`` of every already-stored row and make the load path
    #: raise *projected contract hash mismatch*.  The field is carried in
    #: ``to_payload()`` (so the projection can persist it in its own column)
    #: and read back by ``from_payload()``/``latest_contract()``.
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)
    recorded_by: str = SUPERVISOR_CONTROLLER
    source: str = "supervisor"
    provenance: dict[str, Any] = field(default_factory=dict)
    supersedes_hash: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    @property
    def contract_id(self) -> str:
        return f"mission-step-contract:{self.mission_id}:{self.step_id}:v{self.version}"

    def canonical_payload(self) -> dict[str, Any]:
        """Return the hash-bound fields, excluding transport metadata."""
        return {
            "mission_id": self.mission_id,
            "step_id": self.step_id,
            "version": self.version,
            "resource_id": self.resource_id,
            "capability": self.capability,
            "target_relative_path": self.target_relative_path,
            "instructions": self.instructions,
            "expected_effects": list(self.expected_effects),
            "rollback": self.rollback,
            "risk_class": self.risk_class,
            "verification_criteria": list(self.verification_criteria),
            "expected_artifacts": list(self.expected_artifacts),
            "forbidden_paths": list(self.forbidden_paths),
            "recorded_by": self.recorded_by,
            "source": self.source,
            "provenance": self.provenance,
            "supersedes_hash": self.supersedes_hash,
        }

    def content_hash(self) -> str:
        return hashlib.sha256(
            canonical_json(self.canonical_payload()).encode("utf-8")
        ).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        payload = dict(self.canonical_payload())
        payload.update({
            "contract_id": self.contract_id,
            "contract_hash": self.content_hash(),
            # Not part of canonical_payload()/content_hash(): the projection
            # persists it in its own column so a reloaded contract still knows
            # which Fabric capabilities the executed plan step declared.
            "required_capabilities": list(self.required_capabilities),
        })
        return payload

    def validate(self) -> None:
        _text(self.mission_id, "mission_id")
        _text(self.step_id, "step_id")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise MissionStepContractError("version must be a positive integer")
        _text(self.resource_id, "resource_id")
        _text(self.capability, "capability")
        target = _relative_target(self.target_relative_path)
        if target != self.target_relative_path:
            raise MissionStepContractError("target_relative_path is not canonical")
        _text(self.instructions, "instructions")
        _text_tuple(self.expected_effects, "expected_effects", required=True)
        _mapping(self.rollback, "rollback", required=True)
        if self.risk_class not in RISK_CLASSES:
            raise MissionStepContractError(f"risk_class must be one of {sorted(RISK_CLASSES)}")
        _text_tuple(self.verification_criteria, "verification_criteria", required=True)
        _text_tuple(self.expected_artifacts, "expected_artifacts")
        _text_tuple(self.forbidden_paths, "forbidden_paths")
        _text_tuple(self.required_capabilities, "required_capabilities")
        for capability_id in self.required_capabilities:
            if not capability_id.startswith("cap."):
                raise MissionStepContractError(
                    "required_capabilities must use the canonical cap.* namespace: "
                    f"{capability_id!r}")
        _text(self.recorded_by, "recorded_by")
        _text(self.source, "source")
        _mapping(self.provenance, "provenance")
        if self.supersedes_hash is not None:
            _text(self.supersedes_hash, "supersedes_hash")
            if len(self.supersedes_hash) != 64 or any(
                char not in "0123456789abcdef" for char in self.supersedes_hash.lower()
            ):
                raise MissionStepContractError("supersedes_hash must be a sha256 hex digest")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "MissionStepContract":
        if not isinstance(payload, Mapping):
            raise MissionStepContractError("contract payload must be an object")
        if "provider" in payload or "model" in payload:
            raise MissionStepContractError(
                "provider/model are runtime authority and cannot be contract fields")
        contract = cls(
            mission_id=_text(payload.get("mission_id"), "mission_id"),
            step_id=_text(payload.get("step_id"), "step_id"),
            version=int(payload.get("version", 0)),
            resource_id=_text(payload.get("resource_id"), "resource_id"),
            capability=_text(payload.get("capability"), "capability"),
            target_relative_path=_relative_target(payload.get("target_relative_path")),
            instructions=_text(payload.get("instructions"), "instructions"),
            expected_effects=_text_tuple(payload.get("expected_effects"), "expected_effects", required=True),
            rollback=_mapping(payload.get("rollback"), "rollback", required=True),
            risk_class=_text(payload.get("risk_class", "C1"), "risk_class"),
            verification_criteria=_text_tuple(
                payload.get("verification_criteria"), "verification_criteria", required=True),
            expected_artifacts=_text_tuple(payload.get("expected_artifacts", []), "expected_artifacts"),
            forbidden_paths=_text_tuple(payload.get("forbidden_paths", []), "forbidden_paths"),
            required_capabilities=_text_tuple(
                payload.get("required_capabilities", []), "required_capabilities"),
            recorded_by=_text(payload.get("recorded_by", SUPERVISOR_CONTROLLER), "recorded_by"),
            source=_text(payload.get("source", "supervisor"), "source"),
            provenance=_mapping(payload.get("provenance", {}), "provenance"),
            supersedes_hash=payload.get("supersedes_hash"),
        )
        supplied_hash = payload.get("contract_hash")
        if supplied_hash != contract.content_hash():
            raise MissionStepContractError("contract_hash does not match canonical payload")
        if payload.get("contract_id") not in (None, contract.contract_id):
            raise MissionStepContractError("contract_id does not match canonical identity")
        return contract

    def runtime_kwargs(self, database: FoundryDatabase) -> dict[str, Any]:
        """Resolve this contract into the existing MissionLoop call shape."""
        mission = database.row("missions", "mission_id", self.mission_id)
        if mission is None:
            raise MissionStepContractError(f"unknown mission: {self.mission_id}")
        if str(mission.get("resource_id") or "") != self.resource_id:
            raise MissionStepContractError("contract resource_id does not match mission")
        resource = database.row("resources", "resource_id", self.resource_id)
        if resource is None:
            raise MissionStepContractError(f"unknown resource: {self.resource_id}")
        capabilities = json.loads(str(resource["capabilities_json"]))
        if self.capability not in capabilities:
            raise MissionStepContractError(
                f"capability {self.capability!r} is not registered for resource {self.resource_id}")
        root = Path(str(resource["resolved_root"])).expanduser().resolve()
        target = (root / Path(*self.target_relative_path.split("/"))).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as error:
            raise MissionStepContractError("contract target escapes resource root") from error
        return {
            "mission_id": self.mission_id,
            "step_id": self.step_id,
            "capability": self.capability,
            "target": target,
            "instructions": self.instructions,
            "expected_effects": self.expected_effects,
            "rollback": self.rollback,
            "risk_class": self.risk_class,
            "verification_criteria": list(self.verification_criteria),
            "expected_artifacts": self.expected_artifacts,
            "forbidden_paths": self.forbidden_paths,
        }


def record_step_contract(
    database: FoundryDatabase,
    contract: MissionStepContract,
    *,
    controller: str = SUPERVISOR_CONTROLLER,
) -> tuple[int, bool]:
    """Record one immutable contract through the canonical event ledger."""
    if controller != SUPERVISOR_CONTROLLER:
        raise MissionStepContractError("only the exact Supervisor controller may record contracts")
    if contract.recorded_by != SUPERVISOR_CONTROLLER:
        raise MissionStepContractError("recorded_by must be the exact Supervisor identity")
    _validate_database_binding(database, contract)
    latest = latest_contract(
        database, contract.mission_id, step_id=contract.step_id, allow_invalid=False)
    if latest is not None:
        if contract.version <= latest.version:
            if contract.version == latest.version and contract.content_hash() == latest.content_hash():
                return database.append(Event.create(
                    CONTRACT_EVENT,
                    contract.to_payload(),
                    source_type="supervisor",
                    source_id=SUPERVISOR_CONTROLLER,
                    idempotency_key=f"{CONTRACT_EVENT}:{contract.contract_id}:{contract.content_hash()}",
                ))
            raise MissionStepContractError("contract version is immutable")
        if contract.supersedes_hash != latest.content_hash():
            raise MissionStepContractError("new contract version must supersede the latest hash")
    event = Event.create(
        CONTRACT_EVENT,
        contract.to_payload(),
        source_type="supervisor",
        source_id=SUPERVISOR_CONTROLLER,
        idempotency_key=f"{CONTRACT_EVENT}:{contract.contract_id}:{contract.content_hash()}",
    )
    return database.append(event)


def _validate_database_binding(database: FoundryDatabase, contract: MissionStepContract) -> None:
    mission = database.row("missions", "mission_id", contract.mission_id)
    if mission is None:
        raise MissionStepContractError(f"unknown mission: {contract.mission_id}")
    if str(mission.get("resource_id") or "") != contract.resource_id:
        raise MissionStepContractError("contract resource_id does not match mission")
    if database.row("resources", "resource_id", contract.resource_id) is None:
        raise MissionStepContractError(f"unknown resource: {contract.resource_id}")


def contract_rows(database: FoundryDatabase, mission_id: str) -> list[dict[str, Any]]:
    return database.all("mission_step_contracts", mission_id=mission_id)


def latest_contract(
    database: FoundryDatabase,
    mission_id: str,
    *,
    step_id: str | None = None,
    allow_invalid: bool = True,
) -> MissionStepContract | None:
    """Load and hash-verify the highest version for a mission/step."""
    rows = contract_rows(database, mission_id)
    if step_id is not None:
        rows = [row for row in rows if row["step_id"] == step_id]
    else:
        # A mission may carry historical versions, but the runtime must never
        # guess which of multiple active step IDs is executable.
        latest_by_step: dict[str, dict[str, Any]] = {}
        for candidate in rows:
            key = str(candidate["step_id"])
            current = latest_by_step.get(key)
            if current is None or int(candidate["version"]) > int(current["version"]):
                latest_by_step[key] = candidate
        if len(latest_by_step) > 1:
            raise MissionStepContractError(
                "multiple active step contracts for mission; step binding is required")
        rows = list(latest_by_step.values())
    if not rows:
        return None
    row = max(rows, key=lambda item: (int(item["version"]), str(item["step_id"])))
    try:
        contract = MissionStepContract(
            mission_id=str(row["mission_id"]),
            step_id=str(row["step_id"]),
            version=int(row["version"]),
            resource_id=str(row["resource_id"]),
            capability=str(row["capability"]),
            target_relative_path=str(row["target_relative_path"]),
            instructions=str(row["instructions"]),
            expected_effects=tuple(json.loads(str(row["expected_effects_json"]))),
            rollback=json.loads(str(row["rollback_json"])),
            risk_class=str(row["risk_class"]),
            verification_criteria=tuple(json.loads(str(row["verification_criteria_json"]))),
            expected_artifacts=tuple(json.loads(str(row["expected_artifacts_json"]))),
            forbidden_paths=tuple(json.loads(str(row["forbidden_paths_json"]))),
            required_capabilities=tuple(json.loads(
                str(row["required_capabilities_json"] or "[]"))),
            recorded_by=str(row["recorded_by"]),
            source=str(row["source"]),
            provenance=json.loads(str(row["provenance_json"])),
            supersedes_hash=row["supersedes_hash"],
        )
        if str(row["contract_hash"]) != contract.content_hash():
            raise MissionStepContractError("projected contract hash mismatch")
        _validate_database_binding(database, contract)
        contract.runtime_kwargs(database)
        return contract
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        if allow_invalid:
            raise MissionStepContractError(f"invalid projected contract: {error}") from error
        raise


def latest_contracts_by_step(
    database: FoundryDatabase, mission_id: str,
) -> dict[str, MissionStepContract]:
    """Return the highest-version contract for each step_id on a mission."""
    rows = contract_rows(database, mission_id)
    latest_ids: dict[str, dict[str, Any]] = {}
    for candidate in rows:
        key = str(candidate["step_id"])
        current = latest_ids.get(key)
        if current is None or int(candidate["version"]) > int(current["version"]):
            latest_ids[key] = candidate
    result: dict[str, MissionStepContract] = {}
    for step_id, row in latest_ids.items():
        contract = latest_contract(
            database, mission_id, step_id=step_id, allow_invalid=False)
        if contract is not None:
            result[step_id] = contract
    return result


def next_executable_contract(
    database: FoundryDatabase, mission_id: str,
) -> MissionStepContract | None:
    """Select the next un-passed step contract for a mission.

    Multiple active step_ids are the durable action plan, not an error.
    ``latest_contract(mission_id)`` still fail-closes when a caller asks for
    a single unbound contract; this selector is the production owner for
    sequential dispatch.
    """
    from .verification import accepted_verifications, latest_verdict_for_step
    accepted = accepted_verifications(database, mission_id=mission_id)

    remaining: list[MissionStepContract] = []
    for step_id, contract in latest_contracts_by_step(database, mission_id).items():
        verdict = latest_verdict_for_step(database, mission_id, step_id)
        if (
            verdict is not None
            and str(verdict.get("status") or "") == "PASS"
            and str(verdict.get("verdict_id") or "") in accepted
        ):
            continue
        remaining.append(contract)
    remaining.sort(key=lambda item: (item.step_id, item.version))
    return remaining[0] if remaining else None


def dispatchable_contract_missions(database: FoundryDatabase) -> tuple[str, ...]:
    """Return existing dispatchable missions with an authoritative contract."""
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT mission_id FROM missions WHERE state IN "
            "('ACTION_SELECTION', 'REPAIRING', 'READY', 'VERIFYING') "
            "ORDER BY updated_at, mission_id"
        ).fetchall()
    result: list[str] = []
    for row in rows:
        if contract_rows(database, str(row["mission_id"])):
            result.append(str(row["mission_id"]))
    return tuple(result)
