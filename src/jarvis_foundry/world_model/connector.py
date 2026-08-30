"""Cross-system intelligence connector - what is known somewhere but not understood/connected."""
from __future__ import annotations

import json
from typing import Any

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.world_model.facade import WorldModel


def scan_and_connect(db: FoundryDatabase, wm: WorldModel | None = None) -> dict[str, Any]:
    """Scan existing JARVIS state for understanding gaps and connect them.

    Inspects: missions, knowledge claims, operational intelligence,
    executive objectives, discovery candidates, provider state.
    Creates world entities/relationships/signals/gaps where useful
    intelligence exists but is not yet modeled.
    """
    wm = wm or WorldModel(db)
    created: dict[str, int] = {"entities": 0, "relationships": 0, "signals": 0, "gaps": 0}

    with db.connect() as conn:
        # 1. Missions not yet in world model -> PROJECT/TASK entities
        try:
            missions = conn.execute("SELECT mission_id, objective, state FROM missions LIMIT 50").fetchall()
            for row in missions:
                mid, obj, state = row[0], row[1], row[2]
                existing = wm.entities.get(mid)
                if existing is None and mid and obj:
                    try:
                        wm.entities.record("MISSION", str(obj)[:120], entity_id=mid,
                                           attributes={"state": state}, state="ACTIVE" if state not in ("COMPLETED", "FAILED") else "RESOLVED")
                        created["entities"] += 1
                    except Exception:
                        pass
        except Exception:
            pass

        # 2. Blocked missions -> BLOCKER signals
        try:
            blocked = conn.execute("SELECT mission_id, objective FROM missions WHERE state = 'BLOCKED' LIMIT 20").fetchall()
            for row in blocked:
                mid = row[0]
                r = wm.signals.record("BLOCKER", f"Mission blocked: {mid}",
                                      affected_entities=[mid], confidence=0.8, importance=0.7, urgency=0.6)
                if not r.get("deduplicated") and not r.get("ignored"):
                    created["signals"] += 1
        except Exception:
            pass

        # 3. Knowledge contradictions -> world CONTRADICTION signals
        try:
            contradictions = conn.execute("SELECT contradiction_id, claim_a_id, claim_b_id, reason FROM knowledge_contradictions LIMIT 20").fetchall()
            for row in contradictions:
                cid, a, b, reason = row[0], row[1], row[2], row[3]
                r = wm.signals.record("CONTRADICTION", f"Knowledge contradiction: {a} vs {b}",
                                      summary=str(reason)[:300], affected_entities=[a, b],
                                      confidence=0.8, importance=0.6, urgency=0.5)
                if not r.get("deduplicated") and not r.get("ignored"):
                    created["signals"] += 1
        except Exception:
            pass

        # 4. Stale knowledge claims (expired fresh_until) -> gaps
        try:
            stale = conn.execute(
                "SELECT claim_id, statement FROM knowledge_claims WHERE fresh_until IS NOT NULL AND fresh_until < datetime('now') AND status = 'ACCEPTED' LIMIT 20"
            ).fetchall()
            for row in stale:
                claim_id = row[0]
                wm.gaps.record("UNKNOWN_EXTERNAL", f"Stale knowledge: {claim_id}", subject_id=claim_id)
                created["gaps"] += 1
        except Exception:
            pass

        # 5. Executive objectives -> OWNER_OBJECTIVE entities
        try:
            events = conn.execute(
                "SELECT payload_json FROM events WHERE event_type = 'executive.objective_recorded' ORDER BY sequence DESC LIMIT 20"
            ).fetchall()
            for row in events:
                try:
                    payload = json.loads(row[0])
                    oid = payload.get("objective_id")
                    obj = payload.get("objective", "")
                    if oid and obj and wm.entities.get(oid) is None:
                        wm.entities.record("OWNER_OBJECTIVE", str(obj)[:120], entity_id=oid,
                                           attributes={"status": payload.get("status", "ACTIVE")})
                        created["entities"] += 1
                except Exception:
                    pass
        except Exception:
            pass

        # 6. Provider unknowns -> gaps
        try:
            unknowns = conn.execute(
                "SELECT provider FROM cost_measurements WHERE status IN ('UNKNOWN', 'NOT_AVAILABLE', 'NOT_TESTED') LIMIT 10"
            ).fetchall()
            for row in unknowns:
                prov = row[0]
                if prov:
                    wm.gaps.record("UNKNOWN_PROVIDER_STATE", f"Provider state unknown: {prov}", subject_id=str(prov), blocking=False)
                    created["gaps"] += 1
        except Exception:
            pass

    return {"created": created, "scanned": True}


def detect_surprises(db: FoundryDatabase, wm: WorldModel | None = None) -> list[dict[str, Any]]:
    """What is JARVIS repeatedly surprised by that it should have anticipated?

    Looks at: repeated mission failures, verifier rejections, recovery events
    that could have been predicted from prior world state.
    """
    wm = wm or WorldModel(db)
    surprises: list[dict[str, Any]] = []

    with db.connect() as conn:
        # Repeated failures on same mission pattern
        try:
            failures = conn.execute(
                "SELECT payload_json FROM events WHERE event_type IN ('mission.failed', 'verification.failed') ORDER BY sequence DESC LIMIT 50"
            ).fetchall()
            failure_reasons: dict[str, int] = {}
            for row in failures:
                try:
                    p = json.loads(row[0])
                    reason = str(p.get("reason") or p.get("failure_class") or "unknown")[:80]
                    failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
                except Exception:
                    pass
            for reason, count in failure_reasons.items():
                if count >= 3:
                    surprises.append({"kind": "REPEATED_FAILURE", "reason": reason, "count": count,
                                      "recommendation": f"Create prediction for failure pattern: {reason}"})
        except Exception:
            pass

        # Stale follow-ups that keep reappearing
        try:
            from jarvis_foundry.world_model.read_model import project_world_intelligence
            proj = project_world_intelligence(db, limit=20)
            stale_signals = [s for s in proj.get("signals", []) if s.get("state") == "STALE"]
            if len(stale_signals) >= 3:
                surprises.append({"kind": "STALE_FOLLOWUPS", "count": len(stale_signals),
                                  "recommendation": "Review stale signal handling - many signals going stale without resolution"})
        except Exception:
            pass

    return surprises