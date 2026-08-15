from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from test_ledger import KINDS, registry, valid_event

from jarvis_foundry.kernel.errors import ProjectionUnknownEventError
from jarvis_foundry.kernel.events import EventKindRegistry, LedgerEvent
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.projections import (
    DEFAULT_PROJECTION_HANDLERS,
    GoalProjectionHandler,
    MissionProjectionHandler,
    ProjectionDispatcher,
    ProjectionHandler,
    ProjectionRegistry,
    snapshot_bytes,
)


def make_dispatcher() -> ProjectionDispatcher:
    handlers: dict[str, ProjectionHandler[Any]] = {
        "goals": GoalProjectionHandler(),
        "missions": MissionProjectionHandler(),
    }
    return ProjectionDispatcher(ProjectionRegistry(handlers))


def test_registered_focused_handlers_are_versioned() -> None:
    dispatcher = make_dispatcher()
    assert dispatcher.registry.handler("goals").version() == 1
    assert dispatcher.registry.handler("missions").version() == 1
    assert dispatcher.registry.names() == {"goals", "missions"}


def test_replay_builds_deterministic_byte_identical_snapshot(tmp_path: Path) -> None:
    registry_used = registry()
    ledger = Ledger(tmp_path / "kernel.db", registry=registry_used)
    ledger.append(valid_event(1, "goal.created", "objective", registry_used))
    ledger.append(valid_event(2, "mission.created", "objective", registry_used))
    dispatcher = make_dispatcher()
    snapshot = dispatcher.replay(ledger.replay())
    goals = snapshot.states["goals"]
    assert isinstance(goals, dict)
    assert "goal-1" in goals
    assert goals["goal-1"]["status"] == "ACTIVE"
    assert snapshot_bytes(snapshot) == snapshot_bytes(dispatcher.replay(ledger.replay()))
    assert snapshot_bytes(snapshot) == snapshot_bytes(dispatcher.replay(ledger.replay()))


def test_unknown_event_refusal_is_exhaustive(tmp_path: Path) -> None:
    registry_used = EventKindRegistry().register("goal.created", required_keys=("goal_id",))
    ledger = Ledger(tmp_path / "kernel.db", registry=registry_used)
    event = LedgerEvent.create(
        event_type="goal.created",
        payload={"goal_id": "g-1"},
        source_type="r4-test",
        source_id="x",
        idempotency_key="goal:1",
        event_id="evt-1",
    )
    ledger.append(event)
    dispatcher = ProjectionDispatcher(ProjectionRegistry({"goals": GoalProjectionHandler()}))
    assert "g-1" in dispatcher.replay(ledger.replay()).states["goals"]
    unknown = LedgerEvent.create(
        event_type="mystery.kind",
        payload={},
        source_type="r4-test",
        source_id="x",
        idempotency_key="mystery:1",
        event_id="evt-mystery",
    )
    with pytest.raises(ProjectionUnknownEventError):
        dispatcher.replay([unknown])


def test_dispatch_ignores_unregistered_projection_when_configured(tmp_path: Path) -> None:
    registry_used = registry()
    ledger = Ledger(tmp_path / "kernel.db", registry=registry_used)
    ledger.append(valid_event(1, "goal.created", "objective", registry_used))
    dispatcher = ProjectionDispatcher(ProjectionRegistry({"goals": GoalProjectionHandler()}))
    snapshot = dispatcher.replay(ledger.replay())
    assert "goal-1" in snapshot.states["goals"]


@settings(max_examples=20, deadline=None)
@given(
    indices=st.sets(st.integers(min_value=0, max_value=30), min_size=0, max_size=8).map(sorted),
    text=st.text(max_size=16),
)
def test_projection_replay_is_deterministic_and_idempotent(
    tmp_path_factory: pytest.TempPathFactory, indices: list[int], text: str
) -> None:
    registry_used = registry()
    ledger = Ledger(tmp_path_factory.mktemp("projection") / "kernel.db", registry=registry_used)
    events = [
        valid_event(index, KINDS[index % len(KINDS)], f"{text}-{index}", registry_used)
        for index in indices
    ]
    for event in events:
        ledger.append(event)
    dispatcher = ProjectionDispatcher(ProjectionRegistry(DEFAULT_PROJECTION_HANDLERS))
    first = dispatcher.replay(ledger.replay())
    again = dispatcher.replay(ledger.replay())
    assert snapshot_bytes(first) == snapshot_bytes(again)
    doubled = dispatcher.replay(ledger.replay() + ledger.replay())
    assert snapshot_bytes(doubled) == snapshot_bytes(first)


def test_default_projection_handlers_are_focused_and_versioned() -> None:
    names = set(DEFAULT_PROJECTION_HANDLERS)
    assert {"goals", "missions", "owner_directives", "releases", "incidents", "effects"} <= names
    for name in DEFAULT_PROJECTION_HANDLERS:
        handler = DEFAULT_PROJECTION_HANDLERS[name]
        assert handler.version() == 1
        assert len(handler.event_types()) >= 1
