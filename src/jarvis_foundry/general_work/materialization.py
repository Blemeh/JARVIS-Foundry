"""Materialize an immutable General Work plan onto existing JARVIS authorities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event, canonical_json
from ..executive.autonomy import (
    PlanStep,
    active_plan,
    bind_mission_to_step,
    build_plan,
    rebuild_executive_state,
)
from ..executive.campaigns import (
    create_campaign,
    declare_child_mission,
    rebuild_campaign_state,
    record_child_state,
)
from ..fabric.catalog import get_spec
from ..fabric.model import Semantic
from ..mission.contracts import MissionStepContract, record_step_contract
from ..v3_fault import invoke_fault_hook
from .ledger import PlanClaimReceipt, record_plan_and_claim_targets
from .model import (
    ArtifactAssertion,
    ConsistencyAssertion,
    DeliverableSpec,
    GeneralWorkPlan,
    SourceIdentity,
    WorkStep,
)


MATERIALIZATION_SCHEMA = "v13.general-work-materialization.v1"
MATERIALIZED_EVENT = "general_work.plan_materialized"
_READY_CAPABILITY_STATES = frozenset({"READY", "ACTIVE"})
_TERMINAL_OBJECTIVE_STATES = frozenset(
    {"BLOCKED", "CANCELLED", "COMPLETED", "FAILED", "SUSPENDED"}
)
_MUTATING_SEMANTICS = frozenset(
    {Semantic.WRITE, Semantic.COMMUNICATE, Semantic.EXTERNAL_EFFECT}
)


class GeneralWorkMaterializationError(ValueError):
    """The plan cannot safely enter the existing execution spine."""


@dataclass(frozen=True, slots=True)
class MaterializedStep:
    source_step_id: str
    runtime_step_id: str
    mission_id: str
    child_id: str
    capability_id: str
    target_relative_path: str

    def to_payload(self) -> dict[str, str]:
        return {
            "source_step_id": self.source_step_id,
            "runtime_step_id": self.runtime_step_id,
            "mission_id": self.mission_id,
            "child_id": self.child_id,
            "capability_id": self.capability_id,
            "target_relative_path": self.target_relative_path,
        }


@dataclass(frozen=True, slots=True)
class GeneralWorkMaterialization:
    plan_id: str
    plan_hash: str
    executive_plan_id: str
    campaign_id: str
    steps: tuple[MaterializedStep, ...]
    verifier: MaterializedStep
    claim_receipt: PlanClaimReceipt
    event_sequence: int
    inserted: bool

    @property
    def replayed(self) -> bool:
        return self.claim_receipt.replayed and not self.inserted

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": MATERIALIZATION_SCHEMA,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "executive_plan_id": self.executive_plan_id,
            "campaign_id": self.campaign_id,
            "steps": [step.to_payload() for step in self.steps],
            "verifier": self.verifier.to_payload(),
            "claim_receipt": self.claim_receipt.to_payload(),
            "event_sequence": self.event_sequence,
            "inserted": self.inserted,
            "replayed": self.replayed,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_ref(
    value: object,
    field_name: str,
    *,
    allow_root: bool = False,
) -> str:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise GeneralWorkMaterializationError(
            f"{field_name} must be a non-empty relative path"
        )
    raw = str(value).strip().replace("\\", "/")
    if allow_root and raw in {".", "./"}:
        return "."
    path = PurePosixPath(raw)
    first = path.parts[0] if path.parts else ""
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if (
        path.is_absolute()
        or raw.startswith("//")
        or ":" in first
        or not parts
        or ".." in parts
    ):
        raise GeneralWorkMaterializationError(
            f"{field_name} must stay below the registered resource root"
        )
    return "/".join(parts)


def _resource_binding(
    database: FoundryDatabase,
    plan: GeneralWorkPlan,
) -> tuple[Path, dict[str, str]]:
    objective = rebuild_executive_state(database).objectives.get(plan.objective_id)
    if objective is None:
        raise GeneralWorkMaterializationError(
            f"General Work objective is absent: {plan.objective_id}"
        )
    if objective.status in _TERMINAL_OBJECTIVE_STATES:
        raise GeneralWorkMaterializationError(
            f"General Work objective is not executable: {objective.status}"
        )
    if objective.resource_id != plan.resource_id:
        raise GeneralWorkMaterializationError(
            "General Work plan resource does not match its objective authority"
        )
    resource = database.row("resources", "resource_id", plan.resource_id)
    if resource is None:
        raise GeneralWorkMaterializationError(
            f"General Work resource is absent: {plan.resource_id}"
        )
    root = Path(str(resource["resolved_root"])).expanduser().resolve()
    if not root.is_dir():
        raise GeneralWorkMaterializationError(
            f"General Work resource root is not a directory: {root}"
        )
    try:
        raw_capabilities = json.loads(str(resource["capabilities_json"]))
    except (TypeError, json.JSONDecodeError) as error:
        raise GeneralWorkMaterializationError(
            "General Work resource capabilities are malformed"
        ) from error
    if not isinstance(raw_capabilities, Mapping):
        raise GeneralWorkMaterializationError(
            "General Work resource capabilities must be an object"
        )
    capabilities = {
        str(capability_id): str(state).upper()
        for capability_id, state in raw_capabilities.items()
    }
    return root, capabilities


def _assert_no_symlink_path(root: Path, relative_path: str, field_name: str) -> Path:
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise GeneralWorkMaterializationError(
                f"{field_name} traverses a symbolic link: {relative_path}"
            )
    resolved = (root / Path(*PurePosixPath(relative_path).parts)).resolve(
        strict=False
    )
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise GeneralWorkMaterializationError(
            f"{field_name} escapes the registered resource root"
        ) from error
    return resolved


def _verify_sources(root: Path, sources: Sequence[SourceIdentity]) -> None:
    for source in sources:
        path = _assert_no_symlink_path(root, source.path, "source path")
        try:
            if not path.is_file():
                raise GeneralWorkMaterializationError(
                    f"source is absent or unsupported: {source.path}"
                )
            actual_size = path.stat().st_size
            actual_hash = _sha256(path)
        except PermissionError as error:
            raise GeneralWorkMaterializationError(
                f"source is locked or unreadable: {source.path}"
            ) from error
        except OSError as error:
            raise GeneralWorkMaterializationError(
                f"source cannot be inspected: {source.path}: {error}"
            ) from error
        if actual_size != source.size_bytes or actual_hash != source.sha256:
            raise GeneralWorkMaterializationError(
                f"source identity is stale: {source.path}"
            )


def _step_ancestors(plan: GeneralWorkPlan) -> dict[str, frozenset[str]]:
    by_id = {step.step_id: step for step in plan.steps}
    result: dict[str, frozenset[str]] = {}
    for layer in plan.topological_layers():
        for step_id in layer:
            direct = set(by_id[step_id].dependencies)
            inherited = {
                ancestor
                for dependency in direct
                for ancestor in result[dependency]
            }
            result[step_id] = frozenset(direct | inherited)
    return result


def _capability_for(
    database: FoundryDatabase,
    capabilities: Mapping[str, str],
    action_kind: str,
) -> str:
    capability_id = f"cap.{action_kind}"
    spec = get_spec(database, capability_id)
    if spec is None or spec.action_kind != action_kind:
        raise GeneralWorkMaterializationError(
            f"General Work capability is not registered: {capability_id}"
        )
    state = capabilities.get(capability_id)
    if state not in _READY_CAPABILITY_STATES:
        raise GeneralWorkMaterializationError(
            f"resource capability {capability_id} is {state or 'UNREGISTERED'}"
        )
    return capability_id


def _input_paths(
    plan: GeneralWorkPlan,
    step: WorkStep,
    ancestors: Mapping[str, frozenset[str]],
) -> frozenset[str]:
    sources = {source.source_id: source for source in plan.sources}
    deliverables = {
        deliverable.deliverable_id: deliverable
        for deliverable in plan.deliverables
    }
    by_step = {item.step_id: item for item in plan.steps}
    paths = {
        sources[source_id].path
        for source_id in step.source_ids
    }
    for ancestor_id in ancestors[step.step_id]:
        for output_id in by_step[ancestor_id].output_ids:
            paths.add(deliverables[output_id].path)
    return frozenset(paths)


def _source_references(payload: Mapping[str, Any]) -> tuple[str, ...]:
    references: list[str] = []
    if payload.get("source") is not None:
        references.append(_relative_ref(payload["source"], "payload source"))
    if payload.get("sources") is not None:
        raw = payload["sources"]
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise GeneralWorkMaterializationError(
                "payload sources must be a sequence of relative paths"
            )
        references.extend(
            _relative_ref(value, "payload source")
            for value in raw
        )
    return tuple(references)


def _assert_payload_paths(
    plan: GeneralWorkPlan,
    step: WorkStep,
    *,
    semantic: Semantic,
    ancestors: Mapping[str, frozenset[str]],
) -> tuple[dict[str, Any], str]:
    payload = json.loads(
        canonical_json(step.to_payload()["payload"])
    )
    forbidden = {
        "source_root",
        "source_roots",
        "idempotency_key",
        "repair_attempt",
        "repair_existing",
        "repair_of_effect_id",
    }
    supplied = sorted(forbidden.intersection(payload))
    if supplied:
        raise GeneralWorkMaterializationError(
            "General Work payload cannot supply trusted policy or effect "
            f"identity fields: {supplied}"
        )

    deliverables = {
        deliverable.deliverable_id: deliverable
        for deliverable in plan.deliverables
    }
    output_paths = tuple(
        deliverables[output_id].path for output_id in step.output_ids
    )
    if len(output_paths) > 1:
        raise GeneralWorkMaterializationError(
            f"step {step.step_id} must be split into one mutation per output"
        )
    allowed_inputs = _input_paths(plan, step, ancestors)
    references = _source_references(payload)
    unknown_sources = sorted(set(references).difference(allowed_inputs))
    if unknown_sources:
        raise GeneralWorkMaterializationError(
            f"step {step.step_id} reads paths outside declared lineage: "
            f"{unknown_sources}"
        )

    target_raw = payload.get("target")
    target = (
        _relative_ref(target_raw, "payload target", allow_root=True)
        if target_raw is not None
        else None
    )
    if output_paths:
        expected_target = output_paths[0]
        if target != expected_target:
            raise GeneralWorkMaterializationError(
                f"step {step.step_id} target must equal its claimed deliverable"
            )
        if semantic is Semantic.READ:
            raise GeneralWorkMaterializationError(
                f"read action {step.action_kind} cannot produce a deliverable"
            )
    elif semantic in _MUTATING_SEMANTICS:
        if target is None or target not in allowed_inputs:
            raise GeneralWorkMaterializationError(
                f"unclaimed mutation target in step {step.step_id}"
            )
    elif (
        semantic is Semantic.READ
        and target not in {None, "."}
        and target not in allowed_inputs
    ):
        raise GeneralWorkMaterializationError(
            f"step {step.step_id} reads a target outside declared lineage"
        )

    original_paths = {source.path for source in plan.sources}
    if semantic in _MUTATING_SEMANTICS and target in original_paths:
        raise GeneralWorkMaterializationError(
            f"step {step.step_id} would mutate an immutable source"
        )

    source_by_path = {source.path: source for source in plan.sources}
    expected_sources = {
        reference: source_by_path[reference].sha256
        for reference in references
        if reference in source_by_path
    }
    primary_source = (
        _relative_ref(payload["source"], "payload source")
        if payload.get("source") is not None
        else None
    )
    if primary_source in source_by_path:
        expected_hash = source_by_path[primary_source].sha256
        supplied_hash = payload.get("expected_source_sha256")
        if supplied_hash is not None and (
            str(supplied_hash).removeprefix("sha256:").lower() != expected_hash
        ):
            raise GeneralWorkMaterializationError(
                f"step {step.step_id} carries a stale source hash"
            )
        payload["expected_source_sha256"] = expected_hash
    elif payload.get("expected_source_sha256") is not None:
        raise GeneralWorkMaterializationError(
            f"step {step.step_id} cannot predeclare a generated source hash"
        )
    if expected_sources:
        payload["expected_source_sha256s"] = expected_sources

    payload["idempotency_key"] = (
        f"general-work:{plan.plan_hash}:{step.step_id}"
    )
    contract_target = (
        output_paths[0]
        if output_paths
        else (
            target
            if target not in {None, "."}
            else (
                references[0]
                if references
                else plan.delivery_root
            )
        )
    )
    encoded = canonical_json(payload)
    if len(encoded) > 12_000:
        raise GeneralWorkMaterializationError(
            f"step {step.step_id} instructions exceed the mission context bound"
        )
    return payload, contract_target


def _criteria_for_step(
    plan: GeneralWorkPlan,
    step: WorkStep,
) -> tuple[str, ...]:
    artifact_assertions = {
        assertion.assertion_id: assertion
        for assertion in plan.artifact_assertions
    }
    consistency_assertions = {
        assertion.assertion_id: assertion
        for assertion in plan.consistency_assertions
    }
    criteria = ["Fabric independent readback returns MATCH"]
    for assertion_id in step.verification_assertion_ids:
        assertion = artifact_assertions.get(assertion_id)
        if isinstance(assertion, ArtifactAssertion):
            criteria.append(
                f"{assertion.assertion_id}:{assertion.kind}"
            )
            continue
        consistency = consistency_assertions.get(assertion_id)
        if isinstance(consistency, ConsistencyAssertion):
            criteria.append(
                f"{consistency.assertion_id}:{consistency.operator}"
            )
    return tuple(dict.fromkeys(criteria))


def _runtime_identities(
    plan: GeneralWorkPlan,
) -> tuple[
    dict[str, tuple[str, str, str]],
    tuple[str, str, str],
]:
    prefix = plan.plan_hash[:16]
    identities: dict[str, tuple[str, str, str]] = {}
    ordered = [
        step_id
        for layer in plan.topological_layers()
        for step_id in layer
    ]
    for index, step_id in enumerate(ordered, start=1):
        token = hashlib.sha256(step_id.encode("utf-8")).hexdigest()[:8]
        runtime_step = f"gw-{prefix}-{index:03d}-{token}"
        identities[step_id] = (
            runtime_step,
            f"mission-{runtime_step}",
            f"child-{runtime_step}",
        )
    verifier_step = f"gw-{prefix}-verify"
    return identities, (
        verifier_step,
        f"mission-{verifier_step}",
        f"child-{verifier_step}",
    )


def _ensure_mission(
    database: FoundryDatabase,
    *,
    mission_id: str,
    objective: str,
    resource_id: str,
    acceptance_criteria: Sequence[str],
    next_action: str,
    plan_id: str,
    control_guard: Callable[[], None],
) -> None:
    control_guard()
    row = database.row("missions", "mission_id", mission_id)
    if row is not None:
        try:
            prior_acceptance = tuple(json.loads(str(row["acceptance_json"])))
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise GeneralWorkMaterializationError(
                f"mission acceptance is malformed: {mission_id}"
            ) from error
        if (
            str(row.get("objective") or "") != objective
            or str(row.get("resource_id") or "") != resource_id
            or str(row.get("created_from") or "") != f"general-work:{plan_id}"
            or prior_acceptance != tuple(acceptance_criteria)
        ):
            raise GeneralWorkMaterializationError(
                f"mission id already names different work: {mission_id}"
            )
        return
    database.append(
        Event.create(
            "mission.created",
            {
                "mission_id": mission_id,
                "objective": objective,
                "resource_id": resource_id,
                "state": "PLANNED",
                "acceptance_criteria": list(acceptance_criteria),
                "next_action": next_action,
                "created_from": f"general-work:{plan_id}",
            },
            source_type="supervisor",
            source_id="v13-general-work",
            correlation_id=plan_id,
            idempotency_key=f"mission.created:general-work:{mission_id}",
        )
    )


def _release_mission(
    database: FoundryDatabase,
    mission_id: str,
    *,
    next_action: str,
    control_guard: Callable[[], None],
) -> None:
    transitions = {
        "PLANNED": "ORIENTING",
        "ORIENTING": "ACTION_SELECTION",
        "ACTION_SELECTION": "READY",
    }
    while True:
        row = database.row("missions", "mission_id", mission_id)
        if row is None:
            raise GeneralWorkMaterializationError(
                f"mission disappeared before release: {mission_id}"
            )
        current = str(row.get("state") or "")
        target = transitions.get(current)
        if target is None:
            return
        control_guard()
        database.append(
            Event.create(
                "mission.state_changed",
                {
                    "mission_id": mission_id,
                    "state": target,
                    "next_action": next_action,
                },
                source_type="supervisor",
                source_id="v13-general-work",
                idempotency_key=(
                    f"mission.state_changed:{mission_id}:{target}:general-work"
                ),
            )
        )


def _materialized_event_payload(
    plan: GeneralWorkPlan,
    executive_plan_id: str,
    steps: Sequence[MaterializedStep],
    verifier: MaterializedStep,
) -> dict[str, Any]:
    return {
        "schema": MATERIALIZATION_SCHEMA,
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "objective_id": plan.objective_id,
        "resource_id": plan.resource_id,
        "executive_plan_id": executive_plan_id,
        "campaign_id": plan.campaign_id,
        "steps": [step.to_payload() for step in steps],
        "verifier": verifier.to_payload(),
    }


def _delivery_artifact_refs(plan: GeneralWorkPlan) -> tuple[str, ...]:
    root = PurePosixPath(plan.delivery_root)
    return tuple(
        str(PurePosixPath(deliverable.path).relative_to(root))
        for deliverable in plan.deliverables
    )


def _campaign_contract(
    database: FoundryDatabase,
    plan: GeneralWorkPlan,
    materialized_steps: Sequence[MaterializedStep],
    verifier: MaterializedStep,
) -> tuple[
    str | None,
    int,
    dict[str, float],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    objective = rebuild_executive_state(database).objectives[plan.objective_id]
    all_children = [
        *(step.child_id for step in materialized_steps),
        verifier.child_id,
    ]
    join_id = f"general-work-all:{plan.plan_hash[:24]}"
    step_by_id = {step.step_id: step for step in plan.steps}
    retry_budget = {
        materialized.runtime_step_id: float(
            step_by_id[materialized.source_step_id].max_retries
        )
        for materialized in materialized_steps
    }
    retry_budget[verifier.runtime_step_id] = 0.0
    retry_budget["total"] = float(sum(retry_budget.values()))
    max_parallel = max(
        1,
        max(len(layer) for layer in plan.topological_layers()),
    )
    join_conditions = {
        join_id: {
            "children": all_children,
            "mode": "ALL",
            "required": True,
        }
    }
    metadata = {
        "schema": MATERIALIZATION_SCHEMA,
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "delivery_root": plan.delivery_root,
    }
    return (
        objective.deadline,
        max_parallel,
        retry_budget,
        join_conditions,
        metadata,
    )


def _assert_campaign_replay_compatible(
    database: FoundryDatabase,
    plan: GeneralWorkPlan,
    *,
    deadline: str | None,
    max_parallel: int,
    retry_budget: Mapping[str, float],
    join_conditions: Mapping[str, Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> None:
    prior = rebuild_campaign_state(database).campaigns.get(plan.campaign_id)
    if prior is None:
        return
    if (
        prior.objective_id != plan.objective_id
        or prior.deadline != deadline
        or prior.max_replans != 0
        or prior.max_parallel_children != max_parallel
        or prior.retry_budget != dict(retry_budget)
        or prior.join_conditions
        != {
            key: {
                "children": list(value["children"]),
                "mode": str(value["mode"]).upper(),
                "required": bool(value["required"]),
            }
            for key, value in join_conditions.items()
        }
        or prior.metadata != dict(metadata)
    ):
        raise GeneralWorkMaterializationError(
            "campaign id already names a different immutable contract: "
            f"{plan.campaign_id}"
        )


def _materialization_control_identities(
    plan: GeneralWorkPlan,
    executive_plan_id: str,
    materialized_steps: Sequence[MaterializedStep],
    verifier: MaterializedStep,
) -> set[str]:
    identities = {
        "GLOBAL",
        "general_work",
        "general_work.verify",
        plan.plan_id,
        plan.plan_hash,
        plan.objective_id,
        plan.campaign_id,
        plan.resource_id,
        plan.delivery_root,
        executive_plan_id,
        verifier.runtime_step_id,
        verifier.mission_id,
        verifier.child_id,
        verifier.capability_id,
    }
    for source in plan.sources:
        identities.update((source.source_id, source.path))
    for deliverable in plan.deliverables:
        identities.update((deliverable.deliverable_id, deliverable.path))
    by_source_step = {step.step_id: step for step in plan.steps}
    for materialized in materialized_steps:
        source_step = by_source_step[materialized.source_step_id]
        identities.update(
            (
                source_step.step_id,
                source_step.action_kind,
                materialized.runtime_step_id,
                materialized.mission_id,
                materialized.child_id,
                materialized.capability_id,
                materialized.target_relative_path,
            )
        )
    return identities


def _assert_owner_controls_clear(
    database: FoundryDatabase,
    *,
    stop_path: Path,
    identities: set[str],
) -> None:
    reason = emergency_stop_reason(
        database,
        stop_path,
        identities=identities,
    )
    if reason is None:
        reason = matching_veto_reason(database, identities)
    if reason is not None:
        raise GeneralWorkMaterializationError(
            f"General Work materialization is stopped: {reason}"
        )


def materialize_general_work_plan(
    database: FoundryDatabase,
    plan: GeneralWorkPlan,
    *,
    stop_path: str | Path | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> GeneralWorkMaterialization:
    """Bind a General Work plan to Executive, Campaign, Mission, and Fabric."""

    if plan.ambiguities:
        raise GeneralWorkMaterializationError(
            "General Work plan has unresolved ambiguities: "
            + "; ".join(plan.ambiguities)
        )
    root, resource_capabilities = _resource_binding(database, plan)
    _verify_sources(root, plan.sources)

    identities, verifier_identity = _runtime_identities(plan)
    step_by_id = {step.step_id: step for step in plan.steps}
    ordered_step_ids = [
        step_id
        for layer in plan.topological_layers()
        for step_id in layer
    ]
    deliverable_by_id = {
        deliverable.deliverable_id: deliverable
        for deliverable in plan.deliverables
    }
    ancestors = _step_ancestors(plan)
    prepared: dict[str, tuple[str, dict[str, Any], str]] = {}
    materialized_steps: list[MaterializedStep] = []
    for step_id in ordered_step_ids:
        step = step_by_id[step_id]
        capability_id = _capability_for(
            database,
            resource_capabilities,
            step.action_kind,
        )
        spec = get_spec(database, capability_id)
        if spec is None:  # pragma: no cover - guarded above
            raise GeneralWorkMaterializationError(
                f"capability disappeared during preflight: {capability_id}"
            )
        payload, target = _assert_payload_paths(
            plan,
            step,
            semantic=spec.semantic,
            ancestors=ancestors,
        )
        prepared[step.step_id] = (capability_id, payload, target)
        runtime_step, mission_id, child_id = identities[step.step_id]
        materialized_steps.append(
            MaterializedStep(
                source_step_id=step.step_id,
                runtime_step_id=runtime_step,
                mission_id=mission_id,
                child_id=child_id,
                capability_id=capability_id,
                target_relative_path=target,
            )
        )
    verifier_capability = _capability_for(
        database,
        resource_capabilities,
        "general_work.verify",
    )
    verifier_step_id, verifier_mission_id, verifier_child_id = verifier_identity
    verifier = MaterializedStep(
        source_step_id="package-verification",
        runtime_step_id=verifier_step_id,
        mission_id=verifier_mission_id,
        child_id=verifier_child_id,
        capability_id=verifier_capability,
        target_relative_path=plan.delivery_root,
    )

    executive_plan_id = f"executive-general-work:{plan.plan_hash}"
    prior_active = active_plan(database, plan.objective_id)
    if (
        prior_active is not None
        and prior_active.plan_id != executive_plan_id
    ):
        raise GeneralWorkMaterializationError(
            "objective already has a different active executable plan"
        )

    (
        campaign_deadline,
        max_parallel,
        retry_budget,
        join_conditions,
        campaign_metadata,
    ) = _campaign_contract(
        database,
        plan,
        materialized_steps,
        verifier,
    )
    _assert_campaign_replay_compatible(
        database,
        plan,
        deadline=campaign_deadline,
        max_parallel=max_parallel,
        retry_budget=retry_budget,
        join_conditions=join_conditions,
        metadata=campaign_metadata,
    )
    effective_stop_path = (
        Path(stop_path)
        if stop_path is not None
        else Path(database.path).resolve().parent / "EMERGENCY_STOP"
    )
    control_identities = _materialization_control_identities(
        plan,
        executive_plan_id,
        materialized_steps,
        verifier,
    )

    def assert_owner_controls_clear() -> None:
        _assert_owner_controls_clear(
            database,
            stop_path=effective_stop_path,
            identities=control_identities,
        )

    assert_owner_controls_clear()

    claim_receipt = record_plan_and_claim_targets(
        database,
        plan,
        resource_root=root,
    )
    invoke_fault_hook(fault_hook, "post general_work.plan_claimed")
    assert_owner_controls_clear()

    executive_steps = []
    for materialized in materialized_steps:
        source_step = step_by_id[materialized.source_step_id]
        output_paths = tuple(
            deliverable_by_id[output_id].path
            for output_id in source_step.output_ids
        )
        executive_steps.append(
            PlanStep(
                step_id=materialized.runtime_step_id,
                objective_id=plan.objective_id,
                description=source_step.description,
                dependencies=tuple(
                    identities[dependency][0]
                    for dependency in source_step.dependencies
                ),
                required_capabilities=(materialized.capability_id,),
                resource_id=plan.resource_id,
                output_contract=(
                    output_paths
                    or (f"receipt:{source_step.action_kind}",)
                ),
                verification_contract=_criteria_for_step(plan, source_step),
                fallback="bounded retry, then WAIT or BLOCKED with evidence",
                max_retries=source_step.max_retries,
            )
        )
    executive_steps.append(
        PlanStep(
            step_id=verifier.runtime_step_id,
            objective_id=plan.objective_id,
            description="Verify the complete General Work package",
            dependencies=tuple(
                materialized.runtime_step_id
                for materialized in materialized_steps
            ),
            required_capabilities=(verifier.capability_id,),
            resource_id=plan.resource_id,
            output_contract=tuple(
                deliverable.path for deliverable in plan.deliverables
            ),
            verification_contract=(
                "all source identities remain unchanged",
                "all required artifact assertions pass",
                "all required consistency assertions pass",
                "all required visual checks pass",
            ),
            fallback="repair only the failed producer, then re-verify",
            max_retries=0,
        )
    )
    build_plan(
        database,
        plan.objective_id,
        executive_steps,
        plan_id=executive_plan_id,
        source_id="v13-general-work",
    )
    active = active_plan(database, plan.objective_id)
    if active is None or active.plan_id != executive_plan_id:
        raise GeneralWorkMaterializationError(
            "General Work Executive plan is not the active plan"
        )
    invoke_fault_hook(fault_hook, "post general_work.executive_plan")
    assert_owner_controls_clear()

    join_id = next(iter(join_conditions))
    create_campaign(
        database,
        plan.objective_id,
        campaign_id=plan.campaign_id,
        max_replans=0,
        max_parallel_children=max_parallel,
        retry_budget=retry_budget,
        join_conditions=join_conditions,
        metadata=campaign_metadata,
        stop_path=effective_stop_path,
    )
    invoke_fault_hook(fault_hook, "post general_work.campaign")
    assert_owner_controls_clear()

    materialized_by_source = {
        step.source_step_id: step for step in materialized_steps
    }
    for materialized in materialized_steps:
        source_step = step_by_id[materialized.source_step_id]
        capability_id, payload, _target = prepared[source_step.step_id]
        criteria = _criteria_for_step(plan, source_step)
        output_paths = tuple(
            deliverable_by_id[output_id].path
            for output_id in source_step.output_ids
        )
        _ensure_mission(
            database,
            mission_id=materialized.mission_id,
            objective=source_step.description,
            resource_id=plan.resource_id,
            acceptance_criteria=criteria,
            next_action=f"execute General Work step {source_step.step_id}",
            plan_id=plan.plan_id,
            control_guard=assert_owner_controls_clear,
        )
        assert_owner_controls_clear()
        bind_mission_to_step(
            database,
            plan.objective_id,
            materialized.runtime_step_id,
            materialized.mission_id,
        )
        contract = MissionStepContract(
            mission_id=materialized.mission_id,
            step_id=materialized.runtime_step_id,
            version=1,
            resource_id=plan.resource_id,
            capability=capability_id,
            target_relative_path=materialized.target_relative_path,
            instructions=canonical_json(payload),
            expected_effects=(source_step.action_kind,),
            rollback={
                "strategy": (
                    "read_only_noop"
                    if get_spec(database, capability_id).semantic
                    is Semantic.READ
                    else "preserve_sources_and_reconcile_before_retry"
                ),
                "plan_id": plan.plan_id,
                "plan_hash": plan.plan_hash,
            },
            risk_class="C1",
            verification_criteria=criteria,
            expected_artifacts=output_paths,
            forbidden_paths=(".git",),
            recorded_by="supervisor",
            source="v13-general-work",
            provenance={
                "schema": MATERIALIZATION_SCHEMA,
                "plan_id": plan.plan_id,
                "plan_hash": plan.plan_hash,
                "source_step_id": source_step.step_id,
            },
        )
        assert_owner_controls_clear()
        record_step_contract(database, contract)
        declare_child_mission(
            database,
            plan.campaign_id,
            child_id=materialized.child_id,
            mission_id=materialized.mission_id,
            step_id=materialized.runtime_step_id,
            dependencies=tuple(
                materialized_by_source[dependency].child_id
                for dependency in source_step.dependencies
            ),
            join_ids=(join_id,),
            task_type="builder",
            required_capabilities=(capability_id,),
            isolation_key=materialized.target_relative_path.casefold(),
            max_retries=source_step.max_retries,
            metadata={
                "schema": MATERIALIZATION_SCHEMA,
                "plan_id": plan.plan_id,
                "plan_hash": plan.plan_hash,
                "source_step_id": source_step.step_id,
            },
            stop_path=effective_stop_path,
        )
        invoke_fault_hook(
            fault_hook,
            f"post general_work.step_materialized:{source_step.step_id}",
        )
        assert_owner_controls_clear()

    verifier_payload = canonical_json({"plan_id": plan.plan_id})
    verifier_criteria = (
        "package status is PASS",
        "source hashes and sizes match the immutable plan",
        "deliverable readback and cross-format consistency pass",
    )
    _ensure_mission(
        database,
        mission_id=verifier.mission_id,
        objective="Verify the complete General Work package",
        resource_id=plan.resource_id,
        acceptance_criteria=verifier_criteria,
        next_action=f"verify General Work plan {plan.plan_id}",
        plan_id=plan.plan_id,
        control_guard=assert_owner_controls_clear,
    )
    assert_owner_controls_clear()
    bind_mission_to_step(
        database,
        plan.objective_id,
        verifier.runtime_step_id,
        verifier.mission_id,
    )
    assert_owner_controls_clear()
    record_step_contract(
        database,
        MissionStepContract(
            mission_id=verifier.mission_id,
            step_id=verifier.runtime_step_id,
            version=1,
            resource_id=plan.resource_id,
            capability=verifier.capability_id,
            target_relative_path=plan.delivery_root,
            instructions=verifier_payload,
            expected_effects=("general_work.verify",),
            rollback={
                "strategy": "read_only_noop",
                "plan_id": plan.plan_id,
                "plan_hash": plan.plan_hash,
            },
            risk_class="C0",
            verification_criteria=verifier_criteria,
            expected_artifacts=_delivery_artifact_refs(plan),
            forbidden_paths=(".git",),
            recorded_by="supervisor",
            source="v13-general-work",
            provenance={
                "schema": MATERIALIZATION_SCHEMA,
                "plan_id": plan.plan_id,
                "plan_hash": plan.plan_hash,
                "source_step_id": "package-verification",
            },
        ),
    )
    declare_child_mission(
        database,
        plan.campaign_id,
        child_id=verifier.child_id,
        mission_id=verifier.mission_id,
        step_id=verifier.runtime_step_id,
        dependencies=tuple(
            materialized.child_id for materialized in materialized_steps
        ),
        join_ids=(join_id,),
        task_type="verifier",
        required_capabilities=(verifier.capability_id,),
        isolation_key=f"{plan.delivery_root.casefold()}:verification",
        max_retries=0,
        metadata={
            "schema": MATERIALIZATION_SCHEMA,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "source_step_id": "package-verification",
        },
        stop_path=effective_stop_path,
    )
    invoke_fault_hook(fault_hook, "post general_work.verifier_materialized")
    assert_owner_controls_clear()

    for materialized in (*materialized_steps, verifier):
        _release_mission(
            database,
            materialized.mission_id,
            next_action=(
                f"verify General Work plan {plan.plan_id}"
                if materialized is verifier
                else (
                    "execute General Work step "
                    f"{materialized.source_step_id}"
                )
            ),
            control_guard=assert_owner_controls_clear,
        )
        child = rebuild_campaign_state(database).children[materialized.child_id]
        if child.status == "PENDING":
            record_child_state(
                database,
                materialized.child_id,
                "READY",
                reason="complete General Work DAG released for execution",
                stop_path=effective_stop_path,
            )
    invoke_fault_hook(fault_hook, "post general_work.dag_released")
    assert_owner_controls_clear()

    event_payload = _materialized_event_payload(
        plan,
        executive_plan_id,
        materialized_steps,
        verifier,
    )
    assert_owner_controls_clear()
    event_sequence, inserted = database.append(
        Event.create(
            MATERIALIZED_EVENT,
            event_payload,
            source_type="supervisor",
            source_id="v13-general-work",
            correlation_id=plan.plan_id,
            idempotency_key=(
                f"{MATERIALIZED_EVENT}:{plan.plan_id}:{plan.plan_hash}"
            ),
        )
    )
    return GeneralWorkMaterialization(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        executive_plan_id=executive_plan_id,
        campaign_id=plan.campaign_id,
        steps=tuple(materialized_steps),
        verifier=verifier,
        claim_receipt=claim_receipt,
        event_sequence=event_sequence,
        inserted=inserted,
    )


__all__ = [
    "MATERIALIZATION_SCHEMA",
    "MATERIALIZED_EVENT",
    "GeneralWorkMaterialization",
    "GeneralWorkMaterializationError",
    "MaterializedStep",
    "materialize_general_work_plan",
]
