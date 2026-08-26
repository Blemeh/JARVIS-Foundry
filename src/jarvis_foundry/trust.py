"""Evidence-backed production trust lifecycle.

Trust is a durable, event-derived state machine.  A heartbeat or an elapsed
clock can never promote the product: every required acceptance dimension must
have an explicit, immutable evidence record.  The Supervisor owns transitions
and the Command Center only projects them.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .clock import isoformat
from .database import FoundryDatabase
from .events import Event, EventConflict, canonical_json

TRUST_STATES = ("LAB", "PROVING", "TRUSTED_AUTONOMOUS", "DEGRADED", "SUSPENDED")
_ALLOWED_TRANSITIONS = {
    "LAB": frozenset({"PROVING", "DEGRADED", "SUSPENDED"}),
    "PROVING": frozenset({"TRUSTED_AUTONOMOUS", "LAB", "DEGRADED", "SUSPENDED"}),
    "TRUSTED_AUTONOMOUS": frozenset({"DEGRADED", "SUSPENDED", "PROVING"}),
    "DEGRADED": frozenset({"PROVING", "LAB", "SUSPENDED"}),
    "SUSPENDED": frozenset({"PROVING", "DEGRADED", "LAB"}),
}
REQUIRED_EVIDENCE = (
    "acceptance_profiles",
    "runtime_durability",
    "restart_recovery",
    "stop_revocation",
    "verifier_integrity",
    "false_pass_resistance",
    "provider_routing",
    "release_parity",
    "bounded_failures",
    "schema_integrity",
    "owner_control_gates",
)


class TrustError(ValueError):
    """Raised when a trust transition cannot be justified by evidence."""


@dataclass(frozen=True, slots=True)
class TrustAssessment:
    state: str
    recommended_state: str
    eligible: bool
    checks: dict[str, str]
    missing: tuple[str, ...]
    evidence_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "recommended_state": self.recommended_state,
            "eligible": self.eligible,
            "checks": dict(self.checks),
            "missing": list(self.missing),
            "evidence_digest": self.evidence_digest,
            "required_evidence": list(REQUIRED_EVIDENCE),
        }


def _current_state(database: FoundryDatabase) -> str:
    try:
        with database.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT trust_state FROM supervisor_state WHERE singleton = 1"
            ).fetchone()
    except Exception:  # noqa: BLE001 - trust must fail closed
        return "LAB"
    value = str(row[0]).upper() if row and row[0] else "LAB"
    # ``PROVEN`` was the pre-lifecycle spelling. Read it as the canonical
    # evidence-collection state so an old ledger cannot reset or bypass the
    # explicit promotion gate.
    if value == "PROVEN":
        value = "PROVING"
    return value if value in TRUST_STATES else "LAB"


def _evidence_events(database: FoundryDatabase) -> list[Event]:
    return [
        event for event in database.events(
            readonly=True, event_types=("trust.evidence_recorded",)
        )
        if event.source_type == "supervisor" and event.source_id == "supervisor"
    ]


def assess_trust(database: FoundryDatabase) -> TrustAssessment:
    """Rebuild the trust assessment from explicit evidence records."""
    latest: dict[str, Event] = {}
    for event in _evidence_events(database):
        key = str(event.payload.get("evidence_key") or "")
        if key in REQUIRED_EVIDENCE:
            latest[key] = event
    checks: dict[str, str] = {}
    for key, event in latest.items():
        status = str(event.payload.get("status") or "UNKNOWN").upper()
        evidence = event.payload.get("evidence")
        expected_hash = (
            hashlib.sha256(canonical_json(evidence).encode("utf-8")).hexdigest()
            if isinstance(evidence, dict) and evidence else None
        )
        recorded_hash = str(event.payload.get("evidence_hash") or "")
        if status == "PASS" and expected_hash != recorded_hash:
            checks[key] = "FAIL"
        else:
            checks[key] = status
    missing = tuple(key for key in REQUIRED_EVIDENCE if checks.get(key) != "PASS")
    digest_payload = {
        key: {
            "status": event.payload.get("status"),
            "evidence_hash": event.payload.get("evidence_hash"),
        }
        for key, event in sorted(latest.items())
    }
    digest = hashlib.sha256(canonical_json(digest_payload).encode("utf-8")).hexdigest()
    current = _current_state(database)
    if current in {"DEGRADED", "SUSPENDED"}:
        recommended = current
    elif not latest:
        recommended = "LAB"
    elif missing:
        recommended = "PROVING"
    else:
        recommended = "TRUSTED_AUTONOMOUS"
    return TrustAssessment(
        state=current, recommended_state=recommended, eligible=not missing,
        checks=checks, missing=missing, evidence_digest=digest,
    )


def record_trust_evidence(
    database: FoundryDatabase,
    evidence_key: str,
    *,
    status: str,
    evidence: dict[str, Any],
    controller: str = "supervisor",
    profile_id: str | None = None,
) -> Event:
    """Record one executed acceptance dimension, never an elapsed-time claim."""
    if controller != "supervisor":
        raise TrustError("trust evidence is Supervisor-owned")
    if evidence_key not in REQUIRED_EVIDENCE:
        raise TrustError(f"unknown trust evidence key: {evidence_key}")
    normalized = str(status).upper().strip()
    if normalized not in {"PASS", "FAIL", "NOT_RUN"}:
        raise TrustError("trust evidence status must be PASS, FAIL, or NOT_RUN")
    if not isinstance(evidence, dict) or not evidence:
        raise TrustError("trust evidence payload must be a non-empty object")
    evidence_hash = hashlib.sha256(canonical_json(evidence).encode("utf-8")).hexdigest()
    payload = {
        "evidence_key": evidence_key,
        "status": normalized,
        "evidence": dict(evidence),
        "evidence_hash": evidence_hash,
        "profile_id": profile_id,
        "observed_at": isoformat(),
    }
    event = Event.create(
        "trust.evidence_recorded", payload,
        source_type="supervisor", source_id=controller,
        # Status and profile are part of the evidence identity.  A later
        # failing observation of the same evidence subject must be able to
        # supersede an earlier PASS; omitting them made that transition hit
        # an idempotency collision and could leave trust incorrectly green.
        idempotency_key=(
            f"trust.evidence_recorded:{evidence_key}:{normalized}:"
            f"{profile_id or ''}:{evidence_hash}"
        ),
    )
    try:
        _sequence, created = database.append(event)
    except EventConflict:
        # ``observed_at`` is intentionally fresh for each call, but retries of
        # the same evidence content must resolve to the original immutable
        # record rather than becoming a false idempotency conflict.
        for existing in database.events(
            readonly=True, event_types=("trust.evidence_recorded",)
        ):
            if (existing.idempotency_key == event.idempotency_key
                    and existing.payload.get("evidence_key") == evidence_key
                    and existing.payload.get("status") == normalized
                    and existing.payload.get("evidence_hash") == evidence_hash
                    and existing.payload.get("profile_id") == profile_id):
                return existing
        raise
    if created:
        return event
    # A retry with the same content-addressed evidence returns the immutable
    # ledger event rather than a freshly-generated, unpersisted UUID.
    for existing in database.events(
        readonly=True, event_types=("trust.evidence_recorded",)
    ):
        if existing.idempotency_key == event.idempotency_key:
            return existing
    raise TrustError("trust evidence idempotency row disappeared")


def transition_trust(
    database: FoundryDatabase,
    target: str,
    *,
    reason: str,
    controller: str = "supervisor",
) -> Event:
    """Persist a reversible trust transition after checking its evidence."""
    if controller != "supervisor":
        raise TrustError("trust transitions are Supervisor-owned")
    target = str(target).upper().strip()
    if target not in TRUST_STATES:
        raise TrustError(f"unknown trust state: {target}")
    if not str(reason).strip():
        raise TrustError("trust transitions require a reason")
    assessment = assess_trust(database)
    current = _current_state(database)
    if target == "TRUSTED_AUTONOMOUS" and not assessment.eligible:
        raise TrustError(
            "trust promotion refused; missing evidence: "
            + ", ".join(assessment.missing)
        )
    if target != current and target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise TrustError(f"invalid trust transition: {current} -> {target}")
    if target == current:
        # Return the existing transition when possible; otherwise create a
        # deterministic no-op audit record so the owner can see the request.
        for event in reversed(database.events(
                readonly=True, event_types=("trust.state_changed",))):
            if (event.source_type == "supervisor"
                    and event.source_id == "supervisor"
                    and event.payload.get("to_state") == target):
                return event
    payload = {
        "from_state": current,
        "to_state": target,
        "reason": str(reason).strip(),
        "controller": controller,
        "evidence_digest": assessment.evidence_digest,
        "checks": dict(assessment.checks),
        "missing": list(assessment.missing),
    }
    event = Event.create(
        "trust.state_changed", payload,
        source_type="supervisor", source_id=controller,
        idempotency_key=(
            f"trust.state_changed:{current}:{target}:"
            f"{assessment.evidence_digest}:{hashlib.sha256(str(reason).encode()).hexdigest()[:16]}"
        ),
    )
    _sequence, created = database.append(event)
    if created:
        return event
    for existing in database.events(
        readonly=True, event_types=("trust.state_changed",)
    ):
        if existing.idempotency_key == event.idempotency_key:
            return existing
    raise TrustError("trust transition idempotency row disappeared")


def trust_status(database: FoundryDatabase) -> dict[str, Any]:
    assessment = assess_trust(database)
    history = [
        event.payload for event in database.events(
            readonly=True, event_types=("trust.state_changed",)
        ) if event.source_type == "supervisor" and event.source_id == "supervisor"
    ]
    return {**assessment.to_dict(), "history": history}
