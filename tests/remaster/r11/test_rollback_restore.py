"""Rollback and restore restore real before/after release hashes."""

from __future__ import annotations

from r11_helpers import run_mini_cycle

from jarvis_foundry.evolution_live.engine import EvolutionEngine
from jarvis_foundry.evolution_live.store import copy_tree, tree_hash


def test_rollback_restore_hashes_and_metrics(tmp_path) -> None:
    store, record = run_mini_cycle(tmp_path, "c1")
    engine = EvolutionEngine(store)
    cycle_id = str(record["cycle_id"])
    candidate_release = str(record["promotion"]["release_id"])
    prior_release = str(record["promotion"]["prior_release_id"])

    before = tree_hash(store.live_dir)
    copy_tree(store.release_dir(prior_release), store.live_dir)
    after = tree_hash(store.live_dir)
    assert before != after
    engine.rollback(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=str(record["candidate_id"]),
        release_id=candidate_release,
        prior_release_id=prior_release,
        before_hash=before,
        after_hash=after,
    )
    assert after == store.load_release(prior_release).tree_hash

    before = tree_hash(store.live_dir)
    copy_tree(store.release_dir(candidate_release), store.live_dir)
    after = tree_hash(store.live_dir)
    engine.restore(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=str(record["candidate_id"]),
        release_id=candidate_release,
        before_hash=before,
        after_hash=after,
    )
    assert after == store.load_release(candidate_release).tree_hash
    chain = store.ledger.verify_chain()
    assert chain.valid
