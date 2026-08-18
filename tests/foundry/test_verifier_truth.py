"""V4-S2 (R1b): mission-loop verifier-truth guarantees.

- a verifier verdict that is NOT an explicit PASS (FAIL/BLOCKED/
  NOT_TESTED) never advances the mission: no ACCEPTING, no commit, no
  mission.completed, candidate stays un-executed;
- verifier instructions REQUIRE the structured verdict line;
- evidence.created binds verdicts to FROZEN artifact hashes (the
  deliverable files under the step target are sha256'd at PASS time).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.loop import BLOCKED_OUTCOME, FAILED, MissionLoop
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.router import RoleRouter

from test_mission_loop import mission_fixture


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_step(loop: MissionLoop, *, target: Path, instructions: str = "do the work"):
    return loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=target,
        instructions=instructions,
        expected_effects=("writes within the target directory",),
        rollback={"kind": "git_reset_soft"},
        risk_class="C1",
        verification_criteria=["deliverable exists", "deliverable is correct"],
    )


def test_not_test_verdict_never_advances(tmp_path):
    """A verifier that completes with NOT_TESTED must BLOCK the step
    without recording FAIL or consuming repair budget — never ACCEPTING."""
    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "out.md").write_text("builder output\n", encoding="utf-8")
    adapter = FakeHermesAdapter(outcome="COMPLETED", verdict="NOT_TESTED")
    loop = MissionLoop(database, router, adapter, policy=policy)
    outcome = run_step(loop, target=target)
    assert outcome.outcome == BLOCKED_OUTCOME
    with database.connect() as connection:
        state = connection.execute(
            "SELECT state FROM missions WHERE mission_id = 'mission-1'"
        ).fetchone()[0]
        completed = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'mission.completed'"
        ).fetchone()[0]
        bundles = connection.execute(
            "SELECT COUNT(*) FROM evidence_bundles").fetchone()[0]
        verdict_rows = connection.execute(
            "SELECT status FROM verification_verdicts").fetchall()
        fails = connection.execute(
            "SELECT COUNT(*) FROM verification_verdicts WHERE status = 'FAIL'"
        ).fetchone()[0]
    assert state == "BLOCKED"
    assert completed == 0
    assert bundles == 0
    assert {r[0] for r in verdict_rows} == {"BLOCKED"}
    assert fails == 0


def test_empty_verifier_verdict_does_not_consume_repair_budget(tmp_path):
    """Unknown/empty verifier verdicts must BLOCK like NOT_TESTED, not FAIL."""
    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "out.md").write_text("builder output\n", encoding="utf-8")
    adapter = FakeHermesAdapter(outcome="COMPLETED", verdict=None)
    loop = MissionLoop(database, router, adapter, policy=policy)
    outcome = run_step(loop, target=target)
    assert outcome.outcome == BLOCKED_OUTCOME
    with database.connect() as connection:
        fails = connection.execute(
            "SELECT COUNT(*) FROM verification_verdicts WHERE status = 'FAIL'"
        ).fetchone()[0]
        blocked = connection.execute(
            "SELECT COUNT(*) FROM verification_verdicts WHERE status = 'BLOCKED'"
        ).fetchone()[0]
    assert fails == 0
    assert blocked >= 1


def test_fail_verdict_never_advances(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    adapter = FakeHermesAdapter(outcome="COMPLETED", verdict="FAIL",
                                evidence=("found a defect",))
    loop = MissionLoop(database, router, adapter, policy=policy)
    outcome = run_step(loop, target=target)
    assert outcome.outcome == FAILED
    with database.connect() as connection:
        state = connection.execute(
            "SELECT state FROM missions WHERE mission_id = 'mission-1'"
        ).fetchone()[0]
        verdict_rows = connection.execute(
            "SELECT status FROM verification_verdicts").fetchall()
    assert state == "REPAIRING"
    assert {r[0] for r in verdict_rows} == {"FAIL"}


def test_verifier_instructions_demand_structured_verdict(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "out.md").write_text("deliverable\n", encoding="utf-8")
    adapter = FakeHermesAdapter(outcome="COMPLETED", verdict="PASS")
    loop = MissionLoop(database, router, adapter, policy=policy)
    outcome = run_step(loop, target=target)
    assert outcome.outcome == "PASSED"
    verifier_instructions = adapter.received[-1].instructions
    assert "VERDICT:" in verifier_instructions
    assert "NOT_TESTED" in verifier_instructions


def test_pass_path_binds_artifact_hashes(tmp_path):
    """At PASS the evidence bundle must carry the sha256 of every file
    under the frozen step target — the verdict binds to the artifact."""
    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "report.md").write_text("the deliverable\n", encoding="utf-8")
    (target / "notes.txt").write_text("extra evidence\n", encoding="utf-8")
    adapter = FakeHermesAdapter(outcome="COMPLETED", verdict="PASS")
    loop = MissionLoop(database, router, adapter, policy=policy)
    outcome = run_step(loop, target=target)
    assert outcome.outcome == "PASSED"
    with database.connect() as connection:
        row = connection.execute(
            "SELECT artifact_hashes_json FROM evidence_bundles "
            "ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row is not None
    hashes = __import__("json").loads(row[0])
    assert hashes == {
        "report.md": sha256_file(target / "report.md"),
        "notes.txt": sha256_file(target / "notes.txt"),
    }


def test_pass_path_no_artifact_blocks_model_only_pass(tmp_path):
    """P1-D: an empty target plus a scripted model PASS must not ACCEPT."""
    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()  # empty: no JARVIS-owned artifact predicate
    adapter = FakeHermesAdapter(outcome="COMPLETED", verdict="PASS")
    loop = MissionLoop(database, router, adapter, policy=policy)
    outcome = run_step(loop, target=target)
    assert outcome.outcome == BLOCKED_OUTCOME
    with database.connect() as connection:
        state = connection.execute(
            "SELECT state FROM missions WHERE mission_id = 'mission-1'"
        ).fetchone()[0]
        bundles = connection.execute(
            "SELECT COUNT(*) FROM evidence_bundles").fetchone()[0]
        statuses = [r[0] for r in connection.execute(
            "SELECT status FROM verification_verdicts").fetchall()]
    assert state == "BLOCKED"
    assert bundles == 0
    assert "PASS" not in statuses
