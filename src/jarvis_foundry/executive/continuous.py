from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
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
            if wid in wakes: wakes[wid]["status"] = "TRIGGERED"; wakes[wid]["triggered_at"] = p.get("triggered_at")
    return wakes
def is_wake_due(db, wake: dict, now=None):
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
            st = _reb(db)
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
def due_wakes(db, now=None):
    wakes = rebuild_wake_state(db)
    return [w for w in wakes.values() if w.get("status") == "ACTIVE" and is_wake_due(db, w, now=now)]
def _exec_state_for_objective(db, objective_id: str, wakes: dict | None = None):
    from ..executive.autonomy import rebuild_executive_state as _reb, evaluate_objective_progress as _eval
    st = _reb(db)
    obj = st.objectives.get(objective_id)
    if obj is None: return "UNKNOWN"
    wakes = wakes if wakes is not None else rebuild_wake_state(db)
    my_wakes = [w for w in wakes.values() if w.get("objective_id") == objective_id and w.get("status") == "ACTIVE"]
    has_due = any(is_wake_due(db, w) for w in my_wakes)
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
        from ..executive.autonomy import rank_objectives
        rows = rank_objectives(db)
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
        ev = _eval(db, objective_id)
        if ev.stale: return "STALE"
    except Exception: pass
    if obj.status == "ACTIVE": return "RUNNABLE"
    return obj.status
def continuous_snapshot(db, *, include_wakes=True):
    from ..executive.autonomy import rebuild_executive_state as _reb, rank_objectives as _rank, rebuild_executive_state
    from ..executive.autonomy import executive_autonomy_snapshot as _snap
    wakes = rebuild_wake_state(db) if include_wakes else {}
    states = {}
    st = _reb(db)
    for oid in st.objectives:
        states[oid] = _exec_state_for_objective(db, oid, wakes=wakes)
    due = due_wakes(db) if include_wakes else []
    base = _snap(db)
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

def run_continuous_cycle(db, *, cycle_id: str, controller="supervisor", now=None, max_new_missions=1):
    # One idempotent durable tick: due wakes -> trigger -> autonomy cycle -> materialize
    # Idempotent on cycle_id via autonomy cycle key; wake triggers are idempotent via wake_triggered keys
    if controller != SUPERVISOR_CONTROLLER: raise PermissionError("continuous cycle is Supervisor-owned")
    # Check owner authority first
    from ..control.broker import emergency_stop_reason as _esr
    from pathlib import Path as _P
    if _esr(db, _P(db.path).parent / "EMERGENCY_STOP"):
        return {"cycle_id": cycle_id, "status": "OWNER_BLOCKED", "reason": "STOP active — no mutation"}
    veto = __import__("jarvis_foundry.control.broker", fromlist=["matching_veto_reason"]).matching_veto_reason(db, {"GLOBAL", "executive", "objectives"})
    if veto:
        return {"cycle_id": cycle_id, "status": "OWNER_BLOCKED", "reason": f"veto: {veto}"}
    # Park blocked objectives before evaluating wakes.  This keeps the
    # Supervisor-owned cycle self-contained across direct invocation and
    # restart; the Worker wrapper may call the helper too, but idempotent wake
    # keys make that replay harmless.
    parked = auto_wake_for_blocked(db, controller=controller)
    # World Intelligence is advisory input only.  The Executive bridge admits
    # objective-targeted signals as durable wake conditions; it never creates
    # work itself.
    world_triggered = bridge_world_signals(db, controller=controller)
    # Wake phase
    triggered = tick_wakes(db, controller=controller, now=now)
    triggered = list(dict.fromkeys([*world_triggered, *triggered]))
    # Strategy guard: if top runnable objective is in retry loop, surface escalation instead of re-attempting same strategy
    try:
        from ..executive.autonomy import rank_objectives as _rank2, rebuild_executive_state as _reb2
        rows = _rank2(db)
        top = next((r for r in rows if r.eligible), None)
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
                return {"cycle_id": cycle_id, "status": "ESCALATE", "reason": f"retry loop detected for {top.objective_id} on {loop_strategy!r} — escalation recorded", "triggered_wakes": triggered, "parked_objectives": parked}
    except Exception:
        pass
    # Autonomy phase: delegate to existing authoritative cycle
    from ..executive.autonomy import run_autonomy_cycle as _run_auto
    try:
        cycle = _run_auto(db, cycle_id=cycle_id, controller=controller)
        return {"cycle_id": cycle_id, "status": cycle.status, "reason": cycle.reason, "triggered_wakes": triggered, "parked_objectives": parked, "autonomy_cycle": cycle.to_payload() if hasattr(cycle, "to_payload") else dict(cycle) if isinstance(cycle, dict) else str(cycle)}
    except Exception as e:
        return {"cycle_id": cycle_id, "status": "BLOCKED", "reason": f"{type(e).__name__}: {e}", "triggered_wakes": triggered, "parked_objectives": parked}

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

def auto_wake_for_blocked(db, *, controller="supervisor"):
    # Create wakes for currently blocked objectives that have no wake yet
    created=[]
    try:
        from ..executive.autonomy import rebuild_executive_state as _reb, rank_objectives as _rank
        st=_reb(db)
        wakes=rebuild_wake_state(db)
        active_wake_oids={w.get("objective_id") for w in wakes.values() if w.get("status")=="ACTIVE"}
        for row in _rank(db):
            oid=row.objective_id
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

def schedule_wait_for_provider(db, objective_id: str, *, provider_id: str, reason="", controller="supervisor"):
    wid = f"wake:{objective_id}:provider:{provider_id}"
    wakes = rebuild_wake_state(db)
    if wid in wakes and wakes[wid].get("status")=="ACTIVE": return wakes[wid]
    w = WakeCondition(wake_id=wid, objective_id=objective_id, kind="WAIT_FOR_PROVIDER", reason=reason or f"wait for provider {provider_id}", created_at=__import__("jarvis_foundry.clock", fromlist=["isoformat"]).isoformat(), provider_id=provider_id)
    create_wake(db, w, controller=controller)
    return w.to_payload() if hasattr(w, "to_payload") else dict(w.__dict__)

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
    try:
        auto_wake_for_blocked(db, controller=controller)
    except Exception:
        pass
    tick_wakes(db, controller=controller)

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

__all__ = ["WakeCondition", "create_wake", "clear_wake", "trigger_wake", "rebuild_wake_state", "is_wake_due", "due_wakes", "tick_wakes", "bridge_world_signals", "continuous_snapshot", "blocker_for", "run_continuous_cycle", "record_strategy_attempt", "recent_strategies", "is_retry_loop", "next_strategy_options", "schedule_wait_until", "schedule_wait_for_dependency", "schedule_wait_for_capability", "schedule_wait_for_provider", "schedule_wait_for_owner", "schedule_wait_for_signal", "schedule_wait_for_resource", "clear_wakes_for_objective", "on_mission_outcome", "on_capability_changed", "on_owner_directive", "deadline_urgency", "stale_wakes", "auto_wake_for_blocked"]
