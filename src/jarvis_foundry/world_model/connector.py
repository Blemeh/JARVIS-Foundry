"""Cross-system intelligence connector - what is known somewhere but not understood/connected."""
from __future__ import annotations

import json
from typing import Any, Mapping

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.mission.verification import (
    final_outcome_history,
    record_final_outcome,
    validate_final_outcome_contract,
)
from jarvis_foundry.world_model.facade import WorldModel


def _normalized_status(value: str) -> str:
    status = str(value or "UNKNOWN").upper()
    return {
        "PASS": "SUCCEEDED",
        "PASSED": "SUCCEEDED",
        "COMPLETED": "SUCCEEDED",
        "SUCCESS": "SUCCEEDED",
        "FAIL": "FAILED",
        "FAILED_VERIFICATION": "FAILED",
        "CANCELLED": "REJECTED",
    }.get(status, status)


def writeback_mission_outcome(
    db: FoundryDatabase,
    mission_id: str,
    outcome: str,
    payload: dict[str, Any] | None = None,
    *,
    evidence_refs: tuple[str, ...] = (),
    final_outcome_id: str | None = None,
    source_id: str = "world-model-runtime",
) -> dict[str, Any]:
    """Project one accepted final mission outcome into World Intelligence.

    The status and evidence are read from ``mission.outcome_finalized``. The
    caller's value is checked for disagreement but is never treated as truth.
    Stable outcome identity makes redelivery exactly-once while a changed
    AcceptanceGate/verifier identity becomes a new preserved truth revision.
    """
    mission_id = str(mission_id or "").strip()
    if not mission_id:
        raise ValueError("mission_id is required")
    history = final_outcome_history(db, mission_id=mission_id, limit=20)
    final = next(
        (
            item for item in history
            if (
                final_outcome_id is None
                or str(item.get("outcome_id") or "") == str(final_outcome_id)
            ) and validate_final_outcome_contract(db, item)
        ),
        None,
    )
    if final is None:
        return {
            "status": "NOT_EMITTED",
            "reason": "mission has no accepted final outcome truth",
            "mission_id": mission_id,
            "reported_outcome": _normalized_status(outcome),
            "accepted": False,
            "deduplicated": True,
        }
    status = str(final.get("status") or "UNKNOWN").upper()
    reported = _normalized_status(outcome)
    if reported != status:
        return {
            "status": "NOT_EMITTED",
            "reason": (
                f"reported outcome {reported!r} disagrees with accepted "
                f"final truth {status!r}"),
            "mission_id": mission_id,
            "final_outcome_id": final.get("outcome_id"),
            "accepted": False,
            "deduplicated": True,
        }
    accepted_refs = [
        str(value) for value in final.get("evidence_refs") or [] if value
    ]
    # Legacy callers may supply references, but only references already bound
    # into the accepted contract can reach the WorldModel.
    accepted_refs.extend(
        ref for ref in evidence_refs if ref in set(accepted_refs)
    )
    accepted_refs = list(dict.fromkeys(accepted_refs))[:30]
    details = dict(final.get("actual_observed") or {})
    details.update({
        "mission_id": mission_id,
        "objective_id": final.get("objective_id"),
        "candidate_id": final.get("candidate_id"),
        "project_id": final.get("project_id"),
        "outcome": status,
        "outcome_id": final.get("outcome_id"),
        "evidence_refs": accepted_refs,
        "repair_attempts": list(final.get("repair_attempts") or []),
    })
    mission = db.row("missions", "mission_id", mission_id)
    wm = WorldModel(db)
    if wm.entities.get(mission_id) is None:
        state = (
            "RESOLVED" if status in {"SUCCEEDED", "REJECTED"}
            else "STALE" if status in {"FAIL", "FAILED", "FAILED_VERIFICATION"}
            else "ACTIVE"
        )
        wm.entities.record(
            "MISSION",
            str((mission or {}).get("objective") or mission_id)[:200],
            entity_id=mission_id,
            state=state,
            attributes={"outcome": status, "state": (mission or {}).get("state")},
            provenance={"source": source_id, "mission_id": mission_id},
            authority="SYSTEM_OBSERVATION",
            record_kind="RESULT",
            source_id=source_id,
        )
    observed = wm.observe(
        "mission:outcome",
        mission_id,
        details,
        provenance={
            "mission_id": mission_id,
            "outcome": status,
            "outcome_id": final.get("outcome_id"),
            "evidence_refs": accepted_refs,
        },
        source_id=source_id,
        observation_id=f"mission-outcome:{final['outcome_id']}",
        idempotency_key=(
            f"world.observation_recorded:mission-outcome:{final['outcome_id']}"),
    )
    learning = None
    if not observed.get("deduplicated"):
        learning = wm.learn_from_mission_result(
            mission_id,
            status,
            details,
            final_outcome_id=str(final["outcome_id"]),
            source_id=source_id,
        )
    return {
        "mission_id": mission_id,
        "outcome": status,
        "final_outcome_id": final.get("outcome_id"),
        "accepted": True,
        "observation": observed,
        "learning": learning,
        "deduplicated": bool(observed.get("deduplicated")),
    }


def _record_hybrid_outcome(
    db: FoundryDatabase, final: Mapping[str, Any], *, source_id: str,
) -> dict[str, Any]:
    """Feed the accepted contract into the existing Tier-C observation seam."""

    from jarvis_foundry.hybrid_memory.lifecycle import TierCMemoryStore

    mission_id = str(final.get("mission_id") or "")
    status = str(final.get("status") or "UNKNOWN")
    outcome_id = str(final.get("outcome_id") or "")
    idempotency_key = f"memory.observed:final-outcome:{outcome_id}"
    with db.connect(readonly=True) as connection:
        prior = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = 'memory.observed' "
            "AND idempotency_key = ? ORDER BY sequence DESC LIMIT 1",
            (idempotency_key,),
        ).fetchone()
    if prior is not None:
        try:
            prior_payload = json.loads(str(prior[0]))
        except (TypeError, ValueError):
            prior_payload = {}
        return {
            "memory_id": str(prior_payload.get("memory_id") or ""),
            "outcome_id": outcome_id,
            "deduplicated": True,
        }
    store = TierCMemoryStore(db)
    memory_id = store.record_observation(
        f"accepted final outcome mission={mission_id} result={status}",
        mission_id=mission_id,
        created_by=source_id,
        source_ids=[f"mission:{mission_id}", f"outcome:{outcome_id}"],
        evidence_ids=[str(value) for value in final.get("evidence_refs") or []],
        confidence=0.85,
        idempotency_key=idempotency_key,
    )
    return {
        "memory_id": memory_id,
        "outcome_id": outcome_id,
        "deduplicated": False,
    }


def reconcile_outcome_learning(
    db: FoundryDatabase,
    *,
    mission_id: str,
    source_id: str = "outcome-reconciler",
) -> dict[str, Any]:
    """Close final truth -> calibration -> WorldModel -> Tier-C exactly once.

    This is wiring over the existing authorities, not a learning or scoring
    engine. It can be retried after any crash: every downstream record is
    keyed by the accepted final outcome identity.
    """

    final = record_final_outcome(
        db, mission_id=mission_id, source_id=source_id)
    if not final.get("finalized"):
        return {
            "finalized": False,
            "mission_id": mission_id,
            "reason": final.get("reason"),
            "calibration": None,
            "world": None,
            "memory": None,
        }

    from jarvis_foundry.executive.calibration import record_calibration

    outcome_id = str(final.get("outcome_id") or "")
    calibration = record_calibration(
        db,
        decision_id=str(final.get("decision_id") or f"post-{mission_id}"),
        candidate_id=str(final.get("candidate_id") or ""),
        mission_id=mission_id,
        source=str((final.get("provenance") or {}).get("source") or "mission"),
        objective=str(final.get("attempted") or mission_id),
        outcome=str(final.get("status") or "UNKNOWN"),
        repair_count=len(final.get("repair_attempts") or []),
        actual=dict(final.get("actual_observed") or {}),
        source_id=source_id,
        terminal_event_id=outcome_id,
    )
    world = writeback_mission_outcome(
        db,
        mission_id,
        str(final.get("status") or "UNKNOWN"),
        final_outcome_id=outcome_id,
        source_id=source_id,
    )
    try:
        memory = _record_hybrid_outcome(db, final, source_id=source_id)
    except Exception as error:  # noqa: BLE001 - advisory memory cannot rewrite truth
        memory = {"status": "NOT_EMITTED", "reason": type(error).__name__}
    return {
        "finalized": True,
        "created": bool(final.get("created")),
        "mission_id": mission_id,
        "outcome_id": outcome_id,
        "status": final.get("status"),
        "final": final,
        "calibration": calibration,
        "world": world,
        "memory": memory,
    }


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
