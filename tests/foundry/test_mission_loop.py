"""WP5 mission loop tests.

Gate (03 spec §16 WP5): a fake mission with a deliberately failing builder
step is repaired and continues to the next useful action without owner
prompting — plus honest waiting, proposal validation, and repair-budget
blocking.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.control.action_envelope import evaluate_and_record
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.policy import ActionPolicy
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.domain import ActionEnvelope
from jarvis_foundry.events import Event
from jarvis_foundry.mission.loop import (
    BLOCKED_OUTCOME,
    FAILED,
    MissionLoop,
    PASSED,
    WAITING_OUTCOME,
)
from jarvis_foundry.mission.proposal import ProposalError, propose_next_action
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.results import COMPLETED, RoleResult
from jarvis_foundry.roles.router import RoleRouter


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def git_out(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def make_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    git("init", cwd=repository)
    git("config", "user.email", "jarvis-test@example.invalid", cwd=repository)
    git("config", "user.name", "JARVIS Test", cwd=repository)
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    git("add", "README.md", cwd=repository)
    git("commit", "-m", "baseline", cwd=repository)
    return repository


def mission_fixture(tmp_path, *, next_action: str | None = "deliver feature X",
                    mission_id: str = "mission-1", objective: str = "gate mission"):
    """Start + registered git resource + mission + policy + READY commit
    capability — the full staging surface the loop governs."""
    repository = make_repository(tmp_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-11T15:33:05Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build",
    ))
    database.append(registration_event(
        inspect_git_resource(repository, resource_id="resource-1")))
    database.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": objective,
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["step is repaired and verified"],
         "next_action": next_action},
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.created:{mission_id}",
    ))
    # The executive/orientation layer prepares the mission to the dispatch
    # domain (PLANNED -> ORIENTING -> ACTION_SELECTION), carrying the
    # scheduled requirement explicitly on every state change.
    for state in ("ORIENTING", "ACTION_SELECTION"):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": next_action},
            source_type="test", source_id="test-suite",
            idempotency_key=f"mission.state_changed:{mission_id}:{state}",
        ))
    set_capability_state(database, "resource-1", "git_local_commit", "READY",
                         evidence={"source": "wp5-test"})
    policy = ActionPolicy(database, tmp_path / "STOP")
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go", "fallbacks": ()},
        "verifier": {"preferred_provider": "opencode_go",
                     "fallbacks": ("vertex_ai",)},
        "repair": {"preferred_provider": "opencode_go", "fallbacks": ()},
    })
    return database, repository, policy, router


def commit_envelope(repository: Path, mission_id: str = "mission-1") -> ActionEnvelope:
    return ActionEnvelope(
        action_id="commit-step-1",
        mission_id=mission_id,
        resource_id="resource-1",
        action_kind="git_commit",
        target=repository,
        capability="git_local_commit",
        expected_effects=("create local commit",),
        risk_class="C1",
        verification_required=True,
        rollback={"kind": "git_reset_soft"},
    )


class ScriptedAdapter(FakeHermesAdapter):
    """Deterministic gate adapter: the builder omits the artifact on its
    first attempt (deliberate failure), the verifier inspects the worktree
    and FAILs on the missing artifact, the repair role delivers it, and the
    verifier then PASSes."""

    def __init__(self, worktree: Path, artifact_name: str = "result.txt",
                 *, always_fail: bool = False) -> None:
        super().__init__()
        self.worktree = Path(worktree)
        self.artifact_name = artifact_name
        self.always_fail = always_fail
        self.attempts = {"builder": 0, "repair": 0, "verifier": 0}

    def dispatch(self, manifest, *, heartbeat) -> RoleResult:
        role = manifest.role
        self.attempts[role] = self.attempts.get(role, 0) + 1
        heartbeat()
        artifact = self.worktree / self.artifact_name
        if role == "verifier":
            if artifact.exists() and not self.always_fail:
                return RoleResult(
                    run_id=manifest.run_id, role=role, status=COMPLETED,
                    provider=manifest.provider, model=manifest.model,
                    verdict="PASS", evidence=("artifact present",))
            return RoleResult(
                run_id=manifest.run_id, role=role, status=COMPLETED,
                provider=manifest.provider, model=manifest.model,
                verdict="FAIL", evidence=(f"artifact {self.artifact_name} missing",))
        if role == "repair":
            artifact.write_text("repaired\n", encoding="utf-8")
            return RoleResult(
                run_id=manifest.run_id, role=role, status=COMPLETED,
                provider=manifest.provider, model=manifest.model,
                verdict="PASS", evidence=("repair delivered artifact",))
        # builder: first attempt omits the artifact (deliberate failure)
        if self.attempts[role] == 1:
            return RoleResult(
                run_id=manifest.run_id, role=role, status=COMPLETED,
                provider=manifest.provider, model=manifest.model,
                verdict="PASS", evidence=("built (artifact omitted)",))
        artifact.write_text("delivered\n", encoding="utf-8")
        return RoleResult(
            run_id=manifest.run_id, role=role, status=COMPLETED,
            provider=manifest.provider, model=manifest.model,
            verdict="PASS", evidence=("built",))


def step_kwargs(**overrides):
    values = {
        "mission_id": "mission-1",
        "step_id": "step-1",
        "capability": "write_registered_worktree_files",
        "target": Path("result.txt"),
        "instructions": "build result.txt for the fake mission",
        "expected_effects": ("create result.txt",),
        "rollback": {"kind": "delete_created_file"},
        "risk_class": "C1",
    }
    values.update(overrides)
    return values


# ---------------------------------------------------------------------------
# D2: envelope contract validated BEFORE dispatch (verifier finding F3)
# ---------------------------------------------------------------------------

def test_unknown_capability_rejected_before_dispatch(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = FakeHermesAdapter()
    loop = MissionLoop(database, router, adapter, policy=policy)

    with pytest.raises(ProposalError, match="not registered"):
        loop.run_step(**step_kwargs(capability="teleport_into_canonical_db"))

    assert len(adapter.received) == 0  # nothing was dispatched


def test_empty_rollback_rejected_before_dispatch(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = FakeHermesAdapter()
    loop = MissionLoop(database, router, adapter, policy=policy)

    with pytest.raises(ProposalError, match="rollback contract is required"):
        loop.run_step(**step_kwargs(rollback={}))

    assert len(adapter.received) == 0


def test_out_of_root_target_rejected_before_dispatch(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = FakeHermesAdapter()
    loop = MissionLoop(database, router, adapter, policy=policy)

    with pytest.raises(ProposalError, match="escapes the registered resource root"):
        loop.run_step(**step_kwargs(target=Path("..") / "outside.txt"))

    assert len(adapter.received) == 0


def test_missing_step_id_rejected_before_dispatch(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = FakeHermesAdapter()
    loop = MissionLoop(database, router, adapter, policy=policy)

    with pytest.raises(ProposalError, match="step_id is required"):
        loop.run_step(**step_kwargs(step_id=""))

    assert len(adapter.received) == 0


def test_repair_requires_existing_fail_verdict(tmp_path):
    # Verifier finding F6: a hand-crafted REPAIRING state must not invent a
    # repair pass without a durable FAIL verdict for the step.
    database, repository, policy, router = mission_fixture(tmp_path)
    loop = MissionLoop(database, router, FakeHermesAdapter(), policy=policy)
    loop._advance("mission-1", "READY")
    loop._advance("mission-1", "EXECUTING")
    loop._advance("mission-1", "VERIFYING")
    loop._advance("mission-1", "REPAIRING")

    with pytest.raises(ProposalError, match="no FAIL verdict"):
        loop.run_step(**step_kwargs())


def test_fresh_loop_instance_resumes_repairing_mission(tmp_path):
    # Verifier finding F2: a FRESH MissionLoop instance resuming a mission
    # left in REPAIRING must re-walk the state machine cleanly (run_id-scoped
    # advance keys), never collide on instance-local pass counters.
    database, repository, policy, router = mission_fixture(tmp_path)
    first = MissionLoop(
        database, router, ScriptedAdapter(repository, always_fail=True),
        policy=policy, max_repairs=5)
    failed = first.run_step(**step_kwargs())
    assert failed.outcome == FAILED
    assert database.row("missions", "mission_id", "mission-1")["state"] == "REPAIRING"

    # Brand-new instance resumes: repair delivers, verifier PASSes, commit.
    resume = MissionLoop(
        database, router, ScriptedAdapter(repository),
        policy=policy, max_repairs=5)
    outcome = resume.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    assert outcome.outcome == PASSED
    assert outcome.commit_head is not None
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    assert (repository / "result.txt").exists()


# ---------------------------------------------------------------------------
# GATE: a fake mission with a deliberately failing builder step is repaired
# and continues to the next useful action without owner prompting.
# ---------------------------------------------------------------------------

def test_gate_deliberate_failure_repaired_and_committed_without_owner(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = ScriptedAdapter(repository)
    loop = MissionLoop(database, router, adapter, policy=policy, max_repairs=2)

    outcome = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    # Repaired: PASSED after the repair cycle, no owner prompting anywhere.
    assert outcome.outcome == PASSED
    assert outcome.step_id == "step-1"
    assert outcome.commit_head is not None
    assert (repository / "result.txt").read_text(encoding="utf-8") == "repaired\n"

    # The deliberate failure was caught by the verifier and repaired.
    assert adapter.attempts["builder"] == 1
    assert adapter.attempts["repair"] == 1
    assert adapter.attempts["verifier"] == 2  # FAIL then PASS

    # Durable ledger: 4 agent runs, 2 verdicts (FAIL then PASS), 1 evidence
    # bundle, and a local commit created only after the verified PASS.
    with database.connect() as connection:
        runs = connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
        verdicts = connection.execute(
            """SELECT v.status FROM verification_verdicts v
               JOIN events e ON e.event_id = v.last_event_id
               WHERE v.mission_id = 'mission-1'
               ORDER BY e.sequence"""
        ).fetchall()
        bundles = connection.execute("SELECT COUNT(*) FROM evidence_bundles").fetchone()[0]
    assert runs == 4
    assert [row["status"] for row in verdicts] == ["FAIL", "PASS"]
    assert bundles == 1
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "2"
    message = git_out("log", "-1", "--format=%s", cwd=repository)
    assert message.startswith("jarvis: mission-1 step-1 verified PASS (run ")
    assert git_out("diff", "--name-only", "HEAD~1..HEAD", cwd=repository) == "result.txt"

    # Mission continues to the next action deterministically: with the
    # requirement consumed, the loop records honest waiting — no invented work.
    second = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))
    assert second.outcome == WAITING_OUTCOME
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    assert mission["next_action"] is None


def test_proposal_validation_rejects_unknown_and_nondispatchable(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    with pytest.raises(ProposalError):
        propose_next_action(database, "mission-forged")

    # Advance to EXECUTING -> not dispatchable until the loop returns it.
    from jarvis_foundry.mission.loop import MissionLoop as _Loop
    loop = _Loop(database, router, FakeHermesAdapter(), policy=policy)
    loop._advance("mission-1", "READY")
    loop._advance("mission-1", "EXECUTING")
    with pytest.raises(ProposalError):
        propose_next_action(database, "mission-1")


def test_honest_waiting_when_no_next_action(tmp_path):
    database, repository, policy, router = mission_fixture(
        tmp_path, next_action=None)
    adapter = FakeHermesAdapter()
    loop = MissionLoop(database, router, adapter, policy=policy)

    outcome = loop.run_step(**step_kwargs())

    assert outcome.outcome == WAITING_OUTCOME
    assert len(adapter.received) == 0          # nothing was dispatched
    with database.connect() as connection:
        runs = connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
    assert runs == 0                           # zero events, zero invented work
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"


def test_repair_budget_exhaustion_blocks_precisely(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = ScriptedAdapter(repository, always_fail=True)
    loop = MissionLoop(database, router, adapter, policy=policy, max_repairs=2)

    outcome = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    assert outcome.outcome == BLOCKED_OUTCOME
    assert "repair budget exhausted" in outcome.reason
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "BLOCKED"
    # No commit was ever created after the failed verifications.
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "1"
    # The loop is bounded: exactly max_repairs + 1 dispatch cycles, no hang.
    assert adapter.attempts["builder"] + adapter.attempts["repair"] <= 4


def test_builder_session_failure_blocks_mission(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)

    class BuilderDown(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            if manifest.role == "builder":
                from jarvis_foundry.roles.adapter import ProviderUnavailable
                raise ProviderUnavailable("builder provider down")
            return super().dispatch(manifest, heartbeat=heartbeat)

    loop = MissionLoop(database, router, BuilderDown(), policy=policy)
    outcome = loop.run_until_settled(**step_kwargs())

    assert outcome.outcome == BLOCKED_OUTCOME
    assert "did not complete" in outcome.reason
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "BLOCKED"


def test_verifier_outage_blocks_mission_precisely(tmp_path):
    database, repository, policy, router = mission_fixture(tmp_path)

    class VerifierDown(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            if manifest.role == "verifier":
                from jarvis_foundry.roles.adapter import ProviderUnavailable
                raise ProviderUnavailable("verifier provider down")
            return super().dispatch(manifest, heartbeat=heartbeat)

    loop = MissionLoop(database, router, VerifierDown(), policy=policy)
    outcome = loop.run_until_settled(**step_kwargs())

    assert outcome.outcome == BLOCKED_OUTCOME
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "BLOCKED"
    # The verification verdict is durably BLOCKED with the dependency named.
    verdict = database.row("verification_verdicts", "verdict_id", outcome.verdict_id)
    assert verdict is not None
    assert verdict["status"] == "BLOCKED"
    assert verdict["blocking_dependency"] == "provider_unavailable"


def test_loop_continues_to_second_scheduled_action(tmp_path):
    # After the first step PASSes, the executive schedules the next action;
    # the loop picks it up and completes it without any owner prompting.
    database, repository, policy, router = mission_fixture(tmp_path)
    adapter = ScriptedAdapter(repository)
    loop = MissionLoop(database, router, adapter, policy=policy)

    first = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))
    assert first.outcome == PASSED
    assert adapter.attempts["builder"] == 1  # first step passed first try

    # Executive schedules the next requirement (direct projection update
    # simulates the scheduler; the ledger-based scheduling path is executive
    # layer work beyond this gate).
    with database.transaction() as connection:
        connection.execute(
            "UPDATE missions SET next_action = ? WHERE mission_id = 'mission-1'",
            ("deliver the second feature",))

    second = loop.run_until_settled(
        **step_kwargs(step_id="step-2"), commit_envelope=commit_envelope(repository))

    assert second.outcome == PASSED
    assert second.step_id == "step-2"
    assert (repository / "result.txt").read_text(encoding="utf-8") == "delivered\n"
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "3"
    third = loop.run_until_settled(
        **step_kwargs(step_id="step-2"), commit_envelope=commit_envelope(repository))
    assert third.outcome == WAITING_OUTCOME


def test_commit_governed_by_policy_denial_blocks(tmp_path):
    # Commit is an action: when the policy denies it (capability not ready),
    # the loop must NOT commit — it blocks precisely instead.
    database, repository, policy, router = mission_fixture(tmp_path)
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
    adapter = ScriptedAdapter(repository)
    loop = MissionLoop(database, router, adapter, policy=policy)

    outcome = loop.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    assert outcome.outcome == BLOCKED_OUTCOME
    assert "commit after PASS failed" in outcome.reason
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "1"
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "BLOCKED"


def test_f3_builder_killed_after_mutation_step_reclaimed_without_duplicates(tmp_path):
    """F3 (plan): builder killed after mutation, before result.

    The killed builder's durable aftermath: mission in EXECUTING, its run
    row RUNNING with an expired role lease, and partial work left in the
    worktree. The supervisor's deterministic recovery (reaper + mission
    resume) must make the step re-claimable by a FRESH loop instance: one
    fresh dispatch, one verified PASS, one commit, no duplicate events, and
    the partial work preserved in the committed artifact.
    """
    from jarvis_foundry.roles.reaper import (
        reap_stale_role_runs,
        recover_stale_mission_state,
        role_lease_key,
    )

    database, repository, policy, router = mission_fixture(tmp_path)

    # --- durable aftermath of the killed builder -------------------------
    # (exactly what a crashed dispatcher leaves: mission advanced to
    # EXECUTING, agent_run.started appended, role lease held, then death)
    for state, next_action in (("READY", "deliver feature X"),
                               ("EXECUTING", "deliver feature X")):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": "mission-1", "state": state, "next_action": next_action},
            source_type="supervisor", source_id="mission-loop",
            idempotency_key=f"mission.state_changed:mission-1:{state}:run-killed",
        ))
    database.append(Event.create(
        "agent_run.started",
        {"run_id": "run-killed", "mission_id": "mission-1", "step_id": "step-1",
         "role": "builder", "provider": "opencode_go", "model": "test-model",
         "context_manifest_hash": "0" * 64, "tool_policy": []},
        source_type="supervisor", source_id="role-dispatcher",
        idempotency_key="agent_run.started:run-killed:opencode_go",
    ))
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO leases(lease_key, owner_id, expires_at, heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            (role_lease_key("builder", "mission-1", "step-1"), "run-killed",
             "2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z"),
        )
    # partial mutation: the builder's unfinished work survives on disk
    (repository / "result.txt").write_text("partial\\n", encoding="utf-8")
    assert git_out("status", "--porcelain", cwd=repository) != ""

    # --- deterministic recovery ------------------------------------------
    reaped = reap_stale_role_runs(database)
    assert reaped == ["run-killed"]
    recovered = recover_stale_mission_state(database)
    assert recovered == ["mission-1"]
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    assert mission["next_action"] == "deliver feature X"

    # --- fresh loop instance re-claims the step --------------------------
    fresh = MissionLoop(database, router, ScriptedAdapter(repository), policy=policy)
    outcome = fresh.run_until_settled(
        **step_kwargs(), commit_envelope=commit_envelope(repository))

    assert outcome.outcome == PASSED
    assert outcome.step_id == "step-1"
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    # exactly one commit (baseline + the fresh worker's verified commit)
    assert git_out("rev-list", "--count", "HEAD", cwd=repository) == "2"
    # the partial work was preserved and finished deliberately
    assert (repository / "result.txt").exists()
    # exactly ONE completed BUILDER run for the step; the killed run FAILED.
    # (repair/verifier role runs for the step are legitimate and separate)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT run_id, status FROM agent_runs "
            "WHERE step_id = 'step-1' AND role = 'builder' ORDER BY run_id"
        ).fetchall()
    assert len(rows) == 2
    statuses = {r["run_id"]: r["status"] for r in rows}
    assert statuses["run-killed"] == "FAILED"
    fresh_builder_runs = [rid for rid, status in statuses.items()
                          if rid != "run-killed" and status == "COMPLETED"]
    assert len(fresh_builder_runs) == 1  # one fresh worker, one completion
    # no duplicate recovery events: exactly ONE session-loss resume recorded
    events = [e for e in database.events() if e.event_type == "mission.state_changed"]
    session_loss_resumes = [e for e in events
                            if e.payload.get("reason") == "resume_after_session_loss"
                            and e.payload.get("state") == "ACTION_SELECTION"]
    assert len(session_loss_resumes) == 1
    assert database.quick_check() == "ok"
