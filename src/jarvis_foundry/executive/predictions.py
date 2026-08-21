"""Outcome predictions recorded before executive SELECT."""
from __future__ import annotations

from typing import Any

from ..database import FoundryDatabase
from ..events import Event
from .scoring import CandidateScore

PREDICTION_VERSION = "1.0.0"


def build_prediction(score: CandidateScore) -> dict[str, Any]:
    """Derive bounded predictions from scoring factors — no fabrication."""
    factors = {f.name: f for f in score.factors}
    return {
        "expected_value": factors.get("expected_value", _unknown()).value,
        "expected_effort": (
            None if factors.get("effort") is None
            else round(1.0 - float(factors["effort"].value or 0.0), 4)
        ),
        "expected_duration_seconds": None,
        "expected_cost": None,
        "expected_risk": (
            "HIGH" if (factors.get("reversibility") and (factors["reversibility"].value or 1) < 0.5)
            else "MEDIUM" if score.composite < 0.5 else "LOW"
        ),
        "confidence": round(score.composite, 4),
        "expected_success": round(min(0.95, 0.5 + score.composite * 0.4), 4),
        "prediction_version": PREDICTION_VERSION,
        "unknown_fields": [
            k for k in ("expected_duration_seconds", "expected_cost")
            if True
        ],
    }


def _unknown() -> Any:
    from .scoring import FactorValue
    return FactorValue("expected_value", None, "UNKNOWN", 0.0, 0.0)


def record_prediction(
    database: FoundryDatabase,
    *,
    decision_id: str,
    candidate_id: str,
    prediction: dict[str, Any],
    source_id: str = "executive",
) -> str:
    event = Event.create(
        "executive.prediction_recorded",
        {
            "decision_id": decision_id,
            "candidate_id": candidate_id,
            "prediction": prediction,
            "prediction_version": PREDICTION_VERSION,
        },
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"executive.prediction_recorded:{decision_id}",
    )
    return str(database.append(event)[0])
