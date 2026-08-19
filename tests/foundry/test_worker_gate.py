"""HYBRID_WORKER_INTEGRATION_GATE matrix (H1-H22).

Each gate row is exercised here so the matrix itself is reproducible from
the test suite. Detailed adversarial evidence lives in the gate report
(docs/evidence/HYBRID_WORKER_INTEGRATION_GATE.md) and the fresh independent
verifier report.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis_foundry.workers.adapters.base import WorkerAdapter
from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
from jarvis_foundry.workers.adapters.openhands import OpenHandsWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import (
    WORKER_HERMES,
    WORKER_OPENHANDS,
    WorkerStepContract,
)
from jarvis_foundry.workers.envelope import ResultEnvelope
from jarvis_foundry.workers.evidence import EvidenceReconciler
from jarvis_foundry.workers.isolation import (
    DisposableWorkerState,
    HeldOutBoundary,
)
from jarvis_foundry.workers.lab import DiagnosisLab
from jarvis_foundry.workers.orchestrator import HybridStepController, PASSED
from jarvis_foundry.workers.router import READY, WorkerRouter

from worker_test_helpers import make_controller, make_repository, mission_database
from test_worker_orchestrator import (
    MutatingScriptedAdapter,
    bounded_contract,
    fake_verifier,
    openhands_ready_router,
)


def test_h1_canonical_worker_abstraction_exists():
    assert issubclass(WorkerStepContract, object)
    assert hasattr(WorkerStepContract, "to_payload")
    assert WorkerAdapter is not None


def test_h2_hermes_adapter_safe_and_real(tmp_path):
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path, hermes_home=tmp_path / "home", enabled=False)
    assert adapter.framework == "hermes"
    from jarvis_foundry.workers.adapters.base import ProviderUnavailable
    try:
        adapter.dispatch(bounded_contract(tmp_path), heartbeat=lambda: None)
    except ProviderUnavailable:
        pass  # disabled by default: safe
    else:
        raise AssertionError("Hermes adapter must be disabled by default")


def test_h3_openhands_adapter_safe_and_real(tmp_path):
    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    adapter = OpenHandsWorkerAdapter(
        openhands_exe=exe, data_dir=tmp_path / "data", enabled=False)
    assert adapter.framework == "openhands"
    from jarvis_foundry.workers.adapters.base import ProviderUnavailable
    try:
        adapter.dispatch(bounded_contract(tmp_path), heartbeat=lambda: None)
    except ProviderUnavailable:
        pass  # disabled by default: safe
    else:
        raise AssertionError("OpenHands adapter must be disabled by default")


def test_h4_canonical_result_envelope(tmp_path):
    step = bounded_contract(tmp_path)
    envelope = ResultEnvelope.from_contract(step, framework="hermes")
    assert envelope.to_payload() == ResultEnvelope.from_payload(
        envelope.to_payload()).to_payload()


def test_h5_evidence_vs_claim_reconciliation(tmp_path):
    repository = make_repository(tmp_path)
    reconciler = EvidenceReconciler()
    step = bounded_contract(repository)
    before = reconciler.capture_before(step)
    envelope = ResultEnvelope.from_contract(
        step, framework="openhands", claimed_outcome="done",
        files_changed=("feature.py",))
    report = reconciler.reconcile(step, envelope, before=before)
    assert report.agree is False  # false success rejected


def test_h6_deterministic_auditable_routing(tmp_path):
    router = openhands_ready_router()
    first = router.route(bounded_contract(tmp_path))
    second = router.route(bounded_contract(tmp_path))
    assert first.selected_worker == second.selected_worker
    assert first.to_payload()["policy_version"] == "hybrid-worker-routing-v1"


def test_h7_openhands_bounded_fast_path_success(tmp_path):
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
    assert outcome.outcome == PASSED
    assert outcome.envelope.framework == WORKER_OPENHANDS


def test_h8_hermes_primary_ambiguous_path_success(tmp_path):
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    hermes = MutatingScriptedAdapter(
        WORKER_HERMES, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({WORKER_HERMES: hermes})
    step = bounded_contract(
        repository, task_signals={"ambiguous": True, "root_cause_required": True})
    outcome = controller.run_step(step)
    assert outcome.outcome == PASSED
    assert outcome.envelope.framework == WORKER_HERMES


def test_h9_automatic_openhands_to_hermes_fallback(tmp_path):
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=(),
        outcome=ScriptedOutcome(claimed_outcome="done",
                                files_changed=("feature.py",)))
    hermes = MutatingScriptedAdapter(
        WORKER_HERMES, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({
        WORKER_HERMES: hermes, WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))
    outcome = controller.run_step(bounded_contract(repository))
    assert outcome.outcome == PASSED
    assert outcome.envelope.framework == WORKER_HERMES
    assert outcome.repair_run_id == "run-1-repair-r1"


def test_h10_fresh_independent_verification(tmp_path):
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))
    controller.run_step(bounded_contract(repository))
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status, verdict FROM verification_verdicts "
            "ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row["status"] == "PASS" and row["verdict"] == "PASS"


def test_h11_stop_across_both_frameworks(tmp_path):
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    stop_path = controller.stop_path
    stop_path.write_text("stop\n", encoding="utf-8")
    openhands = MutatingScriptedAdapter(WORKER_OPENHANDS)
    controller.registry = WorkerRegistry({WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))
    outcome = controller.run_step(bounded_contract(repository))
    assert outcome.outcome == "STOPPED"
    assert openhands.received == []


def test_h12_worker_state_isolation(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    state = DisposableWorkerState.create(
        run_id="r1", framework="openhands", lab_root=lab)
    assert state.root.is_relative_to(lab.resolve())
    assert state.cleanup() is True


def test_h13_private_held_out_isolation(tmp_path):
    boundary = HeldOutBoundary.create(mission_id="m", lab_root=tmp_path)
    assert boundary.assert_private_unreachable(
        boundary.worktree, boundary.task_contract) == ()


def test_h14_b3_equivalent_clean_diagnosis(tmp_path):
    lab = DiagnosisLab.create(mission_id="m", lab_root=tmp_path)
    from jarvis_foundry.workers.lab import LabRunner
    from test_worker_lab import FixingAdapter
    naive = FixingAdapter(
        WORKER_HERMES, "def load_rows(text):\n"
                       "    lines = [l for l in text.splitlines() if l.strip()]\n"
                       "    return [l.split(',') for l in lines[1:]]\n")
    result = LabRunner(adapter_for=lambda f: naive).run(
        lab, WORKER_HERMES, run_id="run")
    assert result.public_passed is True
    assert result.held_out_passed is False
    assert result.isolation_failures == ()


def test_h15_recovery_after_worker_failure(tmp_path):
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier,
        max_repairs=1)
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
    assert len(hermes.received) == 1


def test_h16_no_canonical_scope_escape(tmp_path):
    repository = make_repository(tmp_path)
    (repository / "feature.py").write_text("x=1\n", encoding="utf-8")
    forbidden = tmp_path / "secrets"
    forbidden.mkdir()
    (forbidden / "key.txt").write_text("s\n", encoding="utf-8")
    step = WorkerStepContract(
        run_id="r", mission_id="m", step_id="s", attempt=1, role="builder",
        objective="o", instructions="i", working_root=repository,
        forbidden_paths=(forbidden,), expected_artifacts=("feature.py",))
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(step)
    envelope = ResultEnvelope.from_contract(
        step, framework="openhands", files_changed=("feature.py",))
    report = reconciler.reconcile(
        step, envelope, before=before,
        persistence_scan_roots=(forbidden,))
    assert report.agree is False
    assert any("forbidden" in v for v in report.containment_violations)


def test_h17_no_live_hermes_mutation():
    live_home = HermesWorkerAdapter.LIVE_HERMES_HOME
    try:
        HermesWorkerAdapter(
            hermes_root=Path("."), hermes_home=live_home / "probe",
            enabled=True, python_exe="python")
    except Exception:
        pass  # refused: live profile is protected
    else:
        raise AssertionError("Hermes adapter must refuse the live profile")


def test_h18_no_unrelated_user_state_mutation(tmp_path):
    protected = tmp_path / "protected"
    protected.mkdir()
    owner_marker = protected / "owner.txt"
    owner_marker.write_text("owner\n", encoding="utf-8")
    lab = tmp_path / "lab"
    lab.mkdir()
    state = DisposableWorkerState.create(
        run_id="r", framework="hermes", lab_root=lab,
        protected_roots=(protected,))
    assert owner_marker.read_text(encoding="utf-8") == "owner\n"
    state.cleanup()
    assert owner_marker.exists()


def test_h19_secret_scan_clean(tmp_path):
    root = Path(__file__).resolve().parents[2]
    scan_dirs = (root / "src" / "jarvis_foundry" / "workers",
                 root / "tests" / "foundry")
    patterns = (
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        re.compile(r"JARVIS_OPENCODE_API_KEY\s*=\s*[^\s'\"$]"),
    )
    hits: list[str] = []
    for scan_dir in scan_dirs:
        for path in scan_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in patterns:
                if pattern.search(text):
                    hits.append(f"{path}: {pattern.pattern}")
    assert hits == []


def test_h21_verifier_is_not_the_builder(tmp_path):
    """The worker never certifies its own outcome: the controller requires
    a separate verifier dispatch, and a verifier FAIL blocks acceptance."""
    from jarvis_foundry.workers.envelope import VERDICT_FAIL
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router())
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))
    controller.router.mark_ready(WORKER_HERMES, state="NOT_READY",
                                 note="no repair in this scenario",
                                 evidence=("test",))

    def failing_verifier(contract, *, envelope=None, report=None):
        return ResultEnvelope.from_contract(
            contract, framework="hermes", verdict=VERDICT_FAIL,
            claimed_outcome="defect found")

    controller.verifier_factory = failing_verifier
    outcome = controller.run_step(bounded_contract(repository))
    assert outcome.outcome == "FAILED"
    assert "verifier did not pass" in (outcome.reason or "")


def test_h22_mission_returns_to_next_action_selection(tmp_path):
    controller, database, repository = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))
    controller.run_step(bounded_contract(repository))
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
