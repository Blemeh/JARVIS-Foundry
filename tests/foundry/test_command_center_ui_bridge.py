"""Bridge tests for Command Center UI live adapter endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from jarvis_foundry.command_center import (
    CommandCenterServer, build_health_snapshot, build_snapshot,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import sign_command
from jarvis_foundry.deployment import runtime_identity


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_health_endpoint_reports_projection(db: FoundryDatabase, tmp_path: Path) -> None:
    server = CommandCenterServer(db, stop_path=tmp_path / "STOP")
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urlopen(f"{base}/api/health", timeout=3) as response:
            payload = json.loads(response.read())
        assert payload["status"] == "DEGRADED"
        assert payload["controls_configured"] is False
    finally:
        server.close()


def test_health_snapshot_exposes_release_parity_and_duplicate_supervisors(
    db: FoundryDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health must fail closed on duplicate authorities and show all identities."""
    install = tmp_path / "install"
    sha = "a" * 40
    release = install / "releases" / sha
    release.mkdir(parents=True)
    (install / "CURRENT").write_text(sha + "\n", encoding="utf-8")
    (release / "RELEASE.json").write_text(
        '{"source_sha":"%s","release_id":"rel-health"}' % sha,
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(install))
    db.append(Event.create(
        "supervisor.started",
        {"instance_id": "health-owner", "trust_state": "LAB",
         "release_id": "rel-health", "pid": 123},
        source_type="supervisor", source_id="health-owner",
        idempotency_key="supervisor.started:health-owner",
    ))

    def process(pid: int) -> dict:
        return {
            "pid": pid,
            "parent_pid": 1,
            "module": "jarvis_foundry.deployment.headless_launcher",
            "command_line": (
                f'pythonw.exe -m jarvis_foundry.deployment.headless_launcher '
                f'--home "{db.path.parent}"'),
            "release_sha": None,
            "identity_status": "VALID",
            "live": True,
            "creation_identity": {"pid": pid, "create_time": pid},
        }

    monkeypatch.setattr(runtime_identity, "supervisor_processes",
                        lambda: [process(123), process(456)])
    health = build_health_snapshot(db, runtime_home=db.path.parent)
    assert health["canonical_source_sha"] is None
    assert health["installed_release_sha"] == sha
    assert health["runtime_release_sha"] is None
    assert health["parity_state"] == "DUPLICATE_SUPERVISOR"
    assert health["release"]["supervisor_count"] == 2
    assert health["status"] == "DEGRADED"
    assert health["reason"]


def test_server_serves_bundled_react_app_and_spa_routes(
    db: FoundryDatabase, tmp_path: Path,
) -> None:
    static_root = tmp_path / "command-center"
    static_root.mkdir()
    (static_root / "index.html").write_text(
        "<!doctype html><div id='root'>bundled-react</div>", encoding="utf-8")
    server = CommandCenterServer(
        db, stop_path=tmp_path / "STOP", static_root=static_root)
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urlopen(f"{base}/missions/example", timeout=3) as response:
            assert b"bundled-react" in response.read()
        with urlopen(f"{base}/api/command-center", timeout=3) as response:
            payload = json.loads(response.read())
        assert payload["source_of_truth"]["kind"] == "append_only_event_ledger"
    finally:
        server.close()


def test_owner_command_localhost_auto_signs(db: FoundryDatabase, tmp_path: Path) -> None:
    secret = b"ui-bridge-secret"
    server = CommandCenterServer(db, stop_path=tmp_path / "STOP")
    server.configure_owner(secret)
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        body = json.dumps({"command_type": "GOAL", "target": "GLOBAL", "exact_text": "probe owner goal from UI bridge"}).encode()
        with urlopen(Request(f"{base}/api/owner-command", data=body, method="POST"), timeout=3) as response:
            payload = json.loads(response.read())
        assert payload["status"] == "APPLIED"
        assert payload["command_type"] == "GOAL"
        snapshot = build_snapshot(db, stop_path=tmp_path / "STOP")
        assert snapshot["controls"]["goals"]
    finally:
        server.close()


def test_owner_command_stop_everything_activates_sentinel(db: FoundryDatabase, tmp_path: Path) -> None:
    secret = b"ui-bridge-secret"
    stop_path = tmp_path / "STOP"
    server = CommandCenterServer(db, stop_path=stop_path)
    server.configure_owner(secret)
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        body = json.dumps({"command_type": "STOP_EVERYTHING", "target": "GLOBAL", "exact_text": "ui stop probe"}).encode()
        with urlopen(Request(f"{base}/api/owner-command", data=body, method="POST"), timeout=3) as response:
            payload = json.loads(response.read())
        assert payload["status"] == "APPLIED"
        snapshot = build_snapshot(db, stop_path=stop_path)
        assert snapshot["stop"]["active"] is True
    finally:
        server.close()


def test_snapshot_maps_mission_for_ui_reads(db: FoundryDatabase) -> None:
    db.append(Event.create(
        "mission.created",
        {"mission_id": "m-ui-1", "objective": "ui bridge read probe", "state": "PLANNED", "acceptance_criteria": ["read"], "next_action": "dispatch"},
        source_type="test", source_id="ui-bridge", idempotency_key="mission.created:m-ui-1",
    ))
    snapshot = build_snapshot(db)
    mission = next(m for m in snapshot["missions"] if m["mission_id"] == "m-ui-1")
    assert mission["objective"] == "ui bridge read probe"


def test_authenticated_controls_still_require_signature(db: FoundryDatabase, tmp_path: Path) -> None:
    secret = b"ui-bridge-secret"
    server = CommandCenterServer(db, stop_path=tmp_path / "STOP")
    server.configure_owner(secret)
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        body = {"command_type": "PAUSE", "target": "GLOBAL", "exact_text": "pause", "nonce": "n1", "signature": "bad"}
        try:
            urlopen(Request(f"{base}/api/controls", data=json.dumps(body).encode(), method="POST"), timeout=3)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("bad signature accepted")
        good = {
            "command_type": "PAUSE", "target": "GLOBAL", "exact_text": "pause",
            "nonce": "n2", "signature": sign_command(secret, "PAUSE", "GLOBAL", "pause", "n2"),
        }
        with urlopen(Request(f"{base}/api/controls", data=json.dumps(good).encode(), method="POST"), timeout=3) as response:
            assert json.loads(response.read())["status"] == "APPLIED"
    finally:
        server.close()
