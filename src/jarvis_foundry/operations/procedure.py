"""Durable, resumable multi-step broad-operation procedures.

This module deliberately composes the canonical Foundry event ledger.  It does
not introduce a second database, mission selector, capability broker, or
effect journal.  A procedure records the plan and the durable lifecycle around
existing ActionEnvelope/effect tickets; an executor remains responsible for
using those canonical primitives.

The important recovery rule is conservative: a step that was durably started
but has no terminal observation is frozen on restart.  Callers must reconcile
the target/provider or compensate it before the procedure can continue.  A
timeout is never treated as a clean failure and a retry is never automatic.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash


class OperationError(ValueError):
    """A caller supplied an invalid operation or lifecycle transition."""


class OperationIntegrityError(OperationError):
    """The append-only operation history is inconsistent or incomplete."""


class StepStatus(StrEnum):
    PENDING = "PENDING"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FROZEN = "FROZEN"
    COMPENSATED = "COMPENSATED"


_RISK_CLASSES = frozenset({"R0", "R1", "R2", "R3", "C1", "C2", "C3"})
_REVERSIBILITY = frozenset({"reversible", "irreversible"})
_TERMINAL_OUTCOMES = frozenset({
    "COMPLETED", "FAILED", "BLOCKED", "CANCELLED", "COMPENSATED",
})
_UNRESOLVED_STEP_STATES = frozenset({StepStatus.STARTED, StepStatus.FROZEN})
_IDENTITY_ENV = re.compile(r"(?:\$\(|\$\{|%[^%]+%|\*|<[^>]+>)")


def _text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperationError(f"{field_name} must be a non-empty string")
    if any(ord(char) < 32 for char in value):
        raise OperationError(f"{field_name} contains a control character")
    return value.strip()


def _identity(value: object, *, field_name: str) -> str:
    result = _text(value, field_name=field_name)
    if _IDENTITY_ENV.search(result):
        raise OperationError(
            f"{field_name} must be an exact identity, not a wildcard or environment expression"
        )
    return result


def _strings(values: Iterable[object], *, field_name: str) -> tuple[str, ...]:
    result = tuple(_text(value, field_name=field_name) for value in values)
    if len(set(result)) != len(result):
        raise OperationError(f"{field_name} contains duplicate values")
    return result


def _mapping(value: Mapping[str, Any] | None, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise OperationError(f"{field_name} must be an object")
    # Round-trip through canonical JSON makes the event payload immutable from
    # the caller's later mutations and rejects non-JSON values before append.
    try:
        return json.loads(canonical_json(dict(value)))
    except (TypeError, ValueError) as exc:
        raise OperationError(f"{field_name} must contain JSON-safe values") from exc


def _evidence(values: Iterable[object], *, required: bool = False) -> tuple[str, ...]:
    result = _strings(values, field_name="evidence reference")
    if required and not result:
        raise OperationError("at least one evidence reference is required")
    return result


def _hash(value: Mapping[str, Any]) -> str:
    return payload_hash(dict(value))


@dataclass(frozen=True, slots=True)
class OperationStep:
    """One ordered step in a procedure plan.

    ``effect_ticket`` is the stable identity to bind to an existing
    ActionEnvelope/effect key.  The procedure layer never performs the effect
    itself.
    """

    step_id: str
    action_kind: str
    target: str
    preconditions: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    risk_class: str = "R1"
    reversibility: str = "reversible"
    postconditions: tuple[str, ...] = ()
    rollback: dict[str, Any] = field(default_factory=dict)
    compensation: dict[str, Any] = field(default_factory=dict)
    effect_ticket: str = ""
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        step_id = _identity(self.step_id, field_name="step_id")
        action_kind = _identity(self.action_kind, field_name="action_kind")
        target = _identity(self.target, field_name="target")
        if self.risk_class not in _RISK_CLASSES:
            raise OperationError(f"unknown risk class {self.risk_class!r}")
        if self.reversibility not in _REVERSIBILITY:
            raise OperationError(
                f"reversibility must be one of {sorted(_REVERSIBILITY)}"
            )
        ticket = _identity(self.effect_ticket, field_name="effect_ticket")
        preconditions = _strings(self.preconditions, field_name="precondition")
        capabilities = _strings(self.capabilities, field_name="capability")
        postconditions = _strings(self.postconditions, field_name="postcondition")
        rollback = _mapping(self.rollback, field_name="rollback")
        compensation = _mapping(self.compensation, field_name="compensation")
        evidence_refs = _evidence(self.evidence_refs)
        if not rollback and not compensation:
            raise OperationError(
                f"step {step_id} must declare rollback or compensation"
            )
        if self.reversibility == "irreversible" or self.risk_class in {"R2", "R3", "C2", "C3"}:
            if not (rollback.get("method") or rollback.get("strategy") or
                    compensation.get("method") or compensation.get("strategy")):
                raise OperationError(
                    f"step {step_id} requires an executable recovery method"
                )
        object.__setattr__(self, "step_id", step_id)
        object.__setattr__(self, "action_kind", action_kind)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "effect_ticket", ticket)
        object.__setattr__(self, "preconditions", preconditions)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "postconditions", postconditions)
        object.__setattr__(self, "rollback", rollback)
        object.__setattr__(self, "compensation", compensation)
        object.__setattr__(self, "evidence_refs", evidence_refs)

    @classmethod
    def from_mapping(cls, procedure_id: str, value: Mapping[str, Any]) -> "OperationStep":
        if not isinstance(value, Mapping):
            raise OperationError("procedure step must be an object")
        step_id = _identity(value.get("step_id"), field_name="step_id")
        return cls(
            step_id=step_id,
            action_kind=value.get("action_kind", ""),
            target=value.get("target", ""),
            preconditions=tuple(value.get("preconditions", ())),
            capabilities=tuple(value.get("capabilities", ())),
            risk_class=str(value.get("risk_class", "R1")),
            reversibility=str(value.get("reversibility", "reversible")),
            postconditions=tuple(value.get("postconditions", ())),
            rollback=value.get("rollback") or {},
            compensation=value.get("compensation") or {},
            effect_ticket=str(value.get("effect_ticket") or f"effect:{procedure_id}:{step_id}"),
            evidence_refs=tuple(value.get("evidence_refs", ())),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action_kind": self.action_kind,
            "target": self.target,
            "preconditions": list(self.preconditions),
            "capabilities": list(self.capabilities),
            "risk_class": self.risk_class,
            "reversibility": self.reversibility,
            "postconditions": list(self.postconditions),
            "rollback": self.rollback,
            "compensation": self.compensation,
            "effect_ticket": self.effect_ticket,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ProcedurePlan:
    """Immutable plan recorded by ``operation.procedure.created``."""

    procedure_id: str
    mission_id: str
    target: str
    steps: tuple[OperationStep, ...]
    preconditions: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        procedure_id = _identity(self.procedure_id, field_name="procedure_id")
        mission_id = _identity(self.mission_id, field_name="mission_id")
        target = _identity(self.target, field_name="target")
        steps = tuple(self.steps)
        if not steps:
            raise OperationError("a procedure must contain at least one step")
        step_ids = [step.step_id for step in steps]
        tickets = [step.effect_ticket for step in steps]
        if len(set(step_ids)) != len(step_ids):
            raise OperationError("procedure step ids must be unique")
        if len(set(tickets)) != len(tickets):
            raise OperationError("procedure effect tickets must be unique")
        preconditions = _strings(self.preconditions, field_name="precondition")
        capabilities = _strings(self.capabilities, field_name="capability")
        evidence_refs = _evidence(self.evidence_refs)
        metadata = _mapping(self.metadata, field_name="metadata")
        object.__setattr__(self, "procedure_id", procedure_id)
        object.__setattr__(self, "mission_id", mission_id)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "preconditions", preconditions)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "metadata", metadata)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProcedurePlan":
        if not isinstance(value, Mapping):
            raise OperationError("procedure plan must be an object")
        procedure_id = _identity(value.get("procedure_id"), field_name="procedure_id")
        return cls(
            procedure_id=procedure_id,
            mission_id=value.get("mission_id", ""),
            target=value.get("target", ""),
            steps=tuple(OperationStep.from_mapping(procedure_id, item)
                        for item in value.get("steps", ())),
            preconditions=tuple(value.get("preconditions", ())),
            capabilities=tuple(value.get("capabilities", ())),
            evidence_refs=tuple(value.get("evidence_refs", ())),
            metadata=value.get("metadata") or {},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "procedure_id": self.procedure_id,
            "mission_id": self.mission_id,
            "target": self.target,
            "steps": [step.as_dict() for step in self.steps],
            "preconditions": list(self.preconditions),
            "capabilities": list(self.capabilities),
            "evidence_refs": list(self.evidence_refs),
            "metadata": self.metadata,
        }

    @property
    def plan_hash(self) -> str:
        return _hash(self.as_dict())


@dataclass(frozen=True, slots=True)
class StepRuntime:
    step_id: str
    status: StepStatus = StepStatus.PENDING
    attempt: int = 0
    effect_ticket: str = ""
    before_state: dict[str, Any] = field(default_factory=dict)
    after_state: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    reason: str = ""
    compensation_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ProcedureState:
    plan: ProcedurePlan
    steps: tuple[StepRuntime, ...]
    current_step: str | None
    status: str
    terminal_outcome: str | None = None
    terminal_reason: str = ""
    evidence_refs: tuple[str, ...] = ()
    history_sequences: tuple[int, ...] = ()

    def step(self, step_id: str) -> StepRuntime:
        for runtime in self.steps:
            if runtime.step_id == step_id:
                return runtime
        raise KeyError(step_id)

    @property
    def next_step(self) -> OperationStep | None:
        if self.terminal_outcome or self.status in {"FROZEN", "RUNNING", "FAILED"}:
            return None
        for spec, runtime in zip(self.plan.steps, self.steps):
            if runtime.status == StepStatus.PENDING:
                return spec
        return None

    @property
    def unresolved(self) -> bool:
        return any(runtime.status in _UNRESOLVED_STEP_STATES for runtime in self.steps)


def _payload_created(plan: ProcedurePlan) -> dict[str, Any]:
    return {
        **plan.as_dict(),
        "plan_hash": plan.plan_hash,
        "current_step": plan.steps[0].step_id,
    }


def _events(database: FoundryDatabase, procedure_id: str) -> list[tuple[int, str, dict[str, Any]]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT sequence, event_type, payload_json FROM events "
            "WHERE event_type LIKE 'operation.%' "
            "AND json_extract(payload_json, '$.procedure_id') = ? "
            "ORDER BY sequence", (procedure_id,)
        ).fetchall()
    return [(int(row[0]), str(row[1]), json.loads(row[2])) for row in rows]


def _event(
    event_type: str,
    payload: dict[str, Any],
    *,
    plan: ProcedurePlan,
    key: str,
    source_id: str,
) -> Event:
    return Event.create(
        event_type,
        payload,
        source_type="supervisor",
        source_id=source_id,
        correlation_id=plan.mission_id,
        idempotency_key=key,
    )


def load_procedure(database: FoundryDatabase, procedure_id: str) -> ProcedureState:
    """Rebuild a procedure from canonical events after any process restart."""
    procedure_id = _identity(procedure_id, field_name="procedure_id")
    history = _events(database, procedure_id)
    if not history:
        raise KeyError(procedure_id)
    created = [payload for _seq, kind, payload in history
               if kind == "operation.procedure.created"]
    if len(created) != 1:
        raise OperationIntegrityError(
            f"procedure {procedure_id} must have exactly one creation event"
        )
    payload = created[0]
    plan = ProcedurePlan.from_mapping(payload)
    if payload.get("plan_hash") != plan.plan_hash:
        raise OperationIntegrityError(f"procedure {procedure_id} plan hash mismatch")
    if plan.procedure_id != procedure_id:
        raise OperationIntegrityError("procedure event identity mismatch")
    runtimes = {
        step.step_id: StepRuntime(step_id=step.step_id,
                                  effect_ticket=step.effect_ticket)
        for step in plan.steps
    }
    terminal_outcome: str | None = None
    terminal_reason = ""
    evidence: list[str] = list(plan.evidence_refs)
    for sequence, kind, item in history:
        if kind == "operation.procedure.created":
            continue
        if item.get("procedure_id") != procedure_id:
            raise OperationIntegrityError("operation history has a foreign procedure id")
        if kind == "operation.procedure.terminal":
            outcome = str(item.get("outcome", ""))
            if outcome not in _TERMINAL_OUTCOMES:
                raise OperationIntegrityError(f"unknown procedure terminal outcome {outcome!r}")
            if terminal_outcome is not None and terminal_outcome != outcome:
                raise OperationIntegrityError("procedure has conflicting terminal outcomes")
            terminal_outcome = outcome
            terminal_reason = str(item.get("reason", ""))
            evidence.extend(_evidence(item.get("evidence_refs", ())))
            continue
        step_id = item.get("step_id")
        if step_id not in runtimes:
            raise OperationIntegrityError(f"event {kind} references unknown step {step_id!r}")
        current = runtimes[step_id]
        ticket = str(item.get("effect_ticket", current.effect_ticket))
        if ticket != current.effect_ticket:
            raise OperationIntegrityError(f"step {step_id} effect ticket changed")
        if kind == "operation.step.started":
            if current.status != StepStatus.PENDING:
                continue  # an identical delivery was already projected
            before = _mapping(item.get("before_state"), field_name="before_state")
            refs = _evidence(item.get("evidence_refs", ()), required=True)
            runtimes[step_id] = StepRuntime(
                step_id=step_id, status=StepStatus.STARTED,
                attempt=int(item.get("attempt", 1)), effect_ticket=ticket,
                before_state=before, evidence_refs=refs,
            )
        elif kind in {"operation.step.completed", "operation.step.reconciled"}:
            if current.status in {StepStatus.COMPLETED, StepStatus.COMPENSATED}:
                continue
            if current.status not in {StepStatus.STARTED, StepStatus.FROZEN}:
                raise OperationIntegrityError(
                    f"{kind} for {step_id} without STARTED/FROZEN state"
                )
            after = _mapping(item.get("after_state"), field_name="after_state")
            refs = _evidence(item.get("evidence_refs", ()), required=True)
            reconciled_failed = False
            if kind == "operation.step.reconciled":
                outcome = str(item.get("outcome", ""))
                if outcome not in {"COMPLETED", "FAILED"}:
                    raise OperationIntegrityError(
                        f"unknown reconciliation outcome {outcome!r}"
                    )
                reconciled_failed = outcome == "FAILED"
            runtimes[step_id] = StepRuntime(
                step_id=step_id,
                status=StepStatus.FAILED if reconciled_failed else StepStatus.COMPLETED,
                attempt=current.attempt, effect_ticket=ticket,
                before_state=current.before_state, after_state=after,
                evidence_refs=tuple(dict.fromkeys((*current.evidence_refs, *refs))),
                reason=(
                    "reconciled provider state indicates failure"
                    if reconciled_failed else ""
                ),
            )
            evidence.extend(refs)
        elif kind == "operation.step.failed":
            if current.status in {StepStatus.FAILED, StepStatus.COMPENSATED}:
                continue
            if current.status not in {StepStatus.STARTED, StepStatus.FROZEN}:
                raise OperationIntegrityError(f"step.failed for {step_id} without active step")
            refs = _evidence(item.get("evidence_refs", ()), required=True)
            runtimes[step_id] = StepRuntime(
                step_id=step_id, status=StepStatus.FAILED,
                attempt=current.attempt, effect_ticket=ticket,
                before_state=current.before_state,
                after_state=_mapping(item.get("after_state"), field_name="after_state"),
                evidence_refs=tuple(dict.fromkeys((*current.evidence_refs, *refs))),
                reason=str(item.get("reason", "")),
            )
            evidence.extend(refs)
        elif kind == "operation.step.frozen":
            if current.status == StepStatus.FROZEN:
                continue
            if current.status != StepStatus.STARTED:
                raise OperationIntegrityError(f"step.frozen for {step_id} without STARTED state")
            refs = _evidence(item.get("evidence_refs", ()), required=True)
            runtimes[step_id] = StepRuntime(
                step_id=step_id, status=StepStatus.FROZEN,
                attempt=current.attempt, effect_ticket=ticket,
                before_state=current.before_state,
                evidence_refs=tuple(dict.fromkeys((*current.evidence_refs, *refs))),
                reason=str(item.get("reason", "")),
            )
            evidence.extend(refs)
        elif kind == "operation.step.compensated":
            if current.status == StepStatus.COMPENSATED:
                continue
            if current.status not in {StepStatus.FROZEN, StepStatus.FAILED}:
                raise OperationIntegrityError(f"step.compensated for {step_id} without frozen/failed state")
            refs = _evidence(item.get("evidence_refs", ()), required=True)
            compensation_ref = _identity(item.get("compensation_ref"), field_name="compensation_ref")
            runtimes[step_id] = StepRuntime(
                step_id=step_id, status=StepStatus.COMPENSATED,
                attempt=current.attempt, effect_ticket=ticket,
                before_state=current.before_state, after_state=current.after_state,
                evidence_refs=tuple(dict.fromkeys((*current.evidence_refs, *refs))),
                reason=str(item.get("reason", "")),
                compensation_ref=compensation_ref,
            )
            evidence.extend(refs)
        elif kind == "operation.step.reopened":
            if current.status not in {StepStatus.FAILED, StepStatus.FROZEN, StepStatus.COMPENSATED}:
                raise OperationIntegrityError(f"step.reopened for {step_id} is not recoverable")
            runtimes[step_id] = StepRuntime(
                step_id=step_id, status=StepStatus.PENDING,
                attempt=current.attempt, effect_ticket=ticket,
                evidence_refs=current.evidence_refs,
            )
        else:
            raise OperationIntegrityError(f"unknown operation event {kind}")

    ordered = tuple(runtimes[step.step_id] for step in plan.steps)
    if terminal_outcome:
        status = terminal_outcome
        current_step = None
    elif any(runtime.status == StepStatus.FROZEN for runtime in ordered):
        status = "FROZEN"
        current_step = next(runtime.step_id for runtime in ordered
                            if runtime.status == StepStatus.FROZEN)
    elif any(runtime.status == StepStatus.STARTED for runtime in ordered):
        status = "RUNNING"
        current_step = next(runtime.step_id for runtime in ordered
                            if runtime.status == StepStatus.STARTED)
    elif any(runtime.status == StepStatus.FAILED for runtime in ordered):
        status = "FAILED"
        current_step = next(runtime.step_id for runtime in ordered
                            if runtime.status == StepStatus.FAILED)
    elif all(runtime.status == StepStatus.COMPLETED for runtime in ordered):
        # A valid manager writes the terminal event atomically with the final
        # step.  Derive completion here as a safe replay fallback if an older
        # writer left the finalization event out.
        status = "COMPLETED"
        current_step = None
        terminal_outcome = "COMPLETED"
    else:
        status = "READY"
        current_step = next((runtime.step_id for runtime in ordered
                             if runtime.status == StepStatus.PENDING), None)
    return ProcedureState(
        plan=plan, steps=ordered, current_step=current_step, status=status,
        terminal_outcome=terminal_outcome, terminal_reason=terminal_reason,
        evidence_refs=tuple(dict.fromkeys(evidence)),
        history_sequences=tuple(sequence for sequence, _kind, _payload in history),
    )


def create_procedure(
    database: FoundryDatabase,
    plan: ProcedurePlan,
    *,
    source_id: str = "v7b-procedure",
) -> ProcedureState:
    """Append a plan once; identical re-delivery is restart-safe."""
    try:
        existing = load_procedure(database, plan.procedure_id)
    except KeyError:
        existing = None
    if existing is not None:
        if existing.plan.plan_hash != plan.plan_hash:
            raise OperationError(
                f"procedure {plan.procedure_id} already exists with a different plan"
            )
        return existing
    database.append(_event(
        "operation.procedure.created", _payload_created(plan), plan=plan,
        key=f"operation.procedure.created:{plan.procedure_id}", source_id=source_id,
    ))
    return load_procedure(database, plan.procedure_id)


def _require_state(database: FoundryDatabase, procedure_id: str) -> ProcedureState:
    return load_procedure(database, procedure_id)


def _step_spec(state: ProcedureState, step_id: str) -> OperationStep:
    for step in state.plan.steps:
        if step.step_id == step_id:
            return step
    raise KeyError(step_id)


def _require_refs(refs: Iterable[object], fallback: Iterable[str] = ()) -> tuple[str, ...]:
    result = _evidence(refs)
    if not result:
        result = _evidence(fallback)
    if not result:
        raise OperationError("evidence references are required for a lifecycle transition")
    return result


def _require_ticket(state: ProcedureState, step_id: str, effect_ticket: str | None) -> OperationStep:
    spec = _step_spec(state, step_id)
    if effect_ticket is not None and effect_ticket != spec.effect_ticket:
        raise OperationError(f"step {step_id} effect ticket does not match the plan")
    return spec


def start_step(
    database: FoundryDatabase,
    procedure_id: str,
    step_id: str,
    *,
    before_state: Mapping[str, Any],
    evidence_refs: Iterable[str] = (),
    effect_ticket: str | None = None,
    source_id: str = "v7b-procedure",
) -> ProcedureState:
    state = _require_state(database, procedure_id)
    if state.terminal_outcome:
        raise OperationError(f"procedure {procedure_id} is terminal: {state.terminal_outcome}")
    spec = _require_ticket(state, step_id, effect_ticket)
    runtime = state.step(step_id)
    if runtime.status == StepStatus.STARTED:
        return state
    if runtime.status != StepStatus.PENDING:
        raise OperationError(f"step {step_id} is {runtime.status}, not PENDING")
    if state.next_step is None or state.next_step.step_id != step_id:
        raise OperationError("procedure steps must start in order")
    capture = _mapping(before_state, field_name="before_state")
    if not any(capture.get(key) for key in ("ref", "hash", "identity")):
        raise OperationError("before_state must include ref, hash, or identity")
    refs = _require_refs(evidence_refs, spec.evidence_refs or state.plan.evidence_refs)
    payload = {
        "procedure_id": state.plan.procedure_id,
        "mission_id": state.plan.mission_id,
        "step_id": step_id,
        "step_index": next(index for index, item in enumerate(state.plan.steps)
                             if item.step_id == step_id),
        "effect_ticket": spec.effect_ticket,
        "attempt": runtime.attempt + 1,
        "before_state": capture,
        "evidence_refs": list(refs),
    }
    database.append(_event(
        "operation.step.started", payload, plan=state.plan,
        # The logical ticket remains stable across an explicitly compensated
        # retry, while the attempt is a distinct ledger delivery.  A second
        # start is still impossible while the first one is STARTED.
        key=(
            f"operation.step.started:{state.plan.procedure_id}:{step_id}:"
            f"{spec.effect_ticket}:attempt:{payload['attempt']}"
        ),
        source_id=source_id,
    ))
    return load_procedure(database, procedure_id)


def complete_step(
    database: FoundryDatabase,
    procedure_id: str,
    step_id: str,
    *,
    after_state: Mapping[str, Any],
    evidence_refs: Iterable[str],
    postcondition_results: Mapping[str, Any] | None = None,
    effect_ticket: str | None = None,
    source_id: str = "v7b-procedure",
) -> ProcedureState:
    state = _require_state(database, procedure_id)
    if state.terminal_outcome:
        return state
    spec = _require_ticket(state, step_id, effect_ticket)
    runtime = state.step(step_id)
    if runtime.status == StepStatus.COMPLETED:
        return state
    if runtime.status != StepStatus.STARTED:
        raise OperationError(f"step {step_id} is {runtime.status}, not STARTED")
    after = _mapping(after_state, field_name="after_state")
    refs = _require_refs(evidence_refs)
    if spec.postconditions and not postcondition_results:
        raise OperationError(f"step {step_id} requires postcondition results")
    payload = {
        "procedure_id": state.plan.procedure_id,
        "mission_id": state.plan.mission_id,
        "step_id": step_id,
        "effect_ticket": spec.effect_ticket,
        "after_state": after,
        "postcondition_results": _mapping(postcondition_results, field_name="postcondition_results"),
        "evidence_refs": list(refs),
    }
    events = [_event(
        "operation.step.completed", payload, plan=state.plan,
        key=f"operation.step.completed:{state.plan.procedure_id}:{step_id}:{payload_hash(payload)}",
        source_id=source_id,
    )]
    if state.steps[-1].step_id == step_id:
        terminal_payload = {
            "procedure_id": state.plan.procedure_id,
            "mission_id": state.plan.mission_id,
            "outcome": "COMPLETED",
            "reason": "all procedure steps completed",
            "evidence_refs": list(refs),
            "current_step": step_id,
        }
        events.append(_event(
            "operation.procedure.terminal", terminal_payload, plan=state.plan,
            key=f"operation.procedure.terminal:{state.plan.procedure_id}:COMPLETED",
            source_id=source_id,
        ))
    database.append_events(events)
    return load_procedure(database, procedure_id)


def fail_step(
    database: FoundryDatabase,
    procedure_id: str,
    step_id: str,
    *,
    reason: str,
    evidence_refs: Iterable[str],
    after_state: Mapping[str, Any] | None = None,
    terminal: bool = False,
    effect_ticket: str | None = None,
    source_id: str = "v7b-procedure",
) -> ProcedureState:
    state = _require_state(database, procedure_id)
    if state.terminal_outcome:
        return state
    spec = _require_ticket(state, step_id, effect_ticket)
    runtime = state.step(step_id)
    if runtime.status == StepStatus.FAILED:
        return state
    if runtime.status not in {StepStatus.STARTED, StepStatus.FROZEN}:
        raise OperationError(f"step {step_id} is {runtime.status}, not active")
    reason = _text(reason, field_name="reason")
    refs = _require_refs(evidence_refs)
    payload = {
        "procedure_id": state.plan.procedure_id,
        "mission_id": state.plan.mission_id,
        "step_id": step_id,
        "effect_ticket": spec.effect_ticket,
        "reason": reason,
        "after_state": _mapping(after_state, field_name="after_state"),
        "evidence_refs": list(refs),
    }
    events = [_event(
        "operation.step.failed", payload, plan=state.plan,
        key=f"operation.step.failed:{state.plan.procedure_id}:{step_id}:{payload_hash(payload)}",
        source_id=source_id,
    )]
    if terminal:
        terminal_payload = {
            "procedure_id": state.plan.procedure_id,
            "mission_id": state.plan.mission_id,
            "outcome": "FAILED",
            "reason": reason,
            "evidence_refs": list(refs),
            "current_step": step_id,
        }
        events.append(_event(
            "operation.procedure.terminal", terminal_payload, plan=state.plan,
            key=f"operation.procedure.terminal:{state.plan.procedure_id}:FAILED",
            source_id=source_id,
        ))
    database.append_events(events)
    return load_procedure(database, procedure_id)


def freeze_step(
    database: FoundryDatabase,
    procedure_id: str,
    step_id: str,
    *,
    reason: str,
    evidence_refs: Iterable[str],
    effect_ticket: str | None = None,
    source_id: str = "v7b-procedure",
) -> ProcedureState:
    state = _require_state(database, procedure_id)
    if state.terminal_outcome:
        return state
    spec = _require_ticket(state, step_id, effect_ticket)
    runtime = state.step(step_id)
    if runtime.status == StepStatus.FROZEN:
        return state
    if runtime.status != StepStatus.STARTED:
        raise OperationError(f"step {step_id} is {runtime.status}, not STARTED")
    reason = _text(reason, field_name="reason")
    refs = _require_refs(evidence_refs)
    payload = {
        "procedure_id": state.plan.procedure_id,
        "mission_id": state.plan.mission_id,
        "step_id": step_id,
        "effect_ticket": spec.effect_ticket,
        "reason": reason,
        "classification": "UNKNOWN",
        "evidence_refs": list(refs),
    }
    database.append(_event(
        "operation.step.frozen", payload, plan=state.plan,
        key=f"operation.step.frozen:{state.plan.procedure_id}:{step_id}:{payload_hash(payload)}",
        source_id=source_id,
    ))
    return load_procedure(database, procedure_id)


def reconcile_step(
    database: FoundryDatabase,
    procedure_id: str,
    step_id: str,
    *,
    outcome: str,
    after_state: Mapping[str, Any],
    evidence_refs: Iterable[str],
    provider_reference: str,
    effect_ticket: str | None = None,
    source_id: str = "v7b-procedure",
) -> ProcedureState:
    state = _require_state(database, procedure_id)
    if state.terminal_outcome:
        return state
    if outcome not in {"COMPLETED", "FAILED"}:
        raise OperationError("reconciliation outcome must be COMPLETED or FAILED")
    spec = _require_ticket(state, step_id, effect_ticket)
    runtime = state.step(step_id)
    if runtime.status != StepStatus.FROZEN:
        raise OperationError(f"step {step_id} is {runtime.status}, not FROZEN")
    provider_reference = _identity(provider_reference, field_name="provider_reference")
    after = _mapping(after_state, field_name="after_state")
    refs = _require_refs(evidence_refs)
    payload = {
        "procedure_id": state.plan.procedure_id,
        "mission_id": state.plan.mission_id,
        "step_id": step_id,
        "effect_ticket": spec.effect_ticket,
        "outcome": outcome,
        "after_state": after,
        "provider_reference": provider_reference,
        "evidence_refs": list(refs),
    }
    events = [_event(
        "operation.step.reconciled", payload, plan=state.plan,
        key=f"operation.step.reconciled:{state.plan.procedure_id}:{step_id}:{payload_hash(payload)}",
        source_id=source_id,
    )]
    if outcome == "FAILED" and state.steps[-1].step_id == step_id:
        events.append(_event(
            "operation.procedure.terminal", {
                "procedure_id": state.plan.procedure_id,
                "mission_id": state.plan.mission_id,
                "outcome": "FAILED",
                "reason": "reconciled provider state indicates failure",
                "evidence_refs": list(refs),
                "current_step": step_id,
            }, plan=state.plan,
            key=f"operation.procedure.terminal:{state.plan.procedure_id}:FAILED",
            source_id=source_id,
        ))
    if outcome == "COMPLETED" and state.steps[-1].step_id == step_id:
        events.append(_event(
            "operation.procedure.terminal", {
                "procedure_id": state.plan.procedure_id,
                "mission_id": state.plan.mission_id,
                "outcome": "COMPLETED",
                "reason": "ambiguous step reconciled as completed",
                "evidence_refs": list(refs),
                "current_step": step_id,
            }, plan=state.plan,
            key=f"operation.procedure.terminal:{state.plan.procedure_id}:COMPLETED",
            source_id=source_id,
        ))
    database.append_events(events)
    return load_procedure(database, procedure_id)


def compensate_step(
    database: FoundryDatabase,
    procedure_id: str,
    step_id: str,
    *,
    compensation_ref: str,
    evidence_refs: Iterable[str],
    reason: str = "compensation completed",
    effect_ticket: str | None = None,
    source_id: str = "v7b-procedure",
) -> ProcedureState:
    state = _require_state(database, procedure_id)
    if state.terminal_outcome:
        return state
    spec = _require_ticket(state, step_id, effect_ticket)
    runtime = state.step(step_id)
    if runtime.status == StepStatus.COMPENSATED:
        return state
    if runtime.status not in {StepStatus.FROZEN, StepStatus.FAILED}:
        raise OperationError(f"step {step_id} is {runtime.status}, not compensatable")
    if not spec.compensation and not spec.rollback:
        raise OperationError(f"step {step_id} has no compensation or rollback contract")
    compensation_ref = _identity(compensation_ref, field_name="compensation_ref")
    refs = _require_refs(evidence_refs)
    reason = _text(reason, field_name="reason")
    payload = {
        "procedure_id": state.plan.procedure_id,
        "mission_id": state.plan.mission_id,
        "step_id": step_id,
        "effect_ticket": spec.effect_ticket,
        "compensation_ref": compensation_ref,
        "reason": reason,
        "evidence_refs": list(refs),
    }
    database.append(_event(
        "operation.step.compensated", payload, plan=state.plan,
        key=f"operation.step.compensated:{state.plan.procedure_id}:{step_id}:{payload_hash(payload)}",
        source_id=source_id,
    ))
    return load_procedure(database, procedure_id)


def reopen_step(
    database: FoundryDatabase,
    procedure_id: str,
    step_id: str,
    *,
    reason: str,
    evidence_refs: Iterable[str],
    source_id: str = "v7b-procedure",
) -> ProcedureState:
    state = _require_state(database, procedure_id)
    if state.terminal_outcome:
        raise OperationError(f"procedure {procedure_id} is terminal")
    spec = _step_spec(state, step_id)
    runtime = state.step(step_id)
    if runtime.status not in {StepStatus.FAILED, StepStatus.FROZEN, StepStatus.COMPENSATED}:
        raise OperationError(f"step {step_id} is not recoverable from {runtime.status}")
    reason = _text(reason, field_name="reason")
    refs = _require_refs(evidence_refs)
    payload = {
        "procedure_id": state.plan.procedure_id,
        "mission_id": state.plan.mission_id,
        "step_id": step_id,
        "effect_ticket": spec.effect_ticket,
        "reason": reason,
        "evidence_refs": list(refs),
    }
    database.append(_event(
        "operation.step.reopened", payload, plan=state.plan,
        key=f"operation.step.reopened:{state.plan.procedure_id}:{step_id}:{payload_hash(payload)}",
        source_id=source_id,
    ))
    return load_procedure(database, procedure_id)


def terminate_procedure(
    database: FoundryDatabase,
    procedure_id: str,
    *,
    outcome: str,
    reason: str,
    evidence_refs: Iterable[str],
    source_id: str = "v7b-procedure",
) -> ProcedureState:
    state = _require_state(database, procedure_id)
    if state.terminal_outcome:
        if state.terminal_outcome == outcome:
            return state
        raise OperationError("procedure already has a different terminal outcome")
    if outcome not in _TERMINAL_OUTCOMES:
        raise OperationError(f"unknown terminal outcome {outcome!r}")
    if outcome == "COMPLETED" and any(runtime.status != StepStatus.COMPLETED
                                      for runtime in state.steps):
        raise OperationError("procedure cannot complete while steps remain unresolved")
    if any(runtime.status in _UNRESOLVED_STEP_STATES for runtime in state.steps) and outcome not in {"BLOCKED", "FROZEN", "CANCELLED"}:
        raise OperationError("unresolved in-flight steps require freeze/block/cancel outcome")
    reason = _text(reason, field_name="reason")
    refs = _require_refs(evidence_refs)
    payload = {
        "procedure_id": state.plan.procedure_id,
        "mission_id": state.plan.mission_id,
        "outcome": outcome,
        "reason": reason,
        "evidence_refs": list(refs),
        "current_step": state.current_step,
    }
    database.append(_event(
        "operation.procedure.terminal", payload, plan=state.plan,
        key=f"operation.procedure.terminal:{state.plan.procedure_id}:{outcome}",
        source_id=source_id,
    ))
    return load_procedure(database, procedure_id)


def resume_procedure(
    database: FoundryDatabase,
    procedure_id: str,
    *,
    source_id: str = "v7b-recovery",
) -> ProcedureState:
    """Recover after restart without replaying an ambiguous effect.

    A started step is frozen exactly once.  The caller must use
    :func:`reconcile_step` or :func:`compensate_step` before reopening it.
    """
    state = _require_state(database, procedure_id)
    if state.terminal_outcome or state.status != "RUNNING":
        return state
    step_id = state.current_step
    if step_id is None:
        return state
    refs = (f"recovery:restart:{procedure_id}:{step_id}",)
    return freeze_step(
        database, procedure_id, step_id,
        reason="process restart observed a started step without a terminal observation",
        evidence_refs=refs,
        source_id=source_id,
    )
