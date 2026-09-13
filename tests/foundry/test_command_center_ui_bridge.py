"""Bridge tests for Command Center UI live adapter endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from jarvis_foundry.command_center import (
    CommandCenterServer, build_health_snapshot, build_operational_status_snapshot,
    build_snapshot, decision_timeline,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.deployment import runtime_identity


# Local bridge client budget. The endpoint assertions, not the client wait,
# are the contract under test; a 3-second deadline turned host load during
# long serialized suite runs into spurious failures (2.79s observed against
# a 3.0s budget for a localhost projection read).
REQUEST_TIMEOUT_SECONDS = 30.0


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
        with urlopen(f"{base}/api/health", timeout=REQUEST_TIMEOUT_SECONDS) as response:
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
        with urlopen(f"{base}/missions/example", timeout=REQUEST_TIMEOUT_SECONDS) as response:
            assert b"bundled-react" in response.read()
        with urlopen(f"{base}/api/command-center", timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
        assert payload["source_of_truth"]["kind"] == "append_only_event_ledger"
    finally:
        server.close()


def test_operational_status_is_bounded_and_separate_from_full_snapshot(
    db: FoundryDatabase, tmp_path: Path,
) -> None:
    status = build_operational_status_snapshot(db, runtime_home=tmp_path)
    assert status["status"] in {"OK", "DEGRADED", "BLOCKED"}
    assert "mission_counts" in status
    assert "recent_events" in status
    assert "missions" not in status
    assert len(json.dumps(status)) < 64_000

    server = CommandCenterServer(db, stop_path=tmp_path / "STOP", runtime_home=tmp_path)
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urlopen(f"{base}/api/status", timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
        assert payload["status"] in {"OK", "DEGRADED", "BLOCKED"}
        assert "mission_counts" in payload
        assert "missions" not in payload
    finally:
        server.close()


def test_operational_status_excludes_expired_iso_leases(
    db: FoundryDatabase, tmp_path: Path,
) -> None:
    """ISO ``T`` timestamps must not make expired same-day leases look live."""
    now = datetime.now(timezone.utc)
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO leases (lease_key, owner_id, expires_at, heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            (
                "expired-status-lease", "stale-owner",
                (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            ),
        )
        connection.execute(
            "INSERT INTO leases (lease_key, owner_id, expires_at, heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            (
                "active-status-lease", "live-owner",
                (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                now.isoformat().replace("+00:00", "Z"),
            ),
        )
    status = build_operational_status_snapshot(db, runtime_home=tmp_path)
    assert status["active_lease_count"] == 1


def test_owner_command_localhost_auto_signs(db: FoundryDatabase, tmp_path: Path) -> None:
    secret = b"ui-bridge-secret"
    server = CommandCenterServer(db, stop_path=tmp_path / "STOP")
    server.configure_owner(secret)
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        body = json.dumps({"command_type": "GOAL", "target": "GLOBAL", "exact_text": "probe owner goal from UI bridge"}).encode()
        with urlopen(Request(f"{base}/api/owner-command", data=body, method="POST"), timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
        assert payload["status"] == "APPLIED"
        assert payload["command_type"] == "GOAL"
        snapshot = build_snapshot(db, stop_path=tmp_path / "STOP")
        assert snapshot["controls"]["goals"]
    finally:
        server.close()


def test_browser_owner_command_requires_local_http_only_session(
    db: FoundryDatabase, tmp_path: Path,
) -> None:
    secret = b"ui-browser-session-secret"
    server = CommandCenterServer(db, stop_path=tmp_path / "STOP")
    server.configure_owner(secret)
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        exact_text = "preserve this owner text exactly"
        body = json.dumps({
            "command_type": "GOAL",
            "target": "GLOBAL",
            "exact_text": exact_text,
        }).encode()
        unauthenticated = Request(
            f"{base}/api/owner-command",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": "http://127.0.0.1:5173",
            },
        )
        with pytest.raises(HTTPError) as rejected:
            urlopen(unauthenticated, timeout=REQUEST_TIMEOUT_SECONDS)
        assert rejected.value.code == 401

        session_request = Request(
            f"{base}/api/control-session",
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        with urlopen(session_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            assert json.loads(response.read())["status"] == "READY"
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        authenticated = Request(
            f"{base}/api/owner-command",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": "http://127.0.0.1:5173",
                "Cookie": cookie,
            },
        )
        with urlopen(authenticated, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            assert json.loads(response.read())["status"] == "APPLIED"

        snapshot = build_snapshot(db, stop_path=tmp_path / "STOP")
        assert snapshot["controls"]["goals"][0]["exact_text"] == exact_text
    finally:
        server.close()


def test_owner_command_invalidates_cached_snapshot(db: FoundryDatabase, tmp_path: Path) -> None:
    """A successful owner command must be visible on the next UI poll."""
    secret = b"ui-bridge-secret"
    server = CommandCenterServer(db, stop_path=tmp_path / "STOP")
    server.configure_owner(secret)
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urlopen(f"{base}/api/command-center", timeout=REQUEST_TIMEOUT_SECONDS) as response:
            before = json.loads(response.read())
        assert before["controls"]["goals"] == []
        body = json.dumps({
            "command_type": "GOAL",
            "target": "GLOBAL",
            "exact_text": "refresh cached owner goal",
        }).encode()
        with urlopen(Request(f"{base}/api/owner-command", data=body, method="POST"), timeout=REQUEST_TIMEOUT_SECONDS) as response:
            assert json.loads(response.read())["status"] == "APPLIED"
        with urlopen(f"{base}/api/command-center", timeout=REQUEST_TIMEOUT_SECONDS) as response:
            after = json.loads(response.read())
        assert [goal["exact_text"] for goal in after["controls"]["goals"]] == [
            "refresh cached owner goal",
        ]
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
        with urlopen(Request(f"{base}/api/owner-command", data=body, method="POST"), timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
        assert payload["status"] == "APPLIED"
        snapshot = build_snapshot(db, stop_path=stop_path)
        assert snapshot["stop"]["active"] is True
    finally:
        server.close()


def test_snapshot_stop_remains_active_from_durable_control_without_sentinel(
    db: FoundryDatabase, tmp_path: Path,
) -> None:
    secret = b"durable-stop-secret"
    stop_path = tmp_path / "STOP"
    service = OwnerControlService(
        db, secret=secret, stop_path=stop_path, owner_id="test-owner",
    )
    exact_text = "durable stop must remain authoritative"
    nonce = "durable-stop-nonce"
    service.apply(
        "STOP_EVERYTHING",
        target="GLOBAL",
        exact_text=exact_text,
        nonce=nonce,
        signature=sign_command(
            secret, "STOP_EVERYTHING", "GLOBAL", exact_text, nonce,
        ),
    )
    stop_path.unlink()

    snapshot = build_snapshot(db, stop_path=stop_path)

    assert snapshot["stop"]["active"] is True
    assert snapshot["stop"]["record"] is None
    assert snapshot["controls"]["runtime_state"] == "STOPPED"
    assert snapshot["controls"]["state_evidence"]["stop_directive_ids"]


def test_cached_snapshot_refreshes_external_stop_sentinel(
    db: FoundryDatabase, tmp_path: Path,
) -> None:
    stop_path = tmp_path / "STOP"
    server = CommandCenterServer(db, stop_path=stop_path)
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urlopen(f"{base}/api/command-center", timeout=REQUEST_TIMEOUT_SECONDS) as response:
            before = json.loads(response.read())
        assert before["stop"]["active"] is False

        EmergencyStop(stop_path).activate("tray hotkey stop")
        with urlopen(f"{base}/api/command-center", timeout=REQUEST_TIMEOUT_SECONDS) as response:
            stopped = json.loads(response.read())
        assert stopped["stop"]["active"] is True
        assert stopped["controls"]["runtime_state"] == "STOPPED"

        EmergencyStop(stop_path).clear()
        with urlopen(f"{base}/api/command-center", timeout=REQUEST_TIMEOUT_SECONDS) as response:
            resumed = json.loads(response.read())
        assert resumed["stop"]["active"] is False
    finally:
        server.close()


def test_decision_timeline_preserves_durable_non_contiguous_sequences(
    db: FoundryDatabase,
) -> None:
    first = Event.create(
        "mission.created",
        {
            "mission_id": "m-sequence-1",
            "objective": "first durable event",
            "state": "PLANNED",
            "acceptance_criteria": ["sequence is preserved"],
        },
        source_type="test",
        source_id="timeline",
        idempotency_key="mission.created:m-sequence-1",
    )
    heartbeat = Event.create(
        "supervisor.heartbeat",
        {"instance_id": "timeline-heartbeat", "trust_state": "LAB"},
        source_type="supervisor",
        source_id="timeline",
        idempotency_key="supervisor.heartbeat:timeline-gap",
    )
    second = Event.create(
        "mission.created",
        {
            "mission_id": "m-sequence-2",
            "objective": "second durable event",
            "state": "PLANNED",
            "acceptance_criteria": ["sequence is preserved"],
        },
        source_type="test",
        source_id="timeline",
        idempotency_key="mission.created:m-sequence-2",
    )
    first_sequence = db.append(first)[0]
    db.append(heartbeat)
    second_sequence = db.append(second)[0]

    timeline = decision_timeline(db)
    by_id = {item["event_id"]: item["sequence"] for item in timeline}

    assert by_id[first.event_id] == first_sequence
    assert by_id[second.event_id] == second_sequence
    assert second_sequence > first_sequence + 1


def test_timeline_cursor_endpoint_pages_durable_events(
    db: FoundryDatabase, tmp_path: Path,
) -> None:
    sequences: list[int] = []
    for index in range(3):
        event = Event.create(
            "mission.created",
            {
                "mission_id": f"m-page-{index}",
                "objective": f"timeline page {index}",
                "state": "PLANNED",
                "acceptance_criteria": ["cursor page is durable"],
            },
            source_type="test",
            source_id="timeline-page",
            idempotency_key=f"mission.created:m-page-{index}",
        )
        sequences.append(db.append(event)[0])

    server = CommandCenterServer(db, stop_path=tmp_path / "STOP")
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urlopen(
            f"{base}/api/timeline?after_sequence={sequences[0]}&limit=1",
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            first_page = json.loads(response.read())
        assert first_page["schema"] == "v13.command-center-event-page.v1"
        assert [event["sequence"] for event in first_page["events"]] == [
            sequences[1],
        ]
        assert first_page["has_more"] is True
        assert first_page["next_sequence"] == sequences[1]

        with urlopen(
            f"{base}/api/timeline?after_sequence={first_page['next_sequence']}&limit=1",
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            final_page = json.loads(response.read())
        assert [event["sequence"] for event in final_page["events"]] == [
            sequences[2],
        ]
        assert final_page["has_more"] is False
        assert final_page["next_sequence"] == sequences[2]
        assert final_page["ledger_head_sequence"] == sequences[2]
    finally:
        server.close()


def test_timeline_publishes_versioned_lifecycle_stages(
    db: FoundryDatabase,
) -> None:
    event = Event.create(
        "mission.created",
        {
            "mission_id": "m-lifecycle-contract",
            "objective": "publish structured lifecycle truth",
            "state": "PLANNED",
            "acceptance_criteria": ["stage labels are versioned"],
        },
        source_type="test",
        source_id="lifecycle-contract",
        idempotency_key="mission.created:m-lifecycle-contract",
    )
    db.append(event)

    projected = next(
        item for item in decision_timeline(db)
        if item["event_id"] == event.event_id
    )

    assert projected["lifecycle_contract"] == (
        "jarvis.command-center-mission-lifecycle.v1"
    )
    assert projected["lifecycle_stages"] == ["PLAN", "MISSION"]


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
            urlopen(Request(f"{base}/api/controls", data=json.dumps(body).encode(), method="POST"), timeout=REQUEST_TIMEOUT_SECONDS)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("bad signature accepted")
        good = {
            "command_type": "PAUSE", "target": "GLOBAL", "exact_text": "pause",
            "nonce": "n2", "signature": sign_command(secret, "PAUSE", "GLOBAL", "pause", "n2"),
        }
        with urlopen(Request(f"{base}/api/controls", data=json.dumps(good).encode(), method="POST"), timeout=REQUEST_TIMEOUT_SECONDS) as response:
            assert json.loads(response.read())["status"] == "APPLIED"
    finally:
        server.close()
