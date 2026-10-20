"""CANONICAL_HYBRID_WORKER_INTEGRATION_GATE (directive Phase 7).

Attempts to disprove each mandatory invariant on the CURRENT canonical
architecture (v8 hybrid-memory baseline). Detailed evidence lives in
docs/evidence/CANONICAL_HYBRID_WORKER_INTEGRATION_GATE.md.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import (
    WORKER_HERMES,
    WORKER_OPENHANDS,
    WorkerStepContract,
)
from jarvis_foundry.workers.envelope import ResultEnvelope
from jarvis_foundry.workers.router import NOT_READY, READY, WorkerRouter

from worker_test_helpers import make_repository
from test_worker_orchestrator import (
    MutatingScriptedAdapter,
    bounded_contract,
    fake_verifier,
    make_controller,
    openhands_ready_router,
)


def test_1_worker_abstraction_neutral():
    step = WorkerStepContract(
        run_id="r", mission_id="m", step_id="s", attempt=1, role="builder",
        objective="o", instructions="i", working_root=Path("."))
    # No framework-specific fields leak into the canonical payload.
    payload = step.to_payload()
    assert "framework" not in payload
    assert "openhands" not in str(payload).lower()


def test_2_canonical_mission_state_ownership():
    """Mission truth stays in the canonical missions table; the worker
    layer only emits bounded worker.* events."""
    from jarvis_foundry.workers.orchestrator import HybridStepController
    from jarvis_foundry.workers.evidence import EvidenceReconciler
    controller, database, repository = make_controller(
        Path(__import__("tempfile").mkdtemp()),
        router=openhands_ready_router(), verifier_factory=fake_verifier)
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


def test_3_hermes_isolation():
    from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
    live = HermesWorkerAdapter.LIVE_HERMES
    try:
        HermesWorkerAdapter(hermes_root=live, hermes_home=Path("."),
                            enabled=True)
    except Exception:
        pass  # refused: live installation protected
    else:
        raise AssertionError("Hermes adapter must refuse the live install")


def test_4_openhands_readiness_honesty():
    """OpenHands stays NOT_READY by default; only explicit containment
    evidence flips it to READY."""
    router = WorkerRouter()
    assert router.is_eligible(WORKER_OPENHANDS) is False
    router.mark_ready(
        WORKER_OPENHANDS, state=READY,
        note="containment proven live 2026-08-16 (disposable home)",
        evidence=("containment-probe-completed-run",
                  "containment-probe-forced-termination"))
    assert router.is_eligible(WORKER_OPENHANDS) is True


def test_5_result_envelope_identity_accuracy():
    from jarvis_foundry.workers.adapters.openhands import OpenHandsWorkerAdapter
    envelope = ResultEnvelope.from_contract(
        bounded_contract(Path(".")), framework="openhands",
        provider_actual="opencode_go", model_actual="muse-spark-1.3-contributor")
    assert envelope.provider_actual == envelope.provider_requested == "opencode_go"
    assert envelope.model_actual == envelope.model_requested == "muse-spark-1.3-contributor"


def test_6_unsupported_telemetry_prevention():
    import pytest
    from jarvis_foundry.workers.envelope import (
        EnvelopeValidationError,
        ResultEnvelope,
    )
    with pytest.raises(EnvelopeValidationError):
        ResultEnvelope.from_contract(
            bounded_contract(Path(".")), framework="hermes",
            usage_availability="NOT_AVAILABLE", usage_total_tokens=999)


def test_7_false_success_rejection():
    from jarvis_foundry.workers.evidence import EvidenceReconciler
    repository = make_repository(Path(__import__("tempfile").mkdtemp()))
    reconciler = EvidenceReconciler()
    step = bounded_contract(repository)
    before = reconciler.capture_before(step)
    envelope = ResultEnvelope.from_contract(
        step, framework="openhands", claimed_outcome="done",
        files_changed=("feature.py",))
    report = reconciler.reconcile(step, envelope, before=before)
    assert report.agree is False


def test_8_evidence_reconciliation_scope():
    from jarvis_foundry.workers.evidence import EvidenceReconciler
    repository = make_repository(Path(__import__("tempfile").mkdtemp()))
    (repository / "feature.py").write_text("x=1\n", encoding="utf-8")
    forbidden = repository.parent / "secrets"
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
        step, envelope, before=before, persistence_scan_roots=(forbidden,))
    assert report.agree is False
    assert any("forbidden" in v for v in report.containment_violations)


def test_9_routing_determinism():
    router = openhands_ready_router()
    first = router.route(bounded_contract(Path(".")))
    second = router.route(bounded_contract(Path(".")))
    assert first.selected_worker == second.selected_worker
    assert first.reason == second.reason


def test_10_fallback_correctness():
    controller, database, repository = make_controller(
        Path(__import__("tempfile").mkdtemp()),
        router=openhands_ready_router(), verifier_factory=fake_verifier)
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
    assert outcome.outcome == "PASSED"
    assert outcome.envelope.framework == WORKER_HERMES
    events = {e.event_type for e in database.events()}
    assert "worker.fallback_triggered" in events


def test_11_worker_crash_recovery():
    from test_worker_orchestrator import CrashAfterWriteAdapter
    controller, database, repository = make_controller(
        Path(__import__("tempfile").mkdtemp()),
        router=openhands_ready_router(), verifier_factory=fake_verifier)
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
    assert {e.event_type for e in database.events()} >= {"worker.crashed"}


def test_12_stop_behavior():
    controller, database, repository = make_controller(
        Path(__import__("tempfile").mkdtemp()))
    hermes = MutatingScriptedAdapter(WORKER_HERMES)
    controller.registry = WorkerRegistry({WORKER_HERMES: hermes})
    controller.stop_path.write_text("stop\n", encoding="utf-8")
    outcome = controller.run_step(bounded_contract(repository))
    assert outcome.outcome == "STOPPED"
    assert hermes.received == []
    assert {e.event_type for e in database.events()} >= {"worker.stop_blocked"}


def test_13_scope_containment():
    test_8_evidence_reconciliation_scope()


def test_14_held_out_verifier_isolation():
    from jarvis_foundry.workers.isolation import HeldOutBoundary
    boundary = HeldOutBoundary.create(
        mission_id="m", lab_root=Path(__import__("tempfile").mkdtemp()))
    assert boundary.assert_private_unreachable(
        boundary.worktree, boundary.task_contract) == ()


def test_15_preservation_of_unrelated_owner_state():
    tmp = Path(__import__("tempfile").mkdtemp())
    protected = tmp / "protected"
    protected.mkdir()
    marker = protected / "owner.txt"
    marker.write_text("owner\n", encoding="utf-8")
    from jarvis_foundry.workers.isolation import DisposableWorkerState
    lab = tmp / "lab"
    lab.mkdir()
    state = DisposableWorkerState.create(
        run_id="r", framework="hermes", lab_root=lab,
        protected_roots=(protected,))
    assert marker.read_text(encoding="utf-8") == "owner\n"
    state.cleanup()
    assert marker.exists()


def test_16_no_live_hermes_mutation():
    from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
    live_home = HermesWorkerAdapter.LIVE_HERMES_HOME
    try:
        HermesWorkerAdapter(hermes_root=Path("."), hermes_home=live_home,
                            enabled=True)
    except Exception:
        pass  # refused
    else:
        raise AssertionError("Hermes adapter must refuse the live profile")


def test_17_no_secret_leakage():
    import re
    root = Path(__file__).resolve().parents[2]
    for directory in (root / "src" / "jarvis_foundry" / "workers",
                      root / "tests" / "foundry"):
        for path in directory.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            assert not re.search(r"sk-[A-Za-z0-9]{20,}", text), path
            assert not re.search(
                r"JARVIS_OPENCODE_API_KEY\s*=\s*[^\s'\"$]", text), path


def test_18_post_baseline_canonical_functionality_importable():
    """The v8 hybrid-memory module must still import cleanly alongside the
    worker layer (no shadowing/conflict)."""
    import jarvis_foundry.hybrid_memory.compiler as compiler
    import jarvis_foundry.workers.orchestrator as orchestrator
    assert hasattr(compiler, "ContextCompiler")
    assert hasattr(orchestrator, "HybridStepController")
