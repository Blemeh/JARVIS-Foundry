"""World calibration - evidence-driven confidence adjustments."""
from __future__ import annotations

import json
import uuid
from typing import Any

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json


def _now() -> str:
    return isoformat()

MAX_ADJUSTMENT = 0.30
PENALTY_PER_REFUTED = 0.05
BONUS_PER_CONFIRMED = 0.02


class WorldCalibrationStore:
    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    def _calibration_id(self, subject_type: str, subject_id: str) -> str:
        return f"wcal-{subject_type}:{subject_id}"

    def get(self, subject_type: str, subject_id: str) -> dict[str, Any] | None:
        cid = self._calibration_id(subject_type, subject_id)
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM world_calibration WHERE calibration_id = ?", (cid,)).fetchone()
            if row is None:
                return None
            return dict(row)

    def adjustment_for(self, subject_type: str, subject_id: str) -> float:
        row = self.get(subject_type, subject_id)
        if row is None:
            return 0.0
        return float(row.get("adjustment", 0.0))

    def record_outcome(
        self,
        subject_type: str,
        subject_id: str,
        outcome: str,
        *,
        source_id: str = "world-model",
    ) -> dict[str, Any]:
        """Update calibration based on a prediction outcome."""
        cid = self._calibration_id(subject_type, subject_id)
        existing = self.get(subject_type, subject_id)
        if existing:
            total = existing["predictions_total"] + 1
            confirmed = existing["predictions_confirmed"] + (1 if outcome == "CONFIRMED" else 0)
            refuted = existing["predictions_refuted"] + (1 if outcome == "REFUTED" else 0)
        else:
            total = 1
            confirmed = 1 if outcome == "CONFIRMED" else 0
            refuted = 1 if outcome == "REFUTED" else 0
        # Brier-like calibration score
        if total > 0:
            score = confirmed / total
        else:
            score = 0.5
        # Adjustment: penalize refuted, reward confirmed
        # Penalty for refuted predictions reduces future confidence (negative adjustment)
        # Bonus for confirmed predictions increases it slightly
        penalty = min(MAX_ADJUSTMENT, refuted * PENALTY_PER_REFUTED)
        bonus = min(0.10, confirmed * BONUS_PER_CONFIRMED)
        adjustment = bonus - penalty
        # Clamp adjustment
        adjustment = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, adjustment))
        payload: dict[str, Any] = {
            "calibration_id": cid,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "predictions_total": total,
            "predictions_confirmed": confirmed,
            "predictions_refuted": refuted,
            "calibration_score": round(score, 4),
            "adjustment": round(adjustment, 4),
        }
        event = Event.create(
            "world.calibration_recorded", payload,
            source_type="world_model", source_id=source_id,
            idempotency_key=f"world.calibration_recorded:{cid}:{total}",
        )
        self.db.append(event)
        return payload

    def rebuild(self) -> dict[str, dict[str, Any]]:
        """Rebuild calibration from all prediction outcomes."""
        result: dict[str, dict[str, Any]] = {}
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM world_predictions WHERE outcome IN ('CONFIRMED', 'REFUTED')").fetchall()
        for row in rows:
            d = dict(row)
            key = f"{d['subject_type']}:{d['subject_id']}"
            if key not in result:
                result[key] = {"total": 0, "confirmed": 0, "refuted": 0}
            result[key]["total"] += 1
            if d["outcome"] == "CONFIRMED":
                result[key]["confirmed"] += 1
            elif d["outcome"] == "REFUTED":
                result[key]["refuted"] += 1
        return result

    def list_all(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM world_calibration ORDER BY predictions_total DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [dict(r) for r in rows]