"""Structured role-session results (WP4).

Role sessions (executive, builder, verifier, ...) return a ``RoleResult``:
a machine-readable outcome carrying status, verdict, provider identity, model,
evidence, and error class. The dispatcher serializes completed results and
failed results that it receives into the durable ``agent_runs.result_json``
column through ``agent_run.completed`` / ``agent_run.failed`` events. Generic
reaper failures may not include a structured result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..clock import isoformat

RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
BLOCKED = "BLOCKED"
CANCELLED = "CANCELLED"

VALID_STATUSES = frozenset({RUNNING, COMPLETED, FAILED, BLOCKED, CANCELLED})

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_BLOCKED = "BLOCKED"
VERDICT_NOT_TESTED = "NOT_TESTED"

VALID_VERDICTS = frozenset({VERDICT_PASS, VERDICT_FAIL, VERDICT_BLOCKED, VERDICT_NOT_TESTED})


@dataclass(frozen=True, slots=True)
class RoleResult:
    run_id: str
    role: str
    status: str
    provider: str
    model: str
    verdict: str | None = None
    evidence: tuple[str, ...] = field(default_factory=tuple)
    error_class: str | None = None
    error_detail: str | None = None
    cost_usd: float = 0.0
    wait_for_providers: tuple[str, ...] = field(default_factory=tuple)
    effect_action_key: str | None = None
    identity_transition_authorized: bool = False
    started_at: str = field(default_factory=isoformat)
    finished_at: str = field(default_factory=isoformat)

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid role result status: {self.status!r}")
        if self.verdict is not None and self.verdict not in VALID_VERDICTS:
            raise ValueError(f"invalid role result verdict: {self.verdict!r}")

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "run_id": self.run_id,
            "role": self.role,
            "status": self.status,
            "verdict": self.verdict,
            "provider": self.provider,
            "model": self.model,
            "evidence": list(self.evidence),
            "error_class": self.error_class,
            "error_detail": self.error_detail,
            "cost_usd": self.cost_usd,
            "wait_for_providers": list(self.wait_for_providers),
            "effect_action_key": self.effect_action_key,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if self.identity_transition_authorized:
            payload["identity_transition_authorized"] = True
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RoleResult":
        return cls(
            run_id=payload["run_id"],
            role=payload["role"],
            status=payload["status"],
            verdict=payload.get("verdict"),
            provider=payload["provider"],
            model=payload["model"],
            evidence=tuple(payload.get("evidence", [])),
            error_class=payload.get("error_class"),
            error_detail=payload.get("error_detail"),
            cost_usd=float(payload.get("cost_usd", 0.0)),
            wait_for_providers=tuple(payload.get("wait_for_providers", [])),
            effect_action_key=payload.get("effect_action_key"),
            identity_transition_authorized=bool(
                payload.get("identity_transition_authorized", False)),
            started_at=payload.get("started_at", ""),
            finished_at=payload.get("finished_at", ""),
        )
