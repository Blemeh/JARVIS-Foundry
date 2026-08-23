"""V4-6 owner attention queue proof."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.decision_queue import build_owner_attention_queue
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import OwnerControlService, sign_command


SECRET = b"v4-decision-queue-secret"


def _apply(database: FoundryDatabase, command: str, target: str, text: str, nonce: str, directive_id: str):
    signature = sign_command(SECRET, command, target, text, nonce)
    return OwnerControlService(database, secret=SECRET, owner_id="queue-test").apply(
        command,
        target=target,
        exact_text=text,
        nonce=nonce,
        signature=signature,
        directive_id=directive_id,
    )


def test_attention_queue_classifies_evidence_and_resolves_owner_veto(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    veto = _apply(
        database,
        "VETO_ACTION",
        "mission-queue",
        "hold the queue mission",
        "queue-veto-1",
        "queue-veto-1",
    )
    database.append(Event.create(
        "executive.materialization_blocked",
        {
            "decision_id": "queue-decision",
            "candidate_id": "queue-candidate",
            "reason": "provider dependency unavailable",
            "options": ["wait for provider", {"action": "recheck"}],
        },
        source_type="supervisor",
        source_id="queue-test",
        idempotency_key="executive.materialization_blocked:queue-decision",
    ))
    database.append(Event.create(
        "owner_directive.recorded",
        {
            "directive_id": "queue-unknown",
            "command_type": "GOAL",
            "target": "GLOBAL",
            "exact_text": "malformed owner intent",
            "authenticated": True,
        },
        source_type="owner",
        source_id="queue-test",
        idempotency_key="owner_directive.recorded:queue-unknown",
    ))

    queue = build_owner_attention_queue(database)
    classifications = {record["classification"] for record in queue["open"]}
    assert "OWNER_VETO_ACTIVE" in classifications
    assert "BLOCKED_EXTERNAL_DEPENDENCY" in classifications
    assert "UNKNOWN" in classifications
    blocked = next(
        record for record in queue["open"]
        if record["classification"] == "BLOCKED_EXTERNAL_DEPENDENCY"
    )
    assert blocked["options"] == ["wait for provider", {"action": "recheck"}]
    assert blocked["evidence_refs"]
    assert blocked["consequence"]

    revoke_text = "revoke queue veto"
    revoke_nonce = "queue-resume-1"
    OwnerControlService(
        database, secret=SECRET, owner_id="queue-test"
    ).revoke(
        veto.directive_id,
        nonce=revoke_nonce,
        exact_text=revoke_text,
        signature=sign_command(
            SECRET, "RESUME", veto.directive_id, revoke_text, revoke_nonce
        ),
    )
    resolved = build_owner_attention_queue(database)
    veto_record = next(
        record for record in resolved["history"]
        if record["queue_id"] == "attention:owner_directive.recorded:"
        + veto.event_id
    )
    assert veto_record["status"] == "RESOLVED"
    assert all(
        record["classification"] != "OWNER_VETO_ACTIVE"
        for record in resolved["open"]
    )
