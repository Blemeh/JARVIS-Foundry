"""Immutable, deterministic data contracts for General Work planning."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..events import canonical_json

SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

ARTIFACT_ASSERTION_KINDS = frozenset({
    "exists",
    "sha256",
    "nonempty",
    "text_contains",
    "row_count",
    "page_count",
    "slide_count",
    "cell_equals",
    "json_value",
})
CONSISTENCY_OPERATORS = frozenset({"equals", "contains"})
SOURCE_PRESERVATION_POLICIES = frozenset({
    "PRESERVE_ORIGINALS",
    "ALLOW_DERIVED_COPIES",
})

# This policy contains only bounded file, data, and document actions. Callers
# may supply a narrower catalog snapshot when constructing a plan.
GENERAL_WORK_ACTION_KINDS = frozenset({
    "file.read",
    "file.write",
    "file.replace",
    "file.append",
    "file.inspect",
    "file.search",
    "file.copy",
    "file.move",
    "file.rename",
    "dir.list",
    "system.diagnose",
    "system.runtime",
    "artifact.parse",
    "artifact.table.merge",
    "artifact.table.summary",
    "artifact.table.transform",
    "artifact.table.export",
    "artifact.xlsx.create",
    "artifact.xlsx.update",
    "artifact.docx.create",
    "artifact.docx.compose",
    "artifact.docx.edit",
    "artifact.docx.verify",
    "artifact.pdf.create",
    "artifact.pdf.copy",
    "artifact.pdf.combine",
    "artifact.pdf.split",
    "artifact.pptx.create",
    "artifact.pptx.copy",
    "artifact.pptx.update",
    "artifact.image.transform",
})


class GeneralWorkModelError(ValueError):
    """Raised when a General Work contract is malformed or unsafe."""


def _text(value: object, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise GeneralWorkModelError(f"{field_name} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        raise GeneralWorkModelError(f"{field_name} must be non-empty")
    if any(ord(char) < 32 for char in result):
        raise GeneralWorkModelError(f"{field_name} contains a control character")
    return result


def _timestamp(value: object, field_name: str) -> str:
    raw = _text(value, field_name)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise GeneralWorkModelError(f"{field_name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise GeneralWorkModelError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _relative_path(value: object, field_name: str) -> str:
    raw = _text(value, field_name).replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw.startswith("//"):
        raise GeneralWorkModelError(f"{field_name} must be relative")
    if any(part == ".." for part in path.parts):
        raise GeneralWorkModelError(f"{field_name} cannot escape its resource root")
    if any(":" in part for part in path.parts):
        raise GeneralWorkModelError(f"{field_name} cannot contain a drive or stream")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts:
        raise GeneralWorkModelError(f"{field_name} must identify a file")
    return "/".join(parts)


def _format(value: object, field_name: str) -> str:
    result = _text(value, field_name).lower()
    return result[1:] if result.startswith(".") else result


def _integer(value: object, field_name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise GeneralWorkModelError(
            f"{field_name} must be an integer greater than or equal to {minimum}"
        )
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise GeneralWorkModelError(f"{field_name} must be boolean")
    return value


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GeneralWorkModelError(f"{field_name} must be an object")
    return value


def _records(
    value: object,
    record_type: type,
    field_name: str,
) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise GeneralWorkModelError(f"{field_name} must be a list")
    records = tuple(value)
    if any(not isinstance(item, record_type) for item in records):
        raise GeneralWorkModelError(
            f"{field_name} must contain only {record_type.__name__} records"
        )
    return records


def _id_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise GeneralWorkModelError(f"{field_name} must be a list of identifiers")
    result = tuple(_text(item, f"{field_name} item") for item in value)
    if len(set(result)) != len(result):
        raise GeneralWorkModelError(f"{field_name} cannot contain duplicates")
    return tuple(sorted(result))


def _freeze_json(value: object, field_name: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GeneralWorkModelError(f"{field_name} cannot contain a non-finite number")
        return value
    if isinstance(value, Mapping):
        items = tuple(value.items())
        if any(not isinstance(key, str) for key, _ in items):
            raise GeneralWorkModelError(f"{field_name} object keys must be strings")
        normalized: dict[str, Any] = {}
        for key, item in sorted(items, key=lambda pair: pair[0]):
            normalized[key] = _freeze_json(item, f"{field_name}.{key}")
        return MappingProxyType(normalized)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )
    raise GeneralWorkModelError(f"{field_name} must contain only JSON-compatible values")


def _freeze_object(value: object, field_name: str) -> Mapping[str, Any]:
    mapping = _mapping(value, field_name)
    frozen = _freeze_json(mapping, field_name)
    if not isinstance(frozen, Mapping):
        raise GeneralWorkModelError(f"{field_name} must be an object")
    return frozen


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _payload(value: object, field_name: str) -> Mapping[str, Any]:
    return _mapping(value, f"{field_name} payload")


def _unique_index(records: Iterable[Any], attribute: str, field_name: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for record in records:
        identifier = getattr(record, attribute)
        if identifier in result:
            raise GeneralWorkModelError(f"{field_name} identifiers must be unique")
        result[identifier] = record
    return result


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    source_id: str
    path: str
    sha256: str
    format: str
    size_bytes: int
    captured_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id"))
        object.__setattr__(self, "path", _relative_path(self.path, "source path"))
        if not isinstance(self.sha256, str) or not _SHA256_PATTERN.fullmatch(self.sha256):
            raise GeneralWorkModelError("sha256 must be a lowercase 64-hex digest")
        object.__setattr__(self, "format", _format(self.format, "source format"))
        object.__setattr__(
            self, "size_bytes", _integer(self.size_bytes, "size_bytes", minimum=0)
        )
        object.__setattr__(
            self, "captured_at", _timestamp(self.captured_at, "captured_at")
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "path": self.path,
            "sha256": self.sha256,
            "format": self.format,
            "size_bytes": self.size_bytes,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SourceIdentity:
        value = _payload(payload, "source identity")
        return cls(
            source_id=value.get("source_id"),
            path=value.get("path"),
            sha256=value.get("sha256"),
            format=value.get("format"),
            size_bytes=value.get("size_bytes"),
            captured_at=value.get("captured_at"),
        )


@dataclass(frozen=True, slots=True)
class DeliverableSpec:
    deliverable_id: str
    path: str
    format: str
    source_ids: tuple[str, ...]
    producer_step_id: str
    verification_assertion_ids: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    quality_requirements: tuple[str, ...] = ()
    style_requirements: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "deliverable_id", _text(self.deliverable_id, "deliverable_id")
        )
        object.__setattr__(self, "path", _relative_path(self.path, "deliverable path"))
        object.__setattr__(self, "format", _format(self.format, "deliverable format"))
        object.__setattr__(
            self, "source_ids", _id_tuple(self.source_ids, "deliverable source_ids")
        )
        object.__setattr__(
            self, "producer_step_id", _text(self.producer_step_id, "producer_step_id")
        )
        object.__setattr__(
            self,
            "verification_assertion_ids",
            _id_tuple(
                self.verification_assertion_ids,
                "deliverable verification_assertion_ids",
            ),
        )
        object.__setattr__(self, "metadata", _freeze_object(self.metadata, "metadata"))
        object.__setattr__(
            self,
            "quality_requirements",
            _id_tuple(self.quality_requirements, "deliverable quality_requirements"),
        )
        object.__setattr__(
            self,
            "style_requirements",
            _freeze_object(self.style_requirements, "style_requirements"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "deliverable_id": self.deliverable_id,
            "path": self.path,
            "format": self.format,
            "source_ids": list(self.source_ids),
            "producer_step_id": self.producer_step_id,
            "verification_assertion_ids": list(self.verification_assertion_ids),
            "metadata": _thaw_json(self.metadata),
            "quality_requirements": list(self.quality_requirements),
            "style_requirements": _thaw_json(self.style_requirements),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> DeliverableSpec:
        value = _payload(payload, "deliverable")
        return cls(
            deliverable_id=value.get("deliverable_id"),
            path=value.get("path"),
            format=value.get("format"),
            source_ids=value.get("source_ids", []),
            producer_step_id=value.get("producer_step_id"),
            verification_assertion_ids=value.get("verification_assertion_ids", []),
            metadata=value.get("metadata", {}),
            quality_requirements=value.get("quality_requirements", []),
            style_requirements=value.get("style_requirements", {}),
        )


@dataclass(frozen=True, slots=True)
class WorkStep:
    step_id: str
    description: str
    action_kind: str
    payload: Mapping[str, Any]
    dependencies: tuple[str, ...]
    source_ids: tuple[str, ...]
    output_ids: tuple[str, ...]
    verification_assertion_ids: tuple[str, ...]
    max_retries: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _text(self.step_id, "step_id"))
        object.__setattr__(self, "description", _text(self.description, "description"))
        object.__setattr__(self, "action_kind", _text(self.action_kind, "action_kind"))
        object.__setattr__(self, "payload", _freeze_object(self.payload, "payload"))
        for attribute in (
            "dependencies",
            "source_ids",
            "output_ids",
            "verification_assertion_ids",
        ):
            object.__setattr__(
                self,
                attribute,
                _id_tuple(getattr(self, attribute), f"step {attribute}"),
            )
        object.__setattr__(
            self, "max_retries", _integer(self.max_retries, "max_retries", minimum=0)
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "action_kind": self.action_kind,
            "payload": _thaw_json(self.payload),
            "dependencies": list(self.dependencies),
            "source_ids": list(self.source_ids),
            "output_ids": list(self.output_ids),
            "verification_assertion_ids": list(self.verification_assertion_ids),
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> WorkStep:
        value = _payload(payload, "work step")
        return cls(
            step_id=value.get("step_id"),
            description=value.get("description"),
            action_kind=value.get("action_kind"),
            payload=value.get("payload", {}),
            dependencies=value.get("dependencies", []),
            source_ids=value.get("source_ids", []),
            output_ids=value.get("output_ids", []),
            verification_assertion_ids=value.get(
                "verification_assertion_ids", []
            ),
            max_retries=value.get("max_retries", 0),
        )


@dataclass(frozen=True, slots=True)
class ArtifactAssertion:
    assertion_id: str
    deliverable_id: str
    kind: str
    expected: Any
    selector: str
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "assertion_id", _text(self.assertion_id, "assertion_id")
        )
        object.__setattr__(
            self, "deliverable_id", _text(self.deliverable_id, "deliverable_id")
        )
        object.__setattr__(self, "kind", _text(self.kind, "assertion kind"))
        if self.kind not in ARTIFACT_ASSERTION_KINDS:
            raise GeneralWorkModelError(f"unsupported artifact assertion kind: {self.kind}")
        object.__setattr__(
            self, "expected", _freeze_json(self.expected, "assertion expected")
        )
        object.__setattr__(
            self, "selector", _text(self.selector, "assertion selector", allow_empty=True)
        )
        object.__setattr__(self, "required", _boolean(self.required, "required"))

    def to_payload(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "deliverable_id": self.deliverable_id,
            "kind": self.kind,
            "expected": _thaw_json(self.expected),
            "selector": self.selector,
            "required": self.required,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ArtifactAssertion:
        value = _payload(payload, "artifact assertion")
        return cls(
            assertion_id=value.get("assertion_id"),
            deliverable_id=value.get("deliverable_id"),
            kind=value.get("kind"),
            expected=value.get("expected"),
            selector=value.get("selector", ""),
            required=value.get("required", True),
        )


@dataclass(frozen=True, slots=True)
class ConsistencyAssertion:
    assertion_id: str
    left_deliverable_id: str
    left_selector: str
    operator: str
    right_deliverable_id: str
    right_selector: str
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "assertion_id", _text(self.assertion_id, "assertion_id")
        )
        for attribute in ("left_deliverable_id", "right_deliverable_id"):
            object.__setattr__(
                self, attribute, _text(getattr(self, attribute), attribute)
            )
        for attribute in ("left_selector", "right_selector"):
            object.__setattr__(
                self, attribute, _text(getattr(self, attribute), attribute)
            )
        object.__setattr__(self, "operator", _text(self.operator, "operator"))
        if self.operator not in CONSISTENCY_OPERATORS:
            raise GeneralWorkModelError(
                f"unsupported consistency assertion operator: {self.operator}"
            )
        object.__setattr__(self, "required", _boolean(self.required, "required"))

    def to_payload(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "left_deliverable_id": self.left_deliverable_id,
            "left_selector": self.left_selector,
            "operator": self.operator,
            "right_deliverable_id": self.right_deliverable_id,
            "right_selector": self.right_selector,
            "required": self.required,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ConsistencyAssertion:
        value = _payload(payload, "consistency assertion")
        return cls(
            assertion_id=value.get("assertion_id"),
            left_deliverable_id=value.get("left_deliverable_id"),
            left_selector=value.get("left_selector"),
            operator=value.get("operator"),
            right_deliverable_id=value.get("right_deliverable_id"),
            right_selector=value.get("right_selector"),
            required=value.get("required", True),
        )


@dataclass(frozen=True, slots=True)
class GeneralWorkPlan:
    plan_id: str
    objective_id: str
    campaign_id: str
    resource_id: str
    desired_outcome: str
    delivery_root: str
    sources: tuple[SourceIdentity, ...]
    deliverables: tuple[DeliverableSpec, ...]
    steps: tuple[WorkStep, ...]
    artifact_assertions: tuple[ArtifactAssertion, ...]
    consistency_assertions: tuple[ConsistencyAssertion, ...]
    created_at: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    quality_requirements: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    source_preservation: str = "PRESERVE_ORIGINALS"
    schema_version: int = SCHEMA_VERSION
    allowed_action_kinds: InitVar[Iterable[str] | None] = None

    def __post_init__(self, allowed_action_kinds: Iterable[str] | None) -> None:
        schema_version = _integer(
            self.schema_version, "schema_version", minimum=1
        )
        if schema_version != SCHEMA_VERSION:
            raise GeneralWorkModelError(
                f"schema_version must be {SCHEMA_VERSION}"
            )
        object.__setattr__(self, "schema_version", schema_version)
        for attribute in ("plan_id", "objective_id", "campaign_id", "resource_id"):
            object.__setattr__(
                self, attribute, _text(getattr(self, attribute), attribute)
            )
        object.__setattr__(
            self, "desired_outcome", _text(self.desired_outcome, "desired_outcome")
        )
        object.__setattr__(
            self, "delivery_root", _relative_path(self.delivery_root, "delivery_root")
        )
        object.__setattr__(
            self,
            "sources",
            tuple(sorted(
                _records(self.sources, SourceIdentity, "sources"),
                key=lambda item: item.source_id,
            )),
        )
        object.__setattr__(
            self,
            "deliverables",
            tuple(sorted(
                _records(self.deliverables, DeliverableSpec, "deliverables"),
                key=lambda item: item.deliverable_id,
            )),
        )
        object.__setattr__(
            self,
            "steps",
            tuple(sorted(
                _records(self.steps, WorkStep, "steps"),
                key=lambda item: item.step_id,
            )),
        )
        object.__setattr__(
            self,
            "artifact_assertions",
            tuple(sorted(
                _records(
                    self.artifact_assertions,
                    ArtifactAssertion,
                    "artifact_assertions",
                ),
                key=lambda item: item.assertion_id,
            )),
        )
        object.__setattr__(
            self,
            "consistency_assertions",
            tuple(sorted(
                _records(
                    self.consistency_assertions,
                    ConsistencyAssertion,
                    "consistency_assertions",
                ),
                key=lambda item: item.assertion_id,
            )),
        )
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "metadata", _freeze_object(self.metadata, "metadata"))
        object.__setattr__(
            self,
            "quality_requirements",
            _id_tuple(self.quality_requirements, "quality_requirements"),
        )
        object.__setattr__(
            self, "ambiguities", _id_tuple(self.ambiguities, "ambiguities")
        )
        preservation = _text(self.source_preservation, "source_preservation").upper()
        if preservation not in SOURCE_PRESERVATION_POLICIES:
            raise GeneralWorkModelError(
                "source_preservation must be a supported preservation policy"
            )
        object.__setattr__(self, "source_preservation", preservation)

        action_policy = self._action_policy(allowed_action_kinds)
        self._validate_graph(action_policy)

    @staticmethod
    def _action_policy(
        allowed_action_kinds: Iterable[str] | None,
    ) -> frozenset[str]:
        if allowed_action_kinds is None:
            return GENERAL_WORK_ACTION_KINDS
        if isinstance(allowed_action_kinds, str):
            raise GeneralWorkModelError("allowed_action_kinds must be an iterable")
        try:
            policy = frozenset(
                _text(item, "allowed action kind") for item in allowed_action_kinds
            )
        except TypeError as error:
            raise GeneralWorkModelError(
                "allowed_action_kinds must be an iterable"
            ) from error
        if not policy:
            raise GeneralWorkModelError("allowed_action_kinds cannot be empty")
        return policy

    def _validate_graph(self, action_policy: frozenset[str]) -> None:
        sources = _unique_index(self.sources, "source_id", "source")
        deliverables = _unique_index(
            self.deliverables, "deliverable_id", "deliverable"
        )
        steps = _unique_index(self.steps, "step_id", "step")
        artifact_assertions = _unique_index(
            self.artifact_assertions, "assertion_id", "artifact assertion"
        )
        consistency_assertions = _unique_index(
            self.consistency_assertions,
            "assertion_id",
            "consistency assertion",
        )
        overlap = set(artifact_assertions).intersection(consistency_assertions)
        if overlap:
            raise GeneralWorkModelError("assertion identifiers must be globally unique")
        assertions = {**artifact_assertions, **consistency_assertions}
        if not steps:
            raise GeneralWorkModelError("a General Work plan requires at least one step")
        if not deliverables:
            raise GeneralWorkModelError(
                "a General Work plan requires at least one deliverable"
            )
        path_owners: dict[str, str] = {}
        for deliverable in self.deliverables:
            path_key = deliverable.path.casefold()
            prior_owner = path_owners.get(path_key)
            if prior_owner is not None:
                raise GeneralWorkModelError(
                    "deliverable paths must be unique under case-insensitive matching"
                )
            path_owners[path_key] = deliverable.deliverable_id
            delivery_root = f"{self.delivery_root.rstrip('/')}/".casefold()
            if not deliverable.path.casefold().startswith(delivery_root):
                raise GeneralWorkModelError(
                    f"deliverable {deliverable.deliverable_id} is outside delivery_root"
                )
            suffix = PurePosixPath(deliverable.path).suffix.lower().lstrip(".")
            format_aliases = {
                "jpeg": {"jpeg", "jpg"},
                "text": {"txt", "md"},
            }
            allowed_suffixes = format_aliases.get(
                deliverable.format, {deliverable.format}
            )
            if suffix not in allowed_suffixes:
                raise GeneralWorkModelError(
                    f"deliverable {deliverable.deliverable_id} format does not match its path"
                )

        for step in self.steps:
            if step.action_kind not in action_policy:
                raise GeneralWorkModelError(
                    f"unsupported General Work action kind: {step.action_kind}"
                )
            unknown_dependencies = set(step.dependencies).difference(steps)
            if unknown_dependencies:
                raise GeneralWorkModelError(
                    f"step {step.step_id} references unknown dependencies"
                )
            if set(step.source_ids).difference(sources):
                raise GeneralWorkModelError(
                    f"step {step.step_id} references unknown sources"
                )
            if set(step.output_ids).difference(deliverables):
                raise GeneralWorkModelError(
                    f"step {step.step_id} references unknown deliverables"
                )
            if set(step.verification_assertion_ids).difference(assertions):
                raise GeneralWorkModelError(
                    f"step {step.step_id} references unknown assertions"
                )

        for deliverable in self.deliverables:
            if set(deliverable.source_ids).difference(sources):
                raise GeneralWorkModelError(
                    f"deliverable {deliverable.deliverable_id} references unknown sources"
                )
            if deliverable.producer_step_id not in steps:
                raise GeneralWorkModelError(
                    f"deliverable {deliverable.deliverable_id} has an unknown producer"
                )
            if set(deliverable.verification_assertion_ids).difference(
                artifact_assertions
            ):
                raise GeneralWorkModelError(
                    f"deliverable {deliverable.deliverable_id} references unknown "
                    "artifact assertions"
                )
            for assertion_id in deliverable.verification_assertion_ids:
                if (
                    artifact_assertions[assertion_id].deliverable_id
                    != deliverable.deliverable_id
                ):
                    raise GeneralWorkModelError(
                        f"deliverable {deliverable.deliverable_id} references an "
                        "assertion owned by another deliverable"
                    )

        for assertion in self.artifact_assertions:
            if assertion.deliverable_id not in deliverables:
                raise GeneralWorkModelError(
                    f"assertion {assertion.assertion_id} references an unknown deliverable"
                )
        for assertion in self.consistency_assertions:
            if (
                assertion.left_deliverable_id not in deliverables
                or assertion.right_deliverable_id not in deliverables
            ):
                raise GeneralWorkModelError(
                    f"assertion {assertion.assertion_id} references an unknown deliverable"
                )

        owners: dict[str, list[str]] = {identifier: [] for identifier in deliverables}
        for step in self.steps:
            for deliverable_id in step.output_ids:
                owners[deliverable_id].append(step.step_id)
        for deliverable_id, producer_ids in owners.items():
            if len(producer_ids) != 1:
                raise GeneralWorkModelError(
                    f"deliverable {deliverable_id} must have exactly one output producer"
                )
            if deliverables[deliverable_id].producer_step_id != producer_ids[0]:
                raise GeneralWorkModelError(
                    f"deliverable {deliverable_id} producer does not match step outputs"
                )

        layers = self.topological_layers()
        ancestors: dict[str, set[str]] = {}
        for layer in layers:
            for step_id in layer:
                dependency_ids = set(steps[step_id].dependencies)
                ancestors[step_id] = dependency_ids.union(*(
                    ancestors[dependency_id]
                    for dependency_id in dependency_ids
                )) if dependency_ids else set()

        for deliverable in self.deliverables:
            producer = steps[deliverable.producer_step_id]
            available_sources = set(producer.source_ids)
            for ancestor_id in ancestors[producer.step_id]:
                for output_id in steps[ancestor_id].output_ids:
                    available_sources.update(deliverables[output_id].source_ids)
            if not set(deliverable.source_ids).issubset(available_sources):
                raise GeneralWorkModelError(
                    f"deliverable {deliverable.deliverable_id} source lineage is "
                    "outside its dependency closure"
                )

        for step in self.steps:
            available_producers = ancestors[step.step_id] | {step.step_id}
            for assertion_id in step.verification_assertion_ids:
                assertion = assertions[assertion_id]
                if isinstance(assertion, ArtifactAssertion):
                    producer_ids = {
                        deliverables[assertion.deliverable_id].producer_step_id
                    }
                else:
                    producer_ids = {
                        deliverables[assertion.left_deliverable_id].producer_step_id,
                        deliverables[assertion.right_deliverable_id].producer_step_id,
                    }
                if not producer_ids.issubset(available_producers):
                    raise GeneralWorkModelError(
                        f"step {step.step_id} verifies artifacts outside its "
                        "dependency closure"
                    )

    def topological_layers(self) -> tuple[tuple[str, ...], ...]:
        dependencies = {
            step.step_id: set(step.dependencies)
            for step in self.steps
        }
        layers: list[tuple[str, ...]] = []
        remaining = set(dependencies)
        while remaining:
            ready = tuple(sorted(
                step_id
                for step_id in remaining
                if not dependencies[step_id].intersection(remaining)
            ))
            if not ready:
                raise GeneralWorkModelError("work step dependencies contain a cycle")
            layers.append(ready)
            remaining.difference_update(ready)
        return tuple(layers)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "objective_id": self.objective_id,
            "campaign_id": self.campaign_id,
            "resource_id": self.resource_id,
            "desired_outcome": self.desired_outcome,
            "delivery_root": self.delivery_root,
            "sources": [item.to_payload() for item in self.sources],
            "deliverables": [item.to_payload() for item in self.deliverables],
            "steps": [item.to_payload() for item in self.steps],
            "artifact_assertions": [
                item.to_payload() for item in self.artifact_assertions
            ],
            "consistency_assertions": [
                item.to_payload() for item in self.consistency_assertions
            ],
            "created_at": self.created_at,
            "metadata": _thaw_json(self.metadata),
            "quality_requirements": list(self.quality_requirements),
            "ambiguities": list(self.ambiguities),
            "source_preservation": self.source_preservation,
        }

    def fingerprint_payload(self) -> dict[str, Any]:
        payload = self.to_payload()
        payload.pop("created_at")
        for source in payload["sources"]:
            source.pop("captured_at")
        return payload

    def fingerprint(self) -> str:
        return hashlib.sha256(
            canonical_json(self.fingerprint_payload()).encode("utf-8")
        ).hexdigest()

    @property
    def plan_hash(self) -> str:
        return self.fingerprint()

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        allowed_action_kinds: Iterable[str] | None = None,
    ) -> GeneralWorkPlan:
        value = _payload(payload, "general work plan")
        return cls(
            schema_version=value.get("schema_version", SCHEMA_VERSION),
            plan_id=value.get("plan_id"),
            objective_id=value.get("objective_id"),
            campaign_id=value.get("campaign_id"),
            resource_id=value.get("resource_id"),
            desired_outcome=value.get("desired_outcome"),
            delivery_root=value.get("delivery_root"),
            sources=tuple(
                SourceIdentity.from_payload(item)
                for item in value.get("sources", [])
            ),
            deliverables=tuple(
                DeliverableSpec.from_payload(item)
                for item in value.get("deliverables", [])
            ),
            steps=tuple(
                WorkStep.from_payload(item)
                for item in value.get("steps", [])
            ),
            artifact_assertions=tuple(
                ArtifactAssertion.from_payload(item)
                for item in value.get("artifact_assertions", [])
            ),
            consistency_assertions=tuple(
                ConsistencyAssertion.from_payload(item)
                for item in value.get("consistency_assertions", [])
            ),
            created_at=value.get("created_at"),
            metadata=value.get("metadata", {}),
            quality_requirements=value.get("quality_requirements", []),
            ambiguities=value.get("ambiguities", []),
            source_preservation=value.get(
                "source_preservation", "PRESERVE_ORIGINALS"
            ),
            allowed_action_kinds=allowed_action_kinds,
        )


__all__ = [
    "ARTIFACT_ASSERTION_KINDS",
    "CONSISTENCY_OPERATORS",
    "GENERAL_WORK_ACTION_KINDS",
    "SOURCE_PRESERVATION_POLICIES",
    "ArtifactAssertion",
    "ConsistencyAssertion",
    "DeliverableSpec",
    "GeneralWorkModelError",
    "GeneralWorkPlan",
    "SCHEMA_VERSION",
    "SourceIdentity",
    "WorkStep",
]
