"""V4-6 owner attention queue proof."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.decision_queue import build_owner_attention_queue
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import OwnerControlService, sign_command


SECRET = b"v4-decision-queue-secret"


def _apply(database: FoundryDatabase, command: str, target: str, text: str, nonce: str, directive_id: str):
    signature = sign_command(SECRET, command, target, text, nonce)
    return OwnerControlService(database, secret=SECRET, owner_id="queue-test").apply(
        command,
        target=target,
        exact_text=text,
        nonce=nonce,
        signature=signature,
        directive_id=directive_id,
    )


def test_attention_queue_classifies_evidence_and_resolves_owner_veto(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    veto = _apply(
        database,
        "VETO_ACTION",
        "mission-queue",
        "hold the queue mission",
        "queue-veto-1",
        "queue-veto-1",
    )
    database.append(Event.create(
        "executive.materialization_blocked",
        {
            "decision_id": "queue-decision",
            "candidate_id": "queue-candidate",
            "reason": "provider dependency unavailable",
            "options": ["wait for provider", {"action": "recheck"}],
        },
        source_type="supervisor",
        source_id="queue-test",
        idempotency_key="executive.materialization_blocked:queue-decision",
    ))
    database.append(Event.create(
        "owner_directive.recorded",
        {
            "directive_id": "queue-unknown",
            "command_type": "GOAL",
            "target": "GLOBAL",
            "exact_text": "malformed owner intent",
            "authenticated": True,
        },
        source_type="owner",
        source_id="queue-test",
        idempotency_key="owner_directive.recorded:queue-unknown",
    ))

    queue = build_owner_attention_queue(database)
    classifications = {record["classification"] for record in queue["open"]}
    assert "OWNER_VETO_ACTIVE" in classifications
    assert "BLOCKED_EXTERNAL_DEPENDENCY" in classifications
    assert "UNKNOWN" in classifications
    blocked = next(
        record for record in queue["open"]
        if record["classification"] == "BLOCKED_EXTERNAL_DEPENDENCY"
    )
    assert blocked["options"] == ["wait for provider", {"action": "recheck"}]
    assert blocked["evidence_refs"]
    assert blocked["consequence"]

    revoke_text = "revoke queue veto"
    revoke_nonce = "queue-resume-1"
    OwnerControlService(
        database, secret=SECRET, owner_id="queue-test"
    ).revoke(
        veto.directive_id,
        nonce=revoke_nonce,
        exact_text=revoke_text,
        signature=sign_command(
            SECRET, "RESUME", veto.directive_id, revoke_text, revoke_nonce
        ),
    )
    resolved = build_owner_attention_queue(database)
    veto_record = next(
        record for record in resolved["history"]
        if record["queue_id"] == "attention:owner_directive.recorded:"
        + veto.event_id
    )
    assert veto_record["status"] == "RESOLVED"
    assert all(
        record["classification"] != "OWNER_VETO_ACTIVE"
        for record in resolved["open"]
    )

# --- live-evidence regressions (2026-10-21) --------------------------------------
# The live owner queue held 100 items, all UNKNOWN, all executive.project_context_resolved
# telemetry, while 14 real escalations and a stalled delegation were absent.


def _telemetry(database: FoundryDatabase, event_type: str, count: int, tag: str) -> None:
    database.append_events([
        Event.create(
            event_type,
            {"resource_id": f"resource-{index}", "status": "AVAILABLE"},
            source_type="supervisor",
            source_id="queue-test",
            idempotency_key=f"{event_type}:{tag}:{index}",
        )
        for index in range(count)
    ])


def test_per_cycle_telemetry_never_becomes_owner_attention(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    for index, event_type in enumerate((
        "executive.project_context_resolved",
        "executive.context_assembled",
        "executive.autonomy_cycle_recorded",
        "worker_loop.cycle_started",
        "worker_loop.waiting",
        "worker_loop.waiting_heartbeat",
        "worker_loop.portfolio_waiting",
        "trust.gate_report_recorded",
    )):
        _telemetry(database, event_type, 5, f"t{index}")

    queue = build_owner_attention_queue(database)

    assert queue["open_count"] == 0
    assert queue["open"] == []


def _escalation(database: FoundryDatabase, escalation_id: str) -> None:
    database.append(Event.create(
        "executive.escalation_opened",
        {
            "escalation_id": escalation_id,
            "objective_id": "owner-goal:owner-queue",
            "status": "OPEN",
            "decision": "blocked goal lacks a resumable condition",
            "why": "step execute is blocked and no durable wake owns it",
            "recommendation": "keep the owner goal blocked and wait for an owner decision",
            "options": ["owner resolves the dependency", "owner closes the goal"],
        },
        source_type="supervisor",
        source_id="queue-test",
        idempotency_key=f"executive.escalation_opened:{escalation_id}",
    ))


def test_escalation_stays_visible_beyond_the_scan_window_until_resolved(
    tmp_path: Path, monkeypatch,
) -> None:
    from jarvis_foundry import decision_queue

    monkeypatch.setattr(decision_queue, "_RECENT_SCAN_LIMIT", 25)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    _escalation(database, "escalation:old")
    _telemetry(database, "executive.project_context_resolved", 200, "flood")  # buries it

    queue = build_owner_attention_queue(database)
    record = next(r for r in queue["open"] if r["source_event_type"] == "executive.escalation_opened")
    assert record["classification"] == "DECISION_REQUIRED"
    assert "resumable condition" in record["summary"]
    assert record["options"] == ["owner resolves the dependency", "owner closes the goal"]
    assert queue["open_count"] == 1

    database.append(Event.create(
        "executive.escalation_resolved",
        {"escalation_id": "escalation:old", "status": "RESOLVED"},
        source_type="owner", source_id="queue-test",
        idempotency_key="executive.escalation_resolved:escalation:old",
    ))
    resolved = build_owner_attention_queue(database)
    assert resolved["open_count"] == 0
    history = next(r for r in resolved["history"] if r["source_event_type"] == "executive.escalation_opened")
    assert history["status"] == "RESOLVED"


def test_blocked_delegation_is_one_owner_item_that_closes_when_admitted(tmp_path: Path) -> None:
    from test_v13_delegation_service import _apply as apply_delegation
    from test_v13_delegation_service import _database as delegation_database
    from test_v13_delegation_service import _seed_plan

    from jarvis_foundry.delegation.service import DelegationService
    from jarvis_foundry.executive.autonomy import create_objective

    database = delegation_database(tmp_path)
    objective_id = "executive-objective:queue-delegation"
    directive_id = apply_delegation(
        database, "DELEGATE", "Handle the queue objective",
        directive_id="delegate-queue-visible",
        metadata={"context": {"objective_id": objective_id}},
    )
    service = DelegationService(database)
    for _ in range(3):  # a stalled intent is re-evaluated every cycle
        service.process_directive(directive_id)

    queue = build_owner_attention_queue(database)
    blocked = [r for r in queue["open"] if r["source_event_type"] == "delegation.intent_blocked"]
    assert len(blocked) == 1
    assert blocked[0]["classification"] == "DECISION_REQUIRED"

    # The Executive now has the objective and a plan: the delegation is admitted.
    create_objective(database, "Queue objective", objective_id=objective_id, source_id="test-executive-planner")
    _seed_plan(database, "queue-visible", objective_id=objective_id)
    assert service.process_directive(directive_id).status == "ADMITTED"

    after = build_owner_attention_queue(database)
    assert not [r for r in after["open"] if r["source_event_type"] == "delegation.intent_blocked"]


def test_worker_activation_failure_is_shown_until_a_later_cycle_recovers(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "worker_loop.activation_blocked",
        {"error": "while scanning a simple key <<<<<<< HEAD", "error_class": "ScannerError", "phase": "configuration"},
        source_type="supervisor", source_id="queue-test", idempotency_key="activation-blocked-1",
    ))
    queue = build_owner_attention_queue(database)
    assert [r["source_event_type"] for r in queue["open"]] == ["worker_loop.activation_blocked"]
    assert queue["open"][0]["classification"] == "DECISION_REQUIRED"

    _telemetry(database, "worker_loop.cycle_started", 1, "recovered")
    recovered = build_owner_attention_queue(database)
    assert recovered["open_count"] == 0