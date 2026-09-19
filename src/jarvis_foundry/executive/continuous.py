from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from ..clock import isoformat, parse
from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event, EventConflict, canonical_json
SUPERVISOR_CONTROLLER = "supervisor"
WAKE_KINDS = frozenset({"WAIT_UNTIL_TIME", "WAIT_FOR_DEPENDENCY", "WAIT_FOR_CAPABILITY", "WAIT_FOR_PROVIDER", "WAIT_FOR_OWNER", "WAIT_FOR_SIGNAL", "WAIT_FOR_RESOURCE"})
EXEC_STATES = frozenset({"RUNNABLE", "WAITING", "BLOCKED", "DUE", "ACTIVE", "STALE", "SUPERSEDED", "COMPLETE", "FAILED", "OWNER_BLOCKED", "CAPABILITY_BLOCKED", "PROVIDER_BLOCKED"})
def _hash(v):
    import hashlib as _hl
    from ..events import canonical_json as _cj
    return _hl.sha256(_cj(v).encode("utf-8")).hexdigest()[:16]
def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
def _dt(v):
    if not v: return None
    from datetime import datetime, timezone
    if isinstance(v, datetime): return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try: return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError: return None
@dataclass(frozen=True, slots=True)
class WakeCondition:
    wake_id: str
    objective_id: str
    kind: str
    reason: str
    created_at: str
    not_before: str | None = None
    dependency_id: str | None = None
    capability: str | None = None
    provider_id: str | None = None
    signal: str | None = None
    resource_id: str | None = None
    status: str = "ACTIVE"
    triggered_at: str | None = None
    def to_payload(self):
        return {"wake_id": self.wake_id, "objective_id": self.objective_id, "kind": self.kind, "reason": self.reason, "created_at": self.created_at, "not_before": self.not_before, "dependency_id": self.dependency_id, "capability": self.capability, "provider_id": self.provider_id, "signal": self.signal, "resource_id": self.resource_id, "status": self.status, "triggered_at": self.triggered_at}
def _append(db, event_type, payload, *, key, source_id="v9-continuous", correlation_id=None):
    from ..events import Event
    e = Event.create(event_type, dict(payload), source_type="supervisor", source_id=source_id, correlation_id=correlation_id, idempotency_key=key)
    return str(db.append(e)[0])
def _wake_key(w):
    return f"executive.wake_created:{w.wake_id}"
def create_wake(db, wake: WakeCondition, *, controller="supervisor"):
    if wake.kind not in WAKE_KINDS: raise ValueError(f"unknown wake kind: {wake.kind}")
    if controller != SUPERVISOR_CONTROLLER: raise PermissionError("wake creation is Supervisor-owned")
    payload = wake.to_payload()
    return _append(db, "executive.wake_created", payload, key=_wake_key(wake), correlation_id=wake.objective_id)
def clear_wake(db, wake_id: str, *, controller="supervisor", reason=""):
    if controller != SUPERVISOR_CONTROLLER: raise PermissionError("wake clear is Supervisor-owned")
    return _append(db, "executive.wake_cleared", {"wake_id": wake_id, "reason": reason}, key=f"executive.wake_cleared:{wake_id}", correlation_id=wake_id)
def trigger_wake(db, wake_id: str, *, controller="supervisor", reason=""):
    if controller != SUPERVISOR_CONTROLLER: raise PermissionError("wake trigger is Supervisor-owned")
    return _append(db, "executive.wake_triggered", {"wake_id": wake_id, "reason": reason, "triggered_at": isoformat()}, key=f"executive.wake_triggered:{wake_id}:{_hash(reason)}", correlation_id=wake_id)
def rearm_wake(db, wake_id: str, *, controller="supervisor", reason="", generation: str | None = None):
    """Return a triggered wake to ACTIVE when its guarded predicate is false.

    A trigger is a durable notification, not proof that a dependent child was
    released.  Recovery may therefore need to re-arm the same wake after a
    signal raced with dependency/provider/effect reconciliation.  The receipt
    is idempotent per trigger generation and remains inside the existing
    Continuous Executive wake authority.
    """
    if controller != SUPERVISOR_CONTROLLER: raise PermissionError("wake rearm is Supervisor-owned")
    payload = {
        "wake_id": wake_id,
        "reason": reason,
        "rearmed_at": isoformat(),
        "generation": generation,
    }
    return _append(
        db,
        "executive.wake_rearmed",
        payload,
        key=f"executive.wake_rearmed:{wake_id}:{_hash({'generation': generation, 'reason': reason})}",
        correlation_id=wake_id,
    )
def rebuild_wake_state(db):
    wakes = {}
    for ev in db.events(event_type_prefixes=("executive.wake_",)):
        p = ev.payload; typ = ev.event_type
        if typ == "executive.wake_created":
            wid = str(p.get("wake_id") or "")
            if wid: wakes[wid] = dict(p)
        elif typ == "executive.wake_cleared":
            wid = str(p.get("wake_id") or "")
            if wid in wakes: wakes[wid]["status"] = "CLEARED"
        elif typ == "executive.wake_triggered":
            wid = str(p.get("wake_id") or "")
            if wid in wakes:
                wakes[wid]["status"] = "TRIGGERED"
                wakes[wid]["triggered_at"] = p.get("triggered_at")
                wakes[wid]["triggered_event_id"] = ev.event_id
        elif typ == "executive.wake_rearmed":
            wid = str(p.get("wake_id") or "")
            if wid in wakes:
                wakes[wid]["status"] = "ACTIVE"
                wakes[wid]["triggered_at"] = None
                wakes[wid].pop("triggered_event_id", None)
    return wakes
def is_wake_due(db, wake: dict, now=None, *, state=None):
    from datetime import datetime, timezone
    n = now or datetime.now(timezone.utc)
    k = wake.get("kind") or ""
    if wake.get("status") not in ("ACTIVE", None) and wake.get("status") != "ACTIVE":
        # Only ACTIVE wakes can be due; but ledger stores ACTIVE by default
        if wake.get("status") in ("CLEARED", "TRIGGERED"):
            return False
    if k == "WAIT_UNTIL_TIME":
        dt = _dt(wake.get("not_before"))
        return dt is not None and dt <= n
    if k == "WAIT_FOR_DEPENDENCY":
        dep = wake.get("dependency_id")
        if not dep: return False
        try:
            from ..executive.autonomy import rebuild_executive_state as _reb
            st = state or _reb(db)
            obj = st.objectives.get(str(dep))
            return obj is not None and obj.status == "COMPLETED"
        except Exception: return False
    if k == "WAIT_FOR_CAPABILITY":
        cap = wake.get("capability"); rid = wake.get("resource_id")
        if not cap: return False
        try:
            from ..executive.autonomy import _capability_blockers as _cb, PlanStep as _PS
            step = _PS(step_id="probe", objective_id=wake.get("objective_id") or "", description="probe", required_capabilities=(str(cap),), resource_id=rid)
            return not _cb(db, step, resource_id=rid)
        except Exception: return False
    if k == "WAIT_FOR_PROVIDER":
        pid = wake.get("provider_id")
        if not pid: return False
        created = _dt(wake.get("created_at"))
        # Re-probe via the provider ledger.  Historical outage events that
        # caused this wake must not immediately satisfy it; only a later,
        # positive health/auth/availability observation can release the park.
        try:
            for ev in db.events(
                readonly=True,
                newest_first=True,
                limit=500,
                event_type_prefixes=("provider.", "capability."),
            ):
                p = ev.payload
                if not (
                    pid in str(p.get("provider_id") or "")
                    or pid in str(p.get("provider") or "")
                    or pid in str(p.get("capability") or "")
                ):
                    continue
                observed_at = _dt(ev.recorded_at)
                if created is not None and (
                    observed_at is None or observed_at <= created
                ):
                    continue
                health = str(p.get("health") or p.get("status") or "UNKNOWN").upper()
                auth = str(p.get("auth_state") or "UNKNOWN").upper()
                availability = str(p.get("availability") or "UNKNOWN").upper()
                if (
                    health in {"HEALTHY", "READY"}
                    and auth in {"READY", "CONFIGURED"}
                    and availability in {"AVAILABLE", "READY"}
                ):
                    return True
        except Exception:
            pass
        # Without a later positive provider observation, stay parked.  The
        # Supervisor may still explicitly trigger the wake after a verified
        # recovery event through the same durable API.
        return False
    if k == "WAIT_FOR_OWNER":
        # Wakes on next owner directive (GOAL, VETO, CANCEL, etc.)
        try:
            for ev in db.events(event_type_prefixes=("owner_directive.", "executive.objective_", "owner_controls.")):
                if _dt(wake.get("created_at")) and _dt(ev.recorded_at) and _dt(ev.recorded_at) > _dt(wake.get("created_at")):
                    return True
        except Exception:
            pass
        return False
    if k == "WAIT_FOR_SIGNAL":
        sig = wake.get("signal")
        if not sig: return False
        try:
            for ev in db.events(event_type_prefixes=(sig,)):
                if _dt(wake.get("created_at")) and _dt(ev.recorded_at) and _dt(ev.recorded_at) > _dt(wake.get("created_at")):
                    return True
        except Exception:
            pass
        return False
    if k == "WAIT_FOR_RESOURCE":
        rid = wake.get("resource_id")
        if not rid: return False
        try:
            from ..executive.autonomy import rebuild_executive_state as _reb2
            # Resource exists as a capability target — check if resource-registered
            for ev in db.events(event_type_prefixes=("project.registered", "capability.")):
                p = ev.payload
                if rid in str(p.get("resource_id") or ""):
                    if _dt(wake.get("created_at")) and _dt(ev.recorded_at) and _dt(ev.recorded_at) > _dt(wake.get("created_at")):
                        return True
        except Exception:
            pass
        return False
    return False
def due_wakes(db, now=None, *, wakes: dict | None = None, state=None):
    current_wakes = wakes if wakes is not None else rebuild_wake_state(db)
    return [
        wake for wake in current_wakes.values()
        if wake.get("status") == "ACTIVE"
        and is_wake_due(db, wake, now=now, state=state)
    ]


def _exec_state_for_objective(
    db,
    objective_id: str,
    wakes: dict | None = None,
    *,
    state=None,
    ranking=None,
):
    from ..executive.autonomy import (
        _evaluate_objective_progress_pure as _eval_pure,
        rebuild_executive_state as _reb,
        rank_objectives as _rank,
    )
    st = state or _reb(db)
    obj = st.objectives.get(objective_id)
    if obj is None: return "UNKNOWN"
    wakes = wakes if wakes is not None else rebuild_wake_state(db)
    my_wakes = [w for w in wakes.values() if w.get("objective_id") == objective_id and w.get("status") == "ACTIVE"]
    has_due = any(is_wake_due(db, w, state=st) for w in my_wakes)
    if obj.status == "COMPLETED": return "COMPLETE"
    if obj.status in ("CANCELLED", "SUSPENDED"):
        if obj.status == "SUSPENDED": return "OWNER_BLOCKED"
        return "SUPERSEDED"
    if obj.status == "FAILED": return "FAILED"
    # Blocked by owner controls
    veto = matching_veto_reason(db, {"GLOBAL", "executive", "objectives", objective_id})
    if veto: return "OWNER_BLOCKED"
    if emergency_stop_reason(db, db.path.parent / "EMERGENCY_STOP"): return "OWNER_BLOCKED"
    # Capability/provider blocked via progress blockers
    try:
        rows = ranking if ranking is not None else _rank(db, _state=st)
        row = next((r for r in rows if r.objective_id == objective_id), None)
        if row and not row.eligible and any("capability" in b or "provider" in b.lower() for b in row.blockers):
            if any("provider" in b.lower() for b in row.blockers): return "PROVIDER_BLOCKED"
            return "CAPABILITY_BLOCKED"
        if row and not row.eligible and row.blockers: return "BLOCKED"
    except Exception: pass
    if my_wakes and not has_due: return "WAITING"
    if has_due: return "DUE"
    # Stale detection
    try:
        ev = _eval_pure(db, objective_id, state=st)
        if ev.stale: return "STALE"
    except Exception: pass
    if obj.status == "ACTIVE": return "RUNNABLE"
    return obj.status
def continuous_snapshot(db, *, include_wakes=True):
    from ..executive.autonomy import (
        executive_autonomy_snapshot as _snap,
        rank_objectives as _rank,
        rebuild_executive_state as _reb,
    )
    executive_events = db.events(event_type_prefixes=("executive.",))
    st = _reb(db, _events=executive_events)
    ranking = _rank(db, _state=st, _events=executive_events)
    wakes = rebuild_wake_state(db) if include_wakes else {}
    states = {}
    for oid in st.objectives:
        states[oid] = _exec_state_for_objective(
            db, oid, wakes=wakes, state=st, ranking=ranking
        )
    due = due_wakes(db, wakes=wakes, state=st) if include_wakes else []
    base = _snap(
        db, _state=st, _ranking=ranking, _events=executive_events
    )
    base["continuous"] = {"wake_state": wakes, "exec_states": states, "due_wakes": due}
    return base
def tick_wakes(db, *, controller="supervisor", now=None):
    if controller != SUPERVISOR_CONTROLLER: raise PermissionError("tick is Supervisor-owned")
    due = due_wakes(db, now=now)
    triggered = []
    for w in due:
        try:
            trigger_wake(db, w["wake_id"], controller=controller, reason="wake condition satisfied")
            triggered.append(w["wake_id"])
        except Exception: pass
    return triggered


NEXT_ACTION_WAKE_PREFIX = "next-action:"


def consume_next_action_wakes(
    db,
    *,
    cycle_id: str,
    consumer_id: str = "",
    controller="supervisor",
    now=None,
):
    """Consume the Executive's own PASS-generated next-action wakes.

    A verified terminal mission outcome schedules exactly one durable
    ``next-action:{mission_id}`` wake and one ``executive.next_action_scheduled``
    receipt.  This seam is its consumer: the wake must still be ACTIVE and must
    be due, and the receipt records the exact wake, its bound objective, the
    cycle that consumed it, and the runtime identity of that consumer.

    This is not a second selector.  It releases only the Executive's own
    already-scheduled continuation, and a wake that is not due is left ACTIVE
    and reported as deferred rather than silently dropped.
    """
    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("next-action consumption is Supervisor-owned")
    receipts = []
    for wake in rebuild_wake_state(db).values():
        wake_id = str(wake.get("wake_id") or "")
        if not wake_id.startswith(NEXT_ACTION_WAKE_PREFIX):
            continue
        if wake.get("status") != "ACTIVE":
            continue
        objective_id = str(wake.get("objective_id") or "")
        due = bool(is_wake_due(db, wake, now=now))
        mission_id = wake_id[len(NEXT_ACTION_WAKE_PREFIX):]
        payload = {
            "wake_id": wake_id,
            "mission_id": mission_id,
            "objective_id": objective_id,
            "objective_bound": not objective_id.startswith("mission:"),
            "kind": wake.get("kind"),
            "not_before": wake.get("not_before"),
            "due": due,
            "consumer": consumer_id,
            "cycle_id": cycle_id,
            "consumed_at": isoformat(),
        }
        if due:
            try:
                trigger_wake(
                    db, wake_id, controller=controller,
                    reason=f"next action consumed by {consumer_id or 'supervisor'}",
                )
            except Exception:
                pass
        try:
            _append(
                db,
                "executive.next_action_consumed",
                payload,
                key=f"executive.next_action_consumed:{wake_id}:{consumer_id}",
                correlation_id=objective_id,
            )
        except Exception:
            continue
        receipts.append(payload)
    return receipts


def record_next_action_continuation(
    db,
    *,
    cycle_id: str,
    consumed,
    decision_before: int = 0,
    context_before: int = 0,
    controller="supervisor",
):
    """Bind each consumed next-action wake to the fresh decision it produced.

    Without this the receipt would only prove that a wake was triggered.  The
    continuation receipt records whether the same cycle then assembled fresh
    Executive context and made a fresh decision, so an absent decision is
    visible as an explicit ``fresh_decision: false`` rather than passing
    vacuously.
    """
    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("next-action continuation is Supervisor-owned")
    consumed = list(consumed or ())
    if not consumed:
        return []
    decisions = list(db.events(event_types=("executive.decision_recorded",)))
    contests = list(db.events(event_types=("executive.context_assembled",)))
    fresh = decisions[decision_before:]
    decision_id = fresh[-1].payload.get("decision_id") if fresh else None
    decision_kind = fresh[-1].payload.get("decision") if fresh else None
    receipts = []
    for item in consumed:
        payload = {
            "wake_id": item["wake_id"],
            "mission_id": item["mission_id"],
            "objective_id": item["objective_id"],
            "cycle_id": cycle_id,
            "decision_id": decision_id,
            "decision_kind": decision_kind,
            "fresh_decision": decision_id is not None,
            "fresh_context": len(contests) > int(context_before),
            "continued_at": isoformat(),
        }
        try:
            _append(
                db,
                "executive.next_action_continued",
                payload,
                key=f"executive.next_action_continued:{item['wake_id']}:{cycle_id}",
                correlation_id=str(item.get("objective_id") or ""),
            )
        except Exception:
            continue
        receipts.append(payload)
    return receipts


RESEARCH_WAKE_PREFIX = "wake:research:"
RESEARCH_COMPLETION_EVENT = "research.candidate_completed"


def _research_objective_id(candidate_id: str) -> str:
    """The deterministic Executive objective an admitted candidate owns.

    Mirrors the single admission authority (``initiative_a3`` creates
    ``initiative-objective:{candidate_id}``); this resolver never creates it.
    """

    return f"initiative-objective:{candidate_id}"


def _triggered_research_wakes(db, research_bridged) -> list[str]:
    """Bridged wake ids whose canonical wake path durably triggered them.

    ``research_bridged`` holds a receipt per completed research item, including
    the unbound ones whose wake was deliberately never created (and any whose
    creation lost an idempotency race).  Merging those ids into a cycle's
    ``triggered_wakes`` would advertise wakes that do not exist.

    Existence alone is not evidence of a trigger either: a re-armed wake, or a
    wake whose completion is not yet durable, is ACTIVE in the projection while
    its id is still in ``research_bridged``.  A wake may be reported as
    triggered only when the canonical projection records a durable
    ``executive.wake_triggered`` for it, so the reported set is derived from
    the trigger evidence rather than from the wake's existence.
    """

    existing = rebuild_wake_state(db)
    return [
        wake_id
        for wake_id in (str(item.get("wake_id") or "") for item in research_bridged)
        if wake_id
        and (existing.get(wake_id) or {}).get("status") == "TRIGGERED"
        and (existing.get(wake_id) or {}).get("triggered_event_id")
    ]


def _existing_objective_ids(db) -> set[str]:
    try:
        from ..executive.autonomy import rebuild_executive_state
        return {str(key) for key in rebuild_executive_state(db).objectives}
    except Exception:
        return set()


def _research_completion_receipt(db, research_id: str):
    """Return ``{"event_id", "sequence"}`` for the durable completion, if any.

    The lookup is an exact payload query rather than a bounded newest-first
    scan: a long-lived ledger can hold more completions than any scan window,
    and a missed receipt would silently defer a wake that is genuinely due.

    The status is part of the query and mirrors ``projections.py`` exactly.
    The projection seals a completion with ``str(payload.get("status") or
    "COMPLETED")``, so any FALSY status - missing, null, empty string, ``False``
    or ``0`` - becomes ``COMPLETED`` there.  This lookup therefore normalises a
    falsy status to ``COMPLETED`` before comparing, and only refuses the values
    the projection itself would record as non-completed attempts.  A divergence
    in either direction is a defect: matching a FAILED attempt would release a
    wake as ``research_completion_durable``, and refusing a completion the
    projection sealed as COMPLETED would make the bridge create a wake the
    consumer can never release.

    The lookup mirrors that expression for EVERY falsy JSON form the Python
    ``x or 'COMPLETED'`` accepts - a missing path, ``null``, ``false``, ``0``,
    ``0.0``, the empty string, and the empty array/object - by switching on
    ``json_type`` rather than on the extracted value, so a JSON *string*
    ``"[]"`` (truthy in Python, and refused by the projection) is still refused
    here.  The comparison is EXACT text, not case-folded: the projection REFUSES
    ``"completed"``/``"Completed"`` (they are not in its accepted set, so it
    raises instead of sealing), and the receipt must refuse them too — otherwise
    it could cite an event the projection never sealed as the durable completion
    (found by the review of `47fda68`).  Values the projection records as
    non-completed attempts are refused; a completion the projection sealed as
    COMPLETED is always releaseable.
    A divergence in either direction is a defect: matching a FAILED attempt
    would release a wake as ``research_completion_durable``, and refusing a
    completion the projection sealed as COMPLETED would make the bridge create
    a wake the consumer can never release.
    """

    if not research_id:
        return None
    try:
        with db.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT sequence, event_id FROM events "
                "WHERE event_type = ? "
                "AND json_extract(payload_json, '$.research_id') = ? "
                "AND (CASE "
                "       WHEN json_type(payload_json, '$.status') IS NULL "
                "         THEN 'COMPLETED' "
                "       WHEN json_type(payload_json, '$.status') = 'null' "
                "         THEN 'COMPLETED' "
                "       WHEN json_type(payload_json, '$.status') = 'false' "
                "         THEN 'COMPLETED' "
                "       WHEN json_type(payload_json, '$.status') IN ('integer', 'real') "
                "         AND json_extract(payload_json, '$.status') = 0 "
                "         THEN 'COMPLETED' "
                "       WHEN json_type(payload_json, '$.status') = 'text' "
                "         AND json_extract(payload_json, '$.status') = '' "
                "         THEN 'COMPLETED' "
                "       WHEN json_type(payload_json, '$.status') = 'array' "
                "         AND json_extract(payload_json, '$.status') = '[]' "
                "         THEN 'COMPLETED' "
                "       WHEN json_type(payload_json, '$.status') = 'object' "
                "         AND json_extract(payload_json, '$.status') = '{}' "
                "         THEN 'COMPLETED' "
                "       ELSE CAST(json_extract(payload_json, '$.status') "
                "            AS TEXT) END) = 'COMPLETED' "
                "ORDER BY sequence DESC LIMIT 1",
                (RESEARCH_COMPLETION_EVENT, research_id),
            ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return {"event_id": str(row["event_id"]), "sequence": int(row["sequence"])}


def bridge_research_completions(
    db,
    *,
    controller="supervisor",
    limit=100,
):
    """Admit durable candidate-research completions as Executive wakes.

    Research is evidence gathering: it never selects work.  A completed
    candidate dossier is already durable in the canonical research projection
    by the time this reader sees it, so the bridge creates exactly one
    ``WAIT_FOR_SIGNAL`` wake per research id (``wake:research:{research_id}``,
    which is the audit's unique-per-research-item identity) and binds it to the
    Executive objective that owns the candidate.

    A candidate whose objective has not been admitted yet gets an honest
    ``objective_bound: false`` receipt and NO wake: inventing a wake for an
    objective that does not exist would advertise continuation the Executive
    could never consume.  The next cycle binds it once admission lands.

    This is the same narrow Supervisor-owned bridge shape as
    ``bridge_world_signals``: it creates no objective, mission, candidate or
    selector, and it re-derives everything from the canonical projection.
    """

    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("research bridging is Supervisor-owned")
    try:
        from ..control.broker import emergency_stop_reason as _esr
        from pathlib import Path as _P
        if _esr(db, _P(db.path).parent / "EMERGENCY_STOP"):
            return []
    except Exception:
        return []
    try:
        from ..research_queue import candidate_research_state
        rows = list(candidate_research_state(db))
    except Exception:
        return []

    objective_ids = _existing_objective_ids(db)
    wakes = rebuild_wake_state(db)
    bridged: list[dict[str, Any]] = []
    for row in rows[: max(1, min(int(limit), 500))]:
        if str(row.get("status") or "").upper() != "COMPLETED":
            continue
        research_id = str(row.get("research_id") or "")
        candidate_id = str(row.get("candidate_id") or "")
        if not research_id or not candidate_id:
            continue
        wake_id = f"{RESEARCH_WAKE_PREFIX}{research_id}"
        objective_id = _research_objective_id(candidate_id)
        objective_bound = objective_id in objective_ids
        evidence = row.get("evidence") if isinstance(row.get("evidence"), Mapping) else {}
        stance = str(evidence.get("stance") or evidence.get("verdict") or "").upper() or None
        confidence = evidence.get("confidence")
        reason = (
            f"research {research_id} completed for candidate {candidate_id} "
            f"(stance={stance or 'UNKNOWN'}, confidence={confidence}); "
            "reconsider the decision with the findings loaded"
        )
        wake_created = False
        if objective_bound and wake_id not in wakes:
            wake = WakeCondition(
                wake_id=wake_id,
                objective_id=objective_id,
                kind="WAIT_FOR_SIGNAL",
                reason=reason[:480],
                created_at=isoformat(),
                not_before=isoformat(),
                signal=f"{RESEARCH_COMPLETION_EVENT}:{research_id}",
            )
            try:
                create_wake(db, wake, controller=controller)
            except Exception:
                continue
            wakes[wake_id] = wake.to_payload()
            wake_created = True
        completion = _research_completion_receipt(db, research_id)
        payload = {
            "wake_id": wake_id,
            "research_id": research_id,
            "candidate_id": candidate_id,
            "objective_id": objective_id,
            "objective_bound": objective_bound,
            "wake_created": wake_created,
            "status": str(row.get("status") or ""),
            "source_ref": row.get("source_ref"),
            "content_hash": row.get("content_hash"),
            "stance": stance,
            "confidence": confidence,
            "completion_event_id": (completion or {}).get("event_id"),
            "completion_sequence": (completion or {}).get("sequence"),
            "bridged_at": isoformat(),
        }
        receipt_replayed = False
        try:
            _append(
                db,
                "executive.research_bridged",
                payload,
                key=f"executive.research_bridged:{wake_id}:{objective_bound}",
                correlation_id=objective_id,
            )
        except EventConflict:
            # A replay of the same (wake, binding) pair is not a failure: the
            # receipt is already durable, and the caller still needs the wake id.
            receipt_replayed = True
        except Exception:
            continue
        payload["receipt_replayed"] = receipt_replayed
        bridged.append(payload)
    return bridged


def consume_research_wakes(
    db,
    *,
    cycle_id: str,
    consumer_id: str = "",
    controller="supervisor",
    now=None,
):
    """Release bridged research wakes to the cycle that can use them.

    The wake's documented condition is the durable completion of the research
    item it cites (``wake:research:{research_id}`` -> ``research.candidate_completed``
    for that research id).  Because the bridge only creates the wake after the
    completion is already durable, the generic ``is_wake_due`` signal ordering
    (signal strictly after wake creation) can never be satisfied; the exact
    ordering facts are recorded on the receipt instead of being assumed.  A
    wake that is still undecided, or bound to an objective that no longer
    exists, is left ACTIVE and reported as deferred rather than dropped.
    """

    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("research-wake consumption is Supervisor-owned")
    objective_ids = _existing_objective_ids(db)
    receipts = []
    for wake in rebuild_wake_state(db).values():
        wake_id = str(wake.get("wake_id") or "")
        if not wake_id.startswith(RESEARCH_WAKE_PREFIX):
            continue
        if wake.get("status") != "ACTIVE":
            continue
        objective_id = str(wake.get("objective_id") or "")
        research_id = wake_id[len(RESEARCH_WAKE_PREFIX):]
        objective_bound = objective_id in objective_ids
        completion = _research_completion_receipt(db, research_id)
        completion_durable = completion is not None
        signal_due = False
        try:
            signal_due = bool(is_wake_due(db, wake, now=now))
        except Exception:
            signal_due = False
        due = bool(objective_bound and completion_durable)
        payload = {
            "wake_id": wake_id,
            "research_id": research_id,
            "objective_id": objective_id,
            "objective_bound": objective_bound,
            "kind": wake.get("kind"),
            "signal": wake.get("signal"),
            "due": due,
            "due_basis": (
                "research_completion_durable"
                if due and not signal_due
                else "wake_condition_satisfied"
                if due
                else "objective_not_bound"
                if not objective_bound
                else "completion_not_durable"
            ),
            "signal_due_via_generic_predicate": signal_due,
            "completion_event_id": (completion or {}).get("event_id"),
            "completion_sequence": (completion or {}).get("sequence"),
            "consumer": consumer_id,
            "cycle_id": cycle_id,
            "consumed_at": isoformat(),
        }
        if due:
            try:
                trigger_wake(
                    db,
                    wake_id,
                    controller=controller,
                    reason=f"research {research_id} consumed by {consumer_id or 'supervisor'}",
                )
            except Exception:
                pass
        try:
            _append(
                db,
                "executive.research_wake_consumed",
                payload,
                key=f"executive.research_wake_consumed:{wake_id}:{consumer_id}",
                correlation_id=objective_id,
            )
        except EventConflict:
            # The consumption receipt is one per (wake, consumer) pair, and a
            # re-armed research wake reuses both ids, so a second consumption by
            # the same consumer collides.  Record the skip explicitly instead of
            # swallowing it: a silent conflict leaves the wake ACTIVE while the
            # caller sees nothing at all.
            skipped = {
                **payload,
                "consumed": False,
                "reason": "already_recorded_for_this_consumer",
            }
            try:
                _append(
                    db,
                    "executive.research_wake_consumption_skipped",
                    skipped,
                    key=(
                        "executive.research_wake_consumption_skipped:"
                        f"{wake_id}:{consumer_id}:{cycle_id}"
                    ),
                    correlation_id=objective_id,
                )
            except Exception:  # noqa: BLE001 - a skip receipt never aborts the cycle
                pass
            receipts.append(skipped)
            continue
        except Exception:  # noqa: BLE001 - one bad wake must not abort the rest
            continue
        receipts.append(payload)
    return receipts


def record_research_wake_continuation(
    db,
    *,
    cycle_id: str,
    consumed,
    decision_before: int = 0,
    context_before: int = 0,
    expected_decision_id: str | None = None,
    expected_context_cycle_id: str | None = None,
    controller="supervisor",
):
    """Bind each consumed research wake to the fresh decision it produced.

    Identical non-vacuity discipline to ``record_next_action_continuation``: an
    absent fresh context or decision is recorded explicitly as ``false`` rather
    than passing because nothing was checked.
    """

    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("research-wake continuation is Supervisor-owned")
    consumed = list(consumed or ())
    # A skip receipt records that a wake was NOT consumed (a colliding
    # consumption by the same consumer), so it must never produce a continuation:
    # writing "continued" for it would be a false record of a wake the runtime
    # explicitly skipped.  The guard lives here so every caller is covered.
    consumed = [item for item in consumed if item.get("consumed") is not False]
    if not consumed:
        return []
    decisions = list(db.events(event_types=("executive.decision_recorded",)))
    contests = list(db.events(event_types=("executive.context_assembled",)))
    fresh = decisions[decision_before:]
    matching_decisions = [
        event for event in fresh
        if expected_decision_id is None
        or str(event.payload.get("decision_id") or "") == str(expected_decision_id)
    ]
    decision_event = matching_decisions[-1] if matching_decisions else None
    decision_id = decision_event.payload.get("decision_id") if decision_event else None
    decision_kind = decision_event.payload.get("decision") if decision_event else None
    fresh_contexts = contests[context_before:]
    matching_contexts = [
        event for event in fresh_contexts
        if expected_context_cycle_id is None
        or str(event.payload.get("cycle_id") or "") == str(expected_context_cycle_id)
    ]
    receipts = []
    for item in consumed:
        payload = {
            "wake_id": item.get("wake_id"),
            "research_id": item.get("research_id"),
            "objective_id": item.get("objective_id"),
            "cycle_id": cycle_id,
            "decision_id": decision_id,
            "decision_kind": decision_kind,
            "fresh_decision": decision_id is not None,
            "fresh_context": bool(matching_contexts),
            "decision_owned_by_consuming_cycle": bool(
                decision_id
                and (expected_decision_id is None or decision_id == expected_decision_id)
            ),
            "continued_at": isoformat(),
        }
        try:
            _append(
                db,
                "executive.research_wake_continued",
                payload,
                key=f"executive.research_wake_continued:{item.get('wake_id')}:{cycle_id}",
                correlation_id=str(item.get("objective_id") or ""),
            )
        except Exception:
            continue
        receipts.append(payload)
    return receipts


def bridge_world_signals(
    db,
    *,
    controller="supervisor",
    limit=100,
):
    """Admit objective-targeted World Intelligence signals as Executive wakes.

    World Intelligence owns observations and signals; it does not create
    objectives, missions, or schedules.  This narrow Supervisor-owned bridge
    consumes only active signals that explicitly name an existing objective
    (in ``affected_entities`` or provenance), creates one coalesced
    ``WAIT_FOR_SIGNAL`` wake per signal/objective pair, and triggers it once.
    A signal is already durable by the time this reader sees it, so the wake
    is triggered directly rather than pretending a future ledger event is
    required.  Replays converge on the same wake idempotency keys.
    """
    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("world-signal bridging is Supervisor-owned")
    try:
        from ..control.broker import emergency_stop_reason as _esr
        from pathlib import Path as _P
        if _esr(db, _P(db.path).parent / "EMERGENCY_STOP"):
            return []
    except Exception:
        return []

    try:
        state = __import__(
            "jarvis_foundry.executive.autonomy",
            fromlist=["rebuild_executive_state"],
        ).rebuild_executive_state(db)
        objective_ids = set(state.objectives)
        with db.connect(readonly=True) as connection:
            rows = connection.execute(
                "SELECT signal_id, signal_kind, title, summary, salience, "
                "affected_entities_json, provenance_json "
                "FROM world_signals "
                "WHERE state IN ('OPEN', 'MONITORING', 'ACKNOWLEDGED') "
                "ORDER BY urgency DESC, importance DESC, signal_id LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
    except Exception:
        # Older/mid-migration databases, or a malformed advisory row, must not
        # stop the canonical Executive cycle.  The next cycle can retry.
        return []

    wakes = rebuild_wake_state(db)
    bridged: list[str] = []
    for row in rows:
        try:
            affected = json.loads(row["affected_entities_json"] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            affected = []
        try:
            provenance = json.loads(row["provenance_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            provenance = {}
        targets = {
            str(value) for value in affected
            if str(value) in objective_ids
        }
        if isinstance(provenance, Mapping):
            for key in ("objective_id", "objective_ids"):
                value = provenance.get(key)
                values = value if isinstance(value, (list, tuple, set)) else (value,)
                targets.update(str(item) for item in values if str(item) in objective_ids)
        signal_id = str(row["signal_id"] or "")
        if not signal_id or not targets:
            continue
        for objective_id in sorted(targets):
            wake_id = f"wake:{objective_id}:world-signal:{signal_id}"
            wake = wakes.get(wake_id)
            if wake is None:
                reason = (
                    f"world signal {signal_id}: "
                    f"{str(row['title'] or row['signal_kind'] or 'signal')[:240]}"
                )
                wake_payload = WakeCondition(
                    wake_id=wake_id,
                    objective_id=objective_id,
                    kind="WAIT_FOR_SIGNAL",
                    reason=reason,
                    created_at=isoformat(),
                    signal=f"world.signal_recorded:{signal_id}",
                ).to_payload()
                try:
                    create_wake(
                        db,
                        WakeCondition(**wake_payload),
                        controller=controller,
                    )
                    wakes[wake_id] = wake_payload
                    wake = wake_payload
                except Exception:
                    continue
            if wake.get("status") == "ACTIVE":
                try:
                    trigger_wake(
                        db,
                        wake_id,
                        controller=controller,
                        reason=f"world signal {signal_id} is active",
                    )
                    bridged.append(wake_id)
                    wake["status"] = "TRIGGERED"
                except Exception:
                    continue
    return bridged
# PART 2 of continuous engine — appended after engine_full_draft
# Strategy history, deadline awareness, blocker model, idempotency, continuous cycle

def record_strategy_attempt(db, objective_id: str, strategy: str, *, evidence_refs=(), controller="supervisor"):
    if controller != SUPERVISOR_CONTROLLER: raise PermissionError("strategy is Supervisor-owned")
    ts = __import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat()
    payload = {"objective_id": objective_id, "strategy": strategy, "evidence_refs": list(evidence_refs), "recorded_at": ts}
    key = f"executive.strategy_attempted:{objective_id}:{strategy}:{ts}:{__import__('hashlib').sha256((strategy + ts).encode()).hexdigest()[:6]}"
    try:
        return _append(db, "executive.strategy_attempted", payload, key=key, correlation_id=objective_id)
    except Exception: return None

def recent_strategies(db, objective_id: str, limit=10):
    out=[]
    for ev in reversed(db.events(event_type_prefixes=("executive.strategy_",))):
        if ev.payload.get("objective_id")==objective_id:
            out.append(ev.payload.get("strategy"))
            if len(out)>=limit: break
    return out

def is_retry_loop(db, objective_id: str, strategy: str, threshold=3):
    recent = recent_strategies(db, objective_id, limit=threshold)
    return len(recent)>=threshold and all(s==strategy for s in recent[:threshold])

def next_strategy_options(db, objective_id: str):
    # Return not-yet-tried strategies for this objective
    tried = set(recent_strategies(db, objective_id, limit=20))
    candidates = ["alternate_capability", "alternate_provider", "different_decomposition", "repair", "gather_missing_information", "wait_for_condition", "lower_risk_alternate", "escalation"]
    return [c for c in candidates if c not in tried]

@dataclass(frozen=True, slots=True)
class Blocker:
    objective_id: str
    kind: str
    reason: str
    can_resolve: bool
    unblock_condition: str
    requires_owner: bool

def blocker_for(db, objective_id: str):
    state = _exec_state_for_objective(db, objective_id)
    wakes = [w for w in rebuild_wake_state(db).values() if w.get("objective_id")==objective_id and w.get("status")=="ACTIVE"]
    if state == "OWNER_BLOCKED": return Blocker(objective_id, "OWNER_BLOCKED", "owner STOP/veto/revoke", False, "owner clears condition", True)
    if state == "CAPABILITY_BLOCKED": return Blocker(objective_id, "CAPABILITY_BLOCKED", "required capability not READY", True, "capability becomes READY", False)
    if state == "PROVIDER_BLOCKED": return Blocker(objective_id, "PROVIDER_BLOCKED", "provider unavailable", True, "provider becomes available", False)
    if state == "WAITING" and wakes: return Blocker(objective_id, "WAITING", wakes[0].get("reason") or "waiting for condition", True, wakes[0].get("kind") or "unknown", wakes[0].get("kind") in ("WAIT_FOR_OWNER", "WAIT_FOR_SIGNAL"))
    if state == "BLOCKED": return Blocker(objective_id, "BLOCKED", "dependency or resource blocked", True, "dependency resolves", False)
    return None

def _released_research_wakes(consumed_research) -> int:
    """Count the research wakes the consumer actually RELEASED this cycle.

    A consumption receipt whose ``due`` is true but whose ``consumed`` is false
    is a collision (this consumer already recorded the wake), and the wake stays
    ACTIVE - counting it would suppress the zero-change short circuit for a wake
    that never released, so the Executive would re-evaluate on every later cycle
    while the wake is still pending.
    """

    return sum(
        1
        for item in consumed_research
        if item.get("due") is True and item.get("consumed") is not False
    )


def run_continuous_cycle(
    db,
    *,
    cycle_id: str,
    controller="supervisor",
    now=None,
    max_new_missions=1,
    excluded_objective_ids=(),
    discovery: Mapping[str, Any] | None = None,
    stop_path: str | Path | None = None,
    consumer_id: str = "",
    self_expansion: bool = False,
):
    # One idempotent durable tick: due wakes -> trigger -> autonomy cycle -> materialize
    # Idempotent on cycle_id via autonomy cycle key; wake triggers are idempotent via wake_triggered keys
    if controller != SUPERVISOR_CONTROLLER: raise PermissionError("continuous cycle is Supervisor-owned")
    # Check owner authority first
    from ..control.broker import emergency_stop_reason as _esr
    from pathlib import Path as _P
    # The worker loop passes its resolved sentinel; falling back to the DB
    # parent keeps older callers working, and run_cycle refuses to start a
    # cycle where those two paths differ.
    _stop_sentinel = _P(stop_path) if stop_path else _P(db.path).parent / "EMERGENCY_STOP"
    if _esr(db, _stop_sentinel):
        return {"cycle_id": cycle_id, "status": "OWNER_BLOCKED", "reason": "STOP active — no mutation"}
    veto = __import__("jarvis_foundry.control.broker", fromlist=["matching_veto_reason"]).matching_veto_reason(db, {"GLOBAL", "executive", "objectives"})
    if veto:
        return {"cycle_id": cycle_id, "status": "OWNER_BLOCKED", "reason": f"veto: {veto}"}
    # Baselines for the continuation receipt.  Everything this cycle assembles
    # or decides *after* consuming its wakes must read as fresh, so the receipt
    # can never be satisfied by a previous cycle's context or decision.
    decision_before = int(db.event_count(event_type="executive.decision_recorded"))
    context_before = int(db.event_count(event_type="executive.context_assembled"))
    # Park blocked objectives before evaluating wakes.  This keeps the
    # Supervisor-owned cycle self-contained across direct invocation and
    # restart; the Worker wrapper may call the helper too, but idempotent wake
    # keys make that replay harmless.
    excluded = {str(value) for value in excluded_objective_ids}
    parked = auto_wake_for_blocked(
        db,
        controller=controller,
        excluded_objective_ids=excluded,
    )
    # World Intelligence is advisory input only.  The Executive bridge admits
    # objective-targeted signals as durable wake conditions; it never creates
    # work itself.
    world_triggered = bridge_world_signals(db, controller=controller)
    # A completed candidate dossier is evidence, not a selection: the bridge
    # binds it to the objective that owns the candidate, and this cycle
    # consumes it before the generic tick could release it anonymously.
    research_bridged = bridge_research_completions(db, controller=controller)
    consumed_research = consume_research_wakes(
        db,
        cycle_id=cycle_id,
        consumer_id=str(consumer_id or ""),
        controller=controller,
        now=now,
    )
    # The Executive's own PASS-generated next-action wake is consumed by THIS
    # cycle, before the generic due-wake tick could trigger it anonymously.
    consumed_next_action = consume_next_action_wakes(
        db,
        cycle_id=cycle_id,
        consumer_id=str(consumer_id or ""),
        controller=controller,
        now=now,
    )
    # Wake phase
    triggered = tick_wakes(db, controller=controller, now=now)
    triggered = list(dict.fromkeys([
        *world_triggered,
        *_triggered_research_wakes(db, research_bridged),
        *triggered,
    ]))
    # Strategy guard: if top runnable objective is in retry loop, surface escalation instead of re-attempting same strategy
    try:
        from ..executive.autonomy import rank_objectives as _rank2, rebuild_executive_state as _reb2
        rows = _rank2(db)
        top = next(
            (r for r in rows
             if r.eligible and r.objective_id not in excluded),
            None,
        )
        if top is not None:
            recent = recent_strategies(db, top.objective_id, limit=3)
            if len(recent) >= 3 and len(set(recent[:3])) == 1:
                loop_strategy = recent[0]
                # Record escalation (idempotent) and block this cycle from re-attempting same strategy
                try:
                    from ..executive.autonomy import record_escalation as _esc
                    _esc(db, objective_id=top.objective_id, decision=f"retry loop on {loop_strategy}", why=f"strategy {loop_strategy!r} attempted 3 times consecutively", evidence_refs=[], options=next_strategy_options(db, top.objective_id)[:4], recommendation="switch strategy or escalate to owner", delay_consequence="repeated failure without strategy change", controller=controller)
                except Exception:
                    pass
                return {"cycle_id": cycle_id, "status": "ESCALATE", "reason": f"retry loop detected for {top.objective_id} on {loop_strategy!r} — escalation recorded", "triggered_wakes": triggered, "parked_objectives": parked, "research_bridged": research_bridged, "research_wake_consumed": consumed_research}
    except Exception:
        pass
    # Next-work discovery: the Continuous Executive is the only next-work
    # authority, so when the caller has no dispatchable contract it observes
    # registered projects and admits the Executive's selected work here.  The
    # admission authority and the binder's declared need table are shared with
    # every other entry, so this is one seam - not a third selector.
    discovery_report: dict[str, Any] | None = None
    discovery_settings = dict(discovery or {})
    if discovery_settings:
        try:
            from .intelligence import discover_and_admit_next_work as _discover
            # A research-completion wake released this cycle IS the meaningful
            # change for the Executive: it must reach a fresh evaluation even
            # when no observed source moved.
            released_research_wakes = _released_research_wakes(consumed_research)
            admitted_work = _discover(
                db,
                cycle_id=cycle_id,
                research_wake_consumed=released_research_wakes,
                **discovery_settings,
            )
            discovery_report = {
                "status": admitted_work.status,
                "reason": admitted_work.reason,
                "decision_id": admitted_work.decision_id,
                "admission_status": admitted_work.admission_status,
                "mission_id": admitted_work.mission_id,
                "observed": admitted_work.observed,
            }
        except Exception as error:  # noqa: BLE001 - discovery must not abort the cycle
            discovery_report = {
                "status": "BLOCKED",
                "reason": f"{type(error).__name__}: {error}"[:500],
                "mission_id": None,
            }
    # Autonomy phase: delegate to existing authoritative cycle
    from ..executive.autonomy import run_autonomy_cycle as _run_auto
    try:
        cycle = _run_auto(
            db,
            cycle_id=cycle_id,
            controller=controller,
            excluded_objective_ids=tuple(sorted(excluded)),
            self_expansion=bool(self_expansion),
        )
        cycle_payload = cycle.to_payload() if hasattr(cycle, "to_payload") else dict(cycle) if isinstance(cycle, dict) else str(cycle)
        mission_id = None
        if isinstance(cycle_payload, dict):
            materialization = cycle_payload.get("materialization") or {}
            if isinstance(materialization, dict):
                mission_id = materialization.get("mission_id")
        mission_id = mission_id or (discovery_report or {}).get("mission_id")
        continuation = record_next_action_continuation(
            db,
            cycle_id=cycle_id,
            consumed=consumed_next_action,
            decision_before=decision_before,
            context_before=context_before,
            controller=controller,
        )
        research_continuation = record_research_wake_continuation(
            db,
            cycle_id=cycle_id,
            consumed=consumed_research,
            decision_before=decision_before,
            context_before=context_before,
            expected_decision_id=str((discovery_report or {}).get("decision_id") or ""),
            expected_context_cycle_id=cycle_id,
            controller=controller,
        )
        return {"cycle_id": cycle_id, "status": cycle.status, "reason": cycle.reason, "triggered_wakes": triggered, "parked_objectives": parked, "autonomy_cycle": cycle_payload, "mission_id": mission_id, "discovery": discovery_report, "next_action_consumed": consumed_next_action, "next_action_continuation": continuation, "research_bridged": research_bridged, "research_wake_consumed": consumed_research, "research_wake_continuation": research_continuation}
    except Exception as e:
        continuation = []
        try:
            continuation = record_next_action_continuation(
                db,
                cycle_id=cycle_id,
                consumed=consumed_next_action,
                decision_before=decision_before,
                context_before=context_before,
                controller=controller,
            )
        except Exception:
            pass
        research_continuation = []
        try:
            research_continuation = record_research_wake_continuation(
                db,
                cycle_id=cycle_id,
                consumed=consumed_research,
                decision_before=decision_before,
                context_before=context_before,
                expected_decision_id=str((discovery_report or {}).get("decision_id") or ""),
                expected_context_cycle_id=cycle_id,
                controller=controller,
            )
        except Exception:
            pass
        return {"cycle_id": cycle_id, "status": "BLOCKED", "reason": f"{type(e).__name__}: {e}", "triggered_wakes": triggered, "parked_objectives": parked, "discovery": discovery_report, "next_action_consumed": consumed_next_action, "next_action_continuation": continuation, "research_bridged": research_bridged, "research_wake_consumed": consumed_research, "research_wake_continuation": research_continuation}

def schedule_wait_until(db, objective_id: str, *, not_before: str, reason="", controller="supervisor"):
    wid = f"wake:{objective_id}:until:{_hash(not_before)}"
    wakes = rebuild_wake_state(db)
    if wid in wakes and wakes[wid].get("status")=="ACTIVE": return wakes[wid]
    w = WakeCondition(wake_id=wid, objective_id=objective_id, kind="WAIT_UNTIL_TIME", reason=reason or f"wait until {not_before}", created_at=__import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat(), not_before=not_before)
    create_wake(db, w, controller=controller)
    return w.to_payload() if hasattr(w, "to_payload") else dict(w.__dict__)

def schedule_wait_for_dependency(db, objective_id: str, *, dependency_id: str, reason="", controller="supervisor"):
    wid = f"wake:{objective_id}:dep:{dependency_id}"
    wakes = rebuild_wake_state(db)
    if wid in wakes and wakes[wid].get("status")=="ACTIVE": return wakes[wid]
    w = WakeCondition(wake_id=wid, objective_id=objective_id, kind="WAIT_FOR_DEPENDENCY", reason=reason or f"wait for {dependency_id}", created_at=__import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat(), dependency_id=dependency_id)
    create_wake(db, w, controller=controller)
    return w.to_payload() if hasattr(w, "to_payload") else dict(w.__dict__)

# PART 3 — deadlines, escalation wiring, and command-center snapshot helpers
# Append after part2

def deadline_urgency(db, objective_id: str, now=None):
    from datetime import datetime, timezone
    n = now or datetime.now(timezone.utc)
    try:
        from ..executive.autonomy import rebuild_executive_state as _reb
        obj = _reb(db).objectives.get(objective_id)
        if obj is None or not obj.deadline: return 0.0
        dt = _dt(obj.deadline)
        if dt is None: return 0.0
        secs = (dt - n).total_seconds()
        if secs <= 0: return 1.0
        # 14-day horizon like autonomy
        return max(0.0, min(1.0, 1.0 - secs / (14*86400.0)))
    except Exception: return 0.0

def stale_wakes(db, stale_after_seconds=3600.0, now=None):
    from datetime import datetime, timezone
    n = now or datetime.now(timezone.utc)
    wakes = rebuild_wake_state(db)
    out=[]
    for w in wakes.values():
        if w.get("status")!="ACTIVE": continue
        created=_dt(w.get("created_at"))
        if created is None: continue
        age=(n-created).total_seconds()
        if age>stale_after_seconds:
            out.append(w)
    return out

def auto_wake_for_blocked(
    db,
    *,
    controller="supervisor",
    excluded_objective_ids=(),
):
    # Create wakes for currently blocked objectives that have no wake yet
    created=[]
    try:
        from ..executive.autonomy import rebuild_executive_state as _reb, rank_objectives as _rank
        st=_reb(db)
        excluded = {str(value) for value in excluded_objective_ids}
        wakes=rebuild_wake_state(db)
        active_wake_oids={w.get("objective_id") for w in wakes.values() if w.get("status")=="ACTIVE"}
        for row in _rank(db):
            oid=row.objective_id
            if oid in excluded:
                continue
            if row.eligible: continue
            if oid in active_wake_oids: continue
            obj=st.objectives.get(oid)
            if obj is None: continue
            # Infer kind from blockers.  A bound provider is the narrower
            # recovery contract: preserve its identity so a later positive
            # provider sample can release this exact wait.  This must win
            # over the generic capability-health blocker emitted for the same
            # observation; otherwise provider recovery can never wake it.
            provider_blockers = [
                b for b in row.blockers
                if str(b).lower().startswith("provider:")
            ]
            if provider_blockers:
                provider_id = str(provider_blockers[0]).split(":", 2)[1]
                if provider_id:
                    try:
                        schedule_wait_for_provider(
                            db,
                            oid,
                            provider_id=provider_id,
                            reason=f"wait for provider {provider_id}",
                            controller=controller,
                        )
                        created.append(oid)
                    except Exception:
                        pass
                    continue
            if any("dependency" in b for b in row.blockers):
                for b in row.blockers:
                    if b.startswith("dependency:"):
                        dep=b.split(":")[1] if ":" in b else ""
                        if dep:
                            try: schedule_wait_for_dependency(db, oid, dependency_id=dep, controller=controller); created.append(oid); break
                            except Exception: pass
            elif any("capability" in b for b in row.blockers):
                cap=row.blockers[0].split(":")[0] if row.blockers else "unknown"
                wid=f"wake:{oid}:cap:{cap}"
                if wid not in wakes:
                    w=WakeCondition(wake_id=wid, objective_id=oid, kind="WAIT_FOR_CAPABILITY", reason=f"wait for capability {cap}", created_at=__import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat(), capability=cap, resource_id=obj.resource_id)
                    try: create_wake(db, w, controller=controller); created.append(oid)
                    except Exception: pass
            elif any("provider" in b.lower() for b in row.blockers):
                wid=f"wake:{oid}:provider"
                if wid not in wakes:
                    w=WakeCondition(wake_id=wid, objective_id=oid, kind="WAIT_FOR_PROVIDER", reason="wait for provider", created_at=__import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat())
                    try: create_wake(db, w, controller=controller); created.append(oid)
                    except Exception: pass
            elif "resource_unconfigured" in row.blockers:
                wid=f"wake:{oid}:resource"
                if wid not in wakes:
                    w=WakeCondition(wake_id=wid, objective_id=oid, kind="WAIT_FOR_RESOURCE", reason="wait for resource", created_at=__import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat(), resource_id=obj.resource_id)
                    try: create_wake(db, w, controller=controller); created.append(oid)
                    except Exception: pass
    except Exception: pass
    return created

# PART 4 — remaining schedule helpers and idempotent wake coalescing

def schedule_wait_for_capability(db, objective_id: str, *, capability: str, resource_id: str | None = None, reason="", controller="supervisor"):
    wid = f"wake:{objective_id}:cap:{capability}:{resource_id or ''}"
    wakes = rebuild_wake_state(db)
    if wid in wakes and wakes[wid].get("status")=="ACTIVE": return wakes[wid]
    w = WakeCondition(wake_id=wid, objective_id=objective_id, kind="WAIT_FOR_CAPABILITY", reason=reason or f"wait for capability {capability}", created_at=__import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat(), capability=capability, resource_id=resource_id)
    create_wake(db, w, controller=controller)
    return w.to_payload() if hasattr(w, "to_payload") else dict(w.__dict__)

def schedule_wait_for_provider(db, objective_id: str, *, provider_id: str, reason="", controller="supervisor", episode_id: str | None = None):
    # An episode suffix permits the same objective/provider pair to recover,
    # run, and later enter a genuinely new outage without colliding with the
    # immutable wake from the earlier episode.  Legacy callers retain the
    # original stable identity.
    wid = f"wake:{objective_id}:provider:{provider_id}"
    if episode_id:
        wid = f"{wid}:{episode_id}"
    wakes = rebuild_wake_state(db)
    if wid in wakes and wakes[wid].get("status")=="ACTIVE": return wakes[wid]
    w = WakeCondition(wake_id=wid, objective_id=objective_id, kind="WAIT_FOR_PROVIDER", reason=reason or f"wait for provider {provider_id}", created_at=__import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat(), provider_id=provider_id)
    create_wake(db, w, controller=controller)
    return w.to_payload() if hasattr(w, "to_payload") else dict(w.__dict__)


def _objective_for_mission(db, mission_id: str) -> str:
    """Return the existing owning objective, or a mission-scoped wake key."""

    try:
        from ..executive.autonomy import rebuild_executive_state
        state = rebuild_executive_state(db)
        for objective_id, objective in state.objectives.items():
            if mission_id in objective.mission_ids:
                return str(objective_id)
    except Exception:
        pass
    # Disposable/direct missions may not be Executive-owned.  Wakes are
    # still durable and Supervisor-owned; this does not invent an objective.
    return f"mission:{mission_id}"


_PROVIDER_WAIT_CAMPAIGN_KEYS = (
    "objective_id",
    "campaign_id",
    "child_id",
    "builder_run_id",
    "routing_identity",
    "campaign_isolation_key",
    "stop_path",
)
_PROVIDER_WAIT_EFFECT_REQUIREMENT_KEYS = (
    "effect_requirement_id",
    "effect_requirement_event_id",
    "effect_requirement_sequence",
    "effect_requirement_source_id",
)


def _provider_wait_identity_material(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    material: dict[str, Any] = {
        "mission_id": str(payload.get("mission_id") or ""),
        "step_id": str(payload.get("step_id") or ""),
        "run_id": str(payload.get("run_id") or ""),
        "provider_ids": list(payload.get("provider_ids") or ()),
        "effect_action_key": payload.get("effect_action_key"),
        "error_class": payload.get("error_class"),
    }
    if any(key in payload for key in _PROVIDER_WAIT_CAMPAIGN_KEYS[1:]):
        material.update({
            key: payload.get(key)
            for key in _PROVIDER_WAIT_CAMPAIGN_KEYS
        })
    if any(
        key in payload
        for key in _PROVIDER_WAIT_EFFECT_REQUIREMENT_KEYS
    ):
        material.update({
            key: payload.get(key)
            for key in _PROVIDER_WAIT_EFFECT_REQUIREMENT_KEYS
        })
    return material


def _provider_wait_family_id(material: Mapping[str, Any]) -> str:
    return "provider-wait-family-" + hashlib.sha256(
        canonical_json(dict(material)).encode("utf-8")
    ).hexdigest()[:24]


def _provider_wait_id(
    material: Mapping[str, Any],
    *,
    family_id: str,
    generation: int,
) -> str:
    return "provider-wait-" + hashlib.sha256(
        canonical_json({
            **dict(material),
            "wait_family_id": family_id,
            "wait_generation": generation,
        }).encode("utf-8")
    ).hexdigest()[:24]


def _provider_wait_generation(payload: Mapping[str, Any]) -> int:
    value = payload.get("wait_generation")
    if value is None and "wait_family_id" not in payload:
        return 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EventConflict("provider wait generation is invalid")
    return value


def _latest_effect_requirement(
    db: FoundryDatabase,
    *,
    mission_id: str,
    step_id: str,
    run_id: str,
    effect_action_key: str,
) -> dict[str, Any] | None:
    candidates = tuple(
        event
        for event in db.events(
            readonly=True,
            event_types=("provider.effect_reconciliation_required",),
        )
        if (
            str(event.payload.get("run_id") or "") == run_id
            and str(event.payload.get("mission_id") or "") == mission_id
            and str(event.payload.get("step_id") or "") == step_id
            and str(event.payload.get("effect_action_key") or "")
            == effect_action_key
        )
    )
    if not candidates:
        return None
    event_ids = tuple(event.event_id for event in candidates)
    placeholders = ", ".join("?" for _ in event_ids)
    with db.connect(readonly=True) as connection:
        sequences = {
            str(row["event_id"]): int(row["sequence"])
            for row in connection.execute(
                "SELECT event_id, sequence FROM events "
                f"WHERE event_id IN ({placeholders})",
                event_ids,
            ).fetchall()
        }
    valid: list[tuple[int, Event, str]] = []
    for event in candidates:
        generation = event.payload.get("attempt_generation")
        ordinal = event.payload.get("attempt_ordinal")
        requirement_id = str(
            event.payload.get("effect_requirement_id") or ""
        )
        legacy = (
            generation is None
            and ordinal is None
            and not requirement_id
        )
        if legacy:
            requirement_id = f"legacy:{event.event_id}"
            expected_key = (
                "provider.effect_reconciliation_required:"
                f"{run_id}:{effect_action_key}"
            )
        else:
            if (
                not isinstance(generation, int)
                or isinstance(generation, bool)
                or generation < 0
                or not isinstance(ordinal, int)
                or isinstance(ordinal, bool)
                or ordinal < 1
            ):
                raise EventConflict(
                    "effect reconciliation requirement has invalid attempt "
                    "identity"
                )
            expected_requirement_id = (
                f"{run_id}:{generation}:{ordinal}:{effect_action_key}"
            )
            if requirement_id != expected_requirement_id:
                raise EventConflict(
                    "effect reconciliation requirement changed its attempt "
                    "identity"
                )
            expected_key = (
                "provider.effect_reconciliation_required:"
                f"{requirement_id}"
            )
        if (
            event.source_type != "supervisor"
            or event.idempotency_key != expected_key
            or event.event_id not in sequences
        ):
            raise EventConflict(
                "effect reconciliation requirement lacks canonical "
                f"authority: {requirement_id}"
            )
        valid.append((
            sequences[event.event_id],
            event,
            requirement_id,
        ))
    sequence, event, requirement_id = max(
        valid,
        key=lambda item: item[0],
    )
    return {
        "effect_requirement_id": requirement_id,
        "effect_requirement_event_id": event.event_id,
        "effect_requirement_sequence": sequence,
        "effect_requirement_source_id": event.source_id,
    }


def _effect_requirement_boundary(
    db: FoundryDatabase,
    payload: Mapping[str, Any],
) -> int | None:
    present = {
        key
        for key in _PROVIDER_WAIT_EFFECT_REQUIREMENT_KEYS
        if key in payload
    }
    if not present:
        return None
    if present != set(_PROVIDER_WAIT_EFFECT_REQUIREMENT_KEYS):
        raise EventConflict(
            "provider effect wait has a partial requirement binding"
        )
    requirement_id = str(payload.get("effect_requirement_id") or "")
    event_id = str(payload.get("effect_requirement_event_id") or "")
    source_id = str(payload.get("effect_requirement_source_id") or "")
    sequence = payload.get("effect_requirement_sequence")
    if (
        not requirement_id
        or not event_id
        or not source_id
        or not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 1
    ):
        raise EventConflict(
            "provider effect wait requirement binding is invalid"
        )
    with db.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT sequence, event_type, source_type, source_id, "
            "idempotency_key, payload_json FROM events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
    if row is None:
        raise EventConflict(
            "provider effect wait requirement event is missing"
        )
    requirement_payload = json.loads(str(row["payload_json"]))
    action_key = str(payload.get("effect_action_key") or "")
    run_id = str(payload.get("run_id") or "")
    legacy = requirement_id == f"legacy:{event_id}"
    expected_key = (
        f"provider.effect_reconciliation_required:{run_id}:{action_key}"
        if legacy
        else f"provider.effect_reconciliation_required:{requirement_id}"
    )
    if (
        int(row["sequence"]) != sequence
        or str(row["event_type"])
        != "provider.effect_reconciliation_required"
        or str(row["source_type"]) != "supervisor"
        or str(row["source_id"]) != source_id
        or str(row["idempotency_key"]) != expected_key
        or str(requirement_payload.get("run_id") or "") != run_id
        or str(requirement_payload.get("mission_id") or "")
        != str(payload.get("mission_id") or "")
        or str(requirement_payload.get("step_id") or "")
        != str(payload.get("step_id") or "")
        or str(requirement_payload.get("effect_action_key") or "")
        != action_key
        or (
            not legacy
            and str(
                requirement_payload.get("effect_requirement_id") or ""
            ) != requirement_id
        )
    ):
        raise EventConflict(
            "provider effect wait requirement authority is invalid"
        )
    return sequence


def _event_sequence(db: FoundryDatabase, event_id: str) -> int:
    with db.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT sequence FROM events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
    if row is None:
        raise EventConflict(f"event sequence is missing: {event_id}")
    return int(row["sequence"])


def schedule_mission_provider_wait(
    db,
    *,
    mission_id: str,
    step_id: str,
    run_id: str,
    provider_ids: Sequence[str] = (),
    reason: str,
    error_class: str,
    effect_action_key: str | None = None,
    campaign_lineage: Mapping[str, str] | None = None,
    routing_identity: str | None = None,
    campaign_isolation_key: str | None = None,
    stop_path: str | Path | None = None,
    controller: str = "supervisor",
):
    """Park one existing mission/run on existing Executive wake authority."""

    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("provider mission waits are Supervisor-owned")
    resolved_stop_path = (
        str(Path(stop_path).expanduser().resolve()) if stop_path is not None else None
    )
    material: dict[str, Any] = {
        "mission_id": mission_id,
        "step_id": step_id,
        "run_id": run_id,
        "provider_ids": sorted(set(str(p) for p in provider_ids if str(p))),
        "effect_action_key": effect_action_key,
        "error_class": error_class,
    }
    if effect_action_key:
        requirement = _latest_effect_requirement(
            db,
            mission_id=mission_id,
            step_id=step_id,
            run_id=run_id,
            effect_action_key=effect_action_key,
        )
        if requirement is not None:
            material.update(requirement)
    campaign_child_id = ""
    if campaign_lineage:
        material.update({
            "objective_id": (
                str(campaign_lineage.get("objective_id") or "") or None
            ),
            "campaign_id": (
                str(campaign_lineage.get("campaign_id") or "") or None
            ),
            "child_id": (
                str(campaign_lineage.get("child_id") or "") or None
            ),
            "builder_run_id": run_id,
            "routing_identity": routing_identity,
            "campaign_isolation_key": campaign_isolation_key,
            "stop_path": resolved_stop_path,
        })
        # Validate the immutable edge before creating any wake or wait event.
        # A malformed caller must not leave a partially durable wait behind.
        from .campaigns import CampaignError, rebuild_campaign_state

        campaign_child_id = str(campaign_lineage.get("child_id") or "")
        state = rebuild_campaign_state(db)
        child = state.children.get(campaign_child_id)
        campaign = state.campaigns.get(
            str(campaign_lineage.get("campaign_id") or "")
        )
        if (
            child is None
            or campaign is None
            or child.campaign_id != campaign.campaign_id
            or campaign.objective_id
            != str(campaign_lineage.get("objective_id") or "")
            or child.mission_id != mission_id
            or child.routing_identity != routing_identity
            or (
                effect_action_key is not None
                and effect_action_key not in child.effect_keys
            )
        ):
            raise CampaignError(
                "provider wait lineage does not match the bound campaign child"
            )
    family_id = _provider_wait_family_id(material)
    prior_starts = canonical_provider_wait_start_events(
        db,
        mission_id=mission_id,
        run_id=run_id,
    )
    resumed_wait_ids = {
        str(event.payload.get("wait_id") or "")
        for event in canonical_provider_resume_events(
            db,
            mission_id=mission_id,
            step_id=step_id,
            run_id=run_id,
        )
    }
    family_starts = tuple(
        event
        for event in prior_starts
        if str(
            event.payload.get("wait_family_id")
            or _provider_wait_family_id(
                _provider_wait_identity_material(event.payload)
            )
        ) == family_id
    )
    open_starts = tuple(
        event
        for event in family_starts
        if str(event.payload.get("wait_id") or "") not in resumed_wait_ids
    )
    if len(open_starts) > 1:
        raise EventConflict(
            f"provider wait family has multiple open episodes: {family_id}"
        )
    objective_id = str(material.get("objective_id") or "") or _objective_for_mission(
        db, mission_id
    )
    if open_starts:
        payload = dict(open_starts[0].payload)
        wait_id = str(payload["wait_id"])
    else:
        generation = max(
            (
                _provider_wait_generation(event.payload)
                for event in family_starts
            ),
            default=0,
        ) + 1
        wait_id = _provider_wait_id(
            material,
            family_id=family_id,
            generation=generation,
        )
        wake_ids: list[str] = []
        for provider_id in material["provider_ids"]:
            wake = schedule_wait_for_provider(
                db,
                objective_id,
                provider_id=provider_id,
                reason=reason,
                controller=controller,
                episode_id=wait_id,
            )
            wake_ids.append(str(wake["wake_id"]))
        payload = {
            **material,
            "wait_family_id": family_id,
            "wait_generation": generation,
            "wait_id": wait_id,
            "objective_id": objective_id,
            "wake_ids": wake_ids,
            "reason": str(reason)[:1000],
            "created_at": isoformat(),
        }
        try:
            _append(
                db,
                "mission.provider_wait_started",
                payload,
                key=f"mission.provider_wait_started:{wait_id}",
                source_id="provider-resilience",
                correlation_id=mission_id,
            )
        except EventConflict:
            # A concurrent replay may have admitted this exact generation.
            prior = next((
                event.payload
                for event in reversed(canonical_provider_wait_start_events(
                    db,
                    mission_id=mission_id,
                    run_id=run_id,
                ))
                if str(event.payload.get("wait_id") or "") == wait_id
            ), None)
            if prior is None or any(
                prior.get(key) != value
                for key, value in {
                    **material,
                    "wait_family_id": family_id,
                    "wait_generation": generation,
                }.items()
            ):
                raise
            payload = dict(prior)
    if campaign_lineage:
        # Project the same durable wait into the campaign aggregate.  This is
        # not a second wake authority: the mission.provider_wait_* receipt
        # and existing Executive wake ids remain the release condition.
        from .campaigns import record_child_state

        wait_kind = (
            "EFFECT_RECONCILIATION" if effect_action_key else "PROVIDER"
        )
        record_child_state(
            db,
            campaign_child_id,
            "WAITING",
            reason=str(reason)[:1000],
            wait_kind=wait_kind,
            wait_reason=(
                f"reconcile effect {effect_action_key} before retry or fallback"
                if effect_action_key
                else "WAIT_FOR_PROVIDER: " + ", ".join(material["provider_ids"])
            ),
            controller=controller,
            stop_path=resolved_stop_path,
        )
    return payload


def _effect_has_reconciliation(
    db: FoundryDatabase,
    action_key: str,
    *,
    after_sequence: int,
) -> bool:
    with db.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT sequence, source_type, source_id, idempotency_key, "
            "payload_json FROM events WHERE event_type = 'effect.reconciled' "
            "AND sequence > ? ORDER BY sequence",
            (after_sequence,),
        ).fetchall()
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        if (
            str(row["source_type"]) == "supervisor"
            and str(row["source_id"]) == "v5-effect-driver"
            and str(row["idempotency_key"] or "").startswith(
                f"{action_key}.reconciled."
            )
            and str(
                payload.get("action_key")
                or payload.get("effect_action_key")
                or ""
            ) == action_key
        ):
            return True
    return False


def canonical_provider_wait_start_events(
    db: FoundryDatabase,
    *,
    mission_id: str | None = None,
    run_id: str | None = None,
    wait_ids: Sequence[str] | None = None,
) -> tuple[Event, ...]:
    """Return provider waits after validating their immutable authority."""

    selected_wait_ids = (
        {str(wait_id) for wait_id in wait_ids if str(wait_id)}
        if wait_ids is not None
        else None
    )
    canonical: list[Event] = []
    seen_wait_ids: set[str] = set()
    wake_events_by_id: dict[str, list[Event]] = {}
    authority_events = db.events(
        readonly=True,
        event_types=(
            "executive.wake_created",
            "mission.provider_wait_started",
        ),
    )
    authority_order = {
        event.event_id: index
        for index, event in enumerate(authority_events)
    }
    for event in authority_events:
        if event.event_type != "executive.wake_created":
            continue
        wake_id = str(event.payload.get("wake_id") or "")
        if wake_id:
            wake_events_by_id.setdefault(wake_id, []).append(event)
    for event in authority_events:
        if event.event_type != "mission.provider_wait_started":
            continue
        payload = event.payload
        observed_mission = str(payload.get("mission_id") or "")
        observed_step = str(payload.get("step_id") or "")
        observed_run = str(payload.get("run_id") or "")
        wait_id = str(payload.get("wait_id") or "")
        if mission_id is not None and observed_mission != mission_id:
            continue
        if run_id is not None and observed_run != run_id:
            continue
        if selected_wait_ids is not None and wait_id not in selected_wait_ids:
            continue
        raw_providers = payload.get("provider_ids")
        raw_wakes = payload.get("wake_ids")
        providers = (
            tuple(str(item) for item in raw_providers)
            if isinstance(raw_providers, list)
            else ()
        )
        providers_are_strings = bool(
            isinstance(raw_providers, list)
            and all(
                isinstance(item, str) and bool(item)
                for item in raw_providers
            )
        )
        wake_ids = (
            tuple(str(item) for item in raw_wakes)
            if isinstance(raw_wakes, list)
            else ()
        )
        wake_ids_are_strings = bool(
            isinstance(raw_wakes, list)
            and all(
                isinstance(item, str) and bool(item)
                for item in raw_wakes
            )
        )
        effect_action_key = payload.get("effect_action_key")
        objective_id = str(payload.get("objective_id") or "")
        try:
            requirement_boundary = _effect_requirement_boundary(
                db,
                payload,
            )
        except EventConflict as error:
            raise EventConflict(
                "provider wait effect requirement is invalid: "
                f"{wait_id or observed_run or 'UNKNOWN'}"
            ) from error
        start_sequence = _event_sequence(db, event.event_id)
        material = _provider_wait_identity_material({
            **payload,
            "provider_ids": list(providers),
        })
        expected_family_id = _provider_wait_family_id(material)
        try:
            generation = _provider_wait_generation(payload)
        except EventConflict as error:
            raise EventConflict(
                "provider wait start authority is invalid: "
                f"{wait_id or observed_run or 'UNKNOWN'}"
            ) from error
        legacy_identity = (
            "wait_family_id" not in payload
            and "wait_generation" not in payload
        )
        expected_wait_id = (
            "provider-wait-" + hashlib.sha256(
                canonical_json(material).encode("utf-8")
            ).hexdigest()[:24]
            if legacy_identity
            else _provider_wait_id(
                material,
                family_id=expected_family_id,
                generation=generation,
            )
        )
        expected_wake_ids = tuple(
            f"wake:{objective_id}:provider:{provider}:{wait_id}"
            for provider in providers
        )
        wake_authority_valid = True
        for provider, wake_id in zip(providers, wake_ids, strict=False):
            wake_events = wake_events_by_id.get(wake_id, [])
            if len(wake_events) != 1:
                wake_authority_valid = False
                break
            wake_event = wake_events[0]
            wake_payload = wake_event.payload
            if not (
                wake_event.source_type == "supervisor"
                and wake_event.source_id == "v9-continuous"
                and authority_order[wake_event.event_id]
                < authority_order[event.event_id]
                and wake_event.correlation_id == objective_id
                and wake_event.idempotency_key
                == f"executive.wake_created:{wake_id}"
                and str(wake_payload.get("wake_id") or "") == wake_id
                and str(wake_payload.get("objective_id") or "")
                == objective_id
                and str(wake_payload.get("kind") or "")
                == "WAIT_FOR_PROVIDER"
                and str(wake_payload.get("provider_id") or "") == provider
                and str(wake_payload.get("status") or "") == "ACTIVE"
                and str(wake_payload.get("reason") or "")[:1000]
                == str(payload.get("reason") or "")
            ):
                wake_authority_valid = False
                break
        if (
            not wait_id
            or not observed_mission
            or not observed_step
            or not observed_run
            or not objective_id
            or event.source_type != "supervisor"
            or event.source_id != "provider-resilience"
            or event.correlation_id != observed_mission
            or event.idempotency_key
            != f"mission.provider_wait_started:{wait_id}"
            or wait_id != expected_wait_id
            or (
                not legacy_identity
                and (
                    payload.get("wait_family_id") != expected_family_id
                    or payload.get("wait_generation") != generation
                )
            )
            or not providers_are_strings
            or providers != tuple(sorted(set(providers)))
            or not wake_ids_are_strings
            or len(wake_ids) != len(providers)
            or wake_ids != expected_wake_ids
            or not wake_authority_valid
            or (not providers and not str(effect_action_key or ""))
            or not isinstance(payload.get("error_class"), str)
            or not str(payload.get("error_class") or "").strip()
            or not isinstance(payload.get("reason"), str)
            or not str(payload.get("reason") or "").strip()
            or not isinstance(payload.get("created_at"), str)
            or not str(payload.get("created_at") or "").strip()
            or (
                requirement_boundary is not None
                and (
                    not effect_action_key
                    or requirement_boundary >= start_sequence
                )
            )
        ):
            raise EventConflict(
                "provider wait start authority is invalid: "
                f"{wait_id or observed_run or 'UNKNOWN'}"
            )
        if wait_id in seen_wait_ids:
            raise EventConflict(
                f"provider wait has duplicate start receipts: {wait_id}"
            )
        seen_wait_ids.add(wait_id)
        canonical.append(event)
    return tuple(canonical)


def canonical_provider_resume_events(
    db: FoundryDatabase,
    *,
    mission_id: str | None = None,
    step_id: str | None = None,
    run_id: str | None = None,
) -> tuple[Event, ...]:
    """Return exact provider-resume receipts after validating their authority."""

    resumes = tuple(
        event
        for event in db.events(
            readonly=True,
            event_types=("mission.provider_wait_resumed",),
        )
        if (
            mission_id is None
            or str(event.payload.get("mission_id") or "") == mission_id
        )
        and (
            step_id is None
            or str(event.payload.get("step_id") or "") == step_id
        )
        and (
            run_id is None
            or str(event.payload.get("run_id") or "") == run_id
        )
    )
    if not resumes:
        return ()
    wait_ids = {
        str(event.payload.get("wait_id") or "")
        for event in resumes
        if str(event.payload.get("wait_id") or "")
    }
    started = canonical_provider_wait_start_events(
        db,
        wait_ids=tuple(wait_ids),
    )
    support_events = tuple(
        db.events(
            readonly=True,
            event_types=(
                "executive.wake_triggered",
                "provider.effect_reconciled",
            ),
        )
    )
    started_by_wait: dict[str, list[tuple[int, Event]]] = {}
    state_by_key: dict[str, list[Mapping[str, Any]]] = {}
    event_ids = tuple(
        event.event_id
        for event in (*started, *resumes, *support_events)
    )
    state_keys = tuple(
        (
            "mission.state_changed:"
            f"{str(event.payload.get('mission_id') or '')}:"
            "ACTION_SELECTION:provider-resume:"
            f"{str(event.payload.get('wait_id') or '')}"
        )
        for event in resumes
    )
    with db.connect(readonly=True) as connection:
        event_placeholders = ", ".join("?" for _ in event_ids)
        sequences = {
            str(row["event_id"]): int(row["sequence"])
            for row in connection.execute(
                "SELECT sequence, event_id FROM events "
                f"WHERE event_id IN ({event_placeholders})",
                event_ids,
            ).fetchall()
        }
        state_placeholders = ", ".join("?" for _ in state_keys)
        for row in connection.execute(
            "SELECT sequence, event_id, event_type, source_type, source_id, "
            "correlation_id, idempotency_key, payload_json "
            "FROM events "
            f"WHERE idempotency_key IN ({state_placeholders})",
            state_keys,
        ).fetchall():
            state_by_key.setdefault(
                str(row["idempotency_key"]),
                [],
            ).append(row)
    for event in started:
        sequence = sequences[event.event_id]
        wait_id = str(event.payload.get("wait_id") or "")
        if wait_id:
            started_by_wait.setdefault(wait_id, []).append((sequence, event))
    wake_triggers_by_id: dict[str, list[tuple[int, Event]]] = {}
    effect_receipts_by_wait: dict[str, list[tuple[int, Event]]] = {}
    for event in support_events:
        sequence = sequences[event.event_id]
        if event.event_type == "executive.wake_triggered":
            wake_id = str(event.payload.get("wake_id") or "")
            if wake_id:
                wake_triggers_by_id.setdefault(wake_id, []).append(
                    (sequence, event)
                )
        elif event.event_type == "provider.effect_reconciled":
            support_wait_id = str(event.payload.get("wait_id") or "")
            if support_wait_id:
                effect_receipts_by_wait.setdefault(
                    support_wait_id,
                    [],
                ).append((sequence, event))

    resumed: list[Event] = []
    seen_waits: set[str] = set()
    for event in resumes:
        sequence = sequences[event.event_id]
        payload = event.payload
        observed_mission = str(payload.get("mission_id") or "")
        observed_step = str(payload.get("step_id") or "")
        observed_run = str(payload.get("run_id") or "")
        wait_id = str(payload.get("wait_id") or "")
        recovery_provider_id = payload.get("recovery_provider_id")
        if (
            not wait_id
            or not observed_mission
            or not observed_step
            or not observed_run
            or event.source_type != "supervisor"
            or event.source_id != "provider-resilience"
            or event.correlation_id != observed_mission
            or event.idempotency_key
            != f"mission.provider_wait_resumed:{wait_id}"
            or not isinstance(payload.get("resumed_at"), str)
            or not str(payload.get("resumed_at") or "").strip()
            or "recovery_provider_id" not in payload
        ):
            raise EventConflict(
                "provider wait resume authority is invalid: "
                f"{wait_id or observed_run or 'UNKNOWN'}"
            )
        if wait_id in seen_waits:
            raise EventConflict(
                f"provider wait has duplicate resume receipts: {wait_id}"
            )
        started = started_by_wait.get(wait_id, [])
        if len(started) != 1:
            raise EventConflict(
                f"provider wait resume lacks one canonical start: {wait_id}"
            )
        started_sequence, started_event = started[0]
        provider_ids = tuple(
            str(item)
            for item in started_event.payload.get("provider_ids") or ()
        )
        if (
            started_sequence >= sequence
            or started_event.source_type != "supervisor"
            or started_event.source_id != "provider-resilience"
            or started_event.correlation_id != observed_mission
            or started_event.idempotency_key
            != f"mission.provider_wait_started:{wait_id}"
        ):
            raise EventConflict(
                f"provider wait start authority is invalid: {wait_id}"
            )
        if (
            provider_ids
            and (
                not isinstance(recovery_provider_id, str)
                or recovery_provider_id not in provider_ids
            )
        ) or (not provider_ids and recovery_provider_id is not None):
            raise EventConflict(
                f"provider wait recovery identity is invalid: {wait_id}"
            )
        resume_material = dict(payload)
        resume_material.pop("resumed_at", None)
        resume_material.pop("recovery_provider_id", None)
        if canonical_json(resume_material) != canonical_json(
            started_event.payload
        ):
            raise EventConflict(
                f"provider wait resume changed immutable lineage: {wait_id}"
            )
        effect_action_key = str(
            started_event.payload.get("effect_action_key") or ""
        )
        wake_ids = tuple(
            str(item)
            for item in started_event.payload.get("wake_ids") or ()
        )
        if effect_action_key:
            receipts = effect_receipts_by_wait.get(wait_id, [])
            valid_receipts = tuple(
                receipt
                for receipt_sequence, receipt in receipts
                if (
                    started_sequence < receipt_sequence < sequence
                    and receipt.source_type == "supervisor"
                    and receipt.source_id == "provider-resilience"
                    and receipt.correlation_id == observed_mission
                    and receipt.idempotency_key
                    == (
                        f"provider.effect_reconciled:{observed_run}:"
                        f"{effect_action_key}:wait:{wait_id}"
                    )
                    and str(receipt.payload.get("wait_id") or "") == wait_id
                    and str(receipt.payload.get("mission_id") or "")
                    == observed_mission
                    and str(receipt.payload.get("step_id") or "")
                    == observed_step
                    and str(receipt.payload.get("run_id") or "")
                    == observed_run
                    and str(
                        receipt.payload.get("effect_action_key") or ""
                    ) == effect_action_key
                )
            )
            if len(valid_receipts) != 1:
                raise EventConflict(
                    "provider wait resume lacks exact effect reconciliation "
                    f"authority: {wait_id}"
                )
        else:
            try:
                recovery_index = provider_ids.index(
                    str(recovery_provider_id)
                )
                recovery_wake_id = wake_ids[recovery_index]
            except (ValueError, IndexError) as error:
                raise EventConflict(
                    f"provider wait recovery wake is invalid: {wait_id}"
                ) from error
            valid_trigger = any(
                (
                    started_sequence < trigger_sequence < sequence
                    and trigger.source_type == "supervisor"
                    and trigger.source_id == "v9-continuous"
                    and trigger.correlation_id == recovery_wake_id
                    and str(trigger.payload.get("wake_id") or "")
                    == recovery_wake_id
                    and trigger.idempotency_key is not None
                    and trigger.idempotency_key.startswith(
                        f"executive.wake_triggered:{recovery_wake_id}:"
                    )
                    and isinstance(trigger.payload.get("triggered_at"), str)
                    and bool(str(trigger.payload.get("triggered_at") or ""))
                )
                for trigger_sequence, trigger in wake_triggers_by_id.get(
                    recovery_wake_id,
                    (),
                )
            )
            if not valid_trigger:
                raise EventConflict(
                    "provider wait resume lacks exact wake trigger authority: "
                    f"{wait_id}"
                )
        state_key = (
            f"mission.state_changed:{observed_mission}:ACTION_SELECTION:"
            f"provider-resume:{wait_id}"
        )
        state_events = state_by_key.get(state_key, [])
        if len(state_events) != 1:
            raise EventConflict(
                f"provider wait resume lacks one state transition: {wait_id}"
            )
        state_event = state_events[0]
        state_sequence = int(state_event["sequence"])
        state_payload = json.loads(str(state_event["payload_json"]))
        if (
            state_sequence != sequence + 1
            or str(state_event["event_type"]) != "mission.state_changed"
            or str(state_event["source_type"]) != "supervisor"
            or str(state_event["source_id"]) != "provider-resilience"
            or str(state_event["correlation_id"]) != observed_mission
            or str(state_payload.get("mission_id") or "")
            != observed_mission
            or str(state_payload.get("run_id") or "") != observed_run
            or str(state_payload.get("state") or "") != "ACTION_SELECTION"
        ):
            raise EventConflict(
                f"provider wait resume state transition is invalid: {wait_id}"
            )
        seen_waits.add(wait_id)
        resumed.append(event)
    return tuple(resumed)


def provider_resume_run_id(db, *, mission_id: str, step_id: str) -> str | None:
    """Recover the exact builder run released by the latest provider wait."""

    resumed = canonical_provider_resume_events(
        db,
        mission_id=mission_id,
        step_id=step_id,
    )
    if not resumed:
        return None
    return str(resumed[-1].payload.get("run_id") or "") or None


def provider_resume_epoch(
    db: FoundryDatabase,
    *,
    mission_id: str,
    run_id: str,
) -> str | None:
    """Return the latest canonical wait episode that released one run."""

    resumed = canonical_provider_resume_events(
        db,
        mission_id=mission_id,
        run_id=run_id,
    )
    if not resumed:
        return None
    return str(resumed[-1].payload.get("wait_id") or "") or None


def resume_mission_provider_waits(
    db,
    *,
    provider_id: str | None = None,
    stop_path: str | Path | None = None,
    controller: str = "supervisor",
):
    """Release due provider/effect waits exactly once, preserving identity."""

    if controller != SUPERVISOR_CONTROLLER:
        raise PermissionError("provider wait recovery is Supervisor-owned")
    events = canonical_provider_wait_start_events(db)
    tick_wakes(db, controller=controller)
    wake_state = rebuild_wake_state(db)
    resumed_waits = {
        str(event.payload.get("wait_id") or "")
        for event in canonical_provider_resume_events(db)
    }
    resumed: list[str] = []
    for event in events:
        payload = dict(event.payload)
        wait_id = str(payload.get("wait_id") or "")
        if not wait_id or wait_id in resumed_waits:
            continue
        providers = tuple(str(item) for item in payload.get("provider_ids") or ())
        if provider_id is not None and provider_id not in providers:
            continue
        identities = {
            "GLOBAL",
            "executive",
            "campaigns",
            str(payload.get("objective_id") or ""),
            str(payload.get("campaign_id") or ""),
            str(payload.get("child_id") or ""),
            str(payload.get("mission_id") or ""),
        }
        identities.discard("")
        effective_stop_path = (
            Path(stop_path).expanduser().resolve()
            if stop_path is not None
            else Path(str(payload.get("stop_path"))).expanduser().resolve()
            if payload.get("stop_path")
            else Path(db.path).resolve().parent / "EMERGENCY_STOP"
        )
        stop_reason = emergency_stop_reason(
            db,
            effective_stop_path,
            identities=identities,
        ) or matching_veto_reason(db, identities)
        if stop_reason:
            continue
        action_key = str(payload.get("effect_action_key") or "")
        if action_key:
            requirement_boundary = _effect_requirement_boundary(
                db,
                payload,
            )
            due = _effect_has_reconciliation(
                db,
                action_key,
                after_sequence=(
                    requirement_boundary
                    if requirement_boundary is not None
                    else _event_sequence(db, event.event_id)
                ),
            )
            effect_receipt_key = (
                f"provider.effect_reconciled:{payload.get('run_id')}:"
                f"{action_key}:wait:{wait_id}"
            )
            if due and not any(
                item.event_type == "provider.effect_reconciled"
                and item.idempotency_key == effect_receipt_key
                and str(item.payload.get("wait_id") or "") == wait_id
                for item in db.events(
                    readonly=True,
                    event_types=("provider.effect_reconciled",),
                )
            ):
                _append(
                    db,
                    "provider.effect_reconciled",
                    {
                        "wait_id": wait_id,
                        "mission_id": payload.get("mission_id"),
                        "step_id": payload.get("step_id"),
                        "run_id": payload.get("run_id"),
                        "effect_action_key": action_key,
                    },
                    key=(
                        effect_receipt_key
                    ),
                    source_id="provider-resilience",
                    correlation_id=str(payload.get("mission_id") or ""),
                )
        else:
            provider_wakes = tuple(zip(
                providers,
                (
                    str(wake_id)
                    for wake_id in payload.get("wake_ids") or ()
                ),
                strict=False,
            ))
            due = (
                any(
                    candidate_provider == provider_id
                    and wake_state.get(wake_id, {}).get("status")
                    == "TRIGGERED"
                    for candidate_provider, wake_id in provider_wakes
                )
                if provider_id is not None
                else any(
                    wake_state.get(wake_id, {}).get("status") == "TRIGGERED"
                    for _candidate_provider, wake_id in provider_wakes
                )
            )
        if not due:
            continue
        mission_id = str(payload.get("mission_id") or "")
        mission = db.row("missions", "mission_id", mission_id)
        if mission is None or str(mission.get("state") or "") != "BLOCKED":
            continue
        run_id = str(payload.get("run_id") or "")
        recovery_provider_id = provider_id
        if recovery_provider_id is None and providers:
            for candidate_provider, wake_id in zip(
                providers, payload.get("wake_ids") or (), strict=False
            ):
                if wake_state.get(str(wake_id), {}).get("status") == "TRIGGERED":
                    recovery_provider_id = candidate_provider
                    break
        resumed_event = Event.create(
            "mission.provider_wait_resumed",
            {
                **payload,
                "resumed_at": isoformat(),
                "recovery_provider_id": recovery_provider_id,
            },
            source_type="supervisor",
            source_id="provider-resilience",
            correlation_id=mission_id,
            idempotency_key=f"mission.provider_wait_resumed:{wait_id}",
        )
        state_event = Event.create(
            "mission.state_changed",
            {
                "mission_id": mission_id,
                "state": "ACTION_SELECTION",
                "next_action": mission.get("next_action"),
                "run_id": run_id,
                "reason": "provider wait condition satisfied",
            },
            source_type="supervisor",
            source_id="provider-resilience",
            correlation_id=mission_id,
            idempotency_key=(
                f"mission.state_changed:{mission_id}:ACTION_SELECTION:"
                f"provider-resume:{wait_id}"
            ),
        )
        try:
            db.append_events([resumed_event, state_event])
        except EventConflict:
            continue
        resumed.append(wait_id)
        resumed_waits.add(wait_id)
        child_id = str(payload.get("child_id") or "")
        if child_id:
            try:
                from .campaigns import record_child_state

                record_child_state(
                    db,
                    child_id,
                    "READY",
                    reason=(
                        f"provider {recovery_provider_id} recovery released exact child"
                        if recovery_provider_id
                        else "effect reconciliation released exact child"
                    ),
                    controller=controller,
                    stop_path=effective_stop_path,
                )
            except (KeyError, ValueError):
                # Startup reconciliation will retry the projection from the
                # durable mission.provider_wait_resumed receipt.
                pass
    return resumed


def on_provider_readiness_changed(
    db,
    *,
    provider_id: str,
    model: str = "",
    controller: str = "supervisor",
):
    """Existing wake callback for one observed positive provider recovery."""

    return resume_mission_provider_waits(
        db, provider_id=provider_id, controller=controller)

def schedule_wait_for_owner(db, objective_id: str, *, signal: str = "owner_directive", reason="", controller="supervisor"):
    wid = f"wake:{objective_id}:owner:{signal}"
    wakes = rebuild_wake_state(db)
    if wid in wakes and wakes[wid].get("status")=="ACTIVE": return wakes[wid]
    w = WakeCondition(wake_id=wid, objective_id=objective_id, kind="WAIT_FOR_OWNER", reason=reason or "wait for owner input", created_at=__import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat(), signal=signal)
    create_wake(db, w, controller=controller)
    return w.to_payload() if hasattr(w, "to_payload") else dict(w.__dict__)

def schedule_wait_for_signal(db, objective_id: str, *, signal: str, reason="", controller="supervisor"):
    wid = f"wake:{objective_id}:signal:{signal}"
    wakes = rebuild_wake_state(db)
    if wid in wakes and wakes[wid].get("status")=="ACTIVE": return wakes[wid]
    w = WakeCondition(wake_id=wid, objective_id=objective_id, kind="WAIT_FOR_SIGNAL", reason=reason or f"wait for signal {signal}", created_at=__import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat(), signal=signal)
    create_wake(db, w, controller=controller)
    return w.to_payload() if hasattr(w, "to_payload") else dict(w.__dict__)

def schedule_wait_for_resource(db, objective_id: str, *, resource_id: str, reason="", controller="supervisor"):
    wid = f"wake:{objective_id}:resource:{resource_id}"
    wakes = rebuild_wake_state(db)
    if wid in wakes and wakes[wid].get("status")=="ACTIVE": return wakes[wid]
    w = WakeCondition(wake_id=wid, objective_id=objective_id, kind="WAIT_FOR_RESOURCE", reason=reason or f"wait for resource {resource_id}", created_at=__import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat(), resource_id=resource_id)
    create_wake(db, w, controller=controller)
    return w.to_payload() if hasattr(w, "to_payload") else dict(w.__dict__)

def clear_wakes_for_objective(db, objective_id: str, *, controller="supervisor", reason="objective terminal"):
    wakes = rebuild_wake_state(db)
    cleared=[]
    for w in wakes.values():
        if w.get("objective_id")==objective_id and w.get("status")=="ACTIVE":
            try: clear_wake(db, w["wake_id"], controller=controller, reason=reason); cleared.append(w["wake_id"])
            except Exception: pass
    return cleared

def on_mission_outcome(db, mission_id: str, *, controller="supervisor"):
    # Wake dependents whose dependency just completed
    try:
        from ..executive.autonomy import observe_mission_outcome as _obs
        result = _obs(db, mission_id, controller=controller)
        # Tick dependency wakes
        for w in list(rebuild_wake_state(db).values()):
            if w.get("kind")=="WAIT_FOR_DEPENDENCY" and w.get("status")=="ACTIVE":
                dep=w.get("dependency_id")
                if dep and is_wake_due(db, w):
                    try: trigger_wake(db, w["wake_id"], controller=controller, reason=f"dependency {dep} completed via mission {mission_id}")
                    except Exception: pass
        return result
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

def on_capability_changed(db, *, controller="supervisor"):
    reconciled = ()
    from ..fabric.gap_bridge import GapBridgeStopped, reconcile_ready_gaps
    try:
        reconciled = reconcile_ready_gaps(db)
    except GapBridgeStopped:
        # STOP permits no bridge mutation; the durable gap is reconciled on a
        # later Supervisor tick after RESUME.
        return ()
    try:
        auto_wake_for_blocked(db, controller=controller)
    except Exception:
        pass
    tick_wakes(db, controller=controller)
    return reconciled

def on_owner_directive(db, *, controller="supervisor"):
    # Sync goals then tick owner wakes
    try:
        from ..executive.autonomy import sync_owner_goals as _sync
        _sync(db, controller=controller)
    except Exception: pass
    tick_wakes(db, controller=controller)

def escalate_stale_wakes(db, *, stale_after_seconds=3600.0, controller="supervisor"):
    """Surface escalation for wakes parked too long without progress."""
    stale = stale_wakes(db, stale_after_seconds=stale_after_seconds)
    escalated = []
    for w in stale:
        oid = w.get("objective_id")
        if not oid:
            continue
        try:
            from ..executive.autonomy import record_escalation as _esc
            _esc(db, objective_id=str(oid), decision=f"stale wake {w.get('wake_id')}", why=f"wake {w.get('kind')} for {oid} has been parked >{int(stale_after_seconds)}s without progress", evidence_refs=[], options=["owner cancel", "owner reprioritize", "wait longer"], recommendation="review stale blocker or cancel", delay_consequence="objective remains blocked", controller=controller)
            escalated.append(w.get("wake_id"))
        except Exception:
            pass
    return escalated

__all__ = ["WakeCondition", "create_wake", "clear_wake", "trigger_wake", "rebuild_wake_state", "is_wake_due", "due_wakes", "tick_wakes", "consume_next_action_wakes", "record_next_action_continuation", "bridge_world_signals", "bridge_research_completions", "consume_research_wakes", "record_research_wake_continuation", "RESEARCH_WAKE_PREFIX", "continuous_snapshot", "blocker_for", "run_continuous_cycle", "record_strategy_attempt", "recent_strategies", "is_retry_loop", "next_strategy_options", "schedule_wait_until", "schedule_wait_for_dependency", "schedule_wait_for_capability", "schedule_wait_for_provider", "schedule_mission_provider_wait", "canonical_provider_wait_start_events", "canonical_provider_resume_events", "resume_mission_provider_waits", "provider_resume_run_id", "provider_resume_epoch", "on_provider_readiness_changed", "schedule_wait_for_owner", "schedule_wait_for_signal", "schedule_wait_for_resource", "clear_wakes_for_objective", "on_mission_outcome", "on_capability_changed", "on_owner_directive", "deadline_urgency", "stale_wakes", "auto_wake_for_blocked"]
