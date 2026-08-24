"""Catalog validation, selection, and evidence-gated promotion."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .contracts import (
    PROOF_STATUSES,
    SUPPORTED_CAPABILITIES,
    ProcedureDefinition,
    ProofStatus,
    contract_findings,
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    procedure_id: str
    field: str
    message: str
    severity: str = "ERROR"


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(item for item in self.issues if item.severity == "ERROR")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(item for item in self.issues if item.severity == "WARNING")

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": [item.__dict__ if hasattr(item, "__dict__") else {
                "procedure_id": item.procedure_id, "field": item.field,
                "message": item.message, "severity": item.severity,
            } for item in self.errors],
            "warnings": [item.__dict__ if hasattr(item, "__dict__") else {
                "procedure_id": item.procedure_id, "field": item.field,
                "message": item.message, "severity": item.severity,
            } for item in self.warnings],
        }


def validate_definition(
    definition: ProcedureDefinition,
    *,
    supported_capabilities: Iterable[str] = SUPPORTED_CAPABILITIES,
    known_procedure_ids: Iterable[str] = (),
) -> ValidationReport:
    supported = set(supported_capabilities)
    known = set(known_procedure_ids)
    issues: list[ValidationIssue] = []

    def add(field: str, message: str, severity: str = "ERROR") -> None:
        issues.append(ValidationIssue(definition.procedure_id, field, message, severity))

    if definition.oracle is None:
        add("oracle", "a success oracle is required")
    elif definition.oracle.independent_verifier_required is not True:
        add("oracle", "trusted procedure oracles require an independent verifier")
    if not definition.evidence_outputs:
        add("evidence_outputs", "at least one evidence output is required")
    if not definition.known_failures:
        add("known_failures", "at least one known failure mode is required")
    if "stop" not in definition.stop_behavior.lower() and "veto" not in definition.stop_behavior.lower():
        add("stop_behavior", "procedure must expose a STOP or veto seam")

    unsupported = set(definition.capabilities) - supported
    if unsupported:
        add("capabilities", f"unsupported capabilities: {sorted(unsupported)}")
    for index, step in enumerate(definition.steps):
        step_prefix = f"steps[{index}]"
        unsupported_step = set(step.capabilities) - supported
        if unsupported_step:
            add(f"{step_prefix}.capabilities", f"unsupported capabilities: {sorted(unsupported_step)}")
        if step.retry_policy.max_attempts > 1 and (
            not step.retry_policy.retryable_failures or not step.retry_policy.requires_reconciliation
        ):
            add(f"{step_prefix}.retry_policy", "retry requires explicit failure classes and reconciliation")
        if (step.reversibility == "irreversible" or step.risk_class in {"R2", "R3"}) and not step.recovery:
            add(f"{step_prefix}.recovery", "irreversible or high-risk step needs recovery")
        if "stop" not in step.stop_behavior.lower() and "veto" not in step.stop_behavior.lower():
            add(f"{step_prefix}.stop_behavior", "step must expose a STOP or veto seam")
        if not step.evidence_outputs:
            add(f"{step_prefix}.evidence_outputs", "step needs evidence output")

    for field, message in contract_findings(definition):
        add(field, message)
    for dependency in definition.dependencies:
        if dependency == definition.procedure_id:
            add("dependencies", "procedure cannot depend on itself")
        elif known and dependency not in known:
            add("dependencies", f"unknown dependency {dependency!r}")
    if definition.supersedes == definition.procedure_id:
        add("supersedes", "procedure cannot supersede itself")
    if definition.proof_status not in PROOF_STATUSES:
        add("proof_status", f"unknown proof status {definition.proof_status!r}")
    if definition.proof_status in {ProofStatus.PROVEN_ONCE.value, ProofStatus.TRUSTED.value} and not definition.last_verified:
        add("last_verified", "proof status requires a verification date")
    return ValidationReport(tuple(issues))


class ProcedureCatalog:
    """Immutable-by-replacement catalog for selection and supersession."""

    def __init__(
        self,
        definitions: Iterable[ProcedureDefinition],
        *,
        supported_capabilities: Iterable[str] = SUPPORTED_CAPABILITIES,
    ) -> None:
        self._definitions = tuple(definitions)
        self.supported_capabilities = frozenset(supported_capabilities)

    @property
    def definitions(self) -> tuple[ProcedureDefinition, ...]:
        return self._definitions

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(item.procedure_id for item in self._definitions)

    def get(self, procedure_id: str) -> ProcedureDefinition:
        for definition in self._definitions:
            if definition.procedure_id == procedure_id:
                return definition
        raise KeyError(procedure_id)

    def validate(self) -> ValidationReport:
        issues: list[ValidationIssue] = []
        seen: set[str] = set()
        known = set(self.ids)
        for definition in self._definitions:
            if definition.procedure_id in seen:
                issues.append(ValidationIssue(definition.procedure_id, "procedure_id", "duplicate procedure id"))
            seen.add(definition.procedure_id)
            issues.extend(validate_definition(
                definition,
                supported_capabilities=self.supported_capabilities,
                known_procedure_ids=known,
            ).issues)
        issues.extend(self._cycle_issues())
        return ValidationReport(tuple(issues))

    def _cycle_issues(self) -> tuple[ValidationIssue, ...]:
        graph = {item.procedure_id: set(item.dependencies) for item in self._definitions}
        visiting: set[str] = set()
        visited: set[str] = set()
        cycles: set[str] = set()
        stack: list[str] = []

        def visit(node: str) -> None:
            if node in visiting:
                try:
                    cycles.update(stack[stack.index(node):])
                except ValueError:
                    cycles.add(node)
                return
            if node in visited:
                return
            visiting.add(node)
            stack.append(node)
            for child in graph.get(node, ()):
                if child in graph:
                    visit(child)
            stack.pop()
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)
        return tuple(
            ValidationIssue(node, "dependencies", "circular procedure dependency")
            for node in sorted(cycles)
        )

    def select(
        self,
        *,
        domain: str | None = None,
        capabilities: Iterable[str] = (),
        statuses: Iterable[str] = (ProofStatus.TRUSTED.value, ProofStatus.PROVEN_ONCE.value),
    ) -> tuple[ProcedureDefinition, ...]:
        required = set(capabilities)
        allowed = set(statuses)
        selected = [
            item for item in self._definitions
            if (domain is None or item.domain == domain)
            and item.proof_status in allowed
            and required.issubset(item.capabilities)
        ]
        return tuple(sorted(selected, key=lambda item: (item.domain, item.procedure_id)))

    def promote(
        self,
        procedure_id: str,
        *,
        status: str,
        last_verified: str,
        verifier_passed: bool,
        evidence_refs: Iterable[str],
    ) -> "ProcedureCatalog":
        if not verifier_passed:
            raise ValueError("a verifier PASS is required for promotion")
        refs = tuple(str(item).strip() for item in evidence_refs if str(item).strip())
        if not refs:
            raise ValueError("promotion requires evidence references")
        if status not in {ProofStatus.PROVEN_ONCE.value, ProofStatus.TRUSTED.value}:
            raise ValueError(f"invalid promotion status {status!r}")
        updated: list[ProcedureDefinition] = []
        for item in self._definitions:
            if item.procedure_id != procedure_id:
                updated.append(item)
                continue
            metadata = dict(item.metadata)
            metadata["proof_evidence_refs"] = list(refs)
            payload = item.as_dict()
            payload.update({
                "metadata": metadata,
                "proof_status": status,
                "last_verified": last_verified,
                "parameters": item.parameters,
                "steps": item.steps,
                "oracle": item.oracle,
                "known_failures": item.known_failures,
            })
            updated.append(ProcedureDefinition(**payload))
        if not any(item.procedure_id == procedure_id for item in self._definitions):
            raise KeyError(procedure_id)
        return ProcedureCatalog(updated, supported_capabilities=self.supported_capabilities)

    def as_dict(self) -> dict[str, object]:
        return {
            "definitions": [item.as_dict() for item in self._definitions],
            "supported_capabilities": sorted(self.supported_capabilities),
        }


__all__ = [
    "ProcedureCatalog", "ValidationIssue", "ValidationReport", "validate_definition",
]
