"""World predictions - anticipated future states with calibration."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json


def _now() -> str:
    return isoformat()


class WorldPredictionStore:
    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    def record(
        self,
        subject_type: str,
        subject_id: str,
        prediction_kind: str,
        predicted_value: dict[str, Any],
        *,
        prediction_id: str | None = None,
        confidence: float = 0.5,
        horizon_until: str | None = None,
        provenance: dict[str, Any] | None = None,
        source_id: str = "world-model",
        idempotency_key: str | None = None,
    ) -> str:
        if not subject_type or not subject_id or not prediction_kind:
            raise ValueError("subject_type, subject_id, prediction_kind are required")
        pid = prediction_id or f"wp-{uuid.uuid4().hex[:12]}"
        payload: dict[str, Any] = {
            "prediction_id": pid,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "prediction_kind": prediction_kind,
            "predicted_value": predicted_value,
            "confidence": max(0.0, min(1.0, confidence)),
            "predicted_at": _now(),
            "horizon_until": horizon_until,
            "outcome": "PENDING",
            "outcome_value": {},
            "provenance": provenance or {},
        }
        event = Event.create(
            "world.prediction_recorded", payload,
            source_type="world_model", source_id=source_id,
            idempotency_key=idempotency_key or f"world.prediction_recorded:{pid}",
        )
        self.db.append(event)
        return pid

    def record_outcome(
        self,
        prediction_id: str,
        outcome: str,
        outcome_value: dict[str, Any] | None = None,
        *,
        calibration_delta: float | None = None,
        source_id: str = "world-model",
        idempotency_key: str | None = None,
    ) -> str:
        if outcome not in ("CONFIRMED", "REFUTED", "EXPIRED", "SUPERSEDED"):
            raise ValueError(f"invalid outcome {outcome!r}")
        payload: dict[str, Any] = {
            "prediction_id": prediction_id,
            "outcome": outcome,
            "outcome_value": outcome_value or {},
            "outcome_at": _now(),
            "calibration_delta": calibration_delta,
        }
        event = Event.create(
            "world.prediction_outcome_recorded", payload,
            source_type="world_model", source_id=source_id,
            idempotency_key=idempotency_key or f"world.prediction_outcome_recorded:{prediction_id}:{outcome}",
        )
        self.db.append(event)
        return prediction_id

    def get(self, prediction_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM world_predictions WHERE prediction_id = ?", (prediction_id,)).fetchone()
            if row is None:
                return None
            return _row_to_prediction(dict(row))

    def list(
        self,
        subject_type: str | None = None,
        subject_id: str | None = None,
        outcome: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if subject_type:
            clauses.append("subject_type = ?")
            args.append(subject_type)
        if subject_id:
            clauses.append("subject_id = ?")
            args.append(subject_id)
        if outcome:
            clauses.append("outcome = ?")
            args.append(outcome)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM world_predictions{where} ORDER BY predicted_at DESC LIMIT ?", tuple(args) + (max(1, min(limit, 500)),)
            ).fetchall()
        return [_row_to_prediction(dict(r)) for r in rows]

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.list(outcome="PENDING", limit=limit)

    def check_expired(self, now: str | None = None, source_id: str = "world-model") -> int:
        now = now or _now()
        expired = 0
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT prediction_id FROM world_predictions WHERE outcome = 'PENDING' AND horizon_until IS NOT NULL AND horizon_until <= ?", (now,)
            ).fetchall()
        for row in rows:
            try:
                self.record_outcome(row[0], "EXPIRED", source_id=source_id)
                expired += 1
            except (ValueError, Exception):
                pass
        return expired


def _row_to_prediction(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("predicted_value_json", "outcome_value_json", "provenance_json"):
        raw = row.pop(key, None)
        target = key.replace("_json", "")
        if key == "predicted_value_json":
            target = "predicted_value"
        elif key == "outcome_value_json":
            target = "outcome_value"
        try:
            row[target] = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            row[target] = {}
    return row