from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from jarvis_foundry.control.action_envelope import evaluate_and_record
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.policy import ActionPolicy
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.domain import ActionDecision, ActionEnvelope
from jarvis_foundry.events import Event


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def registered_database(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    git("init", cwd=repository)
    git("config", "user.email", "jarvis-test@example.invalid", cwd=repository)
    git("config", "user.name", "JARVIS Test", cwd=repository)
    (repository / "README.md").write_text("test\n", encoding="utf-8")
    git("add", "README.md", cwd=repository)
    git("commit", "-m", "baseline", cwd=repository)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-11T15:33:05Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build",
    ))
    resource = inspect_git_resource(repository, resource_id="resource-1")
    database.append(registration_event(resource))
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": "mission-1", "objective": "test controlled action",
            "resource_id": "resource-1", "state": "PLANNED",
            "acceptance_criteria": ["policy decision is durable"],
        },
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-1",
    ))
    return database, repository


def envelope(repository: Path, **overrides) -> ActionEnvelope:
    values = {
        "action_id": f"action-{uuid.uuid4()}",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "action_kind": "write",
        "target": repository / "result.txt",
        "capability": "write_registered_worktree_files",
        "expected_effects": ("create result.txt",),
        "risk_class": "C1",
        "verification_required": True,
        "rollback": {"kind": "delete_created_file"},
    }
    values.update(overrides)
    return ActionEnvelope(**values)


def test_registered_target_is_allowed(tmp_path):
    database, repository = registered_database(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")

    verdict = evaluate_and_record(database, policy, envelope(repository))

    assert verdict.decision == ActionDecision.ALLOWED
    assert database.row("action_decisions", "action_id", database.events()[-1].payload["action_id"])["decision"] == "ALLOWED"


def test_path_escape_is_rejected(tmp_path):
    database, repository = registered_database(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")

    verdict = policy.evaluate(envelope(repository, target=repository / ".." / "outside.txt"))

    assert verdict.decision == ActionDecision.INVALID_TARGET


def test_active_owner_veto_wins_over_standing_mandate(tmp_path):
    database, repository = registered_database(tmp_path)
    action = envelope(repository)
    database.append(Event.create(
        "owner_directive.recorded",
        {
            "directive_id": "veto-1",
            "command_type": "VETO_ACTION",
            "target": action.action_id,
            "exact_text": "Do not run this action",
        },
        source_type="owner", source_id="local-owner",
    ))
    policy = ActionPolicy(database, tmp_path / "STOP")

    verdict = policy.evaluate(action)

    assert verdict.decision == ActionDecision.VETOED
    assert verdict.matched_veto_ids == ("veto-1",)


def test_stop_blocks_action_without_hermes(tmp_path):
    database, repository = registered_database(tmp_path)
    stop_path = tmp_path / "STOP"
    stop_path.write_text("STOP", encoding="utf-8")
    policy = ActionPolicy(database, stop_path)

    assert policy.evaluate(envelope(repository)).decision == ActionDecision.STOPPED


def test_unready_capability_creates_no_mutation_authority(tmp_path):
    database, repository = registered_database(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")

    verdict = policy.evaluate(envelope(repository, capability="git_push"))

    assert verdict.decision == ActionDecision.NOT_READY


def test_decision_redelivery_dedupes_and_changed_reason_records(tmp_path):
    # Verifier finding B6: the decision idempotency key excluded reason/target,
    # so the same action_id re-delivered with the same decision but a changed
    # reason raised EventConflict. Identical re-delivery must dedupe; a changed
    # payload must record a new decision (projection upserts, last wins).
    database, repository = registered_database(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")
    action = envelope(repository, action_id="action-b6", capability="git_push")

    verdict = evaluate_and_record(database, policy, action)
    assert verdict.decision == ActionDecision.NOT_READY
    events_before = len(database.events())

    # Identical re-delivery -> dedupe, one decision row, no conflict.
    evaluate_and_record(database, policy, action)
    assert len(database.events()) == events_before
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM action_decisions WHERE action_id = 'action-b6'"
        ).fetchone()[0]
    assert count == 1

    # Capability moves DISCOVERED -> PROBING: same decision class, different
    # reason -> new decision recorded, no EventConflict.
    set_capability_state(database, "resource-1", "git_push", "PROBING", evidence={"source": "b6-test"})
    second = evaluate_and_record(database, policy, action)
    assert second.decision == ActionDecision.NOT_READY
    assert "PROBING" in second.reason
    row = database.row("action_decisions", "action_id", "action-b6")
    assert row is not None
    assert "PROBING" in row["reason"]
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM action_decisions WHERE action_id = 'action-b6'"
        ).fetchone()[0]
    assert count == 1  # projection upserts on action_id, last decision wins


def test_unready_capability_with_matching_veto_denies_not_ready(tmp_path):
    # Verifier B2 precedence nuance (documented): capability readiness is checked
    # before veto matching, so an unready capability plus a matching veto yields
    # NOT_READY — the denial is preserved and the veto is not surfaced as the
    # decision. No combination ever allows the action.
    database, repository = registered_database(tmp_path)
    action = envelope(repository, capability="git_push")  # DISCOVERED -> unready
    database.append(Event.create(
        "owner_directive.recorded",
        {
            "directive_id": "veto-1",
            "command_type": "VETO_ACTION",
            "target": action.action_id,
            "exact_text": "Do not run this action",
        },
        source_type="owner", source_id="local-owner",
    ))
    policy = ActionPolicy(database, tmp_path / "STOP")

    verdict = policy.evaluate(action)

    assert verdict.decision == ActionDecision.NOT_READY
    assert "git_push" in verdict.reason


def test_missing_durable_start_mandate_blocks_action(tmp_path):
    database, repository = registered_database(tmp_path)
    with database.transaction() as connection:
        connection.execute("UPDATE system_state SET start_active = 0 WHERE singleton = 1")
    policy = ActionPolicy(database, tmp_path / "STOP")

    verdict = policy.evaluate(envelope(repository))

    assert verdict.decision == ActionDecision.NOT_READY
    assert "START BUILD" in verdict.reason


def test_unknown_mission_cannot_authorize_action(tmp_path):
    database, repository = registered_database(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")

    verdict = policy.evaluate(envelope(repository, mission_id="mission-forged"))

    assert verdict.decision == ActionDecision.NOT_READY
    assert verdict.reason == "mission is not registered"
