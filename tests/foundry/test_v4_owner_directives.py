"""V4-1 owner-directive truth projection and admission invariants."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import (
    OwnerCommandError,
    OwnerControlService,
    sign_command,
)
from jarvis_foundry.owner_directives import project_owner_directives


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _apply(
    db: FoundryDatabase,
    command_type: str,
    *,
    target: str | None = "GLOBAL",
    text: str = "owner intent",
    nonce: str = "nonce-1",
    directive_id: str | None = None,
    supersedes: str | None = None,
    expires_at: str | None = None,
):
    secret = b"v4-owner-secret"
    signature = sign_command(secret, command_type, target, text, nonce)
    return OwnerControlService(db, secret=secret).apply(
        command_type,
        target=target,
        exact_text=text,
        nonce=nonce,
        signature=signature,
        directive_id=directive_id,
        supersedes_directive_id=supersedes,
        expires_at=expires_at,
    )


def test_authenticated_directive_has_reconstructible_provenance(db: FoundryDatabase) -> None:
    result = _apply(db, "GOAL", text="priority=2 build the evidence path", nonce="goal-1", directive_id="goal-1")

    records = project_owner_directives(db)

    assert records[0].directive_id == result.directive_id
    assert records[0].state == "ACTIVE"
    assert records[0].authenticated is True
    assert records[0].source_type == "owner"
    assert records[0].source_id == "local-owner"
    assert records[0].nonce == "goal-1"
    assert records[0].command_hash
    assert records[0].correlation_id
    assert records[0].causation_id
    assert records[0].target_status == "KNOWN"
    assert records[0].unknown_fields == ()


def test_duplicate_directive_id_is_rejected_without_a_second_admission(db: FoundryDatabase) -> None:
    _apply(db, "GOAL", text="same directive", nonce="goal-1", directive_id="goal-1")
    before = len(db.events())

    with pytest.raises(OwnerCommandError, match="already admitted"):
        _apply(db, "GOAL", text="same directive", nonce="goal-2", directive_id="goal-1")

    assert len(db.events()) == before
    assert [r.directive_id for r in project_owner_directives(db)] == ["goal-1"]


def test_supersession_is_durable_and_disables_legacy_enforcement_projection(db: FoundryDatabase) -> None:
    _apply(db, "VETO_ACTION", target="GLOBAL", text="hold old path", nonce="veto-1", directive_id="veto-1")
    _apply(
        db,
        "VETO_ACTION",
        target="GLOBAL",
        text="replace old path",
        nonce="veto-2",
        directive_id="veto-2",
        supersedes="veto-1",
    )

    records = {record.directive_id: record for record in project_owner_directives(db)}
    assert records["veto-1"].state == "SUPERSEDED"
    assert records["veto-1"].superseded_by == "veto-2"
    assert records["veto-2"].state == "ACTIVE"
    assert db.row("directives", "directive_id", "veto-1")["active"] == 0
    assert db.row("directives", "directive_id", "veto-2")["active"] == 1


def test_revocation_and_projection_rebuild_preserve_lifecycle(db: FoundryDatabase) -> None:
    service_result = _apply(db, "CONSTRAINT", text="no external effects", nonce="constraint-1", directive_id="constraint-1")
    secret = b"v4-owner-secret"
    nonce = "revoke-1"
    text = "owner withdrew constraint"
    service = OwnerControlService(db, secret=secret)
    service.revoke(
        service_result.directive_id,
        nonce=nonce,
        exact_text=text,
        signature=sign_command(secret, "RESUME", service_result.directive_id, text, nonce),
    )

    assert project_owner_directives(db)[0].state == "REVOKED"
    db.rebuild_projections()
    assert project_owner_directives(db)[0].state == "REVOKED"


def test_stale_and_unknown_target_truth_is_explicit(db: FoundryDatabase) -> None:
    _apply(
        db,
        "GOAL",
        target="missing-project",
        text="expires soon",
        nonce="goal-stale",
        directive_id="goal-stale",
        expires_at="2020-01-01T00:00:00Z",
    )

    record = project_owner_directives(db, now="2026-08-23T00:00:00Z")[0]
    assert record.state == "STALE"
    assert record.active is False
    assert record.target_status == "UNKNOWN"


def test_malformed_optional_admission_is_visible_as_unknown_not_authority(db: FoundryDatabase) -> None:
    db.append(Event.create(
        "owner_directive.recorded",
        {
            "directive_id": "malformed-1",
            "command_type": "GOAL",
            "target": {"not": "a string"},
            "exact_text": "owner goal without nonce proof",
            "authenticated": True,
        },
        source_type="owner",
        source_id="local-owner",
        idempotency_key="owner_directive.recorded:malformed-1",
    ))

    record = project_owner_directives(db)[0]
    assert record.state == "UNKNOWN"
    assert record.active is False
    assert {"target", "nonce", "nonce_event", "command_hash"}.issubset(record.unknown_fields)

