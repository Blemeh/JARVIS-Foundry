"""Mission-lineage composition and local STOP/veto checks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .procedure import OperationError


class OperationVeto(OperationError):
    """A local or owner STOP signal vetoed a consequential operation."""


def _exact(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperationError(f"{field_name} must be non-empty")
    result = value.strip()
    if any(ord(char) < 32 for char in result):
        raise OperationError(f"{field_name} contains a control character")
    if any(token in result for token in ("*", "${", "%", "..")):
        raise OperationError(f"{field_name} must be exact")
    return result


@dataclass(frozen=True, slots=True)
class MissionLineage:
    mission_id: str
    procedure_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        for key in ("mission_id", "procedure_id", "correlation_id"):
            object.__setattr__(self, key, _exact(getattr(self, key), key))


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    capability: str
    action: str
    target: str
    effect_ticket: str
    status: str
    evidence_refs: tuple[str, ...]
    lineage: MissionLineage
    consequence: str = ""


class CapabilityComposer:
    """Collect multiple capability outcomes under one mission lineage."""

    _STATUSES = frozenset({"PLANNED", "STARTED", "COMPLETED", "FAILED", "FROZEN", "COMPENSATED"})

    def __init__(self, lineage: MissionLineage) -> None:
        self.lineage = lineage
        self._records: list[CapabilityRecord] = []

    @property
    def records(self) -> tuple[CapabilityRecord, ...]:
        return tuple(self._records)

    def record(
        self,
        *,
        capability: str,
        action: str,
        target: str,
        effect_ticket: str,
        status: str,
        evidence_refs: Iterable[str],
        lineage: MissionLineage | None = None,
        consequence: str = "",
    ) -> CapabilityRecord:
        lineage = lineage or self.lineage
        if lineage != self.lineage:
            raise OperationError("capability result has foreign mission lineage")
        capability = _exact(capability, "capability")
        action = _exact(action, "action")
        target = _exact(target, "target")
        effect_ticket = _exact(effect_ticket, "effect_ticket")
        if status not in self._STATUSES:
            raise OperationError(f"unknown capability record status {status!r}")
        if any(record.effect_ticket == effect_ticket for record in self._records):
            raise OperationError(f"effect ticket already recorded: {effect_ticket}")
        refs = tuple(_exact(item, "evidence reference") for item in evidence_refs)
        if not refs:
            raise OperationError("capability results require evidence")
        record = CapabilityRecord(
            capability=capability, action=action, target=target,
            effect_ticket=effect_ticket, status=status,
            evidence_refs=refs, lineage=lineage,
            consequence=consequence,
        )
        self._records.append(record)
        return record

    def require_capabilities(self, capabilities: Iterable[str]) -> None:
        required = {_exact(item, "capability") for item in capabilities}
        completed = {record.capability for record in self._records if record.status == "COMPLETED"}
        missing = required - completed
        if missing:
            raise OperationError(f"mission capabilities incomplete: {sorted(missing)}")

    def evidence_bundle(self) -> dict[str, Any]:
        return {
            "mission_id": self.lineage.mission_id,
            "procedure_id": self.lineage.procedure_id,
            "correlation_id": self.lineage.correlation_id,
            "records": [
                {
                    "capability": record.capability,
                    "action": record.action,
                    "target": record.target,
                    "effect_ticket": record.effect_ticket,
                    "status": record.status,
                    "evidence_refs": list(record.evidence_refs),
                    "consequence": record.consequence,
                }
                for record in self._records
            ],
        }


@dataclass(slots=True)
class VetoGate:
    """A disposable owner-stop gate; it never changes the canonical live STOP."""

    requested: bool = False
    reason: str = ""
    evidence_ref: str = ""

    def request(self, reason: str, *, evidence_ref: str) -> None:
        self.requested = True
        self.reason = _exact(reason, "veto reason")
        self.evidence_ref = _exact(evidence_ref, "veto evidence")

    def clear(self) -> None:
        self.requested = False
        self.reason = ""
        self.evidence_ref = ""

    def check(self, operation_id: str) -> None:
        if self.requested:
            raise OperationVeto(
                f"operation {operation_id} vetoed: {self.reason} ({self.evidence_ref})")


__all__ = ["CapabilityComposer", "CapabilityRecord", "MissionLineage", "OperationVeto", "VetoGate"]
