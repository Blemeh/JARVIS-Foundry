"""Terminal-failure release of duplicate coverage (D4 recovery enabler).

Live 2026-09-30: mission-ecdb7918c2d4 failed terminally on the product's own
unsatisfiable contract, yet `covered_objective` treated its candidate as
permanent coverage — parking owner-c4dbe49e2dd7b49e11583655 forever with no
path back to work.

Repair: terminal FAILED/BLOCKED/CANCELLED missions release the lock for
exactly ONE canonical recovery attempt (ledger-derived count, restart-safe).
COMPLETE missions always cover.  Live missions always cover.  Consumed-history
candidates never cover.  A second terminal failure re-covers permanently:
no loop is possible.

All fixtures use the production lifecycle APIs (generate/score/approve/
record_outcome, legal mission transitions) — never hand-rolled rows.
"""

from __future__ import annotations

from pathlib import Path

from jarvis_foundry.discovery_candidates import (
    approve_candidate,
    covered_objective,
    generate_candidate,
    record_outcome,
    score_candidate,
)

from worker_test_helpers import mission_database

OBJECTIVE = "CI_FAILURE: fix failing CI on blemeh/jarvis-foundry"


def _seed_candidate(database, *, objective: str = OBJECTIVE) -> str:
    result = generate_candidate(database, "mission-seed", "repo", objective)
    assert result["created"] is True
    score_candidate(database, result["candidate_id"])
    row = database.row("candidates", "candidate_id", result["candidate_id"])
    assert row["status"] == "SCORED"
    return result["candidate_id"]


_WALK_SEQ = [0]


def _walk(database, mission_id: str, *states: str) -> None:
    from jarvis_foundry.events import Event

    for state in states:
        _WALK_SEQ[0] += 1
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state,
             "next_action": None if state == "COMPLETE" else "execute"},
            source_type="supervisor", source_id="test",
            idempotency_key=(
                f"mission.state_changed:{mission_id}:{state}:"
                f"walk{_WALK_SEQ[0]}"),
        ))


def _failed_mission(database, *, mission_id: str,
                    objective: str = OBJECTIVE) -> None:
    from jarvis_foundry.events import Event

    database.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": objective,
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["green"], "next_action": "execute",
         "created_from": "portfolio:test"},
        source_type="supervisor", source_id="test",
        idempotency_key=f"missions.created:{mission_id}",
    ))
    _walk(database, mission_id,
          "ORIENTING", "ACTION_SELECTION", "READY", "EXECUTING")


def test_fresh_objective_uncovered(tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    assert covered_objective(database, OBJECTIVE) is None


def test_live_candidate_covers(tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    candidate_id = _seed_candidate(database)
    assert covered_objective(database, OBJECTIVE) == candidate_id


def test_live_mission_covers(tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    _failed_mission(database, mission_id="mission-live")
    assert covered_objective(database, OBJECTIVE) == "mission-live"


def test_complete_mission_covers_forever(tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    _seed_candidate(database)
    _failed_mission(database, mission_id="mission-done")
    _walk(database, "mission-done",
          "BLOCKED", "ACTION_SELECTION", "COMPLETE")
    assert covered_objective(database, OBJECTIVE) == "mission-done"


def test_single_terminal_failure_releases_the_lock(tmp_path: Path) -> None:
    """The live shape: SCORED candidate + one BLOCKED mission → open."""
    database, _repo = mission_database(tmp_path)
    _seed_candidate(database)
    _failed_mission(database, mission_id="mission-ecdb7918c2d4")
    _walk(database, "mission-ecdb7918c2d4", "BLOCKED")
    assert covered_objective(database, OBJECTIVE) is None


def test_second_terminal_failure_recovers_permanently(tmp_path: Path) -> None:
    """Original + recovery both failed → covered again, stable WAIT."""
    database, _repo = mission_database(tmp_path)
    _seed_candidate(database)
    _failed_mission(database, mission_id="mission-old")
    _walk(database, "mission-old", "BLOCKED")
    _failed_mission(database, mission_id="mission-recovery")
    _walk(database, "mission-recovery", "BLOCKED")
    assert covered_objective(database, OBJECTIVE) is not None


def test_consumed_candidate_never_covers(tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    candidate_id = _seed_candidate(database)
    mission_id = approve_candidate(database, candidate_id, "builder",
                                   resource_id="resource-1")
    record_outcome(database, candidate_id, mission_id,
                   {"prediction": "fix CI", "reality": "failed"})
    row = database.row("candidates", "candidate_id", candidate_id)
    assert row["status"] == "EXECUTED"
    # The approved mission is live (PLANNED) → it covers, not the candidate.
    assert covered_objective(database, OBJECTIVE) == mission_id


def test_generate_candidate_releases_after_terminal_failure(
        tmp_path: Path) -> None:
    """End-to-end through the production candidate entry point."""
    database, _repo = mission_database(tmp_path)
    first = generate_candidate(database, "mission-seed", "repo", OBJECTIVE)
    assert first["created"] is True
    _failed_mission(database, mission_id="mission-t1")
    _walk(database, "mission-t1", "BLOCKED")
    second = generate_candidate(database, "mission-seed", "repo", OBJECTIVE)
    assert second["created"] is True, (
        "a terminally failed lineage must not block re-derivation")
    assert second["duplicate_of"] is None
