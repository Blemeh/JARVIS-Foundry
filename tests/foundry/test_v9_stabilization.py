"""Focused V9 stabilization contracts: pure reads, bounded seams, and truth labels."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

import pytest

from jarvis_foundry.command_center import CommandCenterServer, build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.continuous import continuous_snapshot
from jarvis_foundry.migrations import SCHEMA_VERSION


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "supervisor.started",
        {"instance_id": "stabilization-test", "trust_state": "LAB"},
        source_type="supervisor",
        source_id="stabilization-test",
        idempotency_key="stabilization:supervisor.started",
    ))
    database.append(Event.create(
        "emergency_stop.reconciled",
        {"instance_id": "stabilization-test", "reason": "test trust boundary"},
        source_type="supervisor",
        source_id="stabilization-test",
        idempotency_key="stabilization:emergency_stop.reconciled",
    ))
    return database


def _read_routes(server: CommandCenterServer) -> dict[str, object]:
    base = f"http://{server.address[0]}:{server.address[1]}"
    result: dict[str, object] = {}
    for route in (
        "/api/command-center",
        "/api/timeline",
        "/api/executive",
        "/api/executive/objectives",
        "/api/executive/briefing",
    ):
        with urlopen(base + route, timeout=10) as response:
            result[route] = json.loads(response.read())
    return result


def test_continuous_snapshot_is_read_only(db: FoundryDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    statements: list[str] = []
    connect = db.connect

    def traced_connect(*, readonly: bool = False):
        connection = connect(readonly=readonly)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(db, "connect", traced_connect)
    before = db.event_count()
    continuous_snapshot(db)
    after = db.event_count()

    assert after == before
    assert not any("BEGIN IMMEDIATE" in statement.upper() for statement in statements)


def test_read_api_routes_have_no_write_side_effects(
    db: FoundryDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []
    connect = db.connect

    def traced_connect(*, readonly: bool = False):
        connection = connect(readonly=readonly)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(db, "connect", traced_connect)
    server = CommandCenterServer(db, stop_path=tmp_path / "EMERGENCY_STOP")
    server.start()
    try:
        before = db.event_count()
        payloads = _read_routes(server)
        after = db.event_count()
    finally:
        server.close()

    assert after == before
    assert all(payload is not None for payload in payloads.values())
    assert not any("BEGIN IMMEDIATE" in statement.upper() for statement in statements)


def test_timeline_route_does_not_build_complete_snapshot(
    db: FoundryDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis_foundry.command_center as command_center

    def fail_if_snapshot_called(*_args, **_kwargs):
        raise AssertionError("timeline must not build the complete Command Center snapshot")

    monkeypatch.setattr(command_center, "build_snapshot", fail_if_snapshot_called)
    server = CommandCenterServer(db, stop_path=tmp_path / "EMERGENCY_STOP")
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urlopen(base + "/api/timeline", timeout=10) as response:
            payload = json.loads(response.read())
    finally:
        server.close()
    assert isinstance(payload, list)


def test_snapshot_exposes_schema_and_trust_truth(
    db: FoundryDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(tmp_path / "isolated-install"))
    snapshot = build_snapshot(db)
    assert snapshot["health"]["schema"]["live"] == SCHEMA_VERSION
    assert snapshot["health"]["schema"]["package"] == SCHEMA_VERSION
    assert snapshot["health"]["trust_state"] == "SUSPENDED"
    assert snapshot["release_identity"]["current"] is None


def test_heartbeat_does_not_invalidate_product_cache_but_updates_runtime_head(
    db: FoundryDatabase, tmp_path: Path,
) -> None:
    server = CommandCenterServer(db, stop_path=tmp_path / "EMERGENCY_STOP")
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urlopen(base + "/api/command-center", timeout=10) as response:
            first = json.loads(response.read())
        db.append(Event.create(
            "supervisor.heartbeat",
            {"instance_id": "stabilization-test", "trust_state": "SUSPENDED"},
            source_type="supervisor",
            source_id="stabilization-test",
            idempotency_key="stabilization:heartbeat:cache",
        ))
        with urlopen(base + "/api/command-center", timeout=10) as response:
            second = json.loads(response.read())
    finally:
        server.close()

    assert second["source_of_truth"]["ledger_head_sequence"] > first["source_of_truth"]["ledger_head_sequence"]
    assert second["health"]["trust_state"] == "SUSPENDED"
    assert second["timeline"] == first["timeline"]
