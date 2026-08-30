"""WorldModel facade - the single entry point for Lane 3 intelligence."""
from __future__ import annotations

import json
from typing import Any

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase

from jarvis_foundry.world_model.entities import WorldEntityStore
from jarvis_foundry.world_model.relationships import WorldRelationshipStore
from jarvis_foundry.world_model.observations import WorldObservationStore
from jarvis_foundry.world_model.signals import WorldSignalStore
from jarvis_foundry.world_model.predictions import WorldPredictionStore
from jarvis_foundry.world_model.gaps import WorldKnowledgeGapStore
from jarvis_foundry.world_model.patterns import WorldPatternStore
from jarvis_foundry.world_model.calibration import WorldCalibrationStore
from jarvis_foundry.world_model.intelligence import WorldIntelligenceEngine


def _now() -> str:
    return isoformat()


class WorldModel:
    """Coherent evolving understanding of reality.

    Usage:
        wm = WorldModel(db)
        wm.observe("ledger:mission.created", "evt-123", payload)
        wm.entities.record("PROJECT", "My Project", ...)
        wm.signals.record("BLOCKER", "Something is blocked", ...)
        snapshot = wm.snapshot()
        briefing = wm.briefing_context()
    """

    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db
        self.entities = WorldEntityStore(db)
        self.relationships = WorldRelationshipStore(db)
        self.observations = WorldObservationStore(db)
        self.signals = WorldSignalStore(db)
        self.predictions = WorldPredictionStore(db)
        self.gaps = WorldKnowledgeGapStore(db)
        self.patterns = WorldPatternStore(db)
        self.calibration = WorldCalibrationStore(db)
        self.intelligence = WorldIntelligenceEngine(db)

    # ---- observation (primary loop entry) ----

    def observe(
        self,
        source_kind: str,
        source_ref: str,
        payload: dict[str, Any],
        *,
        provenance: dict[str, Any] | None = None,
        source_id: str = "world-model",
        observation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.intelligence.observe(
            source_kind,
            source_ref,
            payload,
            provenance=provenance,
            source_id=source_id,
            observation_id=observation_id,
            idempotency_key=idempotency_key,
        )

    def observe_event(self, event: Any, source_id: str = "world-model") -> dict[str, Any]:
        return self.intelligence.observe_ledger_event(event, source_id=source_id)

    # ---- learning from mission results ----

    def learn_from_mission_result(
        self,
        mission_id: str,
        outcome: str,
        payload: dict[str, Any] | None = None,
        *,
        source_id: str = "world-model",
    ) -> dict[str, Any]:
        """Update world understanding from a mission/verifier outcome."""
        payload = payload or {}
        signals: list[dict[str, Any]] = []
        outcome_upper = outcome.upper()

        if outcome_upper in ("FAILED", "FAIL", "FAILED_VERIFICATION"):
            # Record pattern
            pat = self.patterns.record(
                "MISSION_FAILURE", f"mission {mission_id} failed: {outcome}",
                evidence=[mission_id], confidence=0.5, source_id=source_id,
            )
            # Check if this updates provider expectations
            provider_id = payload.get("provider_id") or payload.get("provider")
            if provider_id:
                # Provider failure pattern
                pat2 = self.patterns.record(
                    "PROVIDER_FAILURE", f"provider {provider_id} failed for mission {mission_id}",
                    evidence=[mission_id, str(provider_id)], confidence=0.5, source_id=source_id,
                )
                if not pat2["is_new"] and pat2["occurrences"] >= 2:
                    r = self.signals.record(
                        "PROVIDER_DEGRADATION", f"Provider {provider_id} repeatedly failing ({pat2['occurrences']} times)",
                        affected_entities=[str(provider_id)], evidence=[mission_id],
                        provenance={"provider_id": str(provider_id), "pattern_id": pat2["pattern_id"]},
                        confidence=0.6, importance=0.7, urgency=0.6, source_id=source_id,
                    )
                    if not r.get("deduplicated") and not r.get("ignored"):
                        full = self.signals.get(r["signal_id"])
                        signals.append(full if full else r)
            # Update mission entity if it exists
            try:
                existing = self.entities.get(mission_id)
                if existing:
                    self.entities.update_state(mission_id, state="STALE", source_id=source_id)
            except (ValueError, Exception):
                pass

        elif outcome_upper in ("PASSED", "PASS", "COMPLETED", "SUCCESS"):
            # Successful procedure reinforces knowledge
            proc_id = payload.get("procedure_id")
            if proc_id:
                self.patterns.record(
                    "SUCCESSFUL_PROCEDURE", f"procedure {proc_id} succeeded for mission {mission_id}",
                    evidence=[mission_id, str(proc_id)], confidence=0.6, source_id=source_id,
                )
            # Mission success: update entity
            try:
                existing = self.entities.get(mission_id)
                if existing:
                    self.entities.update_state(mission_id, state="RESOLVED", resolved_at=_now(), source_id=source_id)
            except (ValueError, Exception):
                pass

        # Calibration: record prediction outcome if there was a prediction for this mission
        for pred in self.predictions.list(subject_id=mission_id, outcome="PENDING", limit=10):
            expected = (pred.get("predicted_value") or {}).get("outcome", "")
            if expected:
                is_confirmed = expected.upper() == outcome_upper
                self.predictions.record_outcome(
                    pred["prediction_id"],
                    "CONFIRMED" if is_confirmed else "REFUTED",
                    source_id=source_id,
                )
                self.calibration.record_outcome(pred["subject_type"], pred["subject_id"], "CONFIRMED" if is_confirmed else "REFUTED", source_id=source_id)

        return {"mission_id": mission_id, "outcome": outcome, "signals": signals}

    # ---- prediction + calibration ----

    def predict(
        self,
        subject_type: str,
        subject_id: str,
        prediction_kind: str,
        predicted_value: dict[str, Any],
        *,
        confidence: float = 0.5,
        horizon_until: str | None = None,
        provenance: dict[str, Any] | None = None,
        source_id: str = "world-model",
    ) -> str:
        # Apply calibration adjustment
        adj = self.calibration.adjustment_for(subject_type, subject_id)
        adjusted_confidence = max(0.05, min(0.95, confidence + adj))
        pid = self.predictions.record(
            subject_type, subject_id, prediction_kind, predicted_value,
            confidence=adjusted_confidence, horizon_until=horizon_until,
            provenance={**(provenance or {}), "raw_confidence": confidence, "calibration_adjustment": adj},
            source_id=source_id,
        )
        return pid

    # ---- snapshot (current authoritative view) ----

    def snapshot(self, limit: int = 100) -> dict[str, Any]:
        """Current world state snapshot - bounded, no full ledger scan per call."""
        with self.db.connect() as conn:
            entity_count = conn.execute("SELECT COUNT(*) FROM world_entities WHERE state = 'ACTIVE'").fetchone()[0]
            signal_count = conn.execute("SELECT COUNT(*) FROM world_signals WHERE state IN ('OPEN', 'MONITORING', 'ACKNOWLEDGED')").fetchone()[0]
            prediction_count = conn.execute("SELECT COUNT(*) FROM world_predictions WHERE outcome = 'PENDING'").fetchone()[0]
            gap_count = conn.execute("SELECT COUNT(*) FROM world_knowledge_gaps WHERE resolved = 0").fetchone()[0]
            pattern_count = conn.execute("SELECT COUNT(*) FROM world_patterns WHERE occurrences >= 2").fetchone()[0]

        return {
            "generated_at": _now(),
            "counts": {
                "active_entities": entity_count,
                "active_signals": signal_count,
                "pending_predictions": prediction_count,
                "unresolved_gaps": gap_count,
                "significant_patterns": pattern_count,
            },
            "entities": self.entities.current_view(limit=min(limit, 50)),
            "signals": self.signals.active(limit=min(limit, 30)),
            "predictions": self.predictions.pending(limit=min(limit, 20)),
            "gaps": self.gaps.unresolved(limit=min(limit, 20)),
            "patterns": self.patterns.significant(limit=min(limit, 10)),
        }

    def historical_trace(self, entity_id: str) -> dict[str, Any]:
        return {
            "entity_id": entity_id,
            "entity": self.entities.get(entity_id),
            "history": self.entities.history(entity_id),
            "context": self.intelligence.synthesize_context(entity_id),
        }

    # ---- executive handoff ----

    def executive_handoff(self, limit: int = 20) -> list[dict[str, Any]]:
        """Clean intelligence contracts for Lane 1 (Executive)."""
        handoff: list[dict[str, Any]] = []
        for sig in self.signals.active(limit=limit):
            if sig["salience"] in ("OWNER_ATTENTION", "URGENT", "WORK_CANDIDATE"):
                handoff.append({
                    "signal_id": sig["signal_id"],
                    "signal_kind": sig["signal_kind"],
                    "salience": sig["salience"],
                    "state": sig["state"],
                    "title": sig["title"],
                    "summary": sig["summary"],
                    "affected_entities": sig.get("affected_entities") or [],
                    "confidence": sig["confidence"],
                    "importance": sig["importance"],
                    "urgency": sig["urgency"],
                    "recommended_action": sig.get("recommended_action"),
                    "evidence": sig.get("evidence") or [],
                    "provenance": sig.get("provenance") or {},
                    "due_at": sig.get("due_at"),
                })
        return handoff

    # ---- briefing ----

    def briefing_context(self) -> dict[str, Any]:
        """Context for the executive briefing - what changed, what matters."""
        snap = self.snapshot(limit=50)
        active = self.signals.active(limit=30)
        # What changed recently: entities updated in last 24h
        recent_entities: list[dict[str, Any]] = []
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_entities WHERE updated_at >= datetime('now', '-1 day') ORDER BY updated_at DESC LIMIT 20"
            ).fetchall()
            for row in rows:
                d = dict(row)
                import json as _json
                for k in ("attributes_json", "provenance_json", "derived_from_json"):
                    raw = d.pop(k, None)
                    t = k.replace("_json", "")
                    if k == "derived_from_json":
                        t = "derived_from"
                    try:
                        d[t] = _json.loads(raw) if raw else ({} if t != "derived_from" else [])
                    except (TypeError, ValueError):
                        d[t] = {} if t != "derived_from" else []
                recent_entities.append(d)

        blocking = self.gaps.blocking_gaps(limit=10)
        return {
            "generated_at": _now(),
            "what_changed": recent_entities[:10],
            "what_matters": [s for s in active if s["salience"] in ("OWNER_ATTENTION", "URGENT")][:10],
            "risks": [s for s in active if s["signal_kind"] == "RISK"][:5],
            "opportunities": [s for s in active if s["signal_kind"] == "OPPORTUNITY"][:5],
            "blockers": [s for s in active if s["signal_kind"] == "BLOCKER"][:5],
            "unresolved_gaps": snap["gaps"][:5],
            "blocking_gaps": blocking,
            "patterns": snap["patterns"][:5],
            "predictions": snap["predictions"][:5],
            "counts": snap["counts"],
            "handoff": self.executive_handoff(limit=10),
        }

    # ---- maintenance ----

    def sweep(self, now: str | None = None) -> dict[str, Any]:
        return self.intelligence.sweep_staleness(now=now)

    def consolidate(self, source_kind: str) -> dict[str, Any]:
        return self.intelligence.consolidate_observations(source_kind)
