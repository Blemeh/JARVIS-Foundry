from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from test_ledger import registry, valid_event

from jarvis_foundry.kernel.errors import ProjectionUnknownEventError
from jarvis_foundry.kernel.events import EventKindRegistry
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.projections import (
    DEFAULT_PROJECTION_HANDLERS,
    GoalProjectionHandler,
    ProjectionDispatcher,
    ProjectionRegistry,
    snapshot_bytes,
)


def build_historical_ledger(path: Path, registry_used: EventKindRegistry) -> Ledger:
    ledger = Ledger(path, registry=registry_used)
    events = [
        valid_event(1, "goal.created", "alpha", registry_used),
        valid_event(2, "mission.created", "beta", registry_used),
        valid_event(3, "supervisor.started", "gamma", registry_used),
        valid_event(4, "owner.directive.recorded", "delta", registry_used),
        valid_event(5, "goal.created", "epsilon", registry_used),
    ]
    for event in events:
        ledger.append(event)
    return ledger


def test_replay_from_sequence_one_matches_incremental_and_is_byte_identical(
    tmp_path: Path,
) -> None:
    registry_used = registry()
    ledger = build_historical_ledger(tmp_path / "history.db", registry_used)
    dispatcher = ProjectionDispatcher(ProjectionRegistry(DEFAULT_PROJECTION_HANDLERS))
    full = dispatcher.replay(ledger.replay())
    incremental = dispatcher.replay(ledger.replay())
    assert snapshot_bytes(full) == snapshot_bytes(incremental)
    assert len(ledger.replay()) == 5
    assert ledger.verify_chain().valid is True


def test_replay_identical_across_restart(tmp_path: Path) -> None:
    registry_used = registry()
    ledger = build_historical_ledger(tmp_path / "history.db", registry_used)
    dispatcher = ProjectionDispatcher(ProjectionRegistry(DEFAULT_PROJECTION_HANDLERS))
    first = dispatcher.replay(ledger.replay())
    reopened = Ledger(tmp_path / "history.db", registry=registry_used)
    assert reopened.verify_chain().valid is True
    second = dispatcher.replay(reopened.replay())
    assert snapshot_bytes(first) == snapshot_bytes(second)


def test_unknown_event_unknown_to_all_handlers_is_refused() -> None:
    registry_used = registry()
    ledger = Ledger(Path(tempfile.mkdtemp()) / "kernel.db", registry=registry_used)
    ledger.append(valid_event(1, "goal.created", "x", registry_used))

    dispatcher = ProjectionDispatcher(ProjectionRegistry({"goals": GoalProjectionHandler()}))
    unknown = ledger.replay()
    unknown[0] = unknown[0].with_event_type("mystery.kind")
    with pytest.raises(ProjectionUnknownEventError):
        dispatcher.replay(unknown)
