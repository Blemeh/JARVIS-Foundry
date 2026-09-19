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
    """Only a wake with durable trigger evidence may be reported as triggered."""

    from jarvis_foundry.executive.continuous import trigger_wake

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
    # Existence alone is not a trigger: the wake is ACTIVE until the canonical
    # path triggers it, so it must not be advertised yet.
    assert _triggered_research_wakes(db, unbound + [{"wake_id": real}]) == []
    trigger_wake(db, real, controller="supervisor", reason="test: real trigger")
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
    """Repair round 3 + 4: the receipt mirrors the projection's own status default.

    ``projections.py`` seals a completion with a falsy status as COMPLETED
    (``str(payload.get("status") or "COMPLETED")``), so every falsy form must be
    durable for the wake, and every non-completed value must not be.  The first
    version of this pin only covered the missing key; the review of 2edd0a0
    showed ``""``, ``False`` and ``0`` still diverged.
    """

    db = _database(tmp_path)
    falsy_ids = {
        "research-no-status": {},
        "research-null-status": {"status": None},
        "research-empty-status": {"status": ""},
        "research-false-status": {"status": False},
        "research-zero-status": {"status": 0},
    }
    for research_id, extra in falsy_ids.items():
        _raw_event(
            db,
            "research.candidate_completed",
            {
                "research_id": research_id,
                "candidate_id": "candidate-repair",
                "cycle_id": "repair",
                "pass_number": 1,
                "evidence": {},
                **extra,
            },
            key=f"repair-test:{research_id}",
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
    for research_id in falsy_ids:
        assert _research_completion_receipt(db, research_id) is not None, research_id
    assert _research_completion_receipt(db, "research-still-failed") is None


def test_a_campaign_refusal_is_durably_classed(tmp_path) -> None:
    """Repair round 5 (goal §6): a refusal carries its class in the durable record.

    The second review's accepted R13 observation was that a repeated refusal is
    summarised but not persisted.  Probing showed the refusal itself IS durable
    (created=False plus the reason) and an identical replay is correctly a no-op,
    so the real gap was narrower: the durable record did not say WHICH kind of
    refusal it was, so two differently-caused refusals for one objective were
    indistinguishable.  The receipt now carries the class.

    This pin FAILS on the pre-fix tree, where refusal_code is absent.
    """

    from jarvis_foundry.executive.autonomy import create_objective
    from jarvis_foundry.executive.campaigns import ensure_warranted_campaign

    db = _database(tmp_path)
    objective_id = "owner-goal:refusal-class"
    create_objective(
        db,
        "Over several missions bring the project to a verified releasable state.",
        objective_id=objective_id,
        resource_id="resource-refusal-class",
        required_capabilities=("write_registered_worktree_files",),
        success_criteria=("each child mission is independently verified",),
        metadata={"decomposition": "MULTI_MISSION", "horizon": "LONG"},
        budget={"max_missions": 4, "max_retries": 1},
        controller="supervisor",
    )

    reported = ensure_warranted_campaign(
        db, objective_id=objective_id, controller="supervisor",
        deadline="not-a-real-deadline",
    )
    refused = [row for row in reported if row.get("created") is False]
    assert refused, "the invalid deadline must be refused, not silently accepted"

    receipts = [
        event.payload
        for event in db.events(
            event_types=("executive.campaign_warranted",), readonly=True
        )
    ]
    durable = [row for row in receipts if row.get("created") is False]
    assert durable, "a refusal the runtime reported must be durable"
    assert durable[0].get("reason"), "the refusal reason must be durable"
    assert durable[0].get("refusal_code") == "DEADLINE_INVALID", (
        "the durable record must name the refusal class"
    )


def test_an_identical_refusal_replay_is_a_no_op(tmp_path) -> None:
    """Positive control: replaying the same refusal adds no duplicate record."""

    from jarvis_foundry.executive.autonomy import create_objective
    from jarvis_foundry.executive.campaigns import ensure_warranted_campaign

    db = _database(tmp_path)
    objective_id = "owner-goal:refusal-replay"
    create_objective(
        db,
        "Over several missions bring the project to a verified releasable state.",
        objective_id=objective_id,
        resource_id="resource-refusal-replay",
        required_capabilities=("write_registered_worktree_files",),
        success_criteria=("each child mission is independently verified",),
        metadata={"decomposition": "MULTI_MISSION", "horizon": "LONG"},
        budget={"max_missions": 4, "max_retries": 1},
        controller="supervisor",
    )
    for _ in range(3):
        ensure_warranted_campaign(
            db, objective_id=objective_id, controller="supervisor",
            deadline="not-a-real-deadline",
        )
    receipts = [
        event.payload
        for event in db.events(
            event_types=("executive.campaign_warranted",), readonly=True
        )
    ]
    assert len(receipts) == 1, "an identical refusal replay must stay idempotent"


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
    """Positive control: the trigger gate does not hide a wake that was triggered."""

    from jarvis_foundry.executive.continuous import trigger_wake

    db = _database(tmp_path)
    wake_id = _research_wake(db, "research-positive-control")
    assert wake_id in set(rebuild_wake_state(db))
    trigger_wake(db, wake_id, controller="supervisor", reason="positive control")
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
