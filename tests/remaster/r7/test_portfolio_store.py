from __future__ import annotations

from pathlib import Path

import pytest
from r7_helpers import FIXED_UTC, make_observation, make_store

from jarvis_foundry.kernel.errors import KernelError
from jarvis_foundry.kernel.portfolio.records import Observation
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def test_append_is_idempotent_and_duplicate_returns_existing(
    tmp_path: Path, fixed_time: str = FIXED_UTC
) -> None:
    store = make_store(tmp_path)
    observation = make_observation(fixed_time=fixed_time)
    first = store.append_observation(observation)
    second = store.append_observation(observation)
    assert first.created is True
    assert second.created is False
    assert second.record == first.record
    assert len(store.observations()) == 1


def test_restart_rebuilds_identical_state(tmp_path: Path, fixed_time: str = FIXED_UTC) -> None:
    root = tmp_path / "portfolio"
    first = PortfolioStore(root)
    first.append_observation(make_observation(observation_id="obs-a", fixed_time=fixed_time))
    first.append_observation(make_observation(observation_id="obs-b", fixed_time=fixed_time))
    before = first.state_bytes()

    reopened = PortfolioStore(root)
    after = reopened.state_bytes()
    assert after == before
    assert [item.observation_id for item in reopened.observations()] == ["obs-a", "obs-b"]


def test_tamper_detection_fails_closed(tmp_path: Path, fixed_time: str = FIXED_UTC) -> None:
    store = make_store(tmp_path)
    root = tmp_path / "portfolio"
    store = PortfolioStore(root)
    store.append_observation(make_observation(fixed_time=fixed_time))
    events = root / "events.jsonl"
    line = events.read_text(encoding="utf-8").splitlines()[0]
    events.write_text(line.replace('"obs-1"', '"obs-x"') + "\n", encoding="utf-8")
    with pytest.raises(KernelError):
        PortfolioStore(root)


def test_snapshot_and_restore_rehearsal(tmp_path: Path, fixed_time: str = FIXED_UTC) -> None:
    store = make_store(tmp_path)
    root = tmp_path / "portfolio"
    store = PortfolioStore(root)
    store.append_observation(make_observation(fixed_time=fixed_time))
    snapshot = store.snapshot_bytes()
    store.append_observation(make_observation(observation_id="obs-2", fixed_time=fixed_time))
    assert len(store.observations()) == 2
    store.restore_bytes(snapshot)
    assert [item.observation_id for item in store.observations()] == ["obs-1"]


def test_append_refuses_unknown_kind(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(KernelError):
        store.append("ghost.record", "key", make_observation())


def test_unique_key_conflict_is_rejected(tmp_path: Path, fixed_time: str = FIXED_UTC) -> None:
    store = make_store(tmp_path)
    first = make_observation(observation_id="obs-a", fixed_time=fixed_time)
    store.append_observation(first)
    conflicting = Observation(
        observation_id="obs-b",
        source_type=first.source_type,
        source_id=first.source_id,
        captured_at=first.captured_at,
        content_hash="0" * 64,
        evidence_refs=first.evidence_refs,
        observed_value="different",
        meaningful_change=True,
        change_hash="1" * 64,
    )
    with pytest.raises(KernelError):
        store.append_observation(conflicting, idempotency_key=f"observation:{first.observation_id}")
