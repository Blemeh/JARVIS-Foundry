"""V6 lane JOB: outcome-feedback loop.

After a disposable improvement is accepted (or rolled back), this
module closes the learning cycle:

- record_outcome binds hypothesis predicted delta vs measured actual
  delta for the SAME target metric and classifies the outcome
  (CONFIRMED / PARTIAL / MISSED / ROLLED_BACK);
- calibration: per-rule running accuracy (confirmed / evaluated) is
  recomputed from the outcome ledger so future confidence is honest;
- anti-recursion: selecting feedback for the next improvement uses a
  DEDUPE identity (same rule + same evidence window never spawns a new
  mission twice) plus a COOLDOWN (a rule that produced an improvement
  within N hours is not immediately re-selected) and BOUNDED selection
  (at most one follow-up per evaluation).

No live mutation: everything appends to the isolated ledger.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from ..database import FoundryDatabase
from ..events import Event, canonical_json

#: minimum hours between accepting two improvements derived from the
#: same detector rule (anti-busywork cooldown)
COOLDOWN_HOURS = 24.0


class FeedbackError(ValueError):
    """An outcome record was refused."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FeedbackError(message)


def record_outcome(database: FoundryDatabase, *, improvement_id: str,
                   detector_rule: str, evidence_window: tuple[str, str],
                   predicted_delta: float, actual_delta: float | None,
                   unit: str, retained: bool,
                   lessons: list[str] | None = None,
                   dedupe_identity: dict[str, Any] | None = None,
                   ) -> dict[str, Any]:
    """Record predicted-vs-actual for ONE completed improvement.

    Classification (honest, no generosity):
    - ROLLED_BACK when retained is False;
    - CONFIRMED when the actual delta meets or beats the prediction
      in the predicted direction;
    - PARTIAL when it moved in the right direction but undershot;
    - MISSED otherwise (including absent measurement — recorded as
      None, never fabricated).
    """
    row = database.row("improvement_records", "improvement_id",
                       improvement_id)
    _require(row is not None,
             f"improvement {improvement_id} does not exist — refused")
    if retained:
        _require(actual_delta is not None,
                 "retained improvement requires a measured actual "
                 "delta — absence of evidence is not success")
    if actual_delta is None:
        classification = "ROLLED_BACK" if not retained else "MISSED"
    else:
        if not retained:
            classification = "ROLLED_BACK"
        elif (predicted_delta < 0 and actual_delta <= predicted_delta) \
                or (predicted_delta > 0 and
                    actual_delta >= predicted_delta):
            classification = "CONFIRMED"
        elif (predicted_delta < 0 and actual_delta < 0) or \
                (predicted_delta > 0 and actual_delta > 0):
            classification = "PARTIAL"
        else:
            classification = "MISSED"

    payload: dict[str, Any] = {
        "improvement_id": improvement_id,
        "detector_rule": detector_rule,
        "evidence_window": list(evidence_window),
        "dedupe_identity": (canonical_json(dedupe_identity)
                            if dedupe_identity is not None else None),
        "predicted_delta": predicted_delta,
        "actual_delta": actual_delta,
        "unit": unit,
        "retained": bool(retained),
        "classification": classification,
        "lessons": list(lessons or []),
    }
    outcome_id = "outcome-" + hashlib.sha256(canonical_json(
        payload).encode("utf-8")).hexdigest()[:12]
    payload["outcome_id"] = outcome_id
    database.append(Event.create(
        "improvement.outcome_recorded", payload,
        source_type="supervisor", source_id="v6",
        idempotency_key=f"improvement.outcome_recorded:{outcome_id}"))
    return payload


def rule_calibration(database: FoundryDatabase,
                     rule: str) -> dict[str, Any]:
    """Running accuracy for one detector rule, from the ledger only."""
    outcomes = [e.payload for e in database.events()
                if e.event_type == "improvement.outcome_recorded"
                and e.payload.get("detector_rule") == rule]
    evaluated = len(outcomes)
    confirmed = sum(1 for o in outcomes
                    if o["classification"] == "CONFIRMED")
    partial = sum(1 for o in outcomes
                  if o["classification"] == "PARTIAL")
    missed = sum(1 for o in outcomes if o["classification"] == "MISSED")
    rolled_back = sum(1 for o in outcomes
                      if o["classification"] == "ROLLED_BACK")
    return {
        "rule": rule, "evaluated": evaluated, "confirmed": confirmed,
        "partial": partial, "missed": missed, "rolled_back": rolled_back,
        # direction accuracy: right-direction rate among decided ones
        "accuracy": round((confirmed + 0.5 * partial) / evaluated, 3)
        if evaluated else None,
    }


def select_followup(database: FoundryDatabase, *, candidates: list[dict],
                    now_iso: str, max_followups: int = 1,
                    cooldown_hours: float = COOLDOWN_HOURS,
                    ) -> dict[str, Any]:
    """Bounded next-improvement selection over feedback.

    candidates: [{"rule", "dedupe_identity", "impact", "confidence"}].
    A candidate is skipped when its dedupe_identity already produced an
    outcome (never spawn the same improvement twice) or when its rule
    produced any outcome inside the cooldown window. Selection ranks by
    impact x calibrated confidence; WAITING when nothing qualifies.
    """
    now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)  # ledger timestamps are naive-local
    outcomes = [e.payload for e in database.events()
                if e.event_type == "improvement.outcome_recorded"]
    seen_identities = {o.get("dedupe_identity") for o in outcomes
                       if o.get("dedupe_identity")}
    recent_by_rule: dict[str, datetime] = {}
    all_events = database.events()
    for event in all_events:
        if event.event_type != "improvement.outcome_recorded":
            continue
        rule = event.payload.get("detector_rule")
        try:
            moment = datetime.fromisoformat(
                str(event.recorded_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is not None:
            moment = moment.replace(tzinfo=None)
        if rule not in recent_by_rule or moment > recent_by_rule[rule]:
            recent_by_rule[rule] = moment

    rejected: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    seen_in_call: set[str] = set()
    for candidate in candidates:
        rule = str(candidate["rule"])
        identity = canonical_json(candidate.get("dedupe_identity"))
        if identity in seen_identities or \
                identity in seen_in_call:
            rejected.append({**candidate,
                             "reason": "duplicate of an already "
                                       "evaluated evidence window"})
            continue
        seen_in_call.add(identity)
        # calibration floor BEFORE cooldown: a rule that does not work
        # is rejected for that reason regardless of recency
        calibration = rule_calibration(database, rule)
        accuracy = calibration["accuracy"]
        if accuracy is not None and accuracy < 0.4:
            rejected.append({**candidate,
                             "reason": f"rule {rule} calibration "
                                       f"{accuracy} below 0.4 floor"})
            continue
        last = recent_by_rule.get(rule)
        if last is not None and \
                now - last < timedelta(hours=cooldown_hours):
            rejected.append({**candidate,
                             "reason": f"rule {rule} in cooldown"})
            continue
        score = float(candidate.get("impact", 0.0)) * (
            float(candidate.get("confidence", 0.5))
            if accuracy is None else accuracy)
        eligible.append({**candidate, "score": round(score, 4)})

    eligible.sort(key=lambda c: -c["score"])
    selected = eligible[:max_followups]
    return {
        "selected": selected,
        "rejected": rejected,
        "waiting": len(selected) == 0,
    }
