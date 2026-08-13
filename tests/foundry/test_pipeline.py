"""V5-S7: mission-loop integration — the connector-execution pipeline.

The full observe -> select -> envelope -> authorize -> execute ->
reconcile -> verify -> learn path through the V5 deterministic control
plane, with connector-outage isolation, STOP during queued/in-flight
work, interruption/resume via the effect journal, no duplicate
mission/effect, and transparent JARVIS representational identity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jarvis_foundry.connectors.contract import ConnectorSpec
from jarvis_foundry.connectors.registry import (disable_connector,
                                                record_health_sample,
                                                register_connector)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.execution.pipeline import (
    IDENTITY,
    ActionCandidate,
    authorize,
    build_envelope,
    execute,
    learn,
    observe,
    run_candidate,
    run_mission,
    select_candidate,
    verify,
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _register_system(db: FoundryDatabase, system_id: str) -> None:
    db.append(Event.create(
        "system.registered",
        {"system_id": system_id, "kind": "browser",
         "capabilities": {"write": "READY"}, "name": system_id,
         "repository": None, "folder": None},
        source_type="supervisor", source_id="v5-test",
        idempotency_key=f"system.registered:{system_id}"))


def _register_connector(db: FoundryDatabase, connector_id: str,
                        health: str = "HEALTHY") -> None:
    register_connector(db, ConnectorSpec(
        connector_id=connector_id, kind="filesystem", version="1.0.0",
        actions={"file.write": "write"},
        capabilities={"write": "READY"},
        retry_policy={"max_attempts": 3, "backoff_s": [1]}))
    record_health_sample(db, connector_id, health)


class _FakeStore:
    def __init__(self) -> None:
        self.content: dict[str, str] = {}
        self.calls = 0
        self.fail_with: BaseException | None = None

    def perform(self, ref: str, payload: str) -> str:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        self.content[ref] = payload
        return f"hash-after:{ref}:{len(payload)}"


def _candidate(action_id: str = "act-1", *, score: float = 0.5,
               connector_id: str = "conn-a",
               target: str = "sys-browser",
               capability: str = "write",
               expected_effect: str = "create notes.txt",
               goal_alignment: str = "mission-1") -> ActionCandidate:
    return ActionCandidate(
        action_id=action_id, action_kind="file.write",
        target_resources=(target,), capability=capability,
        expected_effect=expected_effect,
        expected_effects=(expected_effect,),
        before_state_capture={"ref": "snap-1", "hash": "h-before"},
        score=score, connector_id=connector_id,
        goal_alignment=goal_alignment)


def _mission_setup(db: FoundryDatabase) -> None:
    _register_system(db, "sys-browser")
    _register_connector(db, "conn-a")


# ---------------------------------------------------------------------------
# end-to-end pipeline
# ---------------------------------------------------------------------------

def test_end_to_end_mission(db: FoundryDatabase) -> None:
    _mission_setup(db)
    store = _FakeStore()
    result = run_mission(
        db, mission_id="mission-1", run_id="run-1",
        candidates=[_candidate()],
        perform_map={"conn-a": store.perform})
    assert result["mission_id"] == "mission-1"
    assert result["results"][0]["outcome"] == "COMPLETED"
    assert store.calls == 1
    assert result["identity"] == IDENTITY
    with db.connect() as connection:
        decided = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'action.decided'").fetchone()[0]
        issued = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'capability.issued'").fetchone()[0]
        outcomes = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'mission.outcome.recorded'").fetchone()[0]
    assert decided == 1 and issued == 1 and outcomes == 1


def test_pipeline_steps_in_order(db: FoundryDatabase) -> None:
    """The full chain works as individually callable steps."""
    _mission_setup(db)
    store = _FakeStore()
    observation = observe(db, mission_id="mission-1")
    assert "manifest" in observation and "connectors" in observation
    candidate = select_candidate(db, [_candidate()])
    assert candidate is not None and candidate.action_id == "act-1"
    envelope = build_envelope(candidate, mission_id="mission-1",
                              run_id="run-1")
    capability_id = authorize(db, envelope)
    executed = execute(db, envelope, capability_id,
                       connector_id="conn-a", perform=store.perform)
    assert executed["outcome"] == "COMPLETED"
    verified = verify(db, executed["action_key"], envelope)
    assert verified["verified"] is True
    learned = learn(db, "mission-1", "act-1", executed)
    assert learned["identity"] == IDENTITY


def test_no_duplicate_mission_effect(db: FoundryDatabase) -> None:
    """Running the same mission twice performs the effect ONCE."""
    _mission_setup(db)
    store = _FakeStore()
    candidates = [_candidate()]
    first = run_mission(db, "mission-1", "run-1", candidates,
                        perform_map={"conn-a": store.perform})
    second = run_mission(db, "mission-1", "run-1", candidates,
                         perform_map={"conn-a": store.perform})
    assert first["results"][0]["action_key"] == \
        second["results"][0]["action_key"]
    assert store.calls == 1
    with db.connect() as connection:
        started = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'system.action_started'").fetchone()[0]
    assert started == 1


# ---------------------------------------------------------------------------
# outage isolation + STOP
# ---------------------------------------------------------------------------

def test_connector_outage_isolated(db: FoundryDatabase) -> None:
    """One connector's outage never stops unrelated missions: the dead
    connector's candidate FAILED with a reason, the healthy one
    COMPLETED."""
    _mission_setup(db)
    _register_connector(db, "conn-b", health="DEGRADED")
    store = _FakeStore()
    result = run_mission(
        db, "mission-1", "run-1",
        candidates=[_candidate(action_id="act-a", connector_id="conn-a"),
                    _candidate(action_id="act-b", connector_id="conn-b",
                               expected_effect="create notes-b.txt")],
        perform_map={"conn-a": store.perform, "conn-b": store.perform})
    outcomes = {r["action_id"]: r["outcome"] for r in result["results"]}
    assert outcomes["act-b"] == "NOT_READY"  # outage blocks selection
    assert "health" in [r["reason"] for r in result["results"]
                        if r["action_id"] == "act-b"][0]
    assert outcomes["act-a"] == "COMPLETED"
    assert store.calls == 1  # only the healthy connector ran


def test_stop_blocks_queued_work(db: FoundryDatabase, tmp_path: Path) -> None:
    """STOP during queued work: authorized-but-not-yet-executed
    candidates refuse; already-completed work stays completed."""
    _mission_setup(db)
    store = _FakeStore()
    stop_path = tmp_path / "EMERGENCY_STOP"
    candidates = [_candidate(action_id="act-a"),
                  _candidate(action_id="act-b")]
    # authorize the first, then STOP, then run the queue
    envelope_a = build_envelope(candidates[0], mission_id="mission-1",
                                run_id="run-1")
    authorize(db, envelope_a)
    stop_path.write_text("", encoding="utf-8")
    result = run_mission(
        db, "mission-1", "run-1", candidates,
        perform_map={"conn-a": store.perform}, stop_path=stop_path)
    assert store.calls == 0  # nothing executed after STOP
    reasons = {r["action_id"]: r["reason"] for r in result["results"]}
    assert "STOP" in reasons["act-a"] and "STOP" in reasons["act-b"]


def test_stop_after_completion_stays_completed(db: FoundryDatabase,
                                               tmp_path: Path) -> None:
    _mission_setup(db)
    store = _FakeStore()
    stop_path = tmp_path / "EMERGENCY_STOP"
    result = run_mission(db, "mission-1", "run-1", [_candidate()],
                         perform_map={"conn-a": store.perform})
    assert result["results"][0]["outcome"] == "COMPLETED"
    stop_path.write_text("", encoding="utf-8")
    # a retried delivery after STOP is an idempotent no-op, never a re-run
    retry = run_mission(db, "mission-1", "run-1", [_candidate()],
                        perform_map={"conn-a": store.perform},
                        stop_path=stop_path)
    assert retry["results"][0]["outcome"] == "COMPLETED"
    assert store.calls == 1


def test_interruption_resume_reaps_crash(db: FoundryDatabase) -> None:
    """Interruption between STARTED and COMPLETED: resume reaps the
    crash (FROZEN), never duplicates the effect."""
    _mission_setup(db)
    store = _FakeStore()
    envelope = build_envelope(_candidate(), mission_id="mission-1",
                              run_id="run-1")
    capability_id = authorize(db, envelope)
    # crash: STARTED recorded, process died before completion
    from jarvis_foundry.effects import effect_identity
    from jarvis_foundry.systems import record_external_action
    key, system_id, target_ref, effect_hash = effect_identity(db, envelope)
    record_external_action(db, system_id, target_ref, effect_hash,
                           kind="file.write", started=True)
    result = run_candidate(db, "mission-1", "run-1", _candidate(),
                           connector_id="conn-a", perform=store.perform)
    assert result["outcome"] == "FROZEN"
    assert store.calls == 0


# ---------------------------------------------------------------------------
# selection determinism + capability gates
# ---------------------------------------------------------------------------

def test_selection_deterministic_tie_break(db: FoundryDatabase) -> None:
    _mission_setup(db)
    candidates = [_candidate(action_id="act-b", score=0.5),
                  _candidate(action_id="act-a", score=0.5)]
    first = select_candidate(db, candidates)
    second = select_candidate(db, candidates)
    assert first.action_id == second.action_id == "act-a"  # lowest id


def test_selection_prefers_highest_score(db: FoundryDatabase) -> None:
    _mission_setup(db)
    candidates = [_candidate(action_id="act-low", score=0.2),
                  _candidate(action_id="act-high", score=0.8)]
    assert select_candidate(db, candidates).action_id == "act-high"


def test_not_ready_capability_not_selected(db: FoundryDatabase) -> None:
    _mission_setup(db)
    _register_connector(db, "conn-weak")
    db.append(Event.create(
        "system.registered",
        {"system_id": "sys-weak", "kind": "browser",
         "capabilities": {"write": "PROBING"}, "name": "sys-weak",
         "repository": None, "folder": None},
        source_type="supervisor", source_id="v5-test",
        idempotency_key="system.registered:sys-weak"))
    candidate = _candidate(action_id="act-weak", target="sys-weak")
    assert select_candidate(db, [candidate]) is None


def test_identity_is_transparent(db: FoundryDatabase) -> None:
    assert IDENTITY["role"] == "representational"
    assert IDENTITY["acting_for"] == "owner"
    _mission_setup(db)
    store = _FakeStore()
    result = run_mission(db, "mission-1", "run-1", [_candidate()],
                         perform_map={"conn-a": store.perform})
    assert result["identity"] == IDENTITY
    with db.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'mission.outcome.recorded' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
    import json
    assert json.loads(row[0])["identity"] == IDENTITY


# ---------------------------------------------------------------------------
# S7 round-1 verifier (deleg_0b2cbd47) regressions: M1/M2 + LOW + I1
# ---------------------------------------------------------------------------

def test_rerun_produces_no_outcome_drift(db: FoundryDatabase) -> None:
    """M1: an idempotent re-delivery must NOT append duplicate
    mission.outcome.recorded events — the re-delivery result carries
    the RECORDED details (after_state/cost), so learn()'s payload-hash
    key matches the original event and dedupes."""
    _mission_setup(db)
    store = _FakeStore()
    candidates = [_candidate()]
    run_mission(db, "mission-1", "run-1", candidates,
                perform_map={"conn-a": store.perform})
    run_mission(db, "mission-1", "run-1", candidates,
                perform_map={"conn-a": store.perform})
    db.rebuild_projections()
    run_mission(db, "mission-1", "run-1", candidates,
                perform_map={"conn-a": store.perform})
    with db.connect() as connection:
        outcomes = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'mission.outcome.recorded'").fetchone()[0]
        with_consequence = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'mission.outcome.recorded' AND json_extract(payload_json, "
            "'$.consequence') IS NOT NULL").fetchone()[0]
    assert outcomes == 1  # no drift across runs + rebuild
    assert with_consequence == 1  # the recorded consequence is kept


def test_secret_like_candidate_refused_at_boundary(db: FoundryDatabase) -> None:
    """M2: secret-like free-text candidate fields are refused at the
    mission-loop ingestion boundary — the candidate is REFUSED in
    isolation and the ledger never carries the secret text."""
    _mission_setup(db)
    store = _FakeStore()
    evil = _candidate(action_id="act-evil",
                      goal_alignment="api_key=sk-live-1234")
    with pytest.raises(ValueError):
        build_envelope(evil, mission_id="mission-1", run_id="run-1")
    result = run_mission(db, "mission-1", "run-1", [evil],
                         perform_map={"conn-a": store.perform})
    assert result["results"][0]["outcome"] == "REFUSED"
    assert store.calls == 0
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT payload_json FROM events WHERE payload_json LIKE "
            "'%sk-live-1234%'").fetchall()
    assert rows == []  # the secret never entered the ledger


def test_fresh_execution_result_shape_uniform(db: FoundryDatabase) -> None:
    """LOW: a fresh execution carries performed=True and the recorded
    cost like every other path."""
    _mission_setup(db)
    store = _FakeStore()
    result = run_mission(db, "mission-1", "run-1", [_candidate()],
                         perform_map={"conn-a": store.perform})
    detail = result["results"][0]
    assert detail["performed"] is True
    assert "cost" in detail


def test_foreign_envelope_does_not_burn_capability(db: FoundryDatabase) -> None:
    """I1: an envelope whose ticket is out of scope must be refused
    BEFORE the capability is consumed — a foreign envelope never burns
    a legitimate capability."""
    _mission_setup(db)
    store = _FakeStore()
    candidate = _candidate()
    envelope = build_envelope(candidate, mission_id="mission-1",
                              run_id="run-1")
    capability_id = authorize(db, envelope)
    # same action_id, foreign mission — the ticket is out of scope
    foreign = build_envelope(candidate, mission_id="mission-999",
                             run_id="run-1")
    with pytest.raises(PermissionError):
        execute(db, foreign, capability_id, connector_id="conn-a",
                perform=store.perform)
    with db.connect() as connection:
        used = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'capability.used'").fetchone()[0]
    assert used == 0  # nothing consumed by the refused attempt
    assert store.calls == 0
