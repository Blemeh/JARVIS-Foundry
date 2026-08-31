"""World intelligence engine - the core observe/normalize/detect/signal loop."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event

from jarvis_foundry.world_model.entities import WorldEntityStore
from jarvis_foundry.world_model.relationships import WorldRelationshipStore
from jarvis_foundry.world_model.observations import WorldObservationStore
from jarvis_foundry.world_model.signals import WorldSignalStore
from jarvis_foundry.world_model.predictions import WorldPredictionStore
from jarvis_foundry.world_model.gaps import WorldKnowledgeGapStore
from jarvis_foundry.world_model.patterns import WorldPatternStore


def _now() -> str:
    return isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class WorldIntelligenceEngine:
    """Turns observations into proactive intelligence.

    Loop: OBSERVE -> normalize -> identify entities/events/relationships
          -> compare with known state -> detect change -> update world model
          -> resolve contradiction/supersession -> assess significance
          -> detect blocker/risk/opportunity/follow-up/pattern
          -> generate executive intelligence
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

    # ---- observation intake ----

    def observe(
        self,
        source_kind: str,
        source_ref: str,
        payload: dict[str, Any],
        *,
        provenance: dict[str, Any] | None = None,
        source_id: str = "world-intelligence",
        observation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Primary entry: observe something, update world state, maybe signal."""
        # A stable observation identity is the replay boundary for runtime
        # writeback (for example, a mission terminal result delivered again
        # after restart).  Do not re-run detection or increment patterns when
        # the same observation is already projected.
        if observation_id:
            prior = self.observations.get(observation_id)
            if prior is not None:
                return {
                    "observation_id": observation_id,
                    "signals": [],
                    "entities": list(prior.get("entities") or []),
                    "deduplicated": True,
                }
        result = self.observations.record(
            source_kind,
            source_ref,
            payload,
            observation_id=observation_id,
            idempotency_key=idempotency_key,
            provenance=provenance,
            source_id=source_id,
        )
        # Auto-process: detect what matters
        signals = self._detect_from_observation(result["observation_id"], payload, source_kind)
        return {"observation_id": result["observation_id"], "signals": signals, "entities": result["entities"], "deduplicated": False}

    def observe_ledger_event(self, event: Any, source_id: str = "world-intelligence") -> dict[str, Any]:
        """Observe a ledger event and update world understanding."""
        payload = dict(event.payload) if hasattr(event, "payload") else {}
        source_kind = f"ledger:{event.event_type}" if hasattr(event, "event_type") else "ledger:unknown"
        source_ref = getattr(event, "event_id", "unknown")
        return self.observe(source_kind, source_ref, payload, provenance={"event_type": getattr(event, "event_type", ""), "event_id": source_ref}, source_id=source_id)

    # ---- detection ----

    def _detect_from_observation(self, observation_id: str, payload: dict[str, Any], source_kind: str) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []
        text = json.dumps(payload, sort_keys=True).lower()
        # Preserve explicit cross-system identity on every derived signal so
        # the Executive can wake only the objectives named by the observation.
        # World Intelligence still owns the signal; this is provenance, not a
        # scheduling decision.
        affected_entities = [
            str(payload[key])
            for key in (
                "objective_id", "mission_id", "project_id", "provider_id",
                "capability_id", "resource_id",
            )
            if payload.get(key)
        ]

        # Provider/capability degradation
        if any(w in text for w in ("degraded", "unavailable", "rate_limited", "quota", "timeout", "outage")):
            if "provider" in text or "capability" in text or "connector" in text or "provider_id" in text or "health" in payload:
                r = self.signals.record(
                    "PROVIDER_DEGRADATION", f"Provider/capability degradation detected: {source_kind}",
                    summary=f"Observation {observation_id} indicates degraded provider or capability",
                    affected_entities=affected_entities, evidence=[observation_id],
                    provenance={"observation_id": observation_id, "source_kind": source_kind},
                    confidence=0.7, importance=0.7, urgency=0.6,
                )
                if not r.get("deduplicated") and not r.get("ignored"):
                    full = self.signals.get(r["signal_id"])
                    signals.append(full if full else r)

        # Blocker detection
        if any(w in text for w in ("blocked", "vetoed", "stopped", "paused")):
            r = self.signals.record(
                "BLOCKER", f"Blocker detected: {source_kind}",
                summary=f"Observation {observation_id} indicates a blocker",
                affected_entities=affected_entities, evidence=[observation_id],
                provenance={"observation_id": observation_id},
                confidence=0.7, importance=0.8, urgency=0.7,
            )
            if not r.get("deduplicated") and not r.get("ignored"):
                full = self.signals.get(r["signal_id"])
                signals.append(full if full else r)

        # Stale/failure
        if any(w in text for w in ("failed", "failure", "error", "rejected", "rolled_back")):
            # Check for recurring pattern
            desc = f"failure in {source_kind}"
            pat = self.patterns.record("FAILURE", desc, evidence=[observation_id], confidence=0.4)
            if not pat["is_new"] and pat["occurrences"] >= 3:
                r = self.signals.record(
                    "RECURRING_PATTERN", f"Recurring failure pattern: {source_kind} ({pat['occurrences']} occurrences)",
                    summary=f"Same failure class has occurred {pat['occurrences']} times",
                    affected_entities=affected_entities, evidence=[observation_id],
                    provenance={"pattern_id": pat["pattern_id"], "observation_id": observation_id},
                    confidence=0.6, importance=0.6, urgency=0.5,
                )
                if not r.get("deduplicated") and not r.get("ignored"):
                    full = self.signals.get(r["signal_id"])
                    signals.append(full if full else r)

        # Follow-up / pending
        if any(w in text for w in ("pending", "waiting", "incomplete", "follow_up", "follow-up")):
            r = self.signals.record(
                "FOLLOW_UP", f"Follow-up needed: {source_kind}",
                summary=f"Observation {observation_id} indicates pending follow-up",
                affected_entities=affected_entities, evidence=[observation_id],
                provenance={"observation_id": observation_id},
                confidence=0.5, importance=0.5, urgency=0.4,
            )
            if not r.get("deduplicated") and not r.get("ignored"):
                full = self.signals.get(r["signal_id"])
                signals.append(full if full else r)

        # Deadline proximity
        for key in ("deadline", "due_at", "expires_at"):
            if payload.get(key):
                dt = _parse_time(str(payload[key]))
                if dt:
                    now_dt = datetime.now(timezone.utc)
                    delta = (dt - now_dt).total_seconds()
                    if 0 < delta < 86400 * 3:  # within 3 days
                        r = self.signals.record(
                            "DEADLINE_APPROACHING", f"Deadline approaching: {payload.get(key)}",
                            summary=f"Deadline {payload[key]} is within 3 days",
                            affected_entities=affected_entities, evidence=[observation_id],
                            provenance={"observation_id": observation_id, "deadline": payload[key]},
                            confidence=0.8, importance=0.7, urgency=0.8, due_at=payload[key],
                        )
                        if not r.get("deduplicated") and not r.get("ignored"):
                            full = self.signals.get(r["signal_id"])
                            signals.append(full if full else r)
                    elif delta < 0:
                        r = self.signals.record(
                            "STALE_KNOWLEDGE", f"Overdue: {payload.get(key)}",
                            summary=f"Deadline {payload[key]} has passed",
                            affected_entities=affected_entities, evidence=[observation_id],
                            provenance={"observation_id": observation_id},
                            confidence=0.8, importance=0.7, urgency=0.8,
                        )
                        if not r.get("deduplicated") and not r.get("ignored"):
                            full = self.signals.get(r["signal_id"])
                            signals.append(full if full else r)

        return signals

    # ---- cross-system impact ----

    def assess_impact(self, entity_id: str, max_depth: int = 4) -> dict[str, Any]:
        """Assess cascading impact from an entity through relationships."""
        chain = self.relationships.impact_chain(entity_id, max_depth=max_depth)
        affected = [r["target_id"] for r in chain]
        return {"source": entity_id, "chain": chain, "affected": affected, "depth": max_depth}

    # ---- staleness sweep ----

    def sweep_staleness(self, now: str | None = None) -> dict[str, Any]:
        now = now or _now()
        stale_entities = self.entities.stale_entities(now=now)
        signals_created: list[dict[str, Any]] = []
        for entity in stale_entities:
            r = self.signals.record(
                "STALE_KNOWLEDGE", f"Stale knowledge: {entity['display_name']}",
                summary=f"Entity {entity['entity_id']} ({entity['entity_type']}) is stale since {entity.get('stale_after')}",
                affected_entities=[entity["entity_id"]], evidence=[entity["entity_id"]],
                provenance={"entity_id": entity["entity_id"], "stale_after": entity.get("stale_after")},
                confidence=0.6, importance=0.4, urgency=0.3,
            )
            if not r.get("deduplicated") and not r.get("ignored"):
                signals_created.append(r)
            # Mark entity as STALE
            try:
                self.entities.update_state(entity["entity_id"], state="STALE")
            except (ValueError, Exception):
                pass
        # Also sweep signal staleness
        stale_signals = self.signals.auto_resolve_stale(now=now)
        expired_predictions = self.predictions.check_expired(now=now)
        return {"stale_entities": len(stale_entities), "signals_created": len(signals_created), "stale_signals": stale_signals, "expired_predictions": expired_predictions}

    # ---- consolidation ----

    def consolidate_observations(self, source_kind: str, window_hours: int = 24) -> dict[str, Any]:
        """Consolidate duplicate observations within a time window."""
        # This is a lightweight dedup: count observations with same normalized summary
        obs = self.observations.list(source_kind=source_kind, limit=500)
        seen: dict[str, int] = {}
        for o in obs:
            summary = (o.get("normalized") or {}).get("summary", "")[:200]
            if summary:
                seen[summary] = seen.get(summary, 0) + 1
        duplicates = {k: v for k, v in seen.items() if v > 1}
        return {"total": len(obs), "unique_summaries": len(seen), "duplicates": duplicates}

    # ---- context synthesis ----

    def synthesize_context(self, entity_id: str, max_related: int = 10) -> dict[str, Any]:
        entity = self.entities.get(entity_id)
        if entity is None:
            return {"entity_id": entity_id, "found": False}
        relationships = self.relationships.for_entity(entity_id)
        related_entities: list[dict[str, Any]] = []
        for rel in relationships[:max_related]:
            other_id = rel["target_id"] if rel["source_id"] == entity_id else rel["source_id"]
            other = self.entities.get(other_id)
            if other:
                related_entities.append({"relationship": rel, "entity": other})
        signals = [s for s in self.signals.list(limit=200) if entity_id in (s.get("affected_entities") or [])]
        gaps = [g for g in self.gaps.list(limit=100) if g.get("subject_id") == entity_id]
        return {
            "entity": entity,
            "relationships": relationships,
            "related_entities": related_entities,
            "signals": signals[:10],
            "gaps": gaps,
            "found": True,
        }
