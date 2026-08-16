"""Supervisor ownership, frozen guards, and lifecycle machine tests."""

from __future__ import annotations

import pytest
from r11_helpers import make_store

from jarvis_foundry.evolution_live.engine import EvolutionEngine
from jarvis_foundry.evolution_live.lifecycle import (
    EvolutionControlError,
    LifecycleError,
    LifecycleMachine,
)


def _cycle(engine: EvolutionEngine, cycle_id: str) -> None:
    engine.observe(
        controller="supervisor",
        cycle_id=cycle_id,
        observation="test observation",
        baseline_ref="test baseline",
        baseline_hash="a" * 64,
    )
    engine.hypothesize(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id="candidate-test",
        hypothesis="test hypothesis",
        acceptance_criteria=["original criterion"],
        risk_class="R2",
        surfaces=["probe/workflow.py"],
        budget={"max_cost_usd": 1.0, "capability_scope": ["probe/a"]},
        authority_scope={"targets": ["probe/a"]},
    )


def test_lifecycle_machine_order(tmp_path) -> None:
    machine = LifecycleMachine()
    machine.assert_transition("evolution_live.observed")
    machine = machine.next_milestone("evolution_live.observed")
    machine.assert_transition("evolution_live.hypothesized")
    with pytest.raises(LifecycleError):
        machine.assert_transition("evolution_live.promoted")


def test_candidate_identity_refused_for_effectful_operations(tmp_path) -> None:
    store = make_store(tmp_path / "store")
    engine = EvolutionEngine(store)
    with pytest.raises(EvolutionControlError):
        engine.observe(
            controller="candidate-self",
            cycle_id="cycle-x",
            observation="no",
            baseline_ref="no",
            baseline_hash="a" * 64,
        )
    _cycle(engine, "cycle-x")
    with pytest.raises(EvolutionControlError):
        engine.promote(
            controller="candidate-self",
            cycle_id="cycle-x",
            candidate_id="candidate-test",
            release_id="r",
            live_before_hash="b" * 64,
            live_after_hash="c" * 64,
            artifact_hashes={"probe": "d" * 64},
            outcome_before={"pytest_passed": 0, "pytest_failed": 1},
            outcome_after={"pytest_passed": 1, "pytest_failed": 0},
        )
    with pytest.raises(EvolutionControlError):
        engine.rollback(
            controller="candidate-self",
            cycle_id="cycle-x",
            candidate_id="candidate-test",
            release_id="r",
            prior_release_id="p",
            before_hash="b" * 64,
            after_hash="c" * 64,
        )
    with pytest.raises(EvolutionControlError):
        engine.restore(
            controller="candidate-self",
            cycle_id="cycle-x",
            candidate_id="candidate-test",
            release_id="r",
            before_hash="b" * 64,
            after_hash="c" * 64,
        )


def test_risk_criteria_authority_budget_are_frozen(tmp_path) -> None:
    store = make_store(tmp_path / "store")
    engine = EvolutionEngine(store)
    _cycle(engine, "cycle-frozen")
    with pytest.raises(LifecycleError):
        engine.change_risk(controller="supervisor", cycle_id="cycle-frozen", new_risk="R1")
    with pytest.raises(LifecycleError):
        engine.attempt_criteria_rewrite(
            controller="supervisor",
            cycle_id="cycle-frozen",
            criteria=["replacement"],
        )
    with pytest.raises(LifecycleError):
        engine.attempt_authority_widen(
            controller="supervisor",
            cycle_id="cycle-frozen",
            authority_scope={"targets": ["probe/b"]},
        )
    with pytest.raises(LifecycleError):
        engine.attempt_budget_increase(
            controller="supervisor",
            cycle_id="cycle-frozen",
            budget={"max_cost_usd": 999.0},
        )
    with pytest.raises(EvolutionControlError):
        engine.attempt_disable_stop(controller="candidate-self")


def test_candidate_cannot_certify_itself(tmp_path) -> None:
    store = make_store(tmp_path / "store")
    engine = EvolutionEngine(store)
    _cycle(engine, "cycle-cert")
    with pytest.raises(EvolutionControlError):
        engine.record_verification(
            controller="candidate-test",
            cycle_id="cycle-cert",
            candidate_id="candidate-test",
            verifier_id="candidate-test",
            verdict="PASS",
            evidence_refs=["probe"],
            verification_request_hash="a" * 64,
        )
    with pytest.raises(LifecycleError):
        engine.record_verification(
            controller="supervisor",
            cycle_id="cycle-cert",
            candidate_id="candidate-test",
            verifier_id="candidate-test",
            verdict="PASS",
            evidence_refs=["probe"],
            verification_request_hash="a" * 64,
        )
