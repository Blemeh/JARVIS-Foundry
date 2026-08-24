"""Command Center V7 truth projection: portfolio + incidents from ledger."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.command_center import _portfolio_projection
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def _database(tmp_path: Path) -> FoundryDatabase:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    return db


def test_portfolio_projection_reflects_ledger(tmp_path: Path) -> None:
    db = _database(tmp_path)
    db.append(Event.create(
        "portfolio.selection_recorded",
        {"decision_id": "d1", "decision": "WAIT", "selected_class": "WAIT",
         "entity_id": None, "reason": "no positive-value work",
         "why_now": "waiting preserves capacity",
         "wake_on": ["time", "owner_directive"],
         "revisit_at": "2026-08-24T23:00:00Z", "alternatives": []},
        source_type="supervisor", source_id="cc-test",
        idempotency_key="portfolio.selection_recorded:d1"))
    db.append(Event.create(
        "incident.detected",
        {"incident_id": "inc-1", "kind": "connector_outage",
         "severity": "S3", "correlation_key": "connector:all"},
        source_type="supervisor", source_id="cc-test",
        idempotency_key="incident.detected:inc-1"))
    db.append(Event.create(
        "incident.transitioned",
        {"incident_id": "inc-1", "status": "BLOCKED", "reason": "honest"},
        source_type="supervisor", source_id="cc-test",
        idempotency_key="incident.transitioned:inc-1:blocked"))

    projection = _portfolio_projection(db)
    latest = projection["latest_decision"]
    assert latest["decision"] == "WAIT"
    assert "time" in latest["wake_on"]
    assert projection["incidents"][0]["kind"] == "connector_outage"
    # terminal incident statuses must be reflected, not stale DETECTED
    assert projection["incidents"][0]["status"] == "BLOCKED"
    assert projection["open_incident_count"] == 0
