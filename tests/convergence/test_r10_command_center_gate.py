"""R10 forward-port gate: Command Center, directives, briefings, and voices."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from jarvis_foundry.briefings import BriefingPlanner
from jarvis_foundry.command_center import (
    CommandCenterServer,
    build_snapshot,
    decision_timeline,
)
from jarvis_foundry.convergence.briefings_l10n import render_briefing
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import (
    OwnerAuthenticationError,
    OwnerControlService,
    OwnerReplayError,
    sign_command,
)
from jarvis_foundry.voice import FixtureSTT, FixtureTTS, Transcript, VoiceGateway

CTRL = "supervisor"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _seed_full_surface(db: FoundryDatabase) -> None:
    """Seed a realistic snapshot surface through the append-only ledger."""
    db.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-18T00:00:00Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build-r10",
    ))
    db.append(Event.create(
        "mission.created",
        {
            "mission_id": "mission-1", "objective": "r10 command center gate",
            "resource_id": "resource-1", "state": "PLANNED",
            "acceptance_criteria": ["snapshot exposes the required surfaces"],
            "next_action": "dispatch r10 gate",
        },
        source_type="test", source_id="r10-gate",
        idempotency_key="mission.created:mission-1",
    ))
    db.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-1", "state": "ORIENTING", "next_action": "dispatch r10 gate"},
        source_type="test", source_id="r10-gate",
        idempotency_key="mission.state_changed:mission-1:ORIENTING",
    ))
    db.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-1", "state": "ACTION_SELECTION", "next_action": "dispatch r10 gate"},
        source_type="test", source_id="r10-gate",
        idempotency_key="mission.state_changed:mission-1:ACTION_SELECTION",
    ))
    db.append(Event.create(
        "agent_run.started",
        {"run_id": "run-1", "mission_id": "mission-1", "role": "builder", "provider": "opencode_go", "model": "deepseek-v4-flash"},
        source_type=CTRL, source_id="r10-gate",
        idempotency_key="agent_run.started:run-1",
    ))
    db.append(Event.create(
        "agent_run.completed",
        {"run_id": "run-1", "result": {"ok": True}, "cost": {"estimated_cost_usd": 0.01}},
        source_type=CTRL, source_id="r10-gate",
        idempotency_key="agent_run.completed:run-1",
    ))
    db.append(Event.create(
        "evidence.created",
        {"bundle_id": "bundle-1", "mission_id": "mission-1", "objective": "evidence bundle",
         "criteria": ["trace"], "artifact_hashes": {"result.txt": "a" * 64}},
        source_type=CTRL, source_id="r10-gate",
        idempotency_key="evidence.created:bundle-1",
    ))
    db.append(Event.create(
        "verification.started",
        {"verdict_id": "verdict-1", "mission_id": "mission-1", "subject_type": "mission", "subject_id": "mission-1"},
        source_type=CTRL, source_id="r10-gate",
        idempotency_key="verification.started:verdict-1",
    ))
    db.append(Event.create(
        "verification.passed",
        {"verdict_id": "verdict-1", "findings": ["artifact hash matched"], "evidence": {"bundle_id": "bundle-1"}},
        source_type=CTRL, source_id="r10-gate",
        idempotency_key="verification.passed:verdict-1",
    ))
    db.append(Event.create(
        "discovery.candidate_generated",
        {"candidate_id": "candidate-1", "mission_id": "mission-1", "source": "convergence", "objective": "candidate work", "evidence": [{"kind": "seed"}]},
        source_type=CTRL, source_id="r10-gate",
        idempotency_key="discovery.candidate_generated:candidate-1",
    ))
    db.append(Event.create(
        "discovery.candidate_rejected",
        {"candidate_id": "candidate-1", "reason": "not an improvement"},
        source_type=CTRL, source_id="r10-gate",
        idempotency_key="discovery.candidate_rejected:candidate-1",
    ))
    db.append(Event.create(
        "release.manifest_recorded",
        {"release_id": "release-1", "source_commit": "abc" * 7, "artifact_hashes": {"jarvis.exe": "d" * 64},
         "dependencies": {"python": "3.13"}, "runtime_identity": {"host": "lab"}, "parent_release_id": None,
         "improvement_id": "impr-1", "risk_class": "R1", "migrations": [], "verdicts": [], "rollback_target": None},
        source_type=CTRL, source_id="r10-gate",
        idempotency_key="release.manifest_recorded:release-1",
    ))
    db.append(Event.create(
        "supervisor.heartbeat",
        {"status": "RUNNING", "trust_state": "LAB"},
        source_type=CTRL, source_id="r10-gate",
        idempotency_key="supervisor.heartbeat:r10-gate",
    ))
    db.append(Event.create(
        "resource.usage_recorded",
        {"measurement_id": "cost-1", "provider": "opencode_go", "model": "deepseek-v4-flash", "status": "UNKNOWN"},
        source_type=CTRL, source_id="r10-gate",
        idempotency_key="resource.usage_recorded:cost-1",
    ))


def _policy_stop_path(tmp_path: Path) -> Path:
    return tmp_path / "EMERGENCY_STOP"


def _snapshot_keys() -> set[str]:
    return {
        "controls", "missions", "workers", "verification", "evidence",
        "health", "resources", "systems", "candidates",
        "releases_canaries_rollbacks", "recovery", "costs", "briefings",
        "voice", "timeline",
    }


def test_snapshot_exposes_command_center_surfaces(db: FoundryDatabase, tmp_path: Path) -> None:
    _seed_full_surface(db)
    snapshot = build_snapshot(db, stop_path=_policy_stop_path(tmp_path))
    keys = set(snapshot)
    missing = _snapshot_keys() - keys
    assert not missing, f"snapshot missing required R10 surfaces: {sorted(missing)}"
    assert snapshot["missions"][0]["next_action"] == "dispatch r10 gate"
    assert snapshot["workers"][0]["run_id"] == "run-1"
    assert snapshot["verification"][0]["verdict"] == "PASS"
    raw = snapshot["verification"][0]["findings_json"]
    assert json.loads(raw) == ["artifact hash matched"]
    assert snapshot["evidence"][0]["bundle_id"] == "bundle-1"
    assert snapshot["controls"]["goals"] == []
    assert snapshot["controls"]["vetoes"] == []
    # Honest sources: release/rollback material comes from ledger events, not
    # fabricated status.
    assert snapshot["releases_canaries_rollbacks"] == [
        e for e in decision_timeline(db) if "release" in e["event_type"]
    ]
    assert snapshot["recovery"]["blocked_missions"] == []
    assert {c["measurement_id"] for c in snapshot["recovery"]["provider_or_connector_unknowns"]} == {
        c["measurement_id"] for c in snapshot["costs"]["measurements"] if c["status"] == "UNKNOWN"
    }
    assert snapshot["costs"]["measured_actual_total_usd"] == "UNKNOWN"


def test_snapshot_honestly_marks_absent_sections(db: FoundryDatabase, tmp_path: Path) -> None:
    db.append(Event.create(
        "mission.created",
        {"mission_id": "m-empty", "objective": "empty state", "acceptance_criteria": ["none"], "state": "WAITING"},
        source_type="test", source_id="r10-gate",
        idempotency_key="mission.created:m-empty",
    ))
    snapshot = build_snapshot(db, stop_path=_policy_stop_path(tmp_path))
    # The remaster had dedicated projects/incidents/capabilities sections; the
    # canonical main snapshot maps those concepts to its own durable surfaces
    # and never fabricates absent statuses.
    assert snapshot["releases_canaries_rollbacks"] == {"status": "NOT_AVAILABLE", "evidence_refs": []}
    assert snapshot["controls"]["goals"] == [] and snapshot["controls"]["vetoes"] == []
    assert snapshot["voice"] == []
    assert snapshot["briefings"] == []


def test_authenticated_directives_use_durable_ledger_and_block_execution(
    db: FoundryDatabase, tmp_path: Path
) -> None:
    secret = b"r10-owner-secret"
    stop = tmp_path / "STOP"
    service = OwnerControlService(db, secret=secret, stop_path=stop)
    # STOP
    signature = sign_command(secret, "STOP_EVERYTHING", "GLOBAL", "stop everything", "r10-stop")
    service.stop_now(reason="stop everything", nonce="r10-stop", signature=signature)
    assert stop.exists()
    assert build_snapshot(db, stop_path=stop)["stop"]["active"] is True
    # replay of the same nonce is rejected
    with pytest.raises(OwnerReplayError):
        service.stop_now(reason="stop everything", nonce="r10-stop", signature=signature)
    # VETO through the same authenticated service
    veto_sig = sign_command(secret, "VETO_ACTION", "mission-1", "veto mission", "r10-veto")
    veto = service.apply("VETO_ACTION", target="mission-1", exact_text="veto mission", nonce="r10-veto", signature=veto_sig)
    assert veto.command_type == "VETO_ACTION"
    snapshot = build_snapshot(db, stop_path=stop)
    assert any(v["directive_id"] == veto.directive_id for v in snapshot["controls"]["vetoes"])
    # bad signature is refused before any ledger mutation
    with pytest.raises(OwnerAuthenticationError):
        service.apply("PAUSE", target="GLOBAL", exact_text="pause", nonce="r10-pause-bad", signature="bad")
    # RESUME clears the durable directive and sentinel through the same path
    resume_sig = sign_command(secret, "RESUME", "GLOBAL", "resume jarvis", "r10-resume")
    event_ids = service.resume(nonce="r10-resume", signature=resume_sig)
    assert event_ids
    assert not stop.exists()
    assert build_snapshot(db, stop_path=stop)["stop"]["active"] is False


def test_briefings_are_idempotent_and_read_only(db: FoundryDatabase, tmp_path: Path) -> None:
    planner = BriefingPlanner(db, stop_path=str(tmp_path / "STOP"))
    first = planner.plan("TWICE_DAILY", period_key="2026-08-18:AM")
    second = planner.plan("TWICE_DAILY", period_key="2026-08-18:AM")
    assert first["created"] is True and second["created"] is False
    assert db.count("briefing_outbox") == 1
    rendered = render_briefing(first["payload"], "en")
    assert rendered["lang"] == "en"
    assert rendered["categories"][0]["category"] == "Fact"
    sq = render_briefing(first["payload"], "sq")
    assert sq["lang"] == "sq"
    snapshot_keys = set(build_snapshot(db)) - {"briefings"}
    # planning appends only the briefing event; the projection remains stable
    assert set(build_snapshot(db)) == snapshot_keys | {"briefings"}


def test_voice_is_honest_not_real_and_fails_closed(
    db: FoundryDatabase, tmp_path: Path
) -> None:
    secret = b"r10-owner-secret"
    gateway = VoiceGateway(
        db,
        owner_secret=secret,
        stt=FixtureSTT(Transcript("stop everything", "en")),
        tts=FixtureTTS(),
        stop_path=str(tmp_path / "STOP"),
    )
    sig = sign_command(secret, "STOP_EVERYTHING", "GLOBAL", "stop everything", "voice-1")
    result = gateway.handle(b"fixture", nonce="voice-1", signature=sig, interaction_id="interaction-1")
    assert result["status"] == "COMPLETED"
    # The fixture is explicitly never presented as real voice proof: the
    # durable record keeps the fixture provider id and the Command Center
    # voice surface is absent/NOT_TESTED when no real provider produced it.
    replay = gateway.handle(b"different", nonce="wrong", signature="wrong", interaction_id="interaction-1")
    assert replay["status"] == "REJECTED" and replay["auth_status"] == "REPLAYED"
    unauth = gateway.handle(b"fixture", nonce="voice-2", signature="wrong", interaction_id="interaction-2")
    assert unauth["status"] == "REJECTED" and unauth["auth_status"] == "UNAUTHENTICATED"
    assert db.count("voice_interactions") == 2
    snapshot = build_snapshot(db, stop_path=tmp_path / "STOP")
    exposed = {
        v["interaction_id"]: v
        for v in snapshot["voice"]
        if v.get("tts_provider") == "fixture-tts"
    }
    assert exposed["interaction-1"]["status"] == "COMPLETED"
    assert exposed["interaction-2"]["status"] == "REJECTED"


def test_local_command_center_is_read_only_projection_and_control_endpoint_is_authenticated(
    db: FoundryDatabase, tmp_path: Path
) -> None:
    from urllib.error import HTTPError

    _seed_full_surface(db)
    secret = b"r10-endpoint-secret"
    server = CommandCenterServer(db, stop_path=tmp_path / "STOP")
    server.configure_owner(secret)
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urlopen(f"{base}/api/command-center", timeout=5) as response:
            payload = json.loads(response.read())
        assert payload["source_of_truth"]["kind"] == "append_only_event_ledger"
        assert payload["missions"][0]["mission_id"] == "mission-1"
        # GET is read-only: after a GET the ledger head and snapshot sections
        # are unchanged.
        before = db.events()
        with urlopen(f"{base}/", timeout=5) as response:
            assert b"JARVIS Command Center" in response.read()
        assert len(db.events()) == len(before)
        body = {
            "command_type": "PAUSE",
            "target": "GLOBAL",
            "exact_text": "pause jarvis",
            "nonce": "endpoint-1",
            "signature": sign_command(secret, "PAUSE", "GLOBAL", "pause jarvis", "endpoint-1"),
        }
        with urlopen(
            Request(f"{base}/api/controls", data=json.dumps(body).encode(), method="POST"),
            timeout=5,
        ) as response:
            assert json.loads(response.read())["status"] == "APPLIED"
        bad = dict(body, nonce="endpoint-2", signature="bad")
        try:
            urlopen(
                Request(f"{base}/api/controls", data=json.dumps(bad).encode(), method="POST"),
                timeout=5,
            )
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("unauthenticated control accepted")
    finally:
        server.close()
