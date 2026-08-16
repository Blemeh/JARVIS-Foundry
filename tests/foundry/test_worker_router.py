"""Worker routing: deterministic, auditable, conservative (H6, H9, §8-9)."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.workers.contracts import (
    WORKER_HERMES,
    WORKER_OPENHANDS,
    WorkerStepContract,
)
from jarvis_foundry.workers.router import (
    NOT_READY,
    READY,
    VETOED,
    FALLBACK_TRIGGERS,
    RouteDecision,
    WorkerReadiness,
    WorkerRouter,
    classify_task,
)


def contract(tmp_path: Path, **signals) -> WorkerStepContract:
    return WorkerStepContract(
        run_id="run-1", mission_id="mission-1", step_id="step-1",
        attempt=1, role="builder", objective="o", instructions="i",
        working_root=tmp_path, task_signals=signals,
    )


def ready_router(tmp_path) -> WorkerRouter:
    router = WorkerRouter()
    router.mark_ready(
        WORKER_OPENHANDS, state=READY, note="containment proven in test",
        evidence=("test-containment-proof",))
    return router


def test_bounded_task_routes_to_openhands_when_ready(tmp_path):
    router = ready_router(tmp_path)
    step = contract(tmp_path, bounded=True, explicit_acceptance=True,
                    tests_available=True, narrow_scope=True, reversible=True)
    decision = router.route(step)
    assert decision.selected_worker == WORKER_OPENHANDS
    assert decision.task_classification == "bounded_implementation"
    assert decision.fallback == (WORKER_HERMES,)


def test_ambiguous_task_routes_to_hermes(tmp_path):
    router = ready_router(tmp_path)
    step = contract(tmp_path, ambiguous=True, root_cause_required=True)
    decision = router.route(step)
    assert decision.selected_worker == WORKER_HERMES
    assert decision.task_classification == "ambiguous_diagnosis"


def test_bounded_task_routes_to_hermes_when_openhands_not_ready(tmp_path):
    router = WorkerRouter()  # OpenHands NOT_READY by default
    step = contract(tmp_path, bounded=True, explicit_acceptance=True,
                    tests_available=True, narrow_scope=True)
    decision = router.route(step)
    assert decision.selected_worker == WORKER_HERMES
    assert decision.reason == ("bounded implementation but OpenHands not "
                               "ready; Hermes primary fallback")


def test_vetoed_worker_never_selected(tmp_path):
    router = WorkerRouter()
    router.mark_ready(WORKER_OPENHANDS, state=VETOED,
                      note="owner veto", evidence=("veto-1",))
    step = contract(tmp_path, bounded=True, explicit_acceptance=True,
                    tests_available=True, narrow_scope=True)
    decision = router.route(step)
    assert decision.selected_worker == WORKER_HERMES
    assert WORKER_OPENHANDS not in decision.eligible_workers


def test_no_eligible_worker_yields_honest_block(tmp_path):
    router = WorkerRouter()
    router.mark_ready(WORKER_HERMES, state=VETOED, note="veto",
                      evidence=("veto-hermes",))
    router.mark_ready(WORKER_OPENHANDS, state=NOT_READY, note="not ready",
                      evidence=())
    step = contract(tmp_path, bounded=True)
    decision = router.route(step)
    assert decision.selected_worker == ""
    assert "no eligible worker" in decision.reason


def test_deterministic_repeated_decision(tmp_path):
    router = ready_router(tmp_path)
    step = contract(tmp_path, bounded=True, explicit_acceptance=True,
                    tests_available=True, narrow_scope=True)
    first = router.route(step)
    second = router.route(step)
    assert first.selected_worker == second.selected_worker
    assert first.reason == second.reason
    assert first.evidence_considered == second.evidence_considered
    assert first.route_decision_id != second.route_decision_id


def test_route_decision_is_fully_auditable(tmp_path):
    router = ready_router(tmp_path)
    step = contract(tmp_path, bounded=True, explicit_acceptance=True,
                    tests_available=True, narrow_scope=True)
    decision = router.route(step)
    payload = decision.to_payload()
    rebuilt = RouteDecision.from_payload(payload)
    assert rebuilt == decision
    for key in ("route_decision_id", "eligible_workers", "selected_worker",
                "reason", "evidence_considered", "task_classification",
                "expected_latency", "expected_cost", "confidence",
                "fallback", "policy_version"):
        assert key in payload


def test_mandatory_fallback_triggers():
    router = WorkerRouter()
    for trigger in FALLBACK_TRIGGERS:
        assert router.requires_fallback(trigger)
    assert not router.requires_fallback("harmless_noise")


def test_classification_pure_function(tmp_path):
    step = contract(tmp_path, bounded=True, explicit_acceptance=True,
                    tests_available=True, narrow_scope=True)
    assert classify_task(step).classification == "bounded_implementation"
    ambiguous = contract(tmp_path, ambiguous=True)
    assert classify_task(ambiguous).classification == "ambiguous_diagnosis"
