from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from test_event_ledger import KINDS, valid_event

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.projections import PROJECTION_TABLES


def projection_snapshot(database: FoundryDatabase) -> dict[str, list[dict[str, object]]]:
    snapshot: dict[str, list[dict[str, object]]] = {}
    with database.connect() as connection:
        for table in PROJECTION_TABLES:
            rows = connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            snapshot[table] = [
                {
                    key: str(row[key]) if row[key] is not None else None
                    for key in row.keys()  # noqa: SIM118 - sqlite3.Row needs keys()
                }
                for row in rows
            ]
    return snapshot


def fixed_suite(database: FoundryDatabase) -> None:
    for index, kind in enumerate(KINDS):
        database.append(valid_event(index + 1, kind, f"suite-{index}"))


def test_projection_rebuild_is_idempotent(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    fixed_suite(database)
    before = projection_snapshot(database)
    assert database.rebuild_projections() == len(KINDS)
    after = projection_snapshot(database)
    assert after == before
    database.rebuild_projections()
    assert projection_snapshot(database) == before
    assert database.quick_check() == "ok"


@settings(max_examples=20, deadline=None)
@given(
    indices=st.sets(st.integers(min_value=0, max_value=30), min_size=0, max_size=8).map(sorted),
    text=st.text(max_size=16),
)
def test_projection_replay_is_deterministic(
    tmp_path_factory: pytest.TempPathFactory, indices: list[int], text: str
) -> None:
    database = FoundryDatabase(tmp_path_factory.mktemp("projection") / "foundry.db")
    database.initialize()
    events = [valid_event(index, KINDS[index % len(KINDS)], f"{text}-{index}") for index in indices]
    database.append_events(events)
    first = projection_snapshot(database)
    assert database.rebuild_projections() == len(events)
    assert projection_snapshot(database) == first
    database.append_events(events)
    assert database.rebuild_projections() == len(events)
    assert projection_snapshot(database) == first
