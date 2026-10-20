"""V4-S4 (R3): exactly-once verifier recovery.

DIR-2026-08-12 item 3: if verification is interrupted after a builder
completed, resume verification WITHOUT re-running the builder — generic,
durable and state-aware; the actual external mutation count must be one.

Scenario: pass-1's builder COMPLETED (ledger row + manifest hash), the
verifier was started but never finished (RUNNING row, STARTED verdict —
or a reaped FAILED row). The caller resumes with the SAME run_id:
- the builder run is REPLAYED from the ledger (no re-execution);
- the verifier is dispatched under a FRESH run id (re-dispatching under
  the old id would share the row with a possibly-live orphan session and
  its completion event could conflict or be blocked by the terminal
  guard);
- the dangling STARTED verdict receives an additive correction
  (verification.blocked: interrupted; superseded by the resumed pass).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.loop import MissionLoop
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.manifest import ContextManifest
from jarvis_foundry.roles.results import COMPLETED, RoleResult

from test_mission_loop import mission_fixture

VERIFIER_INSTRUCTIONS = (
    "independently verify step step-1 of mission-1: inspect the frozen "
    "artifact directly and confirm each criterion. At the end, choose "
    "exactly one verdict and emit one line in this format: VERDICT: PASS. "
    "Replace PASS with exactly one of FAIL, BLOCKED, or NOT_TESTED when "
    "appropriate; do not list the alternatives.")


def builder_manifest() -> ContextManifest:
    return ContextManifest(
        run_id="run-x1", role="builder", mission_id="mission-1",
        step_id="step-1", resource_id="resource-1",
        capability="git_local_commit", provider="opencode_go",
        model="deepseek-v4-flash", isolation_group="builder",
        tool_policy=("code", "tools"), budget_usd=0.0,
        duration_seconds=300, instructions="do the work",
        verification_criteria=(
            "deliverable exists", "deliverable is correct"),
    )


def verifier_manifest() -> ContextManifest:
    return ContextManifest(
        run_id="run-x1-verify", role="verifier", mission_id="mission-1",
        step_id="step-1", resource_id="resource-1",
        capability="verification", provider="vertex_ai",
        model="deepseek-v4-flash", isolation_group="verifier",
        tool_policy=("artifact_inspection", "adversarial_verification"),
        budget_usd=0.0, duration_seconds=300,
        instructions=VERIFIER_INSTRUCTIONS,
        verification_criteria=(
            "deliverable exists", "deliverable is correct"),
    )


def state_event(database: FoundryDatabase, state: str) -> None:
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-1", "state": state,
         "next_action": "deliver feature X"},
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.state_changed:mission-1:{state}:pass1"))


def interrupted_pass1(database: FoundryDatabase, *,
                      verifier_status: str = "RUNNING") -> None:
    """Pass-1 durable state: mission walked to VERIFYING, builder
    COMPLETED, verification.started (dangling), verifier started but
    never finished (RUNNING by default, or FAILED for a reaped session).

    The verifier started event uses a NON-matching manifest hash on
    purpose: a same-run_id resume must NOT replay or re-use that run id
    (re-dispatch would conflict on the started idempotency key)."""
    for state in ("READY", "EXECUTING", "VERIFYING"):
        state_event(database, state)
    bm = builder_manifest()
    database.append(Event.create(
        "agent_run.started",
        {"run_id": "run-x1", "mission_id": "mission-1", "step_id": "step-1",
         "role": "builder", "provider": "opencode_go",
         "model": "deepseek-v4-flash",
         "context_manifest_hash": bm.content_hash(),
         "tool_policy": ["code", "tools"]},
        source_type="test", source_id="test-suite",
        idempotency_key="agent_run.started:run-x1:opencode_go"))
    database.append(Event.create(
        "agent_run.completed",
        {"run_id": "run-x1",
         "result": RoleResult(
             run_id="run-x1", role="builder", status=COMPLETED,
             provider="opencode_go", model="deepseek-v4-flash",
             verdict="PASS", evidence=("built the deliverable",),
         ).to_payload(),
         "cost": {"usd": 0.0}},
        source_type="test", source_id="test-suite",
        idempotency_key="agent_run.completed:run-x1"))
    database.append(Event.create(
        "verification.started",
        {"verdict_id": "verdict-v1", "mission_id": "mission-1",
         "subject_type": "mission_step", "subject_id": "step-1",
         "verifier_run_id": "run-x1-verify", "builder_run_id": "run-x1",
         "independence": {"builder_provider": "opencode_go",
                          "verifier_provider": "vertex_ai",
                          "provider_diverse": True,
                          "same_provider_limitation": ""},
         "criteria": ["deliverable exists", "deliverable is correct"]},
        source_type="test", source_id="test-suite",
        idempotency_key="verification.started:verdict-v1"))
    database.append(Event.create(
        "agent_run.started",
        {"run_id": "run-x1-verify", "mission_id": "mission-1",
         "step_id": "step-1", "role": "verifier", "provider": "vertex_ai",
         "model": "deepseek-v4-flash", "context_manifest_hash": "h",
         "tool_policy": ["artifact_inspection", "adversarial_verification"]},
        source_type="test", source_id="test-suite",
        idempotency_key="agent_run.started:run-x1-verify:vertex_ai"))
    if verifier_status == "FAILED":
        database.append(Event.create(
            "agent_run.failed",
            {"run_id": "run-x1-verify",
             "failure_class": "heartbeat_timeout"},
            source_type="test", source_id="test-suite",
            idempotency_key="agent_run.failed:run-x1-verify:heartbeat_timeout"))
    # driver walk: VERIFYING -> BLOCKED -> ACTION_SELECTION (session_lost)
    for state in ("BLOCKED", "ACTION_SELECTION"):
        state_event(database, state)


def count_started(database: FoundryDatabase, run_id: str) -> int:
    with database.connect() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'agent_run.started' AND payload_json LIKE ?",
            (f'%"{run_id}"%',)).fetchone()[0]


def verdict_status(database: FoundryDatabase, verdict_id: str) -> str:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status FROM verification_verdicts WHERE verdict_id = ?",
            (verdict_id,)).fetchone()
    return row[0] if row else "MISSING"


def test_resume_after_interrupted_verifier_runs_builder_exactly_once(tmp_path):
    """The directive's core proof: resume verification without re-running
    the builder; external mutation count stays one."""
    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    deliverable = target / "report.md"
    deliverable.write_text("pass-1 builder output\n", encoding="utf-8")
    before_hash = hashlib.sha256(deliverable.read_bytes()).hexdigest()

    interrupted_pass1(database)

    adapter = FakeHermesAdapter(outcome="COMPLETED", verdict="PASS")
    loop = MissionLoop(database, router, adapter, policy=policy)
    outcome = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=target,
        instructions="do the work", run_id="run-x1",
        expected_effects=("writes within the target directory",),
        rollback={"kind": "git_reset_soft"}, risk_class="C1",
        verification_criteria=["deliverable exists", "deliverable is correct"])

    assert outcome.outcome == "PASSED"
    # the builder was EXECUTED exactly once (the resume replayed it)
    assert count_started(database, "run-x1") == 1
    # the deliverable was mutated exactly once: byte-identical after resume
    assert hashlib.sha256(deliverable.read_bytes()).hexdigest() == before_hash
    # the verifier was dispatched under one deterministic, content-addressed
    # fresh run id and completed.  The exact suffix is a contract digest, not
    # a process-local counter.
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT run_id, status FROM agent_runs "
            "WHERE run_id LIKE 'run-x1-verify-v%'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "COMPLETED"
    # the dangling STARTED verdict got the additive correction
    assert verdict_status(database, "verdict-v1") == "BLOCKED"
    # a fresh verdict was recorded PASS
    with database.connect() as connection:
        passed = connection.execute(
            "SELECT COUNT(*) FROM verification_verdicts WHERE status = 'PASS'"
        ).fetchone()[0]
    assert passed == 1


def test_resume_after_reaped_verifier_row_uses_fresh_id(tmp_path):
    """A reaped (FAILED) verifier row must also resume under a fresh run
    id — reusing the failed id would let the completion event be blocked
    by the terminal guard (S3), leaving the row FAILED while the loop
    advances."""
    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "report.md").write_text("output\n", encoding="utf-8")
    interrupted_pass1(database, verifier_status="FAILED")

    adapter = FakeHermesAdapter(outcome="COMPLETED", verdict="PASS")
    loop = MissionLoop(database, router, adapter, policy=policy)
    outcome = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=target,
        instructions="do the work", run_id="run-x1",
        expected_effects=("writes within the target directory",),
        rollback={"kind": "git_reset_soft"}, risk_class="C1",
        verification_criteria=["deliverable exists", "deliverable is correct"])

    assert outcome.outcome == "PASSED"
    with database.connect() as connection:
        fresh = connection.execute(
            "SELECT run_id, status FROM agent_runs "
            "WHERE run_id LIKE 'run-x1-verify-v%'"
        ).fetchall()
    assert len(fresh) == 1
    assert fresh[0][1] == "COMPLETED"
    # the reaped row stays FAILED (monotonic), the fresh row COMPLETED
    with database.connect() as connection:
        rows = dict(connection.execute(
            "SELECT run_id, status FROM agent_runs ORDER BY run_id").fetchall())
    assert rows["run-x1-verify"] == "FAILED"
    assert rows[fresh[0][0]] == "COMPLETED"
    assert verdict_status(database, "verdict-v1") == "BLOCKED"


def test_completed_verifier_with_changed_manifest_is_re_evaluated(tmp_path):
    """A completed verifier from an older manifest is immutable history.

    The repaired loop must create a fresh content-addressed verifier session
    when its instructions/contract change, rather than silently replaying an
    incompatible PASS.
    """
    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "report.md").write_text("output\n", encoding="utf-8")
    interrupted_pass1(database)
    # finish the verifier run properly (real hash so the replay matches)
    vm = verifier_manifest()
    with database.connect() as connection:
        connection.execute(
            "UPDATE agent_runs SET context_manifest_hash = ? "
            "WHERE run_id = 'run-x1-verify'", (vm.content_hash(),))
    database.append(Event.create(
        "agent_run.completed",
        {"run_id": "run-x1-verify",
         "result": RoleResult(
             run_id="run-x1-verify", role="verifier", status=COMPLETED,
             provider="vertex_ai", model="deepseek-v4-flash",
             verdict="PASS", evidence=("verified the deliverable",),
         ).to_payload(),
         "cost": {"usd": 0.0}},
        source_type="test", source_id="test-suite",
        idempotency_key="agent_run.completed:run-x1-verify"))

    adapter = FakeHermesAdapter(outcome="COMPLETED", verdict="FAIL")
    loop = MissionLoop(database, router, adapter, policy=policy)
    outcome = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=target,
        instructions="do the work", run_id="run-x1",
        expected_effects=("writes within the target directory",),
        rollback={"kind": "git_reset_soft"}, risk_class="C1",
        verification_criteria=["deliverable exists", "deliverable is correct"])

    # The changed contract is evaluated by the fake verifier, which returns
    # FAIL; the historical PASS is not reused as authorization.
    assert outcome.outcome == "FAILED"
    assert count_started(database, "run-x1-verify") == 1
    with database.connect() as connection:
        fresh = connection.execute(
            "SELECT run_id, status FROM agent_runs "
            "WHERE run_id LIKE 'run-x1-verify-v%'"
        ).fetchall()
    assert len(fresh) == 1
    assert fresh[0][1] == "COMPLETED"
    # the dangling verdict was corrected; the fresh verdict recorded FAIL
    assert verdict_status(database, "verdict-v1") == "BLOCKED"
    with database.connect() as connection:
        passed = connection.execute(
            "SELECT COUNT(*) FROM verification_verdicts WHERE status = 'PASS'"
        ).fetchone()[0]
    assert passed == 0
