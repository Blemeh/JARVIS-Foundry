"""Typed, serializable data shapes for the unattended acceptance harness.

The harness consumes these records and emits them as evidence. They are not
JARVIS authority records and never replace the canonical event ledger.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


class ClockMode(StrEnum):
    REAL_TIME = "REAL_TIME"
    ACCELERATED_DISPOSABLE = "ACCELERATED_DISPOSABLE"


class VerdictStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"
    ABORTED = "ABORTED"
    INCOMPLETE = "INCOMPLETE"
    NOT_TESTED = "NOT_TESTED"


def _string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _finite_number(value: Any, field_name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise ValueError(f"{field_name} must be finite and >= {minimum}")
    return number


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _sequence(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    return tuple(_string(item, field_name) for item in value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    return value


@dataclass(frozen=True, slots=True)
class ResourceEnvelope:
    max_event_count: int = 100_000
    max_db_bytes: int = 64 * 1024 * 1024
    max_retry_count: int = 10_000
    max_provider_cost: float = 5.0
    max_processes: int = 512
    max_workers: int = 64
    max_task_count: int = 64
    max_cpu_seconds: float | None = None
    max_memory_bytes: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_event_count", "max_db_bytes", "max_retry_count", "max_processes",
            "max_workers", "max_task_count",
        ):
            _nonnegative_int(getattr(self, name), name)
        _finite_number(self.max_provider_cost, "max_provider_cost")
        if self.max_cpu_seconds is not None:
            _finite_number(self.max_cpu_seconds, "max_cpu_seconds")
        if self.max_memory_bytes is not None:
            _nonnegative_int(self.max_memory_bytes, "max_memory_bytes")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ResourceEnvelope":
        data = _mapping(value, "resource_envelope")
        fields = {
            "max_event_count", "max_db_bytes", "max_retry_count",
            "max_provider_cost", "max_processes", "max_workers", "max_task_count",
            "max_cpu_seconds", "max_memory_bytes",
        }
        unknown = sorted(set(data) - fields)
        if unknown:
            raise ValueError(f"resource_envelope has unknown fields: {unknown}")
        defaults = cls()
        return cls(**{name: data.get(name, getattr(defaults, name)) for name in fields})

    def to_mapping(self) -> dict[str, Any]:
        return {
            "max_event_count": self.max_event_count,
            "max_db_bytes": self.max_db_bytes,
            "max_retry_count": self.max_retry_count,
            "max_provider_cost": self.max_provider_cost,
            "max_processes": self.max_processes,
            "max_workers": self.max_workers,
            "max_task_count": self.max_task_count,
            "max_cpu_seconds": self.max_cpu_seconds,
            "max_memory_bytes": self.max_memory_bytes,
        }

    def violations(self, sample: Mapping[str, Any]) -> list[str]:
        limits = {
            "event_count": self.max_event_count,
            "db_bytes": self.max_db_bytes,
            "retry_count": self.max_retry_count,
            "provider_cost": self.max_provider_cost,
            "process_count": self.max_processes,
            "worker_count": self.max_workers,
            "task_count": self.max_task_count,
            "cpu_seconds": self.max_cpu_seconds,
            "memory_bytes": self.max_memory_bytes,
        }
        return [
            f"{name}={sample[name]} exceeds {limit}"
            for name, limit in limits.items()
            if limit is not None and sample.get(name) is not None
            and float(sample[name]) > float(limit)
        ]


@dataclass(frozen=True, slots=True)
class FaultSpec:
    fault_id: str
    at_seconds: float
    kind: str
    target: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _string(self.fault_id, "fault_id")
        _finite_number(self.at_seconds, "at_seconds")
        _string(self.kind, "kind")
        _string(self.target, "target")
        if not isinstance(self.parameters, Mapping):
            raise ValueError("parameters must be an object")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FaultSpec":
        data = _mapping(value, "scheduled_fault")
        required = {"fault_id", "at_seconds", "kind", "target"}
        missing = sorted(required - set(data))
        if missing:
            raise ValueError(f"scheduled_fault missing fields: {missing}")
        return cls(
            fault_id=_string(data["fault_id"], "fault_id"),
            at_seconds=_finite_number(data["at_seconds"], "at_seconds"),
            kind=_string(data["kind"], "kind"),
            target=_string(data["target"], "target"),
            parameters=_mapping(data.get("parameters"), "parameters"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id,
            "at_seconds": self.at_seconds,
            "kind": self.kind,
            "target": self.target,
            "parameters": _json_safe(self.parameters),
        }


_SCENARIO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,127}$")


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    scenario_id: str
    duration_seconds: float
    clock_mode: ClockMode
    seed: int
    initial_state: Mapping[str, Any]
    required_capabilities: tuple[str, ...]
    scheduled_faults: tuple[FaultSpec, ...]
    expected_invariants: tuple[str, ...]
    resource_envelope: ResourceEnvelope
    owner_spam_threshold: int
    busywork_threshold: int
    restart_checkpoints: tuple[str, ...]
    required_end_state: str
    rollback_requirement: str
    evidence_output: str
    sample_interval_seconds: float = 60.0
    wall_time_invariants: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _SCENARIO_ID.fullmatch(self.scenario_id):
            raise ValueError("scenario_id must be 2-128 safe filename characters")
        _finite_number(self.duration_seconds, "duration_seconds", minimum=0.001)
        if not isinstance(self.clock_mode, ClockMode):
            raise ValueError("clock_mode must be a ClockMode")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        if not isinstance(self.initial_state, Mapping):
            raise ValueError("initial_state must be an object")
        _finite_number(self.sample_interval_seconds, "sample_interval_seconds", minimum=0.001)
        for name in ("owner_spam_threshold", "busywork_threshold"):
            _nonnegative_int(getattr(self, name), name)
        ids = [fault.fault_id for fault in self.scheduled_faults]
        if len(ids) != len(set(ids)):
            raise ValueError("scheduled_faults must have unique fault_id values")
        if any(fault.at_seconds > self.duration_seconds for fault in self.scheduled_faults):
            raise ValueError("scheduled fault cannot occur after scenario duration")

    @property
    def ordered_faults(self) -> tuple[FaultSpec, ...]:
        return tuple(sorted(self.scheduled_faults, key=lambda item: (item.at_seconds, item.fault_id)))

    @property
    def required_fault_ids(self) -> tuple[str, ...]:
        return tuple(
            fault.fault_id for fault in self.ordered_faults
            if bool(fault.parameters.get("required", True))
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScenarioSpec":
        data = _mapping(value, "scenario")
        required = {
            "scenario_id", "duration_seconds", "clock_mode", "seed", "initial_state",
            "required_capabilities", "scheduled_faults", "expected_invariants",
            "resource_envelope", "owner_spam_threshold", "busywork_threshold",
            "restart_checkpoints", "required_end_state", "rollback_requirement",
            "evidence_output",
        }
        missing = sorted(required - set(data))
        if missing:
            raise ValueError(f"scenario missing fields: {missing}")
        faults_value = data["scheduled_faults"]
        if not isinstance(faults_value, (list, tuple)):
            raise ValueError("scheduled_faults must be a list")
        return cls(
            scenario_id=_string(data["scenario_id"], "scenario_id"),
            duration_seconds=_finite_number(data["duration_seconds"], "duration_seconds", minimum=0.001),
            clock_mode=ClockMode(_string(data["clock_mode"], "clock_mode")),
            seed=int(data["seed"]),
            initial_state=_mapping(data["initial_state"], "initial_state"),
            required_capabilities=_sequence(data["required_capabilities"], "required_capabilities"),
            scheduled_faults=tuple(FaultSpec.from_mapping(item) for item in faults_value),
            expected_invariants=_sequence(data["expected_invariants"], "expected_invariants"),
            resource_envelope=ResourceEnvelope.from_mapping(data["resource_envelope"]),
            owner_spam_threshold=_nonnegative_int(data["owner_spam_threshold"], "owner_spam_threshold"),
            busywork_threshold=_nonnegative_int(data["busywork_threshold"], "busywork_threshold"),
            restart_checkpoints=_sequence(data["restart_checkpoints"], "restart_checkpoints"),
            required_end_state=_string(data["required_end_state"], "required_end_state"),
            rollback_requirement=_string(data["rollback_requirement"], "rollback_requirement"),
            evidence_output=_string(data["evidence_output"], "evidence_output"),
            sample_interval_seconds=_finite_number(
                data.get("sample_interval_seconds", 60.0),
                "sample_interval_seconds", minimum=0.001,
            ),
            wall_time_invariants=_sequence(data.get("wall_time_invariants"), "wall_time_invariants"),
        )

    @classmethod
    def from_path(cls, path: str | Path) -> "ScenarioSpec":
        source = Path(path)
        text = source.read_text(encoding="utf-8")
        if source.suffix.lower() == ".json":
            value = json.loads(text)
        else:
            try:
                import yaml
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise RuntimeError("YAML scenarios require PyYAML") from exc
            value = yaml.safe_load(text)
        return cls.from_mapping(value)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "duration_seconds": self.duration_seconds,
            "clock_mode": self.clock_mode.value,
            "seed": self.seed,
            "initial_state": _json_safe(self.initial_state),
            "required_capabilities": list(self.required_capabilities),
            "scheduled_faults": [fault.to_mapping() for fault in self.ordered_faults],
            "expected_invariants": list(self.expected_invariants),
            "resource_envelope": self.resource_envelope.to_mapping(),
            "owner_spam_threshold": self.owner_spam_threshold,
            "busywork_threshold": self.busywork_threshold,
            "restart_checkpoints": list(self.restart_checkpoints),
            "required_end_state": self.required_end_state,
            "rollback_requirement": self.rollback_requirement,
            "evidence_output": self.evidence_output,
            "sample_interval_seconds": self.sample_interval_seconds,
            "wall_time_invariants": list(self.wall_time_invariants),
        }


@dataclass(frozen=True, slots=True)
class RunCheckpoint:
    run_id: str
    scenario_id: str
    phase: str
    logical_time_seconds: float
    next_fault_index: int
    fired_fault_ids: tuple[str, ...]
    sampled_invariants: tuple[str, ...]
    artifacts: Mapping[str, Any]
    jarvis_identities: Mapping[str, Any]
    next_checkpoint: str | None
    status: str
    saved_at: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "phase": self.phase,
            "logical_time_seconds": self.logical_time_seconds,
            "next_fault_index": self.next_fault_index,
            "fired_fault_ids": list(self.fired_fault_ids),
            "sampled_invariants": list(self.sampled_invariants),
            "artifacts": _json_safe(self.artifacts),
            "jarvis_identities": _json_safe(self.jarvis_identities),
            "next_checkpoint": self.next_checkpoint,
            "status": self.status,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RunCheckpoint":
        data = _mapping(value, "checkpoint")
        return cls(
            run_id=_string(data["run_id"], "run_id"),
            scenario_id=_string(data["scenario_id"], "scenario_id"),
            phase=_string(data["phase"], "phase"),
            logical_time_seconds=_finite_number(data["logical_time_seconds"], "logical_time_seconds"),
            next_fault_index=_nonnegative_int(data["next_fault_index"], "next_fault_index"),
            fired_fault_ids=_sequence(data.get("fired_fault_ids"), "fired_fault_ids"),
            sampled_invariants=_sequence(data.get("sampled_invariants"), "sampled_invariants"),
            artifacts=_mapping(data.get("artifacts"), "artifacts"),
            jarvis_identities=_mapping(data.get("jarvis_identities"), "jarvis_identities"),
            next_checkpoint=(str(data["next_checkpoint"]) if data.get("next_checkpoint") else None),
            status=_string(data["status"], "status"),
            saved_at=_string(data["saved_at"], "saved_at"),
        )


@dataclass(frozen=True, slots=True)
class FaultRecord:
    fault_id: str
    kind: str
    target: str
    scheduled_at_seconds: float
    fired_at_seconds: float
    phase: str
    injected: bool
    status: str
    expected: str
    observed: Mapping[str, Any]
    containment: str
    recovery: str
    evidence_refs: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id,
            "kind": self.kind,
            "target": self.target,
            "scheduled_at_seconds": self.scheduled_at_seconds,
            "fired_at_seconds": self.fired_at_seconds,
            "phase": self.phase,
            "injected": self.injected,
            "status": self.status,
            "expected": self.expected,
            "observed": _json_safe(self.observed),
            "containment": self.containment,
            "recovery": self.recovery,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class InvariantRecord:
    invariant_id: str
    observed_at: str
    status: str
    value: Any
    required: bool
    wall_time_required: bool
    reason: str
    evidence_refs: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "invariant_id": self.invariant_id,
            "observed_at": self.observed_at,
            "status": self.status,
            "value": _json_safe(self.value),
            "required": self.required,
            "wall_time_required": self.wall_time_required,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ResourceSample:
    observed_at: str
    logical_time_seconds: float
    values: Mapping[str, Any]
    violations: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "logical_time_seconds": self.logical_time_seconds,
            "values": _json_safe(self.values),
            "violations": list(self.violations),
        }
