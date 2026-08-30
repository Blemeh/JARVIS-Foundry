"""Intelligence signals - proactive detection with lifecycle and salience."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json
from jarvis_foundry.world_model.constants import SIGNAL_KINDS, SIGNAL_STATES, SALIENCE_LEVELS


def _now() -> str:
    return isoformat()


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return None


def _dedupe_key(signal_kind: str, affected: list[str], title: str) -> str:
    payload = {"kind": signal_kind, "affected": sorted(affected), "title": title.strip().lower()[:120]}
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()[:16]


SALIENCE_ORDER = {level: idx for idx, level in enumerate(SALIENCE_LEVELS)}

# Practical salience rules
SALIENCE_RULES: dict[str, str] = {
    "BLOCKER": "OWNER_ATTENTION",
    "RISK": "MONITOR",
    "OPPORTUNITY": "RECOMMEND",
    "FOLLOW_UP": "FOLLOW_UP",
    "STALE_KNOWLEDGE": "REMEMBER",
    "CONTRADICTION": "OWNER_ATTENTION",
    "DEPENDENCY_CHANGE": "MONITOR",
    "PROVIDER_DEGRADATION": "URGENT",
    "RECURRING_PATTERN": "MONITOR",
    "DEADLINE_APPROACHING": "URGENT",
    "MISSING_NEXT_STEP": "RECOMMEND",
    "ABANDONED_WORK": "MONITOR",
    "UNRESOLVED_DECISION": "OWNER_ATTENTION",
    "STALLED_OBJECTIVE": "OWNER_ATTENTION",
    "PROJECT_DRIFT": "MONITOR",
    "OBJECTIVE_DRIFT": "MONITOR",
    "KNOWLEDGE_GAP": "REMEMBER",
}


def classify_salience(
    signal_kind: str,
    *,
    confidence: float = 0.5,
    importance: float = 0.5,
    urgency: float = 0.5,
    is_blocking: bool = False,
    is_recurring: bool = False,
) -> str:
    base = SALIENCE_RULES.get(signal_kind, "REMEMBER")
    score = (confidence * 0.3 + importance * 0.4 + urgency * 0.3)
    if is_blocking and SALIENCE_ORDER[base] < SALIENCE_ORDER["OWNER_ATTENTION"]:
        return "OWNER_ATTENTION"
    if urgency >= 0.8 and score >= 0.7:
        return "URGENT"
    if score < 0.2:
        return "IGNORE"
    if score < 0.35:
        return "REMEMBER"
    return base


ALLOWED_SIGNAL_TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"MONITORING", "ACKNOWLEDGED", "RESOLVED", "SUPERSEDED", "STALE"},
    "MONITORING": {"ACKNOWLEDGED", "RESOLVED", "SUPERSEDED", "STALE", "OPEN"},
    "ACKNOWLEDGED": {"RESOLVED", "SUPERSEDED", "STALE", "MONITORING"},
    "RESOLVED": set(),
    "SUPERSEDED": set(),
    "STALE": {"OPEN", "RESOLVED"},
}


class WorldSignalStore:
    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    def record(
        self,
        signal_kind: str,
        title: str,
        *,
        signal_id: str | None = None,
        summary: str = "",
        affected_entities: list[str] | None = None,
        evidence: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
        confidence: float = 0.5,
        importance: float = 0.5,
        urgency: float = 0.5,
        salience: str | None = None,
        state: str = "OPEN",
        stale_after: str | None = None,
        due_at: str | None = None,
        recommended_action: str | None = None,
        source_id: str = "world-model",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if signal_kind not in SIGNAL_KINDS:
            raise ValueError(f"invalid signal_kind {signal_kind!r}")
        if not title or not title.strip():
            raise ValueError("title is required")
        if state not in SIGNAL_STATES:
            raise ValueError(f"invalid state {state!r}")
        affected = affected_entities or []
        dedupe = _dedupe_key(signal_kind, affected, title)
        # Dedupe: one durable signal per underlying issue
        existing = self._find_by_dedupe(dedupe)
        if existing and existing["state"] in ("OPEN", "MONITORING", "ACKNOWLEDGED"):
            return {"signal_id": existing["signal_id"], "deduplicated": True, "existing": existing}
        if salience is None:
            salience = classify_salience(signal_kind, confidence=confidence, importance=importance, urgency=urgency)
        if salience not in SALIENCE_LEVELS:
            raise ValueError(f"invalid salience {salience!r}")
        # IGNORE signals are not persisted
        if salience == "IGNORE":
            return {"signal_id": None, "ignored": True, "reason": "salience IGNORE"}
        sid = signal_id or f"ws-{uuid.uuid4().hex[:12]}"
        payload: dict[str, Any] = {
            "signal_id": sid,
            "signal_kind": signal_kind,
            "salience": salience,
            "state": state,
            "title": title.strip(),
            "summary": summary,
            "affected_entities": affected,
            "evidence": evidence or [],
            "provenance": provenance or {},
            "confidence": max(0.0, min(1.0, confidence)),
            "importance": max(0.0, min(1.0, importance)),
            "urgency": max(0.0, min(1.0, urgency)),
            "dedupe_key": dedupe,
            "stale_after": stale_after,
            "due_at": due_at,
            "recommended_action": recommended_action,
        }
        event = Event.create(
            "world.signal_recorded", payload,
            source_type="world_model", source_id=source_id,
            idempotency_key=idempotency_key or f"world.signal_recorded:{sid}",
        )
        self.db.append(event)
        return {"signal_id": sid, "deduplicated": False, "salience": salience}

    def transition(
        self,
        signal_id: str,
        target_state: str,
        *,
        resolved_at: str | None = None,
        supersedes: str | None = None,
        source_id: str = "world-model",
        idempotency_key: str | None = None,
    ) -> str:
        if target_state not in SIGNAL_STATES:
            raise ValueError(f"invalid target_state {target_state!r}")
        current = self.get(signal_id)
        if current is None:
            raise ValueError(f"unknown signal {signal_id}")
        if target_state not in ALLOWED_SIGNAL_TRANSITIONS.get(current["state"], set()):
            raise ValueError(f"illegal transition {current['state']} -> {target_state}")
        payload: dict[str, Any] = {"signal_id": signal_id, "state": target_state}
        if resolved_at:
            payload["resolved_at"] = resolved_at
        if supersedes:
            payload["supersedes"] = supersedes
        event = Event.create(
            "world.signal_state_changed", payload,
            source_type="world_model", source_id=source_id,
            idempotency_key=idempotency_key or f"world.signal_state_changed:{signal_id}:{target_state}:{_now()}",
        )
        self.db.append(event)
        return signal_id

    def resolve(self, signal_id: str, *, source_id: str = "world-model") -> str:
        return self.transition(signal_id, "RESOLVED", resolved_at=_now(), source_id=source_id)

    def acknowledge(self, signal_id: str, *, source_id: str = "world-model") -> str:
        return self.transition(signal_id, "ACKNOWLEDGED", source_id=source_id)

    def supersede(self, old_id: str, new_id: str, *, source_id: str = "world-model") -> str:
        return self.transition(old_id, "SUPERSEDED", supersedes=new_id, source_id=source_id)

    def get(self, signal_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM world_signals WHERE signal_id = ?", (signal_id,)).fetchone()
            if row is None:
                return None
            return _row_to_signal(dict(row))

    def _find_by_dedupe(self, dedupe_key: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_signals WHERE dedupe_key = ? ORDER BY created_at DESC LIMIT 1", (dedupe_key,)
            ).fetchone()
            if row is None:
                return None
            return _row_to_signal(dict(row))

    def list(
        self,
        signal_kind: str | None = None,
        state: str | None = None,
        salience: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if signal_kind:
            clauses.append("signal_kind = ?")
            args.append(signal_kind)
        if state:
            clauses.append("state = ?")
            args.append(state)
        if salience:
            clauses.append("salience = ?")
            args.append(salience)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM world_signals{where} ORDER BY updated_at DESC LIMIT ?", tuple(args) + (max(1, min(limit, 500)),)
            ).fetchall()
        return [_row_to_signal(dict(r)) for r in rows]

    def active(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_signals WHERE state IN ('OPEN', 'MONITORING', 'ACKNOWLEDGED') ORDER BY urgency DESC, importance DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [_row_to_signal(dict(r)) for r in rows]

    def auto_resolve_stale(self, now: str | None = None, source_id: str = "world-model") -> int:
        """Auto-resolve signals whose underlying condition is stale."""
        now = now or _now()
        resolved = 0
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT signal_id FROM world_signals WHERE state IN ('OPEN', 'MONITORING') AND stale_after IS NOT NULL AND stale_after <= ?", (now,)
            ).fetchall()
        for row in rows:
            try:
                self.transition(row[0], "STALE", source_id=source_id)
                resolved += 1
            except (ValueError, Exception):
                pass
        return resolved

    def check_due(self, now: str | None = None) -> list[dict[str, Any]]:
        """Signals whose due_at has passed and are still open."""
        now = now or _now()
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_signals WHERE state IN ('OPEN', 'MONITORING', 'ACKNOWLEDGED') AND due_at IS NOT NULL AND due_at <= ? ORDER BY due_at", (now,)
            ).fetchall()
        return [_row_to_signal(dict(r)) for r in rows]


def _row_to_signal(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("affected_entities_json", "evidence_json", "provenance_json"):
        raw = row.pop(key, None)
        target = key.replace("_json", "")
        if key == "affected_entities_json":
            target = "affected_entities"
        try:
            row[target] = json.loads(raw) if raw else ([] if target != "provenance" else {})
        except (TypeError, ValueError):
            row[target] = [] if target != "provenance" else {}
    return row