"""Simulated restart resumes without duplicate events or effects."""

from __future__ import annotations

from r11_helpers import run_mini_cycle

from jarvis_foundry.evolution_live.engine import EvolutionEngine


def test_restart_after_isolate_does_not_duplicate_effects(tmp_path) -> None:
    store, record = run_mini_cycle(tmp_path, "c4", restart_after="isolate")
    restart = record["restart"]
    assert restart["simulated"] is True
    assert restart["clone_effect_status_after_restart"] == "replayed"
    assert restart["clone_replayed_without_duplicate_effect"] is True
    events = store.ledger.read()
    event_ids = [event.event_id for event in events]
    assert len(event_ids) == len(set(event_ids))
    chain = store.ledger.verify_chain()
    assert chain.valid

    waiting = EvolutionEngine(store).waiting(
        controller="supervisor",
        cadence_key="restart-test",
        reason="no useful improvement",
        next_action="wait",
    )
    replay = EvolutionEngine(store).waiting(
        controller="supervisor",
        cadence_key="restart-test",
        reason="no useful improvement",
        next_action="wait",
    )
    assert waiting.created is True
    assert replay.created is False
