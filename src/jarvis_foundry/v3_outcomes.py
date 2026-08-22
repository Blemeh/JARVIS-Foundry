"""V3 Discovery and Building: outcome recording service (WP6).

Immediate outcome window for MVP (delayed 7d/30d windows belong to
WP8-HARD). Rules carried from the spec and existing regressions:

- Trigger ONLY on ``mission.completed`` with technical PASSED — a
  blocked/failed mission never produces an accepted outcome;
- predicted and actual stay DISTINCT fields; missing measurement data
  is honest ``UNKNOWN``, never manufactured PASS (AC-12);
- every outcome lands in ``candidate_outcomes`` via the projection and
  carries calibration delta when both values exist.
"""
from __future__ import annotations

from typing import Any

from .database import FoundryDatabase
from .events import Event


def record_outcome(
    database: FoundryDatabase, *, candidate_id: str, mission_id: str,
    mission_outcome: str,
    predicted_value: dict[str, Any] | None = None,
    actual_value: dict[str, Any] | None = None,
    confidence: float = 0.0,
    source_id: str = "v3-outcomes",
) -> dict[str, Any]:
    """Record the immediate-window outcome for one candidate.

    Returns the recorded payload; raises on non-PASSED mission
    outcomes (the caller records nothing in that case — honesty by
    construction, matching the slice-2 regression).
    """
    if mission_outcome != "PASSED":
        raise ValueError(
            f"outcome recording requires a PASSED mission "
            f"(got {mission_outcome!r}) — failed/blocked missions never "
            "produce accepted outcomes")

    has_measurement = bool(predicted_value) and bool(actual_value)
    window = "immediate"
    payload: dict[str, Any] = {
        "candidate_id": candidate_id,
        "mission_id": mission_id,
        "window": window,
        "predicted_value": predicted_value or {},
        "actual_value": actual_value or {},
        "status": "RECORDED" if has_measurement else "UNKNOWN",
        "confidence": max(0.0, min(1.0, float(confidence))),
        "outcome_id": f"{candidate_id}:{window}",
    }
    database.append(Event.create(
        "discovery.candidate_outcome_recorded", payload,
        source_type="supervisor", source_id=source_id,
        idempotency_key=f"outcome:{candidate_id}:{window}"))

    if has_measurement:
        # Calibration hook (existing executive machinery): predicted vs
        # actual delta feeds future selection weight. Best-effort — a
        # calibration failure must not unrecord an honest outcome.
        try:
            _record_calibration(database, candidate_id=candidate_id,
                                mission_id=mission_id,
                                predicted=predicted_value or {},
                                actual=actual_value or {})
        except Exception:  # noqa: BLE001 — calibration is advisory here
            pass
    return payload


def _record_calibration(database: FoundryDatabase, *, candidate_id: str,
                        mission_id: str, predicted: dict[str, Any],
                        actual: dict[str, Any]) -> None:
    """Append ``executive.calibration_recorded`` with a numeric delta."""
    predicted_score = _numeric(predicted)
    actual_score = _numeric(actual)
    if predicted_score is None or actual_score is None:
        return
    database.append(Event.create(
        "executive.calibration_recorded",
        {"candidate_id": candidate_id, "mission_id": mission_id,
         "predicted": predicted_score, "actual": actual_score,
         "delta": round(actual_score - predicted_score, 4),
         "direction": "over" if actual_score < predicted_score else "under"},
        source_type="supervisor", source_id="v3-outcomes",
        idempotency_key=f"calibration:{candidate_id}:{mission_id}"))


def _numeric(value: dict[str, Any]) -> float | None:
    for key in ("score", "value", "result"):
        item = value.get(key)
        if isinstance(item, (int, float)):
            return float(item)
    return None


def outcome_for_mission(database: FoundryDatabase,
                        mission_id: str) -> dict[str, Any] | None:
    """The immediate-window outcome row for a mission's candidate."""
    with database.connect() as connection:
        row = connection.execute(
            "SELECT created_from FROM missions WHERE mission_id = ?",
            (mission_id,)).fetchone()
        if row is None or not row[0]:
            return None
        candidate_id = str(row[0]).split(":", 1)[1]
    return database.row("candidate_outcomes", "outcome_id",
                        f"{candidate_id}:immediate")


def maintenance_scan_hook(database: FoundryDatabase, *,
                          loop_id: str) -> dict[str, Any]:
    """Minimal continuation hook (MVP stub): report maintenance-scan
    readiness after outcomes. The real wake integration is WP8-HARD.
    """
    with database.connect() as connection:
        unknown = connection.execute(
            "SELECT COUNT(*) FROM candidate_outcomes WHERE status = "
            "'UNKNOWN'").fetchone()[0]
        recorded = connection.execute(
            "SELECT COUNT(*) FROM candidate_outcomes WHERE status = "
            "'RECORDED'").fetchone()[0]
    return {"loop_id": loop_id, "outcomes_recorded": int(recorded),
            "outcomes_unknown": int(unknown), "wake_scheduled": False}
