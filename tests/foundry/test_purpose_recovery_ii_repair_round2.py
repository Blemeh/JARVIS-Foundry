"""Repair round 2 pins — the defects the final adversarial review of bd47279 found.

Each test fails on the reviewed tree and passes only with the repair, so none of
them is vacuous:

* ``test_only_existing_research_wakes_are_reported`` — the cycle named wake ids
  that did not exist (every ``research_bridged`` row's ``wake_id`` was merged
  into ``triggered_wakes`` unconditionally).
* ``test_failed_completion_is_not_a_durable_completion`` — the completion
  receipt matched a FAILED attempt, so a failed pass could release a wake under
  the ``research_completion_durable`` basis.
* ``test_colliding_consumption_is_reported_not_swallowed`` — a colliding
  consumption receipt was swallowed and the caller saw nothing.
* ``test_resolver_declines_a_declaration_whose_file_is_absent`` — the expansion
  resolver certified an adaptation whose declared tracked file is not on disk.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid

import pytest

from jarvis_foundry.capability_solution_discovery import (
    CapabilityCoverage,
    CompatibilityAssessment,
    IntegrationCostEstimate,
    MaintenanceEvidence,
    MaturityEvidence,
    ReuseTier,
    SolutionCandidate,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import canonical_json
from jarvis_foundry.executive.continuous import (
    RESEARCH_WAKE_PREFIX,
    WakeCondition,
    _research_completion_receipt,
    _triggered_research_wakes,
    consume_research_wakes,
    create_wake,
    rebuild_wake_state,
)
from jarvis_foundry.fabric.expansion_resolver import (
    JARVIS_SOURCE_ADAPTERS,
    _declared_source_present,
    canonical_blueprint_resolver,
)
from jarvis_foundry.fabric.gap_bridge import ExpansionWorkRequest

CAPABILITY = "cap.hash.file.sha256"
ACTION = "hash.file.sha256"


def _database(tmp_path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _raw_event(db: FoundryDatabase, event_type: str, payload: dict, *, key: str) -> None:
    """Write one ledger event without the projection.

    The research projection refuses a completion for an item that was never
    started, and the reviewed defect lived in the receipt lookup itself, so the
    lookup is exercised directly instead of through the writer.
    """

    with db.connect() as connection:
        columns = [str(row["name"]) for row in connection.execute("PRAGMA table_info(events)")]
        version = connection.execute(
            "SELECT schema_version FROM events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        payload_json = canonical_json(payload)
        values = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "occurred_at": "2026-09-19T00:00:00+00:00",
            "recorded_at": "2026-09-19T00:00:00+00:00",
            "source_type": "supervisor",
            "source_id": "repair-round2-test",
            "correlation_id": "",
            "idempotency_key": key,
            "schema_version": int(version["schema_version"]) if version else 1,
            "payload_json": payload_json,
            "payload_hash": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        }
        names = [name for name in values if name in columns]
        connection.execute(
            f"INSERT INTO events ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
            [values[name] for name in names],
        )
        connection.commit()


def _research_wake(db: FoundryDatabase, research_id: str) -> str:
    wake_id = f"{RESEARCH_WAKE_PREFIX}{research_id}"
    create_wake(
        db,
        WakeCondition(
            wake_id=wake_id,
            objective_id=f"initiative-objective:candidate-{research_id}",
            kind="WAIT_FOR_SIGNAL",
            reason="repair-round2 pin",
            created_at="2026-09-19T00:00:00+00:00",
            not_before="2026-09-19T00:00:00+00:00",
            signal=f"research.candidate_completed:{research_id}",
        ),
        controller="supervisor",
    )
    return wake_id


def test_only_existing_research_wakes_are_reported(tmp_path) -> None:
    """A bridged id whose wake does not exist must not be reported as triggered."""

    db = _database(tmp_path)
    unbound = [
        {
            "wake_id": f"{RESEARCH_WAKE_PREFIX}research-never-created",
            "objective_bound": False,
            "wake_created": False,
        }
    ]
    assert _triggered_research_wakes(db, unbound) == []

    real = _research_wake(db, "research-really-created")
    assert _triggered_research_wakes(db, unbound + [{"wake_id": real}]) == [real]
    assert _triggered_research_wakes(db, unbound) == []


def test_failed_completion_is_not_a_durable_completion(tmp_path) -> None:
    """Only a COMPLETED attempt may release a wake as durable."""

    db = _database(tmp_path)
    base = {
        "candidate_id": "candidate-repair",
        "cycle_id": "repair",
        "pass_number": 1,
        "evidence": {},
    }
    _raw_event(
        db,
        "research.candidate_completed",
        {**base, "research_id": "research-failed", "status": "FAILED"},
        key="repair-test:failed",
    )
    _raw_event(
        db,
        "research.candidate_completed",
        {**base, "research_id": "research-completed", "status": "COMPLETED"},
        key="repair-test:completed",
    )
    assert _research_completion_receipt(db, "research-failed") is None
    assert _research_completion_receipt(db, "research-completed") is not None


def test_colliding_consumption_is_reported_not_swallowed(tmp_path, monkeypatch) -> None:
    """A colliding consumption receipt must surface, not vanish."""

    import jarvis_foundry.executive.continuous as continuous
    from jarvis_foundry.events import EventConflict

    db = _database(tmp_path)
    research_id = "research-repair-collision"
    _research_wake(db, research_id)
    _raw_event(
        db,
        "research.candidate_completed",
        {
            "research_id": research_id,
            "candidate_id": "candidate-repair",
            "cycle_id": "repair",
            "pass_number": 1,
            "status": "COMPLETED",
            "evidence": {},
        },
        key="repair-test:durable",
    )

    first = consume_research_wakes(
        db, cycle_id="repair-1", consumer_id="worker", controller="supervisor"
    )
    # The consumption receipt is written for every ACTIVE research wake, whether
    # or not this scratch ledger binds its objective, so the collision below is
    # exercised independently of the release decision.
    assert any(
        str(item.get("wake_id")) == f"{RESEARCH_WAKE_PREFIX}{research_id}"
        for item in first
    )
    assert db.event_count(event_type="executive.research_wake_consumed") == 1

    real_append = continuous._append
    state = {"raised": False}

    def _flaky_append(db_, event_type, payload, **kwargs):
        if event_type == "executive.research_wake_consumed" and not state["raised"]:
            state["raised"] = True
            raise EventConflict("idempotency conflict (injected)")
        return real_append(db_, event_type, payload, **kwargs)

    monkeypatch.setattr(continuous, "_append", _flaky_append)
    second = consume_research_wakes(
        db, cycle_id="repair-2", consumer_id="worker", controller="supervisor"
    )
    monkeypatch.undo()

    assert state["raised"] is True
    skipped = [item for item in second if item.get("consumed") is False]
    assert skipped, "the colliding consumption must produce a receipt"
    assert skipped[0]["reason"] == "already_recorded_for_this_consumer"
    assert db.event_count(event_type="executive.research_wake_consumption_skipped") == 1


def test_a_skipped_consumption_writes_no_continuation(tmp_path, monkeypatch) -> None:
    """Repair round 3: a skip receipt must not become a 'continued' record.

    The round-2 repair returned the skip receipt in the consumer's list, and
    that list is what ``run_continuous_cycle`` hands to
    ``record_research_wake_continuation``.  Writing "continued" for a wake the
    runtime explicitly skipped is a false record (found by the review of
    862b745).
    """

    import jarvis_foundry.executive.continuous as continuous
    from jarvis_foundry.events import EventConflict
    from jarvis_foundry.executive.continuous import record_research_wake_continuation

    db = _database(tmp_path)
    research_id = "research-repair-continuation"
    _research_wake(db, research_id)
    _raw_event(
        db,
        "research.candidate_completed",
        {
            "research_id": research_id,
            "candidate_id": "candidate-repair",
            "cycle_id": "repair",
            "pass_number": 1,
            "status": "COMPLETED",
            "evidence": {},
        },
        key="repair-test:continuation",
    )

    real_append = continuous._append
    state = {"raised": False}

    def _flaky_append(db_, event_type, payload, **kwargs):
        if event_type == "executive.research_wake_consumed" and not state["raised"]:
            state["raised"] = True
            raise EventConflict("idempotency conflict (injected)")
        return real_append(db_, event_type, payload, **kwargs)

    monkeypatch.setattr(continuous, "_append", _flaky_append)
    receipts = consume_research_wakes(
        db, cycle_id="continuation-1", consumer_id="worker", controller="supervisor"
    )
    monkeypatch.undo()

    skipped = [item for item in receipts if item.get("consumed") is False]
    assert skipped, "the collision must still produce its skip receipt"
    written = record_research_wake_continuation(
        db, cycle_id="continuation-1", consumed=receipts, controller="supervisor"
    )
    assert written == [], "a skipped consumption must not be recorded as continued"
    assert db.event_count(event_type="executive.research_wake_continued") == 0


def test_a_real_consumption_still_writes_a_continuation(tmp_path) -> None:
    """Positive control for the guard above: a real consumption is continued."""

    from jarvis_foundry.executive.continuous import record_research_wake_continuation

    db = _database(tmp_path)
    research_id = "research-repair-continuation-positive"
    _research_wake(db, research_id)
    _raw_event(
        db,
        "research.candidate_completed",
        {
            "research_id": research_id,
            "candidate_id": "candidate-repair",
            "cycle_id": "repair",
            "pass_number": 1,
            "status": "COMPLETED",
            "evidence": {},
        },
        key="repair-test:continuation-positive",
    )
    receipts = consume_research_wakes(
        db, cycle_id="continuation-2", consumer_id="worker", controller="supervisor"
    )
    assert receipts and receipts[0].get("consumed") is not False
    written = record_research_wake_continuation(
        db, cycle_id="continuation-2", consumed=receipts, controller="supervisor"
    )
    assert written and db.event_count(event_type="executive.research_wake_continued") == 1


def test_status_less_completion_is_durable_like_the_projection(tmp_path) -> None:
    """Repair round 3: the receipt mirrors the projection's own status default.

    ``projections.py`` seals a completion that carries no status as COMPLETED,
    so a status-less event must be a durable completion for the wake.  Ignoring
    it created a wake the consumer could never release while the same cycle
    advertised it (found by the review of 862b745).
    """

    db = _database(tmp_path)
    _raw_event(
        db,
        "research.candidate_completed",
        {
            "research_id": "research-no-status",
            "candidate_id": "candidate-repair",
            "cycle_id": "repair",
            "pass_number": 1,
            "evidence": {},
        },
        key="repair-test:no-status",
    )
    _raw_event(
        db,
        "research.candidate_completed",
        {
            "research_id": "research-still-failed",
            "candidate_id": "candidate-repair",
            "cycle_id": "repair",
            "pass_number": 1,
            "status": "FAILED",
            "evidence": {},
        },
        key="repair-test:still-failed",
    )
    assert _research_completion_receipt(db, "research-no-status") is not None
    assert _research_completion_receipt(db, "research-still-failed") is None


def test_declared_source_present_checks_the_checkout() -> None:
    assert _declared_source_present("src/jarvis_foundry/fabric/executors.py") is True
    assert _declared_source_present("src/jarvis_foundry/fabric/not_a_file.py") is False
    assert _declared_source_present("") is False


def _candidate(locator: str) -> SolutionCandidate:
    return SolutionCandidate(
        candidate_id="candidate-repair-0001",
        gap_id="gap-repair-1",
        tier=ReuseTier.JARVIS_SOURCE,
        name="repair candidate",
        solution_kind="JARVIS_COMPONENT",
        locator=locator,
        version="0.1.0",
        summary="repair round 2 pin",
        coverage=CapabilityCoverage(required_actions=(ACTION,)),
        compatibility=CompatibilityAssessment(state="COMPATIBLE", score=1.0),
        integration_cost=IntegrationCostEstimate(
            person_hours=0.5,
            adapter_files=1,
            new_dependencies=0,
            configuration_steps=1,
        ),
        maturity=MaturityEvidence(state="MATURE", score=1.0),
        maintenance=MaintenanceEvidence(state="ACTIVE", score=1.0),
    )


def _request() -> ExpansionWorkRequest:
    return ExpansionWorkRequest(
        work_id="repair-work-1",
        gap_id="gap-repair-1",
        origin_objective_id="repair-objective",
        root_objective_id="repair-objective",
        candidate_id="candidate-repair-0001",
        required_capability_id=CAPABILITY,
        requirement_set_id="repair-requirement-set",
        budget={"max_attempts": 1, "max_total_attempts_for_root": 1},
        expansion_depth=1,
    )


def test_resolver_declines_a_declaration_whose_file_is_absent(monkeypatch) -> None:
    """String equality must not be enough: the declared file has to exist."""

    declared = str(JARVIS_SOURCE_ADAPTERS[ACTION]["source_path"])
    control = _candidate(f"source:{declared}")
    assert canonical_blueprint_resolver(control, _request()) is not None

    absent = "src/jarvis_foundry/fabric/not_a_file.py"
    monkeypatch.setitem(
        JARVIS_SOURCE_ADAPTERS,
        ACTION,
        {**dict(JARVIS_SOURCE_ADAPTERS[ACTION]), "source_path": absent},
    )
    mutated = dataclasses.replace(control, locator=f"source:{absent}")
    assert canonical_blueprint_resolver(mutated, _request()) is None
    # ...and the same declaration is adaptable again once the real path returns.
    monkeypatch.undo()
    assert canonical_blueprint_resolver(control, _request()) is not None


def test_wake_state_projection_is_unchanged_for_a_real_wake(tmp_path) -> None:
    """Positive control: the gate does not hide a wake that exists."""

    db = _database(tmp_path)
    wake_id = _research_wake(db, "research-positive-control")
    assert wake_id in set(rebuild_wake_state(db))
    assert _triggered_research_wakes(db, [{"wake_id": wake_id}]) == [wake_id]


def test_receipt_payload_is_json_serialisable(tmp_path) -> None:
    """The evidence path stays JSON-clean (no NUL / non-serialisable values)."""

    db = _database(tmp_path)
    _raw_event(
        db,
        "research.candidate_completed",
        {"research_id": "research-json", "status": "COMPLETED", "evidence": {}},
        key="repair-test:json",
    )
    json.dumps(_research_completion_receipt(db, "research-json"))
