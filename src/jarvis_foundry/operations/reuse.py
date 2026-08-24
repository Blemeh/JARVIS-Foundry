"""Verified procedure-template registration and explicit parameter reuse."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash
from .procedure import OperationError, ProcedureState


class TemplateError(OperationError):
    pass


def _exact(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TemplateError(f"{field_name} must be non-empty")
    result = value.strip()
    if any(ord(char) < 32 for char in result):
        raise TemplateError(f"{field_name} contains a control character")
    if any(token in result for token in ("*", "${", "%", "..")):
        raise TemplateError(f"{field_name} must be exact")
    return result


@dataclass(frozen=True, slots=True)
class ProcedureTemplate:
    template_id: str
    version: str
    source_plan_hash: str
    parameters: tuple[str, ...]
    preconditions: tuple[str, ...]
    known_failure_modes: tuple[str, ...]
    oracle: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for key in ("template_id", "version", "source_plan_hash", "oracle"):
            object.__setattr__(self, key, _exact(getattr(self, key), key))
        for key in ("parameters", "preconditions", "known_failure_modes", "evidence_refs"):
            values = tuple(_exact(item, key[:-1] if key.endswith("s") else key)
                           for item in getattr(self, key))
            if len(set(values)) != len(values):
                raise TemplateError(f"{key} must be unique")
            object.__setattr__(self, key, values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "version": self.version,
            "source_plan_hash": self.source_plan_hash,
            "parameters": list(self.parameters),
            "preconditions": list(self.preconditions),
            "known_failure_modes": list(self.known_failure_modes),
            "oracle": self.oracle,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProcedureTemplate":
        return cls(
            template_id=value["template_id"], version=value["version"],
            source_plan_hash=value["source_plan_hash"],
            parameters=tuple(value.get("parameters", ())),
            preconditions=tuple(value.get("preconditions", ())),
            known_failure_modes=tuple(value.get("known_failure_modes", ())),
            oracle=value["oracle"], evidence_refs=tuple(value.get("evidence_refs", ())),
        )


class ProcedureTemplateRegistry:
    """Persist templates as ordinary ledger evidence only after PASS."""

    def register_verified(
        self,
        database: FoundryDatabase,
        state: ProcedureState,
        *,
        template_id: str,
        version: str,
        parameters: Iterable[str],
        preconditions: Iterable[str],
        known_failure_modes: Iterable[str],
        oracle: str,
        evidence_refs: Iterable[str],
        source_id: str = "v7b-procedure-reuse",
    ) -> ProcedureTemplate:
        if state.terminal_outcome != "COMPLETED" or state.unresolved:
            raise TemplateError("only a fully completed, reconciled procedure can become reusable")
        refs = tuple(_exact(item, "evidence reference") for item in evidence_refs)
        if not refs:
            raise TemplateError("verified template requires outcome evidence")
        template = ProcedureTemplate(
            template_id=_exact(template_id, "template_id"),
            version=_exact(version, "version"),
            source_plan_hash=state.plan.plan_hash,
            parameters=tuple(parameters),
            preconditions=tuple(preconditions),
            known_failure_modes=tuple(known_failure_modes),
            oracle=oracle,
            evidence_refs=refs,
        )
        payload = {
            **template.as_dict(),
            "mission_id": state.plan.mission_id,
            "procedure_id": state.plan.procedure_id,
        }
        key = f"procedure.template.registered:{template.template_id}:{template.version}"
        with database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json, payload_hash FROM events WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        if row is not None:
            if row["payload_hash"] != payload_hash(payload):
                raise TemplateError("template id/version already has different evidence")
            return ProcedureTemplate.from_mapping(json.loads(row["payload_json"]))
        database.append(Event.create(
            "procedure.template.registered", payload,
            source_type="supervisor", source_id=source_id,
            correlation_id=state.plan.mission_id, idempotency_key=key,
        ))
        return template

    def list_templates(self, database: FoundryDatabase) -> tuple[ProcedureTemplate, ...]:
        with database.connect(readonly=True) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM events WHERE event_type = 'procedure.template.registered' ORDER BY sequence"
            ).fetchall()
        return tuple(ProcedureTemplate.from_mapping(json.loads(row[0])) for row in rows)

    def instantiate(
        self,
        template: ProcedureTemplate,
        parameters: Mapping[str, str],
    ) -> dict[str, Any]:
        supplied = {_exact(key, "parameter name"): _exact(value, "parameter value")
                    for key, value in parameters.items()}
        missing = set(template.parameters) - supplied.keys()
        unknown = supplied.keys() - set(template.parameters)
        if missing or unknown:
            raise TemplateError(
                f"template parameters mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}")
        # Values are explicit inputs; nothing from a web page or retrieved
        # content can silently become a procedure parameter.
        return {
            "template_id": template.template_id,
            "version": template.version,
            "source_plan_hash": template.source_plan_hash,
            "parameters": dict(supplied),
            "preconditions": template.preconditions,
            "known_failure_modes": template.known_failure_modes,
            "oracle": template.oracle,
            "evidence_refs": template.evidence_refs,
        }

__all__ = ["ProcedureTemplate", "ProcedureTemplateRegistry", "TemplateError"]
