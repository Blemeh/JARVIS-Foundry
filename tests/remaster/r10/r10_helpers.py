"""Shared helpers for the R10 regression suite (no top-level conftest)."""

from __future__ import annotations

from pathlib import Path

from jarvis_foundry.command_center.proving import ProvingLayout
from jarvis_foundry.command_center.seeding import R10_EVIDENCE_UTC, seed_proving_stores
from jarvis_foundry.kernel.clock import FixedClock

REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_ROOT = REPO_ROOT / "docs" / "remaster" / "evidence"
FIXED_UTC = R10_EVIDENCE_UTC


def make_layout(tmp_path: Path, name: str = "proving") -> ProvingLayout:
    return ProvingLayout(tmp_path / name)


def seed_layout(layout: ProvingLayout) -> dict[str, object]:
    return seed_proving_stores(layout, EVIDENCE_ROOT)


def fixed_clock() -> FixedClock:
    return FixedClock(FIXED_UTC)
