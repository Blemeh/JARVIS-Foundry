"""Regression: a terminal mission closes the commit claims it stranded.

The commit effect identity binds the dispatching run, so a claim can only
be resolved by a later pass of the same run. When a mission reaches a
terminal state while such a claim is still STARTED -- because a later step
blocked, or the process died -- nothing else will ever close it, and the
pre-live ambiguity audit correctly refuses to proceed on a non-terminal
effect. The mission lifecycle closes it, reading truth from Git.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.effects import FAILED, STARTED, effect_state  # noqa: E402
from jarvis_foundry.events import Event  # noqa: E402
from jarvis_foundry.mission.commit import close_mission_commit_claims  # noqa: E402


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _start_claim(database, action_key, *, mission_id, run_id, step_id="01-plan"):
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
                "mission_id": mission_id,
                "step_id": step_id,
                "run_id": run_id,
            },
        },
        source_type="supervisor",
        source_id="v5-effect-driver",
        idempotency_key=f"{action_key}.started",
    ))


def test_terminal_closure_abandons_an_unresolvable_claim(tmp_path):
    database = _database(tmp_path)
    key = "effect:" + "d" * 64
    _start_claim(database, key, mission_id="mission-dead", run_id="run-gone")
    assert effect_state(database, key)["status"] == STARTED

    closed = close_mission_commit_claims(
        database, mission_id="mission-dead", worktree=None)

    assert closed == [key]
    assert effect_state(database, key)["status"] == FAILED


def test_terminal_closure_leaves_other_missions_alone(tmp_path):
    database = _database(tmp_path)
    mine = "effect:" + "e" * 64
    theirs = "effect:" + "f" * 64
    _start_claim(database, mine, mission_id="mission-a", run_id="run-a")
    _start_claim(database, theirs, mission_id="mission-b", run_id="run-b")

    close_mission_commit_claims(database, mission_id="mission-a", worktree=None)

    assert effect_state(database, mine)["status"] == FAILED
    assert effect_state(database, theirs)["status"] == STARTED


def test_terminal_closure_is_idempotent(tmp_path):
    database = _database(tmp_path)
    key = "effect:" + "1" * 64
    _start_claim(database, key, mission_id="mission-a", run_id="run-a")
    first = close_mission_commit_claims(database, mission_id="mission-a", worktree=None)
    second = close_mission_commit_claims(database, mission_id="mission-a", worktree=None)
    assert first == [key]
    assert second == []
    assert effect_state(database, key)["status"] == FAILED
