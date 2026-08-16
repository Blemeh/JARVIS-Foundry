"""Shared helpers for the R11 regression suite (no top-level conftest)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "remaster" / "r11"))

from run_cycles import _specs, prepare_baseline_repo, run_single_cycle

from jarvis_foundry.evolution_live.engine import EvolutionEngine
from jarvis_foundry.evolution_live.store import EvolutionStore

EVIDENCE_UTC = "2026-08-16T18:00:00Z"


def make_store(root: Path) -> EvolutionStore:
    return EvolutionStore(root, fixed_utc=EVIDENCE_UTC)


def spec_by_id(spec_id: str) -> dict[str, object]:
    spec = next(item for item in _specs() if item["id"] == spec_id)
    return dict(spec)


def run_mini_cycle(
    root: Path,
    spec_id: str,
    *,
    restart_after: str | None = None,
) -> tuple[EvolutionStore, dict[str, object]]:
    store = make_store(root / "store")
    engine = EvolutionEngine(store)
    baseline = prepare_baseline_repo(root)
    record = run_single_cycle(
        engine,
        store,
        spec_by_id(spec_id),
        baseline,
        sys.executable,
        EVIDENCE_UTC,
        restart_after=restart_after,
    )
    return store, record
