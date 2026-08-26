from __future__ import annotations

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.trust import (
    REQUIRED_EVIDENCE,
    TrustError,
    assess_trust,
    record_trust_evidence,
    transition_trust,
    trust_status,
)
from jarvis_foundry.supervisor.service import Supervisor


def _database(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_trust_starts_in_lab_and_refuses_missing_evidence(tmp_path):
    database = _database(tmp_path)
    assessment = assess_trust(database)
    assert assessment.state == "LAB"
    assert assessment.recommended_state == "LAB"
    assert assessment.eligible is False
    with pytest.raises(TrustError, match="missing evidence"):
        transition_trust(database, "TRUSTED_AUTONOMOUS", reason="premature")


def test_non_supervisor_evidence_cannot_promote(tmp_path):
    database = _database(tmp_path)
    database.append(Event.create(
        "trust.evidence_recorded",
        {"evidence_key": REQUIRED_EVIDENCE[0], "status": "PASS",
         "evidence": {"source": "builder"}, "evidence_hash": "bad"},
        source_type="builder", source_id="builder-1",
        idempotency_key="forged-trust-evidence",
    ))
    assert assess_trust(database).checks == {}
    with pytest.raises(TrustError, match="Supervisor-owned"):
        record_trust_evidence(
            database, REQUIRED_EVIDENCE[0], status="PASS",
            evidence={"run": "x"}, controller="builder",
        )


def test_all_evidence_promotes_and_retry_returns_immutable_event(tmp_path):
    database = _database(tmp_path)
    events = {}
    for key in REQUIRED_EVIDENCE:
        first = record_trust_evidence(
            database, key, status="PASS", evidence={"profile": key},
        )
        second = record_trust_evidence(
            database, key, status="PASS", evidence={"profile": key},
        )
        assert first.event_id == second.event_id
        events[key] = first.event_id
    assessment = assess_trust(database)
    assert assessment.eligible is True
    assert assessment.missing == ()
    assert assessment.recommended_state == "TRUSTED_AUTONOMOUS"
    transition_trust(database, "PROVING", reason="acceptance evidence collected")
    promoted = transition_trust(
        database, "TRUSTED_AUTONOMOUS", reason="all acceptance dimensions passed",
    )
    assert trust_status(database)["state"] == "TRUSTED_AUTONOMOUS"
    assert promoted.payload["evidence_digest"] == assessment.evidence_digest


def test_trust_demotion_is_persisted_and_projection_rebuilds(tmp_path):
    database = _database(tmp_path)
    for key in REQUIRED_EVIDENCE:
        record_trust_evidence(
            database, key, status="PASS", evidence={"profile": key},
        )
    transition_trust(database, "PROVING", reason="promotion evidence staged")
    transition_trust(database, "TRUSTED_AUTONOMOUS", reason="promotion proof")
    demoted = transition_trust(database, "DEGRADED", reason="runtime parity drift")
    assert demoted.payload["to_state"] == "DEGRADED"
    assert trust_status(database)["state"] == "DEGRADED"
    database.rebuild_projections()
    assert trust_status(database)["state"] == "DEGRADED"
    assert trust_status(database)["history"][-1]["to_state"] == "DEGRADED"


def test_trusted_state_survives_supervisor_restart(tmp_path):
    database = _database(tmp_path)
    for key in REQUIRED_EVIDENCE:
        record_trust_evidence(
            database, key, status="PASS", evidence={"profile": key},
        )
    transition_trust(database, "PROVING", reason="all proof recorded")
    transition_trust(database, "TRUSTED_AUTONOMOUS", reason="certification complete")

    stop_path = tmp_path / "EMERGENCY_STOP"
    first = Supervisor(database, stop_path, instance_id="trust-restart-a")
    assert first.start() == "RUNNING"
    first.stop()
    assert trust_status(database)["state"] == "TRUSTED_AUTONOMOUS"

    second = Supervisor(database, stop_path, instance_id="trust-restart-b")
    assert second.start() == "RUNNING"
    try:
        assert trust_status(database)["state"] == "TRUSTED_AUTONOMOUS"
        started = [event for event in database.events()
                   if event.event_type == "supervisor.started"][-1]
        assert started.payload["trust_state"] == "TRUSTED_AUTONOMOUS"
    finally:
        second.stop()
