"""V5-14 durable connector attempt evidence."""

from __future__ import annotations

import json
from pathlib import Path

from jarvis_foundry.connectors.contract import ConnectorAttempt
from jarvis_foundry.connectors.evidence import attempts_for_mission, record_attempt
from jarvis_foundry.database import FoundryDatabase


def test_attempt_evidence_is_safe_append_only_and_restart_readable(tmp_path: Path) -> None:
    db_path = tmp_path / "foundry.db"
    database = FoundryDatabase(db_path)
    database.initialize()
    attempt = ConnectorAttempt(
        attempt_id="attempt-1", connector_id="conn-http-v5",
        action_kind="http.write", target_ref="/write",
        idempotency_key="request-1", payload_hash="a" * 64,
        outcome="COMPLETED", before_state={"hash": "before"},
        after_state={"hash": "after"}, provider_ref="provider:1",
        evidence_refs=("oracle:1",), reconciled=True,
    )
    first = record_attempt(
        database, attempt, mission_id="mission-1", resource_id="resource-http",
        effect_ticket_id="effect-1",
    )
    duplicate = record_attempt(
        database, attempt, mission_id="mission-1", resource_id="resource-http",
        effect_ticket_id="effect-1",
    )
    assert first == duplicate
    with database.connect() as connection:
        payload = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = 'connector.attempt_recorded'"
        ).fetchone()[0]
        assert '"payload":' not in payload
        assert json.loads(payload)["mission_id"] == "mission-1"
    reopened = FoundryDatabase(db_path)
    reopened.initialize()
    rows = attempts_for_mission(reopened, "mission-1")
    assert len(rows) == 1
    assert rows[0]["provider_ref"] == "provider:1"
    assert rows[0]["reconciled"] is True
