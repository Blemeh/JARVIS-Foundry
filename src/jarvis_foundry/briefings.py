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
from .briefing_items import (
    DEFAULT_ITEM_LIMIT,
    briefing_item_keys,
    exception_item,
    project_briefing_items,
)


def _period_key(now: datetime) -> str:
    half = "AM" if now.hour < 12 else "PM"
    return f"{now.date().isoformat()}:{half}"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


class BriefingPlanner:
    def __init__(self, database: FoundryDatabase, *, stop_path: str | None = None) -> None:
        self.database = database
        self.stop_path = stop_path

    def _payload(
        self,
        kind: str,
        period_key: str,
        snapshot: dict[str, Any],
        selection: dict[str, Any],
    ) -> dict[str, Any]:
        costs = snapshot["costs"]
        payload: dict[str, Any] = {
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
            "effect_truth": dict(snapshot.get("effect_truth") or {}),
            "evolution_truth": dict(snapshot.get("evolution_truth") or {}),
            "items": list(selection.get("items") or []),
            "selection": dict(selection),
            "decision_required_items": [
                item for item in selection.get("items") or []
                if item.get("owner_action_required") != "INFORM"
            ],
        }
        ledger_items = project_briefing_items(
            self.database,
            after_sequence=0,
            now=snapshot["generated_at"],
            limit=max(DEFAULT_ITEM_LIMIT, self.database.event_count(readonly=True)),
            max_scan=10000,
        )
        digest_input = {
            "kind": kind,
            "ledger_items": [
                {"dedupe_key": item.dedupe_key, "source_sequence": item.source_sequence}
                for item in ledger_items
            ],
            "missions": snapshot["missions"],
            "controls": snapshot["controls"],
            "health": snapshot["health"],
            "costs": snapshot["costs"],
        }
        payload["content_digest"] = hashlib.sha256(
            json.dumps(digest_input, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return payload

    def _prior_selections(self) -> tuple[list[dict[str, Any]], set[str]]:
        rows: list[dict[str, Any]] = []
        keys: set[str] = set()
        with self.database.connect(readonly=True) as connection:
            stored = connection.execute(
                "SELECT status, payload_json FROM briefing_outbox ORDER BY rowid"
            ).fetchall()
        for row in stored:
            try:
                payload = json.loads(row[1] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            selection = payload.get("selection")
            if isinstance(selection, dict):
                rows.append(selection)
            items = payload.get("items")
            if isinstance(items, list):
                keys.update(briefing_item_keys(items))
        return rows, keys

    def _select_items(
        self,
        kind: str,
        period_key: str,
        *,
        now: datetime,
        limit: int = DEFAULT_ITEM_LIMIT,
    ) -> dict[str, Any]:
        prior, prior_keys = self._prior_selections()
        kind_cursors = [
            _safe_int(selection.get("cursor_end"), 0)
            for selection in prior
            if isinstance(selection, dict) and selection.get("kind") == kind
        ]
        cursor_start = max(kind_cursors, default=0)
        projected = project_briefing_items(
            self.database,
            after_sequence=cursor_start,
            now=now,
            limit=max(limit * 4, limit),
            max_scan=10000,
        )
        filtered = projected
        if kind == "EXCEPTION":
            filtered = [item for item in projected if exception_item(item)]
        deduped_count = sum(1 for item in filtered if item.dedupe_key in prior_keys)
        candidates = [item for item in filtered if item.dedupe_key not in prior_keys]
        selected = candidates[: max(1, _safe_int(limit, DEFAULT_ITEM_LIMIT))]
        head = self.database.event_count(readonly=True)
        if selected:
            # The lowest selected sequence is the safe checkpoint.  Priority
            # sorting may put a newer item before older unselected items; a
            # higher cursor would skip those older items permanently.
            cursor_end = min(item.source_sequence for item in selected)
        elif kind == "EXCEPTION":
            cursor_end = cursor_start
        else:
            cursor_end = head
        return {
            "kind": kind,
            "period_key": period_key,
            "cursor_start": cursor_start,
            "cursor_end": cursor_end,
            "ledger_head": head,
            "selected_count": len(selected),
            "deduped_count": deduped_count,
            "late_event_count": sum(item.freshness == "LATE" for item in selected),
            "future_event_count": sum(item.freshness == "FUTURE" for item in selected),
            "zero_change": not selected,
            "items": [item.to_dict() for item in selected],
        }

    def _latest_content_digest(self, kind: str) -> str | None:
        with self.database.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT payload_json FROM briefing_outbox WHERE kind = "
                "? ORDER BY rowid DESC LIMIT 1",
                (kind,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            return None
        value = payload.get("content_digest") if isinstance(payload, dict) else None
        return str(value) if value else None

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
                "status": existing[1], "payload": self._safe_existing_payload(existing[2]),
            }
        snapshot = build_snapshot(self.database, stop_path=self.stop_path, now=clock.isoformat())
        selection = self._select_items(kind, period, now=clock)
        payload = self._payload(kind, period, snapshot, selection)
        if kind == "TWICE_DAILY" and selection["zero_change"]:
            prior_digest = self._latest_content_digest(kind)
            if prior_digest and prior_digest == payload["content_digest"]:
                return {
                    "briefing_id": None,
                    "briefing_key": key,
                    "sequence": None,
                    "created": False,
                    "suppressed": True,
                    "reason": "ZERO_CHANGE",
                    "status": "SUPPRESSED",
                    "payload": payload,
                }
        if kind == "EXCEPTION" and selection["zero_change"]:
            return {
                "briefing_id": None,
                "briefing_key": key,
                "sequence": None,
                "created": False,
                "suppressed": True,
                "reason": "NO_NEW_EXCEPTION",
                "status": "SUPPRESSED",
                "payload": payload,
            }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        event = Event.create(
            "briefing.planned",
            {"briefing_id": f"briefing-{digest[:24]}", "briefing_key": key, "kind": kind,
             "period_key": period, "idempotency_key": f"briefing:{key}",
             "briefing": payload, "evidence": {
                 "ledger_head": snapshot["source_of_truth"]["ledger_head_sequence"],
                 "selection": selection,
             }},
            source_type="supervisor", source_id="briefing-planner",
            idempotency_key=f"briefing.planned:{key}",
        )
        sequence, created = self.database.append(event)
        return {"briefing_id": event.payload["briefing_id"], "briefing_key": key, "sequence": sequence, "created": created, "payload": payload}

    @staticmethod
    def _safe_existing_payload(raw: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {"status": "UNKNOWN", "reason": "stored briefing payload is malformed"}
        return dict(parsed) if isinstance(parsed, dict) else {
            "status": "UNKNOWN", "reason": "stored briefing payload is not an object"
        }

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
