"""Regression: a superseded commit claim must not strand the ledger.

The commit effect identity binds the dispatching run_id, so a repair run
re-dispatching the same mission step writes a different identity. If the
earlier attempt died before invoking the connector, its claim stayed
STARTED forever, and the pre-live duplicate/ambiguity audit correctly read
that as an unreconciled effect. commit_after_pass now closes the superseded
claim: abandon when Git shows no commit for that exact run, reconcile when
the superseded run did commit.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.effects import (  # noqa: E402
    FAILED,
    FROZEN,
    STARTED,
    abandon_effect,
    effect_state,
)
from jarvis_foundry.events import Event  # noqa: E402


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _start_claim(database: FoundryDatabase, action_key: str, *, run_id: str) -> None:
    database.append(Event.create(
        "system.action_started",
        {
            "action_key": action_key,
            "before_state_capture": {},
            "system_id": "git-local",
            "target_ref": "C:/tmp/worktree",
            "payload_hash": action_key.removeprefix("effect:"),
            "kind": "git_commit",
            "request_identity": {
                "mission_id": "mission-superseded",
                "step_id": "01-plan",
                "run_id": run_id,
            },
        },
        source_type="supervisor",
        source_id="v5-effect-driver",
        idempotency_key=f"{action_key}.started",
    ))


def test_abandon_closes_a_started_claim_without_fabricating_completion(tmp_path):
    database = _database(tmp_path)
    action_key = "effect:" + "a" * 64
    _start_claim(database, action_key, run_id="run-old")
    assert effect_state(database, action_key)["status"] == STARTED

    abandon_effect(database, action_key, reason="superseded before dispatch")

    state = effect_state(database, action_key)
    assert state["status"] == FAILED
    assert state["status"] != "COMPLETED"


def test_abandon_refuses_a_terminal_effect(tmp_path):
    database = _database(tmp_path)
    action_key = "effect:" + "b" * 64
    _start_claim(database, action_key, run_id="run-old")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE system_actions SET status = ? WHERE action_key = ?",
            (FROZEN, action_key),
        )
    with pytest.raises(Exception):
        abandon_effect(database, action_key, reason="too late")


def test_abandonment_event_alone_still_derives_failed(tmp_path):
    """A crash between the event and the journal update must not resurrect it."""
    database = _database(tmp_path)
    action_key = "effect:" + "c" * 64
    _start_claim(database, action_key, run_id="run-old")
    database.append(Event.create(
        "effect.abandoned",
        {"action_key": action_key, "reason": "superseded", "performed": False},
        source_type="supervisor",
        source_id="v5-effect-driver",
        idempotency_key=f"{action_key}.abandoned.manual",
    ))
    assert effect_state(database, action_key)["status"] == FAILED
