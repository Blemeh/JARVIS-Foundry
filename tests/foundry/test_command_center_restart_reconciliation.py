"""The Command Center is a projection of the ledger, not a second source of truth.

Proof for the capability-matrix row "reflects canonical state ... recovery after
reconnect/restart": a brand-new server instance over the same database (a restart,
possibly on another port) must serve exactly the durable truth - nothing lost from
before the restart, everything recorded while it was down, and the owner STOP
sentinel (a filesystem fact) still in force.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

import pytest

from jarvis_foundry.command_center import CommandCenterServer
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.owner_controls import OwnerControlService, sign_command

TIMEOUT = 30.0
SECRET = b"restart-reconciliation-owner-secret-32b"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _record_goal(database: FoundryDatabase, text: str, nonce: str) -> str:
    service = OwnerControlService(database, secret=SECRET, owner_id="owner")
    service.apply(
        "GOAL", target="resource-restart", exact_text=text, nonce=nonce,
        signature=sign_command(SECRET, "GOAL", "resource-restart", text, nonce),
    )
    directives = [
        event.payload["directive_id"]
        for event in database.events(event_types=("owner_directive.recorded",))
        if event.payload.get("exact_text") == text or text in json.dumps(event.payload)
    ]
    assert directives, "the goal must be durably recorded"
    return str(directives[-1])


def _get(base: str, path: str):
    with urlopen(f"{base}{path}", timeout=TIMEOUT) as response:
        assert response.status == 200
        return json.loads(response.read())


def _serve(database: FoundryDatabase, stop_path: Path) -> tuple[CommandCenterServer, str]:
    server = CommandCenterServer(database, stop_path=stop_path)
    server.start()
    return server, f"http://{server.address[0]}:{server.address[1]}"


def test_restart_serves_everything_before_and_during_downtime(db: FoundryDatabase, tmp_path: Path) -> None:
    stop_path = tmp_path / "EMERGENCY_STOP"
    first_id = _record_goal(db, "Keep the Command Center truthful across restarts", "restart-1")

    server_a, base_a = _serve(db, stop_path)
    try:
        before = _get(base_a, "/api/command-center")
        status_before = _get(base_a, "/api/status")
        timeline_before = _get(base_a, "/api/timeline")
    finally:
        server_a.close()
    assert first_id in json.dumps(before["owner_directives"])
    assert status_before["stop_active"] is False

    # While no server is running, durable truth keeps changing.
    second_id = _record_goal(db, "Recorded while the server was down", "restart-2")
    stop_path.write_text("owner stop while down", encoding="utf-8")

    server_b, base_b = _serve(db, stop_path)
    try:
        after = _get(base_b, "/api/command-center")
        status_after = _get(base_b, "/api/status")
        timeline_after = _get(base_b, "/api/timeline")
    finally:
        server_b.close()

    directives_after = json.dumps(after["owner_directives"])
    assert first_id in directives_after, "nothing recorded before the restart may be lost"
    assert second_id in directives_after, "everything recorded while down must appear"
    truth_before, truth_after = before["source_of_truth"], after["source_of_truth"]
    assert truth_after["kind"] == truth_before["kind"]
    # The ledger only moves forward; a restart must not rewind or fork it.
    assert truth_after["ledger_head_sequence"] > truth_before["ledger_head_sequence"]
    assert truth_after["event_count"] > truth_before["event_count"]
    assert status_after["directive_counts"] != status_before["directive_counts"]
    # STOP is a filesystem fact that must survive the restart and be reported truthfully.
    assert status_after["stop_active"] is True
    assert len(timeline_after) >= len(timeline_before)


def test_two_servers_over_one_database_agree(db: FoundryDatabase, tmp_path: Path) -> None:
    """Reconnecting to a different instance cannot change the answer."""
    stop_path = tmp_path / "EMERGENCY_STOP"
    _record_goal(db, "Single source of truth", "agree-1")
    server_a, base_a = _serve(db, stop_path)
    server_b, base_b = _serve(db, stop_path)
    try:
        assert base_a != base_b
        a = _get(base_a, "/api/status")
        b = _get(base_b, "/api/status")
    finally:
        server_a.close()
        server_b.close()
    for key in ("directive_counts", "mission_counts", "stop_active", "schema", "trust_state"):
        assert a[key] == b[key], key
