"""Self-certification refusal probes are real, durable, and fail closed."""

from __future__ import annotations

from r11_helpers import make_store

from jarvis_foundry.evolution_live.engine import EvolutionEngine
from jarvis_foundry.evolution_live.probes import SelfCertificationProbes


def test_all_self_certification_probes_refuse(tmp_path) -> None:
    store = make_store(tmp_path / "store")
    engine = EvolutionEngine(store)
    results = SelfCertificationProbes(store, engine).run()
    refused = [item for item in results if item.get("refused") is True]
    assert len(refused) == len(results)
    assert len({item["probe_id"] for item in results}) == len(results)
    assert store.failed_history_exists("probe-failed")
    assert (store.root / "stores" / "evidence-probe.txt").is_file()
    chain = store.ledger.verify_chain()
    assert chain.valid
    refusal_events = [
        event for event in store.ledger.read() if event.event_type == "evolution_live.refused"
    ]
    assert len(refusal_events) == len(results)
