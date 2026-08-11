"""WP3 capability state transition tests."""

from __future__ import annotations

import json

import pytest

from jarvis_foundry.control.capabilities import (
    InvalidCapabilityState,
    set_capability_state,
)
from jarvis_foundry.control.policy import ActionPolicy
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.domain import ActionDecision, ActionEnvelope
from jarvis_foundry.events import Event
from jarvis_foundry.reconcile import reconcile

from test_action_policy import envelope, registered_database


def _capabilities(database: FoundryDatabase, resource_id: str) -> dict:
    row = database.row("resources", "resource_id", resource_id)
    return json.loads(row["capabilities_json"])


def test_capability_state_transition_is_durable_and_rebuildable(tmp_path):
    database, repository = registered_database(tmp_path)

    set_capability_state(database, "resource-1", "git_local_commit", "READY", evidence={"tests": "ok"})
    assert _capabilities(database, "resource-1")["git_local_commit"] == "READY"

    set_capability_state(database, "resource-1", "git_local_commit", "DEGRADED", evidence={"reason": "rollback rehearsal failed"})
    assert _capabilities(database, "resource-1")["git_local_commit"] == "DEGRADED"

    before = _capabilities(database, "resource-1")
    assert database.rebuild_projections() == 5  # start + registered + mission + 2 capability events
    assert _capabilities(database, "resource-1") == before
    assert reconcile(database).consistent is True


def test_capability_state_events_are_idempotent_per_transition(tmp_path):
    database, repository = registered_database(tmp_path)
    event = set_capability_state(database, "resource-1", "git_push", "DISCOVERED")
    sequence, inserted = database.append(event)
    assert inserted is False
    assert _capabilities(database, "resource-1")["git_push"] == "DISCOVERED"


def test_invalid_state_is_rejected(tmp_path):
    database, repository = registered_database(tmp_path)
    with pytest.raises(InvalidCapabilityState):
        set_capability_state(database, "resource-1", "git_push", "MAYBE")
    with pytest.raises(InvalidCapabilityState):
        set_capability_state(database, "resource-1", "", "READY")


def test_policy_accepts_ready_and_active_but_denies_others(tmp_path):
    database, repository = registered_database(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")

    assert policy.evaluate(envelope(repository, capability="read_registered_files")).decision == ActionDecision.ALLOWED

    set_capability_state(database, "resource-1", "read_registered_files", "ACTIVE")
    assert policy.evaluate(envelope(repository, capability="read_registered_files")).decision == ActionDecision.ALLOWED

    set_capability_state(database, "resource-1", "read_registered_files", "PROBING")
    verdict = policy.evaluate(envelope(repository, capability="read_registered_files"))
    assert verdict.decision == ActionDecision.NOT_READY
    assert "PROBING" in verdict.reason


def test_capability_state_on_unknown_resource_rolls_back(tmp_path):
    database, repository = registered_database(tmp_path)
    before = len(database.events())
    with pytest.raises(ValueError, match="unknown resource"):
        set_capability_state(database, "resource-forged", "git_push", "READY")
    assert len(database.events()) == before  # event rolled back with its projection
    assert reconcile(database).consistent is True
