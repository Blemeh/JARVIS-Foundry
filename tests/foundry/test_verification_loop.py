"""WP5 verification-loop tests: verdicts, independence metadata, and the
commit-only-after-PASS invariant."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.control.policy import ActionPolicy
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.commit import CommitError, commit_after_pass
from jarvis_foundry.mission.loop import BLOCKED_OUTCOME, MissionLoop, PASSED
from jarvis_foundry.mission.verification import (
    latest_verdict_for_step,
    record_verification_outcome,
    start_verification,
)
from jarvis_foundry.roles.router import RoleRouter

from test_mission_loop import (
    ScriptedAdapter,
    commit_envelope,
    git_out,
    mission_fixture,
    step_kwargs,
)


def independence(database: FoundryDatabase, verdict_id: str) -> dict:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT independence_json FROM verification_verdicts WHERE verdict_id = ?",
            (verdict_id,),
        ).fetchone()
    return json.loads(row["independence_json"])


def test_verdict_records_independence_metadata(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = ScriptedAdapter(repository)
    loop = MissionLoop(database, router, adapter, policy=policy)

    outcome = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    assert outcome.outcome == PASSED
    latest = latest_verdict_for_step(database, "mission-1", "step-1")
    assert latest["status"] == "PASS"
    assert latest["verdict"] == "PASS"
    assert latest["builder_run_id"] != latest["verifier_run_id"]
    meta = independence(database, latest["verdict_id"])
    # Provider diversity is preferred and recorded: builder opencode_go,
    # verifier resolved to vertex_ai via the fallback chain.
    assert meta["builder_provider"] == "opencode_go"
    assert meta["verifier_provider"] == "vertex_ai"
    assert meta["provider_diverse"] is True
    assert meta["same_provider_limitation"] == ""
    # Criteria and findings are durable.
    assert "independent verifier" in json.dumps(json.loads(latest["criteria_json"]))


def test_verdict_fail_records_findings_and_repair_requirements(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    start_verification(
        database,
        verdict_id="verdict-x1",
        mission_id="mission-1",
        step_id="step-1",
        builder_run_id="run-b",
        verifier_run_id="run-v",
        independence={"builder_provider": "p1", "verifier_provider": "p2",
                      "provider_diverse": True, "same_provider_limitation": ""},
        criteria=["artifact present"],
    )
    record_verification_outcome(
        database, verdict_id="verdict-x1", outcome="FAIL",
        findings=["artifact result.txt missing"],
        evidence={"verifier_run_id": "run-v"},
        repair_requirements=["rebuild the step artifact and re-verify"],
    )

    verdict = database.row("verification_verdicts", "verdict_id", "verdict-x1")
    assert verdict["status"] == "FAIL"
    assert json.loads(verdict["findings_json"]) == ["artifact result.txt missing"]
    assert json.loads(verdict["repair_requirements_json"]) == [
        "rebuild the step artifact and re-verify"]
    assert verdict["issued_at"] is not None


def test_commit_refuses_without_pass_verdict(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    envelope = commit_envelope(repository)

    # No verdict at all -> refuse.
    with pytest.raises(CommitError, match="no verification verdict"):
        commit_after_pass(
            database, mission_id="mission-1", step_id="step-1", run_id="run-1",
            worktree=repository, policy=policy, envelope=envelope)

    # A FAIL verdict -> refuse (never commit on FAIL).
    start_verification(
        database, verdict_id="verdict-x2", mission_id="mission-1",
        step_id="step-1", builder_run_id="run-b", verifier_run_id="run-v",
        independence={}, criteria=[])
    record_verification_outcome(
        database, verdict_id="verdict-x2", outcome="FAIL",
        findings=["broken"], evidence={})
    with pytest.raises(CommitError, match="requires verified PASS"):
        commit_after_pass(
            database, mission_id="mission-1", step_id="step-1", run_id="run-1",
            worktree=repository, policy=policy, envelope=envelope)
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "1"


def test_commit_after_pass_creates_exactly_one_deterministic_commit(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    (repository / "result.txt").write_text("delivered\n", encoding="utf-8")
    start_verification(
        database, verdict_id="verdict-x3", mission_id="mission-1",
        step_id="step-1", builder_run_id="run-b", verifier_run_id="run-v",
        independence={}, criteria=[])
    record_verification_outcome(
        database, verdict_id="verdict-x3", outcome="PASS", findings=[],
        evidence={"verifier_run_id": "run-v"})

    state = commit_after_pass(
        database, mission_id="mission-1", step_id="step-1", run_id="run-b",
        worktree=repository, policy=policy, envelope=commit_envelope(repository))

    assert state["commit_head"] is not None
    assert state["baseline_commit"] != state["commit_head"]
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "2"
    assert git_out("log", "-1", "--format=%s", cwd=repository) == (
        "jarvis: mission-1 step-1 verified PASS (run run-b)")
    assert git_out("diff", "--name-only", "HEAD~1..HEAD", cwd=repository) == "result.txt"
    assert state["files_changed"] == ["result.txt"]


def test_policy_denied_commit_refused(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    (repository / "result.txt").write_text("delivered\n", encoding="utf-8")
    start_verification(
        database, verdict_id="verdict-x4", mission_id="mission-1",
        step_id="step-1", builder_run_id="run-b", verifier_run_id="run-v",
        independence={}, criteria=[])
    record_verification_outcome(
        database, verdict_id="verdict-x4", outcome="PASS", findings=[],
        evidence={})
    with database.transaction() as connection:
        connection.execute(
            "UPDATE resources SET capabilities_json = ? WHERE resource_id = 'resource-1'",
            (json.dumps({
                "read_registered_files": "READY",
                "write_registered_worktree_files": "READY",
                "run_registered_local_commands": "READY",
                "git_local_commit": "PROBING",
                "git_push": "DISCOVERED",
            }),))

    with pytest.raises(CommitError, match="not allowed"):
        commit_after_pass(
            database, mission_id="mission-1", step_id="step-1", run_id="run-b",
            worktree=repository, policy=policy, envelope=commit_envelope(repository))
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "1"


def test_latest_verdict_orders_by_ledger_sequence_not_timestamps(tmp_path):
    # Verifier finding F1: with identical recorded timestamps, the latest-
    # verdict selection must follow ledger sequence, never random verdict_id.
    database, repository, policy, router = mission_fixture(tmp_path)
    for idx, outcome in enumerate(("FAIL", "PASS")):
        start_verification(
            database, verdict_id=f"verdict-seq-{idx}", mission_id="mission-1",
            step_id="step-1", builder_run_id=f"run-b{idx}",
            verifier_run_id=f"run-v{idx}", independence={}, criteria=[])
        record_verification_outcome(
            database, verdict_id=f"verdict-seq-{idx}", outcome=outcome,
            findings=[], evidence={})
    # Force identical wall-clock text — the old updated_at ordering would
    # tie-break on verdict_id and could select the FAIL verdict.
    with database.transaction() as connection:
        connection.execute("UPDATE verification_verdicts SET updated_at = '2026-08-11T00:00:00Z'")

    latest = latest_verdict_for_step(database, "mission-1", "step-1")
    assert latest["verdict_id"] == "verdict-seq-1"  # PASS recorded last, wins

    # Reverse order: FAIL recorded last must win.
    start_verification(
        database, verdict_id="verdict-seq-2", mission_id="mission-1",
        step_id="step-1", builder_run_id="run-b2", verifier_run_id="run-v2",
        independence={}, criteria=[])
    record_verification_outcome(
        database, verdict_id="verdict-seq-2", outcome="FAIL", findings=[], evidence={})
    latest = latest_verdict_for_step(database, "mission-1", "step-1")
    assert latest["verdict_id"] == "verdict-seq-2"


def test_commit_binds_run_id_to_verdict_builder_run(tmp_path):
    # Verifier finding F7: the committed run must be the PASS verdict's
    # builder run — a mismatched run_id is refused even with a PASS verdict.
    database, repository, policy, router = mission_fixture(tmp_path)
    (repository / "result.txt").write_text("x\n", encoding="utf-8")
    start_verification(
        database, verdict_id="verdict-x5", mission_id="mission-1",
        step_id="step-1", builder_run_id="run-real", verifier_run_id="run-v",
        independence={}, criteria=[])
    record_verification_outcome(
        database, verdict_id="verdict-x5", outcome="PASS", findings=[],
        evidence={})

    with pytest.raises(CommitError, match="does not match"):
        commit_after_pass(
            database, mission_id="mission-1", step_id="step-1",
            run_id="run-IMPOSTOR", worktree=repository, policy=policy,
            envelope=commit_envelope(repository))
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "1"


def test_loop_never_commits_on_blocked_outcome(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = ScriptedAdapter(repository, always_fail=True)
    loop = MissionLoop(database, router, adapter, policy=policy, max_repairs=1)

    outcome = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    assert outcome.outcome == BLOCKED_OUTCOME
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "1"
    with database.connect() as connection:
        bundles = connection.execute("SELECT COUNT(*) FROM evidence_bundles").fetchone()[0]
    assert bundles == 0  # evidence bundles exist only for verified PASS



def _commit_key(worktree, run_id):
    """The exact run-scoped commit effect identity for this fixture step."""
    from jarvis_foundry.mission.commit import _commit_effect_id

    return _commit_effect_id(
        mission_id="mission-1", step_id="step-1", run_id=run_id,
        worktree=worktree.resolve(), envelope=commit_envelope(worktree),
    )


def test_superseded_run_claim_is_closed_by_the_next_run(tmp_path):
    """A dead run's commit claim must not strand the ledger STARTED.

    The commit effect identity binds the run_id, so a repair run writes a
    different identity for the same step. The claim left behind by the killed
    run can never complete and must be closed, or the pre-live ambiguity
    audit refuses to proceed on an effect that will never resolve.
    """
    database, repository, policy, router = mission_fixture(tmp_path)
    (repository / "result.txt").write_text("delivered\n", encoding="utf-8")
    start_verification(
        database, verdict_id="verdict-s1", mission_id="mission-1",
        step_id="step-1", builder_run_id="run-b", verifier_run_id="run-v",
        independence={}, criteria=[])
    record_verification_outcome(
        database, verdict_id="verdict-s1", outcome="PASS", findings=[],
        evidence={"verifier_run_id": "run-v"})

    from jarvis_foundry.effects import STARTED, effect_state

    stale_key = _commit_key(repository, "run-old")
    database.append(Event.create(
        "system.action_started",
        {
            "action_key": stale_key,
            "before_state_capture": {},
            "system_id": "resource-1",
            "target_ref": str(repository.resolve()),
            "payload_hash": stale_key.removeprefix("effect:"),
            "kind": "git_commit",
            "request_identity": {
                "mission_id": "mission-1",
                "step_id": "step-1",
                "run_id": "run-old",
            },
        },
        source_type="supervisor",
        source_id="v5-effect-driver",
        idempotency_key=f"{stale_key}.started",
    ))
    assert effect_state(database, stale_key)["status"] == STARTED

    commit_after_pass(
        database, mission_id="mission-1", step_id="step-1", run_id="run-b",
        worktree=repository, policy=policy, envelope=commit_envelope(repository))

    assert effect_state(database, stale_key)["status"] == "FAILED"
