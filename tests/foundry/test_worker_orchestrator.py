"""Hybrid control loop: automatic success, failure, fallback, STOP (H7-H11,
H15, H18, H22; §15-17)."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES, WORKER_OPENHANDS, WorkerStepContract
from jarvis_foundry.workers.envelope import COMPLETED, VERDICT_FAIL, ResultEnvelope
from jarvis_foundry.workers.result_contract import RESULT_FILENAME
from jarvis_foundry.workers.router import READY, WorkerRouter

from worker_test_helpers import make_controller, mission_database


class MutatingScriptedAdapter(ScriptedWorkerAdapter):
    """Scripted adapter that really mutates the worktree (observable state)."""

    def __init__(self, framework, *, write_files=(), content=None,
                 outcome=None, fail_provider=False, on_dispatch=None,
                 outcomes_by_run=None):
        super().__init__(
            framework, outcome=outcome, fail_provider=fail_provider,
            outcomes_by_run=outcomes_by_run)
        self.write_files = tuple(write_files)
        self.content = content
        self.on_dispatch = on_dispatch

    def dispatch(self, contract, *, heartbeat):
        if self.on_dispatch is not None:
            self.on_dispatch(contract)
        for rel in self.write_files:
            path = contract.working_root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                self.content or f"content of {rel}\n", encoding="utf-8")
        return super().dispatch(contract, heartbeat=heartbeat)


class CrashAfterWriteAdapter(MutatingScriptedAdapter):
    """Mutates the worktree, then dies like a crashed worker process."""

    def dispatch(self, contract, *, heartbeat):
        for rel in self.write_files:
            path = contract.working_root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("partial mutation\n", encoding="utf-8")
        raise RuntimeError("worker process killed mid-run")


def bounded_contract(repository: Path, **overrides) -> WorkerStepContract:
    fields = {
        "run_id": "run-1",
        "mission_id": "mission-1",
        "step_id": "step-1",
        "attempt": 1,
        "role": "builder",
        "objective": "implement feature.py",
        "instructions": "implement feature.py and run the tests",
        "working_root": repository,
        "task_signals": {"bounded": True, "explicit_acceptance": True,
                         "tests_available": True, "narrow_scope": True,
                         "reversible": True},
        "expected_artifacts": ("feature.py",),
        "verification_criteria": ("feature.py exists",),
    }
    fields.update(overrides)
    return WorkerStepContract(**fields)


def fake_verifier(contract, *, envelope=None, report=None):
    verdict = "PASS" if report.agree else "FAIL"
    return ResultEnvelope.from_contract(
        contract, framework="hermes", verdict=verdict,
        claimed_outcome=f"verifier judged {verdict}")


def openhands_ready_router() -> WorkerRouter:
    router = WorkerRouter()
    router.mark_ready(WORKER_OPENHANDS, state=READY,
                      note="containment proven in test",
                      evidence=("test-containment-proof",))
    return router


def test_runner_contract_requires_machine_result_file(tmp_path):
    """The controller must append the machine-ingestable result-file
    requirement to provider instructions; a provider that omits it stays
    NOT_TESTED instead of inheriting JARVIS-observed diffs into its
    claim."""
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))
    step = bounded_contract(repository)
    outcome = controller.run_step(step)
    assert outcome.outcome == "PASSED"
    assert RESULT_FILENAME in openhands.received[0].instructions


def test_openhands_bounded_fast_path_success_returns_to_next_action(tmp_path):
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    hermes = MutatingScriptedAdapter(WORKER_HERMES)
    controller.registry = WorkerRegistry({
        WORKER_HERMES: hermes, WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))

    step = bounded_contract(repository)
    outcome = controller.run_step(step)

    assert outcome.outcome == "PASSED"
    assert outcome.envelope.framework == WORKER_OPENHANDS
    assert outcome.verdict_id is not None
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    events = {e.event_type for e in database.events()}
    assert {"worker.route_decided", "worker.run_recorded",
            "worker.evidence_reconciled", "verification.passed"} <= events


def test_ambiguous_task_routes_to_hermes_and_passes(tmp_path):
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    hermes = MutatingScriptedAdapter(
        WORKER_HERMES, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({WORKER_HERMES: hermes})

    step = bounded_contract(
        repository, run_id="run-amb",
        task_signals={"ambiguous": True, "root_cause_required": True})
    outcome = controller.run_step(step)
    assert outcome.outcome == "PASSED"
    assert outcome.envelope.framework == WORKER_HERMES
    assert hermes.received[0].run_id == "run-amb"


def test_openhands_false_success_falls_back_to_hermes_repair_and_passes(tmp_path):
    """§15 strongest proof: OpenHands claims success, evidence refutes it,
    JARVIS does not advance, Hermes repairs, fresh verifier passes."""
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=(),  # claims success, changes nothing
        outcome=ScriptedOutcome(
            claimed_outcome="implemented feature.py",
            files_changed=("feature.py",)))
    hermes = MutatingScriptedAdapter(
        WORKER_HERMES, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({
        WORKER_HERMES: hermes, WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))

    step = bounded_contract(repository)
    outcome = controller.run_step(step)

    assert outcome.outcome == "PASSED"
    assert outcome.envelope.framework == WORKER_HERMES
    assert outcome.repair_run_id == "run-1-repair-r1"
    assert openhands.received[0].run_id == "run-1"
    assert hermes.received[0].run_id == "run-1-repair-r1"
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    events = {e.event_type for e in database.events()}
    assert "worker.fallback_triggered" in events
    # No PASS was recorded for the failed OpenHands run.
    failed_verdicts = [e for e in database.events()
                       if e.event_type == "verification.failed"]
    assert len(failed_verdicts) == 1


def test_repair_budget_exhaustion_blocks(tmp_path):
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier,
        max_repairs=2)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=(),
        outcome=ScriptedOutcome(claimed_outcome="done",
                                files_changed=("feature.py",)))
    hermes = MutatingScriptedAdapter(
        WORKER_HERMES, write_files=(),
        outcome=ScriptedOutcome(claimed_outcome="done",
                                files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({
        WORKER_HERMES: hermes, WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))

    outcome = controller.run_step(bounded_contract(repository))
    assert outcome.outcome == "BLOCKED"
    assert "repair budget exhausted" in (outcome.reason or "")
    assert len(hermes.received) == 2  # both repair attempts ran
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "BLOCKED"


def test_stop_blocks_dispatch_before_any_worker(tmp_path):
    controller, database, repository = make_controller(tmp_path)
    hermes = MutatingScriptedAdapter(WORKER_HERMES)
    controller.registry = WorkerRegistry({WORKER_HERMES: hermes})
    stop_path = controller.stop_path
    stop_path.write_text("stop\n", encoding="utf-8")

    outcome = controller.run_step(bounded_contract(repository))
    assert outcome.outcome == "STOPPED"
    assert hermes.received == []
    events = {e.event_type for e in database.events()}
    assert "worker.stop_blocked" in events


def test_stop_cancels_queued_fallback(tmp_path):
    """STOP appearing after the OpenHands failure cancels the Hermes repair."""
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    stop_path = controller.stop_path

    def raise_stop(_contract):
        stop_path.write_text("stop\n", encoding="utf-8")

    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=(), on_dispatch=raise_stop,
        outcome=ScriptedOutcome(claimed_outcome="done",
                                files_changed=("feature.py",)))
    hermes = MutatingScriptedAdapter(WORKER_HERMES)
    controller.registry = WorkerRegistry({
        WORKER_HERMES: hermes, WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))

    outcome = controller.run_step(bounded_contract(repository))
    # The repair loop must never dispatch Hermes after STOP appears.
    assert outcome.outcome == "STOPPED"
    assert hermes.received == []
    events = {e.event_type for e in database.events()}
    assert "worker.stop_blocked" in events


def test_provider_unavailable_blocks_honestly(tmp_path):
    controller, database, repository = make_controller(
        tmp_path, verifier_factory=fake_verifier)
    hermes = MutatingScriptedAdapter(WORKER_HERMES, fail_provider=True)
    controller.registry = WorkerRegistry({WORKER_HERMES: hermes})

    outcome = controller.run_step(bounded_contract(repository))
    assert outcome.outcome == "BLOCKED"
    assert "provider unavailable" in (outcome.reason or "")


def test_worker_claim_never_certifies_itself(tmp_path):
    """A PASS-claiming worker with agreeing evidence still needs the
    independent verifier; verifier FAIL -> no acceptance."""
    def failing_verifier(contract, *, envelope=None, report=None):
        return ResultEnvelope.from_contract(
            contract, framework="hermes", verdict="FAIL",
            claimed_outcome="verifier found a defect")

    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(),
        verifier_factory=failing_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    hermes = MutatingScriptedAdapter(WORKER_HERMES)
    controller.registry = WorkerRegistry({
        WORKER_HERMES: hermes, WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))
    # No eligible repair path: the controller must refuse acceptance and
    # block rather than let the worker certify itself.
    controller.router.mark_ready(WORKER_HERMES, state="NOT_READY",
                                 note="no hermes in this scenario",
                                 evidence=("test",))

    outcome = controller.run_step(bounded_contract(repository))
    assert outcome.outcome == "FAILED"
    assert "verifier did not pass" in (outcome.reason or "")
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "BLOCKED"
    passed = [e for e in database.events()
              if e.event_type == "verification.passed"]
    assert passed == []


def test_route_evidence_and_verdict_durable(tmp_path):
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))

    outcome = controller.run_step(bounded_contract(repository))
    with database.connect() as connection:
        verdict = connection.execute(
            "SELECT status, verdict FROM verification_verdicts WHERE "
            "mission_id = 'mission-1' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert verdict["status"] == "PASS"
    assert verdict["verdict"] == "PASS"


def test_worker_crash_after_mutation_recovers_via_hermes(tmp_path):
    """Path D: OpenHands mutates the worktree then crashes. JARVIS must
    capture the partial state, refuse success, and recover via Hermes."""
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    openhands = CrashAfterWriteAdapter(WORKER_OPENHANDS, write_files=("feature.py",))
    hermes = MutatingScriptedAdapter(
        WORKER_HERMES, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({
        WORKER_HERMES: hermes, WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))

    outcome = controller.run_step(bounded_contract(repository))

    assert outcome.outcome == "PASSED"
    assert outcome.envelope.framework == WORKER_HERMES
    assert outcome.repair_run_id == "run-1-repair-r1"
    events = {e.event_type for e in database.events()}
    assert "worker.crashed" in events
    crash_event = next(e for e in database.events()
                       if e.event_type == "worker.crashed")
    assert crash_event.payload["partial_state"]["changed_files"] == ["feature.py"]
    assert crash_event.payload["error_class"] == "RuntimeError"
    # The partial mutation must be visible in the worktree for the repair.
    assert (repository / "feature.py").exists()
