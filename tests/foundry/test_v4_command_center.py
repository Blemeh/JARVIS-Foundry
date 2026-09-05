"""Roadmap-V4 surface contracts: durable projection, auth, replay, outbox, voice."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jarvis_foundry.briefings import BriefingPlanner
from jarvis_foundry.command_center import build_snapshot, command_center_readonly_connection, decision_timeline
from jarvis_foundry.command_center import CommandCenterServer
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import OwnerAuthenticationError, OwnerControlService, OwnerReplayError, sign_command
from jarvis_foundry.voice import FixtureSTT, FixtureTTS, Transcript, VoiceGateway
from jarvis_foundry.control.broker import emergency_stop_reason


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_snapshot_is_ledger_projection_and_timeline_has_evidence(db: FoundryDatabase, tmp_path: Path) -> None:
    created = Event.create(
        "mission.created",
        {"mission_id": "m-1", "objective": "observe", "acceptance_criteria": ["trace"], "state": "PLANNED"},
        source_type="supervisor", source_id="test", idempotency_key="mission.created:m-1",
    )
    db.append(created)
    snapshot = build_snapshot(db, stop_path=tmp_path / "STOP")
    assert snapshot["source_of_truth"]["event_count"] == 1
    assert snapshot["missions"][0]["mission_id"] == "m-1"
    assert snapshot["missions"][0]["record_ref"]["event_id"] == created.event_id
    assert decision_timeline(db)[0]["event_id"] == created.event_id
    db.rebuild_projections()
    assert build_snapshot(db, stop_path=tmp_path / "STOP")["missions"] == snapshot["missions"]


def test_briefing_outbox_is_duplicate_free_and_recoverable(db: FoundryDatabase) -> None:
    planner = BriefingPlanner(db)
    first = planner.plan("TWICE_DAILY", period_key="2026-08-13:AM")
    retry = planner.plan("TWICE_DAILY", period_key="2026-08-13:AM")
    assert first["created"] is True and retry["created"] is False
    assert db.count("briefing_outbox") == 1
    assert planner.claim(first["briefing_id"])
    assert planner.deliver_local(first["briefing_id"])
    planner.plan("EXCEPTION", period_key="provider:down")
    with db.connect() as connection:
        rows = connection.execute("SELECT status, attempt_count FROM briefing_outbox ORDER BY briefing_id").fetchall()
    assert any(row[0] == "DELIVERED" and row[1] == 1 for row in rows)


def test_owner_controls_authentication_replay_and_stop_survive_without_hermes(db: FoundryDatabase, tmp_path: Path) -> None:
    secret = b"owner-secret"
    stop = tmp_path / "STOP"
    service = OwnerControlService(db, secret=secret, stop_path=stop)
    signature = sign_command(secret, "STOP_EVERYTHING", "GLOBAL", "stop everything", "nonce-1")
    result = service.stop_now(reason="stop everything", nonce="nonce-1", signature=signature)
    assert result.command_type == "STOP_EVERYTHING" and stop.exists()
    with pytest.raises(OwnerReplayError):
        service.stop_now(reason="stop everything", nonce="nonce-1", signature=signature)
    with pytest.raises(OwnerAuthenticationError):
        service.apply("PAUSE", target="GLOBAL", exact_text="pause", nonce="nonce-2", signature="bad")
    constraint_signature = sign_command(secret, "ADD_CONSTRAINT", "GLOBAL", "no pushes", "nonce-3")
    service.apply("ADD_CONSTRAINT", target="GLOBAL", exact_text="no pushes", nonce="nonce-3", signature=constraint_signature)
    assert build_snapshot(db)["controls"]["constraints"][0]["command_type"] == "ADD_CONSTRAINT"
    assert emergency_stop_reason(db, stop) == "emergency stop marker is present"
    assert db.row("directives", "directive_id", result.directive_id)["command_type"] == "STOP_EVERYTHING"


def test_voice_routes_authenticated_english_and_rejects_impersonation(db: FoundryDatabase, tmp_path: Path) -> None:
    secret = b"owner-secret"
    gateway = VoiceGateway(db, owner_secret=secret, stt=FixtureSTT(Transcript("stop everything", "en")), tts=FixtureTTS(), stop_path=str(tmp_path / "STOP"))
    signature = sign_command(secret, "STOP_EVERYTHING", "GLOBAL", "stop everything", "voice-1")
    result = gateway.handle(b"fixture", nonce="voice-1", signature=signature, interaction_id="interaction-1")
    assert result["status"] == "COMPLETED" and result["auth_status"] == "AUTHENTICATED"
    replay = gateway.handle(b"different", nonce="wrong", signature="wrong", interaction_id="interaction-1")
    assert replay["status"] == "REJECTED" and replay["auth_status"] == "REPLAYED"
    unauth = gateway.handle(b"fixture", nonce="voice-2", signature="wrong", interaction_id="interaction-2")
    assert unauth["status"] == "REJECTED" and unauth["auth_status"] == "UNAUTHENTICATED"
    assert db.count("voice_interactions") == 2


def test_unknown_cost_measurement_is_not_zero(db: FoundryDatabase) -> None:
    db.append(Event.create(
        "resource.usage_recorded",
        {"measurement_id": "cost-1", "provider": "opencode_go", "model": "deepseek-v4-flash", "status": "UNKNOWN", "failure_class": "provider_unavailable"},
        source_type="supervisor", source_id="test", idempotency_key="resource.usage_recorded:cost-1",
    ))
    snapshot = build_snapshot(db)
    assert snapshot["costs"]["measured_actual_total_usd"] == "UNKNOWN"
    assert snapshot["costs"]["unknown_measurements"] == 1


def test_snapshot_bridges_v5_effects_and_v6_gates_without_provider_authority(
        db: FoundryDatabase) -> None:
    db.append(Event.create(
        "connector.attempt_recorded",
        {
            "attempt_id": "attempt-snapshot-1",
            "mission_id": "mission-snapshot",
            "resource_id": "resource-local",
            "effect_ticket_id": "effect-snapshot-1",
            "connector_id": "conn-local",
            "action_kind": "file.write",
            "outcome": "UNKNOWN",
            "error_code": "provider_timeout",
            "before_state": {"secret": "must-not-be-projected"},
            "evidence_refs": ["evidence-attempt-1"],
        },
        source_type="supervisor", source_id="test",
        idempotency_key="connector.attempt_recorded:snapshot-1",
    ))
    db.append(Event.create(
        "verifier.run_recorded",
        {
            "verification_id": "verification-snapshot-1",
            "improvement_id": "improvement-snapshot-1",
            "all_criteria_pass": False,
            "trusted_independent_pass": False,
            "evidence_refs": ["verifier-evidence-1"],
        },
        source_type="supervisor", source_id="test",
        idempotency_key="verifier.run_recorded:snapshot-1",
    ))
    snapshot = build_snapshot(db)
    effect = snapshot["effect_truth"]
    evolution = snapshot["evolution_truth"]
    assert effect["status"] == "AVAILABLE"
    assert effect["summary"]["unknown"] == 1
    assert "before_state" not in effect["attempts"][0]
    assert evolution["records"][0]["status"] == "FAIL"
    assert evolution["summary"]["eligible_decisions"] == 0
    packet = snapshot["briefing_packet"]
    assert packet["effect_truth"]["schema"] == "v5.effect-truth.v1"
    assert packet["evolution_truth"]["schema"] == "v6.evolution-truth.v1"


def test_measured_usage_without_actual_dollars_stays_unknown(db: FoundryDatabase) -> None:
    db.append(Event.create(
        "resource.usage_recorded",
        {"measurement_id": "cost-tokens", "provider": "opencode_go", "model": "deepseek-v4-flash", "status": "MEASURED", "total_tokens": 25},
        source_type="supervisor", source_id="test", idempotency_key="resource.usage_recorded:cost-tokens",
    ))
    snapshot = build_snapshot(db)
    assert snapshot["costs"]["measured_actual_total_usd"] == "UNKNOWN"


def test_local_command_center_server_is_read_only_projection(db: FoundryDatabase, tmp_path: Path) -> None:
    import json
    from urllib.request import urlopen

    server = CommandCenterServer(db, stop_path=tmp_path / "STOP")
    server.start()
    try:
        with urlopen(f"http://{server.address[0]}:{server.address[1]}/api/command-center", timeout=3) as response:
            payload = json.loads(response.read())
        assert payload["source_of_truth"]["kind"] == "append_only_event_ledger"
        with urlopen(f"http://{server.address[0]}:{server.address[1]}/", timeout=3) as response:
            assert b"JARVIS Command Center" in response.read()
    finally:
        server.close()


def test_command_center_connection_rejects_writes_and_missing_db_is_not_created(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    with command_center_readonly_connection(database) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("INSERT INTO events(event_id) VALUES ('command-center-write-probe')")

    missing_path = tmp_path / "missing" / "foundry.db"
    missing = FoundryDatabase(missing_path)
    with pytest.raises(FileNotFoundError):
        with command_center_readonly_connection(missing):
            pass
    assert not missing_path.exists()


def test_command_center_exposes_direct_durable_record_routes(db: FoundryDatabase, tmp_path: Path) -> None:
    import json
    from urllib.error import HTTPError
    from urllib.request import urlopen

    created = Event.create(
        "mission.created",
        {"mission_id": "m-direct", "objective": "inspect evidence", "acceptance_criteria": ["trace"], "state": "PLANNED"},
        source_type="supervisor", source_id="test", idempotency_key="mission.created:m-direct",
    )
    db.append(created)
    db.append(Event.create(
        "connector.attempt_recorded",
        {
            "attempt_id": "attempt-direct",
            "mission_id": "m-direct",
            "connector_id": "connector-direct",
            "action_kind": "file.write",
            "outcome": "COMPLETED",
            "provider_ref": "provider-receipt-direct",
            "evidence_refs": ["evidence-direct"],
        },
        source_type="supervisor", source_id="test",
        idempotency_key="connector.attempt_recorded:attempt-direct",
    ))
    server = CommandCenterServer(db, stop_path=tmp_path / "STOP")
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urlopen(f"{base}/api/record/mission/m-direct", timeout=3) as response:
            mission = json.loads(response.read())
        assert mission["status"] == "FOUND"
        assert mission["record"]["mission_id"] == "m-direct"
        with urlopen(f"{base}/api/record/event/{created.event_id}", timeout=3) as response:
            event = json.loads(response.read())
        assert event["status"] == "FOUND"
        assert event["record"]["event_id"] == created.event_id
        with urlopen(f"{base}/api/record/provider_attempt/attempt-direct", timeout=3) as response:
            attempt = json.loads(response.read())
        assert attempt["status"] == "FOUND"
        assert attempt["record"]["mission_id"] == "m-direct"
        assert attempt["record"]["provider_ref"] == "provider-receipt-direct"
        try:
            urlopen(f"{base}/api/record/mission/not-found", timeout=3)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("missing durable record returned success")
    finally:
        server.close()


def test_local_control_endpoint_is_authenticated_and_fail_closed(db: FoundryDatabase, tmp_path: Path) -> None:
    import json
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    secret = b"endpoint-secret"
    server = CommandCenterServer(db, stop_path=tmp_path / "STOP")
    server.configure_owner(secret)
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}/api/controls"
        body = {"command_type": "PAUSE", "target": "GLOBAL", "exact_text": "pause jarvis", "nonce": "endpoint-1", "signature": sign_command(secret, "PAUSE", "GLOBAL", "pause jarvis", "endpoint-1")}
        with urlopen(Request(base, data=json.dumps(body).encode(), method="POST"), timeout=3) as response:
            assert json.loads(response.read())["status"] == "APPLIED"
        bad = dict(body, nonce="endpoint-2", signature="bad")
        try:
            urlopen(Request(base, data=json.dumps(bad).encode(), method="POST"), timeout=3)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("unauthenticated control accepted")
    finally:
        server.close()
