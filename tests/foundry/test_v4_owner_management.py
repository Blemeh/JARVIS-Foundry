"""V4-2 owner management semantics at executive and mission boundaries."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import generate_candidate, score_candidate
from jarvis_foundry.events import Event
from jarvis_foundry.executive.eligibility import (
    assess_eligibility,
    enable_admission_authority,
)
from jarvis_foundry.executive.scoring import rank_candidates
from jarvis_foundry.mission.proposal import propose_next_action
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.owner_directives import constraint_context

from worker_test_helpers import mission_database


SECRET = b"v4-owner-management-secret"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _apply(
    database: FoundryDatabase,
    command_type: str,
    *,
    target: str | None = "GLOBAL",
    text: str = "owner intent",
    nonce: str,
    directive_id: str,
    metadata: dict | None = None,
):
    signature = sign_command(
        SECRET,
        command_type,
        target,
        text,
        nonce,
        metadata=metadata,
    )
    return OwnerControlService(
        database, secret=SECRET, owner_id="owner-management-test"
    ).apply(
        command_type,
        target=target,
        exact_text=text,
        nonce=nonce,
        signature=signature,
        directive_id=directive_id,
        metadata=metadata,
    )


def test_authenticated_reprioritization_changes_candidate_ranking(db: FoundryDatabase) -> None:
    first = generate_candidate(
        db,
        "mission-exec",
        "maintenance",
        "Document gateway recovery alpha",
    )["candidate_id"]
    second = generate_candidate(
        db,
        "mission-exec",
        "maintenance",
        "Document gateway recovery beta",
    )["candidate_id"]
    score_candidate(db, first)
    score_candidate(db, second)
    rows = [
        db.row("candidates", "candidate_id", first),
        db.row("candidates", "candidate_id", second),
    ]

    _apply(
        db,
        "REPRIORITIZE",
        target=second,
        text="priority=10 raise beta above the current queue",
        nonce="reprioritize-beta",
        directive_id="reprioritize-beta",
    )

    ranked = rank_candidates(db, rows)
    selected = next(score for score in ranked if score.candidate_id == second)
    assert ranked[0].candidate_id == second
    assert selected.owner_directive_priority == 1.0
    assert selected.owner_priority_directive_ids == ("reprioritize-beta",)
    assert any(f.name == "owner_directive_priority" for f in selected.factors)


def test_constraint_projection_is_explicit_about_unknown_and_conflicting_text(db: FoundryDatabase) -> None:
    _apply(
        db,
        "CONSTRAINT",
        text="Do not deploy after business hours",
        nonce="constraint-unknown",
        directive_id="constraint-unknown",
    )
    _apply(
        db,
        "CONSTRAINT",
        text="external effects are denied",
        nonce="constraint-deny",
        directive_id="constraint-deny",
        metadata={"constraint_key": "external_effects", "mode": "DENY"},
    )
    _apply(
        db,
        "ADD_CONSTRAINT",
        text="external effects are allowed",
        nonce="constraint-allow",
        directive_id="constraint-allow",
        metadata={"constraint_key": "external_effects", "mode": "ALLOW"},
    )

    context = constraint_context(db, targets={"GLOBAL"})

    assert context["status"] == "CONFLICTING"
    assert context["unknown"][0]["directive_id"] == "constraint-unknown"
    assert context["conflicts"][0]["constraint_key"] == "external_effects"
    assert set(context["conflicts"][0]["directive_ids"]) == {
        "constraint-deny",
        "constraint-allow",
    }
    assert context["conflicts"][0]["status"] == "CONFLICTING"


def test_mission_proposal_carries_durable_constraints_verbatim(tmp_path: Path) -> None:
    database, _ = mission_database(tmp_path)
    _apply(
        database,
        "CONSTRAINT",
        target="mission-1",
        text="Keep the report under 1200 bytes",
        nonce="constraint-mission",
        directive_id="constraint-mission",
    )

    proposal = propose_next_action(database, "mission-1")

    assert proposal.owner_constraint_status == "UNKNOWN"
    assert proposal.owner_constraints[0]["exact_text"] == "Keep the report under 1200 bytes"


def test_resource_veto_blocks_candidate_before_action_selection(tmp_path: Path) -> None:
    database, _ = mission_database(tmp_path)
    epoch = "owner-management-epoch"
    database.append(Event.create(
        "executive.activation_recorded",
        {"epoch": epoch, "decision_version": "1.0.0"},
        source_type="supervisor",
        source_id="owner-management-test",
        idempotency_key=f"executive.activation_recorded:{epoch}",
    ))
    enable_admission_authority(database, epoch, source_id="owner-management-test")
    candidate_id = generate_candidate(
        database,
        "mission-1",
        "maintenance",
        "Execute the registered gateway recovery report",
    )["candidate_id"]
    score_candidate(database, candidate_id)
    _apply(
        database,
        "VETO_ACTION",
        target="resource-1",
        text="hold all actions on this resource",
        nonce="veto-resource",
        directive_id="veto-resource",
    )

    result = assess_eligibility(
        database,
        database.row("candidates", "candidate_id", candidate_id),
        activation_epoch=epoch,
        resource_id="resource-1",
        capability="git_local_commit",
    )

    assert result.eligible is False
    assert result.reason == "veto_active"
