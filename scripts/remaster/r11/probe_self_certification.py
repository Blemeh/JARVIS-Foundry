"""Prove candidates cannot certify, promote, relax, widen, or erase anything."""

from __future__ import annotations

import sys
import time

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    TEMP_BASE,
    safe_clear,
    write_json,
)

from jarvis_foundry.evolution_live.engine import EvolutionEngine
from jarvis_foundry.evolution_live.probes import SelfCertificationProbes
from jarvis_foundry.evolution_live.store import EvolutionStore


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    root = TEMP_BASE / "probes"
    safe_clear(root)
    root.mkdir(parents=True, exist_ok=True)
    store = EvolutionStore(root / "store", fixed_utc=EVIDENCE_UTC)
    engine = EvolutionEngine(store)
    probes = SelfCertificationProbes(store, engine).run()
    chain = store.ledger.verify_chain()
    refused = [item for item in probes if item.get("refused") is True]
    all_refused = len(refused) == len(probes)
    payload = {
        "schema": "jarvis-r11-self-certification-probes-v1",
        "evidence_utc": EVIDENCE_UTC,
        "passed": all_refused,
        "probe_count": len(probes),
        "refused_count": len(refused),
        "probes": probes,
        "ledger": {
            "event_count": len(store.ledger.read()),
            "chain_valid": chain.valid,
            "head_hash": chain.head_hash,
        },
        "failed_history_preserved": store.failed_history_exists("probe-failed"),
        "evidence_file_preserved": (store.root / "stores" / "evidence-probe.txt").is_file(),
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    write_json(EVIDENCE_DIR / "R11_PROBES.json", payload)
    print(
        f"self-certification probes: passed={all_refused} "
        f"refused={len(refused)}/{len(probes)} chain_valid={chain.valid} "
        f"events={payload['ledger']['event_count']} "
        f"wall_seconds={payload['wall_seconds']}"
    )
    return 0 if all_refused else 1


if __name__ == "__main__":
    sys.exit(main())
