"""Durable, local-only briefing planner and idempotent outbox."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .clock import isoformat
from .command_center import build_snapshot
from .database import FoundryDatabase
from .events import Event


def _period_key(now: datetime) -> str:
    half = "AM" if now.hour < 12 else "PM"
    return f"{now.date().isoformat()}:{half}"


class BriefingPlanner:
    def __init__(self, database: FoundryDatabase, *, stop_path: str | None = None) -> None:
        self.database = database
        self.stop_path = stop_path

    def _payload(self, kind: str, period_key: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        costs = snapshot["costs"]
        return {
            "kind": kind,
            "period_key": period_key,
            "generated_at": snapshot["generated_at"],
            "what_is_doing": [{"mission_id": m.get("mission_id"), "state": m.get("state"), "objective": m.get("objective"), "evidence": m.get("record_ref")} for m in snapshot["missions"][:20]],
            "decisions": snapshot["timeline"][-20:],
            "failures_repairs": snapshot["recovery"]["recovery_events"][-20:],
            "costs": {"actual_usd": costs["measured_actual_total_usd"], "estimated_usd": costs["measured_estimated_total_usd"], "unknown_measurements": costs["unknown_measurements"]},
            "knowledge_changes": [e for e in snapshot["timeline"] if e["event_type"].startswith("knowledge.")][-20:],
            "next_actions": [{"mission_id": m.get("mission_id"), "next_action": m.get("next_action"), "evidence": m.get("record_ref")} for m in snapshot["missions"][:20]],
            "unknowns_and_blockers": snapshot["recovery"]["provider_or_connector_unknowns"] + snapshot["recovery"]["blocked_missions"],
        }

    def plan(self, kind: str, *, period_key: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        if kind not in {"TWICE_DAILY", "EXCEPTION", "MISSION_SUMMARY"}:
            raise ValueError("unsupported briefing kind")
        clock = now or datetime.now(timezone.utc)
        period = period_key or (_period_key(clock) if kind == "TWICE_DAILY" else clock.isoformat())
        key = f"{kind}:{period}"
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT briefing_id, status, payload_json FROM briefing_outbox WHERE briefing_key = ?",
                (key,),
            ).fetchone()
        if existing is not None:
            return {
                "briefing_id": existing[0], "briefing_key": key,
                "sequence": None, "created": False,
                "status": existing[1], "payload": json.loads(existing[2]),
            }
        snapshot = build_snapshot(self.database, stop_path=self.stop_path, now=clock.isoformat())
        payload = self._payload(kind, period, snapshot)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        event = Event.create(
            "briefing.planned",
            {"briefing_id": f"briefing-{digest[:24]}", "briefing_key": key, "kind": kind,
             "period_key": period, "idempotency_key": f"briefing:{key}",
             "briefing": payload, "evidence": {"ledger_head": snapshot["source_of_truth"]["ledger_head_sequence"]}},
            source_type="supervisor", source_id="briefing-planner",
            idempotency_key=f"briefing.planned:{key}",
        )
        sequence, created = self.database.append(event)
        return {"briefing_id": event.payload["briefing_id"], "briefing_key": key, "sequence": sequence, "created": created, "payload": payload}

    def claim(self, briefing_id: str) -> bool:
        event = Event.create("briefing.claimed", {"briefing_id": briefing_id}, source_type="supervisor", source_id="briefing-outbox", idempotency_key=f"briefing.claimed:{briefing_id}")
        return self.database.append(event)[1]

    def deliver_local(self, briefing_id: str) -> bool:
        event = Event.create("briefing.delivered", {"briefing_id": briefing_id, "sink": "local-test-sink"}, source_type="supervisor", source_id="briefing-outbox", idempotency_key=f"briefing.delivered:{briefing_id}")
        return self.database.append(event)[1]

    def fail(self, briefing_id: str, reason: str, *, blocked: bool = False) -> bool:
        status = "BLOCKED" if blocked else "FAILED"
        event = Event.create("briefing.failed", {"briefing_id": briefing_id, "status": status, "reason": reason}, source_type="supervisor", source_id="briefing-outbox", idempotency_key=f"briefing.failed:{briefing_id}:{status}:{hashlib.sha256(reason.encode()).hexdigest()[:12]}")
        return self.database.append(event)[1]
