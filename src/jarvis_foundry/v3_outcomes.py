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


def _terminal_payload(database: FoundryDatabase, mission_id: str,
                      mission_outcome: str) -> tuple[str, dict[str, Any], Event]:
    """Prove that an outcome belongs to a durable completed mission.

    A caller-supplied pair of IDs is not authority.  The mission projection
    and the append-only ``mission.completed`` event are the authority, and
    the candidate must be the one named by ``missions.created_from``.
    """
    mission = database.row("missions", "mission_id", mission_id)
    if mission is None:
        raise ValueError(f"outcome mission {mission_id!r} does not exist")
    created_from = str(mission.get("created_from") or "")
    if not created_from.startswith("candidate:"):
        raise ValueError(
            f"outcome mission {mission_id!r} is not linked to a candidate")
    candidate_id = created_from.split(":", 1)[1]
    if not candidate_id:
        raise ValueError(f"outcome mission {mission_id!r} has an empty candidate link")
    candidate = database.row("candidates", "candidate_id", candidate_id)
    if candidate is None:
        raise ValueError(
            f"outcome mission {mission_id!r} references missing candidate {candidate_id!r}")
    if str(mission.get("state") or "") != "COMPLETE":
        raise ValueError(
            f"outcome mission {mission_id!r} is not durably COMPLETE")

    completed: list[Event] = []
    for event in database.events():
        if event.event_type != "mission.completed":
            continue
        if str(event.payload.get("mission_id") or "") != mission_id:
            continue
        completed.append(event)
    if not completed:
        raise ValueError(
            f"outcome mission {mission_id!r} has no mission.completed proof")
    terminal = completed[-1]
    raw_outcome = terminal.payload.get("outcome")
    if isinstance(raw_outcome, dict):
        technical = (raw_outcome.get("technical")
                     or raw_outcome.get("status")
                     or raw_outcome.get("outcome"))
    else:
        technical = raw_outcome
    if str(technical or "") != mission_outcome:
        raise ValueError(
            f"outcome mission {mission_id!r} terminal proof is "
            f"{technical!r}, not {mission_outcome!r}")
    return candidate_id, dict(raw_outcome) if isinstance(raw_outcome, dict) else {
        "technical": raw_outcome,
    }, terminal


def _lineage(database: FoundryDatabase, *, candidate_id: str,
             mission_id: str, completed: Event) -> dict[str, Any]:
    """Return the strongest available durable IDs for one terminal outcome."""
    events = database.events()
    decision_id: str | None = None
    for event in events:
        if event.event_type not in (
                "executive.decision_recorded",
                "executive.decision_materialized"):
            continue
        if str(event.payload.get("candidate_id") or "") != candidate_id:
            continue
        if event.payload.get("mission_id") not in (None, mission_id):
            continue
        value = event.payload.get("decision_id")
        if value:
            decision_id = str(value)
    contracts = [row for row in database.all("mission_step_contracts")
                 if str(row.get("mission_id") or "") == mission_id]
    contract = contracts[-1] if contracts else {}
    verifier_id: str | None = None
    verifier_run_id: str | None = None
    evidence_id: str | None = None
    worker_run_id: str | None = None
    for event in events:
        payload = event.payload
        if str(payload.get("mission_id") or "") != mission_id:
            continue
        if event.event_type == "agent_run.completed":
            run_id = str(payload.get("run_id") or "")
            run_row = (database.row("agent_runs", "run_id", run_id)
                       if run_id else None)
            role = str((run_row or {}).get("role") or payload.get("role") or "")
            if role == "verifier":
                verifier_run_id = run_id or verifier_run_id
            else:
                worker_run_id = run_id or worker_run_id
        elif event.event_type == "evidence.created":
            evidence_id = str(payload.get("bundle_id") or "") or evidence_id
        elif event.event_type in ("verification.passed", "verification.failed",
                                  "verification.blocked"):
            verifier_id = str(payload.get("verdict_id") or "") or verifier_id
            verifier_run_id = str(payload.get("verifier_run_id") or "") or verifier_run_id
    return {
        "candidate_id": candidate_id,
        "decision_id": decision_id,
        "mission_id": mission_id,
        "step_id": str(contract.get("step_id") or "") or None,
        "worker_run_id": worker_run_id,
        "evidence_bundle_id": evidence_id,
        "verifier_run_id": verifier_run_id,
        "verdict_id": verifier_id,
        "mission_completed_event_id": completed.event_id,
    }


def record_outcome(
    database: FoundryDatabase, *, candidate_id: str, mission_id: str,
    mission_outcome: str,
    predicted_value: dict[str, Any] | None = None,
    actual_value: dict[str, Any] | None = None,
    confidence: float = 0.0,
    source_id: str = "v3-outcomes",
    continuation_loop_id: str | None = None,
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

    linked_candidate_id, terminal_value, completed = _terminal_payload(
        database, mission_id, mission_outcome)
    if linked_candidate_id != candidate_id:
        raise ValueError(
            f"candidate {candidate_id!r} is not the durable candidate linked "
            f"to mission {mission_id!r}")

    # A mission.completed/PASSED event is not sufficient by itself. The
    # exact latest PASS verdict must also carry the durable AcceptanceGate
    # receipt; this prevents a preliminary verification.passed from feeding
    # candidate learning or calibration.
    from .executive.calibration import outcome_from_mission

    actual_truth = outcome_from_mission(database, mission_id)
    if actual_truth.get("outcome") != "PASSED":
        raise ValueError(
            f"outcome mission {mission_id!r} lacks final AcceptanceGate "
            "acceptance for its latest verifier PASS")

    # Missing measurements stay missing.  The terminal event proves that the
    # mission passed, but a technical PASS is not a measured actual value.
    # Runtime callers that have real predicted/actual values must supply them.
    if predicted_value is None:
        predicted_value = {}
    if actual_value is None:
        actual_value = {}
    has_measurement = bool(predicted_value) and bool(actual_value)
    lineage = _lineage(database, candidate_id=candidate_id,
                       mission_id=mission_id, completed=completed)
    window = "immediate"
    payload: dict[str, Any] = {
        "candidate_id": candidate_id,
        "mission_id": mission_id,
        "window": window,
        "predicted_value": predicted_value or {},
        "actual_value": actual_value or {},
        "outcome": {"predicted": predicted_value or {},
                     "actual": actual_value or {},
                     "status": "RECORDED" if has_measurement else "UNKNOWN"},
        "status": "RECORDED" if has_measurement else "UNKNOWN",
        "confidence": max(0.0, min(1.0, float(confidence))),
        "outcome_id": f"{candidate_id}:{window}",
        "lineage": lineage,
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
    continuation = maintenance_scan_hook(
        database, loop_id=continuation_loop_id or mission_id)
    payload["continuation"] = continuation
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
    """Schedule one durable maintenance scan and the next selection.

    The request is append-only and idempotent.  It does not invent a
    candidate: when no eligible candidate exists, the scheduled selection is
    an honest WAIT that names the condition which can wake it later.
    """
    with database.connect() as connection:
        unknown = connection.execute(
            "SELECT COUNT(*) FROM candidate_outcomes WHERE status = "
            "'UNKNOWN'").fetchone()[0]
        recorded = connection.execute(
            "SELECT COUNT(*) FROM candidate_outcomes WHERE status = "
            "'RECORDED'").fetchone()[0]
        eligible = connection.execute(
            "SELECT COUNT(*) FROM candidates WHERE status IN "
            "('NEW', 'SCORED', 'ELIGIBLE')").fetchone()[0]
    next_selection_id = f"exec-{loop_id}:next"
    wait_reason = (None if eligible else
                   "no eligible candidates; wait for fresh evidence")
    payload = {
        "loop_id": loop_id,
        "outcomes_recorded": int(recorded),
        "outcomes_unknown": int(unknown),
        "eligible_candidates": int(eligible),
        "next_selection_id": next_selection_id,
        "wait_reason": wait_reason,
    }
    database.append(Event.create(
        "discovery.maintenance_scan_scheduled", payload,
        source_type="supervisor", source_id="v3-outcomes",
        idempotency_key=f"maintenance.scan:{loop_id}"))
    database.append(Event.create(
        "executive.next_selection_requested",
        {**payload, "request_kind": "MAINTENANCE_OUTCOME_FOLLOW_UP"},
        source_type="supervisor", source_id="v3-outcomes",
        idempotency_key=f"executive.next_selection:{loop_id}"))
    return {**payload, "wake_scheduled": True}


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
        # The predicate requires BOTH the mission id AND outcome=PASSED so a
        # raw-written non-PASSED completion can never satisfy the gate.
        if not completed_pass:
            with database.connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) FROM events WHERE event_type = "
                    "'mission.completed' AND payload_json LIKE ? "
                    "AND payload_json LIKE ?",
                    (f'%\"mission_id\":\"{mission_id}\"%',
                     '%\"outcome\":\"PASSED\"%'),
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
    if actual_value.get("outcome") != "PASSED":
        return {"recorded": False, "idempotent": False,
                "skipped_reason": (
                    "mission has no final AcceptanceGate acceptance for "
                    "its latest verifier PASS")}

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
