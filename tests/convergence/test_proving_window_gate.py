"""Focused proving-window gate on canonical main.

The gate proves the durable Supervisor-owned proving contract without
fabricating elapsed time: registration, restart-safe replay, healthy-day
counting from real heartbeat/degraded/STOP evidence, verified-mission
counting, degraded/stop day exclusion, and honest WAITING until the real
duration and mission criteria are met. Provider/voice/UI/external connector
effects stay NOT_TESTED boundaries.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.verification import (
    record_verification_acceptance,
    record_verification_outcome,
    start_verification,
)
from jarvis_foundry.proving import (
    ProvingError,
    checkpoint_window,
    close_window,
    healthy_days,
    register_window,
    verified_mission_count,
    window_status,
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "proving.db")
    database.initialize()
    return database


def _now() -> datetime:
    return datetime.now(UTC)


def _append(db: FoundryDatabase, event_type: str, payload: dict,
            *, occurred_at: datetime, key: str,
            source: str = "test") -> None:
    db.append(Event.create(
        event_type, payload,
        source_type="test", source_id=source,
        occurred_at=isoformat(occurred_at),
        idempotency_key=key,
    ))


def _heartbeat(db: FoundryDatabase, at: datetime, *, instance: str = "sup-1",
               index: int = 0) -> None:
    _append(db, "supervisor.heartbeat",
            {"instance_id": instance, "trust_state": "LAB"},
            occurred_at=at, key=f"hb:{instance}:{index}")


def _verified_mission(db: FoundryDatabase, mission_id: str, *,
                      builder: str = "builder-run-1",
                      verifier: str = "verifier-run-1",
                      occurred_at: datetime | None = None) -> None:
    occurred = occurred_at or _now()
    db.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": f"objective {mission_id}",
         "acceptance_criteria": ["durable pass"]},
        source_type="test", source_id="test-suite",
        occurred_at=isoformat(occurred),
        idempotency_key=f"mission.created:{mission_id}",
    ))
    db.append(Event.create(
        "evidence.created",
        {"bundle_id": f"bundle-{mission_id}", "mission_id": mission_id,
         "objective": f"objective {mission_id}", "criteria": ["durable pass"]},
        source_type="test", source_id="test-suite",
        occurred_at=isoformat(occurred),
        idempotency_key=f"evidence.created:{mission_id}",
    ))
    db.append(Event.create(
        "mission.completed",
        {"mission_id": mission_id, "objective": f"objective {mission_id}"},
        source_type="test", source_id="test-suite",
        occurred_at=isoformat(occurred),
        idempotency_key=f"mission.completed:{mission_id}",
    ))
    start_verification(
        db,
        verdict_id=f"verdict-{mission_id}",
        mission_id=mission_id,
        step_id="step-1",
        builder_run_id=builder,
        verifier_run_id=verifier,
        independence={"provider_a": "commandcode", "provider_b": "commandcode",
                      "fresh": True},
        criteria=["durable pass"],
    )
    for event in db.events():
        if event.event_type == "verification.started" and \
                event.payload["verdict_id"] == f"verdict-{mission_id}":
            occurred_at = event.occurred_at
            break
    record_verification_outcome(
        db,
        verdict_id=f"verdict-{mission_id}",
        outcome="PASS",
        findings=[],
        evidence={"artifact": f"evidence-{mission_id}"},
    )
    record_verification_acceptance(
        db,
        verdict_id=f"verdict-{mission_id}",
        mission_id=mission_id,
        step_id="step-1",
        builder_run_id=builder,
        verifier_run_id=verifier,
        checks={"test": "accepted"},
    )


def _register(db: FoundryDatabase, window_id: str = "proving-1", *,
              started: datetime | None = None,
              days: int = 7, missions: int = 10) -> None:
    register_window(
        db, window_id,
        required_days=days,
        required_missions=missions,
        started_at=isoformat(started or _now()),
        mission_id="mission-window",
    )


def test_registration_is_append_only_and_idempotent(db: FoundryDatabase) -> None:
    _register(db, started=_now())
    rows = [event for event in db.events()
            if event.event_type == "proving.window_registered"]
    assert len(rows) == 1
    assert rows[0].payload["controller"] == "supervisor"
    assert db.verify_event_integrity() == len(db.events())

    with pytest.raises(ProvingError):
        register_window(db, "proving-1", started_at=isoformat(_now()),
                        required_days=0, controller="supervisor")


def test_registration_refuses_non_supervisor(db: FoundryDatabase) -> None:
    with pytest.raises(ProvingError):
        register_window(db, "proving-x", controller="worker")


def test_restart_safe_replay_after_reopen(db: FoundryDatabase,
                                          tmp_path: Path) -> None:
    started = _now()
    _register(db, started=started)
    _heartbeat(db, started)
    _heartbeat(db, started + timedelta(days=1))
    _verified_mission(db, "mission-a")
    observed = _now()
    before = window_status(db, "proving-1", observed_at=observed)

    reopened = FoundryDatabase(tmp_path / "proving.db")
    after = window_status(reopened, "proving-1", observed_at=observed)
    assert after == before
    assert after["state"] == "OPEN"
    assert reopened.rebuild_projections() >= len(
        [event for event in reopened.events()
         if event.event_type.startswith("proving.")])


def test_healthy_day_counting_and_observed_window(db: FoundryDatabase) -> None:
    started = _now() - timedelta(days=4)
    _register(db, started=started)
    for day in range(5):
        _heartbeat(db, started + timedelta(days=day), index=day)
    observed = started + timedelta(days=5)
    days = healthy_days(db, "proving-1", observed_at=observed)
    assert days["count"] == 5
    assert days["excluded"] == []
    status = window_status(db, "proving-1", observed_at=observed)
    assert status["elapsed_days"] == 5
    assert status["healthy_days"] == 5
    assert status["verdict"] == "WAITING"
    assert status["met"] is False


def test_degraded_and_stop_days_are_excluded(db: FoundryDatabase) -> None:
    started = _now() - timedelta(days=5)
    _register(db, started=started)
    for day in range(6):
        if day in (1, 3):
            _append(db, "supervisor.degraded",
                    {"instance_id": "sup-1", "trust_state": "LAB",
                     "reason": "test degraded"},
                    occurred_at=started + timedelta(days=day),
                    key=f"degraded:{day}")
        else:
            _heartbeat(db, started + timedelta(days=day), index=day)
    _append(db, "owner_directive.recorded",
            {"directive_id": "dir-pause", "command_type": "PAUSE",
             "target": "GLOBAL", "exact_text": "pause", "authenticated": True},
            occurred_at=started + timedelta(days=4),
            key="pause:dir-pause")
    _append(db, "owner_directive.revoked",
            {"directive_id": "dir-pause", "authenticated": True},
            occurred_at=started + timedelta(days=5),
            key="pause:revoke")
    observed = started + timedelta(days=6)
    days = healthy_days(db, "proving-1", observed_at=observed)
    assert days["count"] == 2
    reasons = {entry["reason"] for entry in days["excluded"]}
    assert reasons == {"no heartbeat", "stop/pause directive"}


def test_verified_mission_count_requires_independent_pass(
        db: FoundryDatabase) -> None:
    _verified_mission(db, "mission-a")
    _verified_mission(db, "mission-b", builder="same-run",
                      verifier="same-run")
    assert verified_mission_count(db) == 1


def test_verified_mission_count_excludes_pre_window_work(
        db: FoundryDatabase) -> None:
    started = _now() - timedelta(days=4)
    _register(db, started=started, days=2, missions=2)
    _verified_mission(db, "mission-old",
                      occurred_at=started - timedelta(days=2))
    _verified_mission(db, "mission-new",
                      occurred_at=started + timedelta(days=1))
    status = window_status(db, "proving-1", observed_at=started + timedelta(days=5))
    assert status["verified_mission_count"] == 1
    assert status["met"] is False
    assert verified_mission_count(
        db, started_at=isoformat(started)) == 1
    assert verified_mission_count(db) == 2


def test_authenticated_veto_and_stop_mission_exclude_days(
        db: FoundryDatabase) -> None:
    started = _now() - timedelta(days=4)
    _register(db, started=started, days=2, missions=1)
    for day in range(4):
        _heartbeat(db, started + timedelta(days=day), index=day)
    _append(db, "owner_directive.recorded",
            {"directive_id": "dir-veto", "command_type": "VETO_ACTION",
             "target": "mission-window", "exact_text": "veto proving window",
             "authenticated": True},
            occurred_at=started + timedelta(days=1),
            key="veto:dir-veto")
    _append(db, "owner_directive.recorded",
            {"directive_id": "dir-stop-mission",
             "command_type": "STOP_MISSION", "target": "GLOBAL",
             "exact_text": "stop the window mission",
             "authenticated": True},
            occurred_at=started + timedelta(days=2),
            key="stop-mission:dir-stop-mission")
    observed = started + timedelta(days=3)
    days = healthy_days(db, "proving-1", observed_at=observed)
    assert days["count"] == 1
    reasons = {entry["reason"] for entry in days["excluded"]}
    assert reasons == {"veto/stop-mission directive"}


def test_revoked_veto_and_stop_mission_do_not_exclude_days(
        db: FoundryDatabase) -> None:
    started = _now() - timedelta(days=4)
    _register(db, started=started, days=2, missions=1)
    for day in range(4):
        _heartbeat(db, started + timedelta(days=day), index=day)
    _append(db, "owner_directive.recorded",
            {"directive_id": "dir-veto", "command_type": "VETO_ACTION",
             "target": "mission-window", "exact_text": "veto proving window",
             "authenticated": True},
            occurred_at=started + timedelta(days=1),
            key="veto:dir-veto")
    _append(db, "owner_directive.recorded",
            {"directive_id": "dir-stop-mission",
             "command_type": "STOP_MISSION", "target": "GLOBAL",
             "exact_text": "stop the window mission",
             "authenticated": True},
            occurred_at=started + timedelta(days=2),
            key="stop-mission:dir-stop-mission")
    _append(db, "owner_directive.revoked",
            {"directive_id": "dir-veto", "authenticated": True},
            occurred_at=started + timedelta(days=2),
            key="veto:revoke")
    _append(db, "owner_directive.revoked",
            {"directive_id": "dir-stop-mission", "authenticated": True},
            occurred_at=started + timedelta(days=2),
            key="stop-mission:revoke")
    observed = started + timedelta(days=1)
    days = healthy_days(db, "proving-1", observed_at=observed)
    assert days["count"] == 1


def test_window_stays_waiting_until_real_criteria_met(
        db: FoundryDatabase) -> None:
    started = _now() - timedelta(days=3)
    _register(db, started=started, days=2, missions=1)
    for day in range(4):
        _heartbeat(db, started + timedelta(days=day), index=day)
    _verified_mission(db, "mission-a")
    observed = started + timedelta(days=4)
    status = window_status(db, "proving-1", observed_at=observed)
    assert status["met"] is True
    assert status["verdict"] == "READY"
    close_window(db, "proving-1", observed_at=observed)
    status = window_status(db, "proving-1", observed_at=observed)
    assert status["state"] == "PASSED"
    assert status["verdict"] == "PASSED"
    with pytest.raises(ProvingError):
        close_window(db, "proving-1", observed_at=observed)
    with pytest.raises(ProvingError):
        checkpoint_window(db, "proving-1", observed_at=observed)


def test_checkpoint_and_termination_outcomes_require_decision(
        db: FoundryDatabase) -> None:
    started = _now() - timedelta(days=1)
    _register(db, started=started, days=2, missions=1)
    _heartbeat(db, started)
    checkpoint_window(db, "proving-1", checkpoint_id="cp-1",
                      observed_at=started)
    rows = [event for event in db.events()
            if event.event_type == "proving.window_checkpoint"]
    assert len(rows) == 1
    assert rows[0].payload["snapshot"]["external_effect_boundaries"] == {
        "provider": "NOT_TESTED",
        "voice": "NOT_TESTED",
        "ui": "NOT_TESTED",
        "connectors": "NOT_TESTED",
    }
    with pytest.raises(ProvingError):
        close_window(db, "proving-1", observed_at=started,
                     outcome="FAILED", reason="")
    close_window(db, "proving-1", observed_at=started,
                 outcome="SUSPENDED", reason="owner directive")
    status = window_status(db, "proving-1", observed_at=started)
    assert status["state"] == "SUSPENDED"


def test_provider_voice_ui_connectors_remain_not_tested(
        db: FoundryDatabase) -> None:
    _register(db, started=_now())
    status = window_status(db, "proving-1")
    assert status["external_effect_boundaries"] == {
        "provider": "NOT_TESTED",
        "voice": "NOT_TESTED",
        "ui": "NOT_TESTED",
        "connectors": "NOT_TESTED",
    }
