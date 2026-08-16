"""Typed records for the R12 fault campaign."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REQUIRED_OUTCOME_FIELDS: tuple[str, ...] = (
    "truthful_state",
    "blast_radius",
    "incident_record",
    "reconciliation",
    "suspension",
    "unrelated_work_continuation",
    "compensation",
    "recovery",
    "truthful_briefing",
)


@dataclass(frozen=True)
class FaultSpec:
    fault_id: str
    name: str
    description: str
    category: str

    def to_dict(self) -> dict[str, str]:
        return {
            "fault_id": self.fault_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
        }


@dataclass(frozen=True)
class FaultOutcome:
    fault_id: str
    name: str
    passed: bool
    truthful_state: str
    blast_radius: dict[str, Any]
    incident_record: dict[str, Any]
    reconciliation: dict[str, Any]
    suspension: dict[str, Any]
    unrelated_work_continuation: dict[str, Any]
    compensation: dict[str, Any]
    recovery: dict[str, Any]
    truthful_briefing: str
    evidence: tuple[dict[str, str], ...] = field(default_factory=tuple)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id,
            "name": self.name,
            "passed": self.passed,
            "truthful_state": self.truthful_state,
            "blast_radius": dict(self.blast_radius),
            "incident_record": dict(self.incident_record),
            "reconciliation": dict(self.reconciliation),
            "suspension": dict(self.suspension),
            "unrelated_work_continuation": dict(self.unrelated_work_continuation),
            "compensation": dict(self.compensation),
            "recovery": dict(self.recovery),
            "truthful_briefing": self.truthful_briefing,
            "evidence": [dict(item) for item in self.evidence],
            "error": self.error,
        }
