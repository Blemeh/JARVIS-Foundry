"""Durable ledger events, projections, and idempotency tests."""

from __future__ import annotations

from r11_helpers import make_store

from jarvis_foundry.evolution_live.engine import EvolutionEngine


def test_ledger_chain_and_projection(tmp_path) -> None:
    store = make_store(tmp_path / "store")
    engine = EvolutionEngine(store)
    engine.observe(
        controller="supervisor",
        cycle_id="cycle-p",
        observation="observed",
        baseline_ref="baseline",
        baseline_hash="a" * 64,
    )
    engine.hypothesize(
        controller="supervisor",
        cycle_id="cycle-p",
        candidate_id="candidate-p",
        hypothesis="hypothesis",
        acceptance_criteria=["c"],
        risk_class="R1",
        surfaces=["probe/config/values.py"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["probe"]},
        authority_scope={"targets": ["probe"]},
    )
    snapshot = store.snapshot()
    state = snapshot.states["cycles"]["cycle-p"]
    assert state["risk_class"] == "R1"
    assert state["last_event"] == "evolution_live.hypothesized"
    report = store.ledger.verify_chain()
    assert report.valid
    assert report.sequence_count == 2


def test_waiting_is_idempotent_after_restart(tmp_path) -> None:
    store = make_store(tmp_path / "store")
    engine = EvolutionEngine(store)
    first = engine.waiting(
        controller="supervisor",
        cadence_key="cadence-a",
        reason="no useful improvement",
        next_action="wait",
    )
    assert first.created is True
    replay = EvolutionEngine(store).waiting(
        controller="supervisor",
        cadence_key="cadence-a",
        reason="no useful improvement",
        next_action="wait",
    )
    assert replay.created is False
    assert replay.event.event_id == first.event.event_id
    snapshot = store.snapshot()
    assert "cadence-a" in snapshot.states["waiting"]
