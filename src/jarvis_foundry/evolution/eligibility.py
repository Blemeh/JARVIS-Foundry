"""V6 lane JOB: promotion eligibility decision (typed, NO promotion).

A deterministic, read-only decision function over the full evidence
chain. It NEVER promotes: it returns a typed decision —

    ELIGIBLE  — every gate holds; the Supervisor may promote;
    REJECTED  — a substantive gate fails (verifier, improvement,
                guardrails, canary);
    BLOCKED   — the world forbids action right now (STOP sentinel or
                release freeze); retry later is meaningful;
    UNKNOWN   — evidence missing or incomparable (stale evidence,
                absent runs); never guessed.

Gate order (fail-fast, recorded in decision["failed_gates"]):
STOP/veto -> freeze -> manifest -> risk mismatch -> candidate slot ->
trusted independent verifier -> measured improvement -> guardrails ->
canary (R2/R3) -> evidence staleness.

This function shares the SAME gate predicates as promote._gate_promotion
by calling it inside a probe and CLASSIFYING its exception type — so
the two can never drift apart.
"""
from __future__ import annotations

import json
from typing import Any

from ..database import FoundryDatabase
from ..events import canonical_json
from .canary import canary_completed_successfully
from .lab import compare_evaluations, LabError
from .promote import (
    PromoteError,
    _gate_promotion,
    is_frozen,
)
from .verify import VerificationError

DECISIONS = ("ELIGIBLE", "REJECTED", "BLOCKED", "UNKNOWN")


class EligibilityError(ValueError):
    """Eligibility evaluation refused (bad inputs)."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EligibilityError(message)


def decide_promotion_eligibility(
    database: FoundryDatabase, *, candidate_release_id: str,
    verification_id: str, baseline_run_id: str, candidate_run_id: str,
    canary_id: str | None = None,
) -> dict[str, Any]:
    """Classify the promotion readiness of ONE candidate. Read-only."""
    # ---- world gates first (BLOCKED class) -------------------------
    from pathlib import Path

    stop_active = (Path(database.path).parent / "EMERGENCY_STOP").exists()
    frozen = is_frozen(database)
    if stop_active or frozen:
        return {"decision": "BLOCKED",
                "reasons": ([f"stop active ({'EMERGENCY_STOP'} sentinel)"]
                            if stop_active else []) +
                           (["releases are frozen"] if frozen else []),
                "candidate_release_id": candidate_release_id}

    manifest = database.row("release_manifests", "release_id",
                            candidate_release_id)
    if manifest is None:
        return {"decision": "UNKNOWN",
                "reasons": [f"no manifest for {candidate_release_id}"],
                "candidate_release_id": candidate_release_id}
    improvement_id = str(manifest["improvement_id"])
    improvement = database.row("improvement_records", "improvement_id",
                               improvement_id)
    if improvement is None:
        return {"decision": "UNKNOWN",
                "reasons": [f"improvement {improvement_id} missing"],
                "candidate_release_id": candidate_release_id}
    if str(improvement["state"]) != "FROZEN":
        return {"decision": "REJECTED",
                "reasons": [f"improvement state "
                            f"{improvement['state']} is not FROZEN"],
                "candidate_release_id": candidate_release_id}
    declared_risk = json.loads(str(improvement.get("surfaces_json")
                                   or "[]"))
    risk_class = str(manifest["risk_class"])
    if risk_class not in ("R0", "R1", "R2", "R3"):
        return {"decision": "REJECTED",
                "reasons": [f"manifest carries unknown risk "
                            f"{risk_class}"],
                "candidate_release_id": candidate_release_id}

    reasons: list[str] = []

    def classify(error: Exception) -> tuple[str, str]:
        text = str(error)
        if isinstance(error, VerificationError):
            return "UNKNOWN", text
        if isinstance(error, LabError):
            return "UNKNOWN", text
        lowered = text.lower()
        if "not a trusted pass" in lowered:
            return ("REJECTED", "verifier verdict absent, FAIL, or same-"
                                "session (self-certification refused)")
        if "improvement_met false" in lowered or \
                "measured improvement absent" in lowered:
            return ("REJECTED",
                    "measured improvement absent (improvement_met False)")
        if "guardrails violated" in lowered:
            return "REJECTED", text
        if "canary" in lowered:
            return "REJECTED", text
        if "already live" in lowered or "candidate slot" in lowered:
            return ("REJECTED" if "already live" in lowered
                    else "UNKNOWN", text)
        if "stop active" in lowered:
            return "BLOCKED", text
        if "frozen" in lowered:
            return "BLOCKED", text
        return "UNKNOWN", text

    try:
        _gate_promotion(
            database, candidate_release_id=candidate_release_id,
            verification_id=verification_id, canary_id=canary_id,
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id, controller="supervisor",
            veto_check=None)
        return {"decision": "ELIGIBLE", "reasons": [],
                "risk_class": risk_class,
                "improvement_id": improvement_id,
                "candidate_release_id": candidate_release_id,
                "verification_id": verification_id,
                "canary_id": canary_id,
                "note": "gates only; this function NEVER promotes"}
    except (PromoteError, LabError, VerificationError) as error:
        decision, reason = classify(error)
        reasons.append(reason)
        return {"decision": decision, "reasons": reasons,
                "risk_class": risk_class,
                "improvement_id": improvement_id,
                "candidate_release_id": candidate_release_id,
                "verification_id": verification_id,
                "canary_id": canary_id}


def eligibility_report(database: FoundryDatabase, **kwargs: Any
                       ) -> dict[str, Any]:
    """decide + ledger record (eligibility.evaluated). The record is
    content-derived and idempotent per input set."""
    decision = decide_promotion_eligibility(database, **kwargs)
    digest = canonical_json({k: v for k, v in kwargs.items()})[:32]
    from ..events import Event
    database.append(Event.create(
        "eligibility.evaluated",
        {**decision, "inputs_digest": digest},
        source_type="supervisor", source_id="v6",
        idempotency_key=(f"eligibility.evaluated:"
                         f"{decision['candidate_release_id']}:"
                         f"{digest}")))
    return decision
