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


# --------------------------------------------------------------------------
# WP-B: AUTOMATIC post-dispatch outcome closure (single authoritative seam)
#
# The worker loop's terminal branch is the ONE place a harmless mission
# result becomes an outcome: ``reconcile_mission_outcome`` derives the
# prediction from the recorded executive prediction event and the actual
# from durable mission truth, then calls the canonical OutcomeService
# (``record_outcome``). Idempotency is structural — the outcome id
# (``candidate:{id}:immediate``) is keyed once in candidate_outcomes AND
# the ledger append dedupes on ``outcome:{candidate}:{window}`` — so any
# retry/restart/re-scan records exactly one outcome. PASSED-only rules,
# honest UNKNOWN, and STOP/lease semantics are preserved because this
# wrapper never bypasses record_outcome and only reads durable state.
# --------------------------------------------------------------------------

def reconcile_mission_outcome(
    database: FoundryDatabase, *, mission_id: str,
    source_id: str = "v3-outcomes",
) -> dict[str, Any]:
    """Authoritative automatic handoff: terminal PASSED -> OutcomeService.

    Returns ``{"recorded": bool, "idempotent": bool, "skipped_reason": str|None}``:
    - ``recorded``  — an outcome was appended by THIS call;
    - ``idempotent``— the outcome already existed (or was just written);
    - non-PASSED or non-candidate missions skip honestly without events.
    """
    mission = database.row("missions", "mission_id", mission_id)
    if mission is None:
        return {"recorded": False, "idempotent": False,
                "skipped_reason": f"unknown mission {mission_id}"}
    created_from = str(mission.get("created_from") or "")
    if not created_from.startswith("candidate:"):
        return {"recorded": False, "idempotent": False,
                "skipped_reason": "mission not candidate-lineage"}
    candidate_id = created_from.split(":", 1)[1]

    outcome_id = f"{candidate_id}:immediate"
    existing = database.row("candidate_outcomes", "outcome_id", outcome_id)
    if existing is not None:
        return {"recorded": False, "idempotent": True,
                "skipped_reason": None}

    # Terminal technical truth from the durable projection: only a mission
    # that actually COMPLETED with its completion event PASSED qualifies.
    state = str(mission.get("state") or "")
    completed_pass = False
    if state == "COMPLETE":
        for event in database.events():
            if event.event_type != "mission.completed":
                continue
            if str(event.payload.get("mission_id") or "") == mission_id \
                    and str(event.payload.get("outcome") or "") == "PASSED":
                completed_pass = True
                break
        # Bounded re-check via SQL when the Python scan missed it (e.g.
        # very large ledgers): the indexed type scan stays cheap either way.
        if not completed_pass:
            with database.connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) FROM events WHERE event_type = "
                    "'mission.completed' AND payload_json LIKE ?",
                    (f'%\"mission_id\":\"{mission_id}\"%',),
                ).fetchone()
            completed_pass = bool(row and row[0])
    if not completed_pass:
        return {"recorded": False, "idempotent": False,
                "skipped_reason": f"mission not terminal PASSED (state={state})"}

    # Predicted value: the recorded executive prediction for this candidate
    # (honest {} when none exists — status becomes UNKNOWN downstream).
    predicted_value: dict[str, Any] = {}
    for event in database.events():
        if event.event_type == "executive.prediction_recorded" \
                and str(event.payload.get("candidate_id") or "") == candidate_id:
            raw = event.payload.get("prediction")
            if isinstance(raw, dict):
                predicted_value = raw

    # Actual value: durable mission truth via the existing calibration reader.
    from .executive.calibration import outcome_from_mission

    actual_value = outcome_from_mission(database, mission_id)

    record_outcome(
        database, candidate_id=candidate_id, mission_id=mission_id,
        mission_outcome="PASSED",
        predicted_value=predicted_value or None,
        actual_value={"outcome": actual_value.get("outcome"),
                      "verdict": actual_value.get("verdict"),
                      "repair_count": actual_value.get("repair_count"),
                      "duration_seconds": actual_value.get("duration_seconds")},
        confidence=float(predicted_value.get("confidence") or 0.0)
        if isinstance(predicted_value.get("confidence"), (int, float)) else 0.0,
        source_id=source_id,
    )
    return {"recorded": True, "idempotent": True, "skipped_reason": None}


def reconcile_pending_outcomes(
    database: FoundryDatabase, *, limit: int = 100,
    source_id: str = "v3-outcomes",
) -> dict[str, Any]:
    """Catch-up scan: close crash windows left open by a dead process.

    Bounded scan over candidate-lineage missions that reached terminal
    COMPLETE but have no candidate_outcomes row; each is offered to
    :func:`reconcile_mission_outcome`. Safe to run repeatedly at startup:
    exactly-once is enforced per mission.
    """
    candidates: list[str] = []
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT m.mission_id FROM missions m
            WHERE m.state = 'COMPLETE'
              AND m.created_from LIKE 'candidate:%'
              ORDER BY m.updated_at DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        ids = [str(row[0]) for row in rows]
        for mission_id in ids:
            row = connection.execute(
                "SELECT COUNT(*) FROM candidate_outcomes WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
            if row is not None and int(row[0]) == 0:
                candidates.append(mission_id)
    reconciled = 0
    skipped = 0
    for mission_id in candidates:
        result = reconcile_mission_outcome(
            database, mission_id=mission_id, source_id=source_id)
        if result["recorded"]:
            reconciled += 1
        elif result["skipped_reason"]:
            skipped += 1
    return {"reconciled": reconciled, "scanned": len(candidates),
            "skipped": skipped}
