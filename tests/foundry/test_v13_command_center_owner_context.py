"""Command Center consumes Owner Context as an advisory read projection only."""
from __future__ import annotations

import hashlib
from pathlib import Path

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import OwnerControlService, sign_command


NOW = "2026-09-04T08:00:00Z"


def _apply(
    service: OwnerControlService,
    secret: bytes,
    command_type: str,
    text: str,
    *,
    target: str = "GLOBAL",
    nonce: str,
) -> None:
    service.apply(
        command_type,
        target=target,
        exact_text=text,
        nonce=nonce,
        signature=sign_command(secret, command_type, target, text, nonce),
    )


def test_snapshot_projects_bounded_advisory_owner_context(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    secret = b"command-center-owner-context"
    service = OwnerControlService(
        database,
        secret=secret,
        stop_path=tmp_path / "STOP",
        owner_id="owner-context-test",
    )
    _apply(
        service,
        secret,
        "GOAL",
        "Keep the live Command Center grounded in durable truth",
        nonce="context-goal",
    )
    _apply(
        service,
        secret,
        "REPRIORITIZE",
        "Prioritize reconnect correctness",
        target="jarvis",
        nonce="context-priority",
    )
    preference = "Use concise owner briefings for routine recovery"
    database.append(Event.create(
        "owner_context.item_recorded",
        {
            "context_id": "preference:concise-recovery",
            "kind": "PREFERENCE",
            "text": preference,
            "state": "ACTIVE",
            "scope": {"project_ids": ["jarvis"]},
            "confidence": 1.0,
            "observed_at": NOW,
            "effective_at": NOW,
            "provenance": {
                "source_type": "repository",
                "source_id": "owner-context-reference",
                "source_ref": "docs:owner-context-test",
                "content_hash": hashlib.sha256(
                    preference.encode("utf-8")
                ).hexdigest(),
                "captured_at": NOW,
                "evidence_refs": ["event:preference-source"],
                "authority": "REPOSITORY_AUTHORITY",
            },
        },
        source_type="owner_context",
        source_id="owner-context-test",
        idempotency_key="owner_context.item_recorded:preference:concise-recovery",
        occurred_at=NOW,
    ))

    projection = build_snapshot(database, stop_path=tmp_path / "STOP", now=NOW)[
        "owner_context"
    ]

    assert projection["contract"] == "jarvis.owner-context.v1"
    assert projection["authority"] == {
        "model": "ADVISORY_EVENT_DERIVED",
        "owner_controls": "AUTHORITATIVE_AND_HIGHER_PRECEDENCE",
        "execution": "NONE",
    }
    assert projection["history"] == []
    assert projection["bounds"]["history_included"] is False
    assert projection["sections"]["goals"][0]["text"].startswith("Keep the live")
    assert projection["sections"]["priorities"][0]["text"].startswith("Prioritize")
    assert projection["sections"]["preferences"][0]["provenance"]["source_ref"] == (
        "docs:owner-context-test"
    )
    assert projection["sections"]["standing_orders"] == []


def test_malformed_owner_context_event_fails_closed(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "owner_context.item_recorded",
        {
            "context_id": "",
            "kind": "PREFERENCE",
            "text": "unproven preference",
        },
        source_type="import",
        source_id="untrusted",
        idempotency_key="owner_context.item_recorded:malformed-command-center",
        occurred_at=NOW,
    ))

    projection = build_snapshot(database, now=NOW)["owner_context"]

    assert projection["unknown"]
    malformed = next(
        record for record in projection["unknown"]
        if record["kind"] == "PREFERENCE"
    )
    assert malformed["state"] == "UNKNOWN"
    assert malformed["current"] is False
    assert malformed["provenance"]["authority"] == "UNKNOWN"


def test_active_owner_context_is_not_hidden_by_newer_lifecycle_noise(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    text = "Keep the original active preference visible"
    database.append(Event.create(
        "owner_context.item_recorded",
        {
            "context_id": "preference:older-active",
            "kind": "PREFERENCE",
            "text": text,
            "state": "ACTIVE",
            "scope": {"global": True},
            "confidence": 1.0,
            "observed_at": NOW,
            "effective_at": NOW,
            "provenance": {
                "source_type": "owner",
                "source_id": "owner-context-test",
                "source_ref": "event:older-active",
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "captured_at": NOW,
                "evidence_refs": [],
                "authority": "AUTHENTICATED_OWNER",
                "owner_directive_id": "directive-older-active",
            },
        },
        source_type="owner_context",
        source_id="owner-context-test",
        idempotency_key="owner_context.item_recorded:preference:older-active",
        occurred_at=NOW,
    ))
    for index in range(550):
        database.append(Event.create(
            "owner_context.item_state_changed",
            {
                "context_id": f"missing:{index}",
                "from_state": "ACTIVE",
                "to_state": "RESOLVED",
                "reason": "bounded-noise-probe",
                "changed_at": NOW,
            },
            source_type="owner_context",
            source_id="owner-context-test",
            idempotency_key=f"owner_context.noise:{index}",
            occurred_at=NOW,
        ))

    projection = build_snapshot(database, now=NOW)["owner_context"]

    assert [
        record["context_id"]
        for record in projection["sections"]["preferences"]
    ] == ["preference:older-active"]
