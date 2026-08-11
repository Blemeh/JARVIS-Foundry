from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, EventConflict, EventIntegrityError, canonical_json, payload_hash
from jarvis_foundry.migrations import SCHEMA_VERSION, MIGRATIONS
from jarvis_foundry.projections import apply_event


def mission_created(mission_id: str = "mission-1") -> Event:
    return Event.create(
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": "Prove durable autonomy",
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": ["state survives restart"],
        },
        source_type="test",
        source_id="test-suite",
        idempotency_key=f"mission.created:{mission_id}",
    )


def test_duplicate_event_is_idempotent(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    event = mission_created()

    first_sequence, first_inserted = database.append(event)
    second_sequence, second_inserted = database.append(event)

    assert first_inserted is True
    assert second_inserted is False
    assert second_sequence == first_sequence
    assert len(database.events()) == 1


def test_idempotency_collision_is_rejected(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    original = mission_created()
    database.append(original)
    conflicting = mission_created("mission-2")
    conflicting = replace(conflicting, idempotency_key=original.idempotency_key)

    with pytest.raises(EventConflict):
        database.append(conflicting)


def test_retry_with_new_event_id_and_same_payload_is_deduplicated(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    first = mission_created()
    retry = mission_created()

    database.append(first)
    sequence, inserted = database.append(retry)

    assert inserted is False
    assert sequence == 1
    assert len(database.events()) == 1


def test_concurrent_duplicate_delivery_creates_one_event(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()

    def deliver(_):
        return database.append(mission_created())

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(deliver, range(24)))

    assert sum(1 for _, inserted in results if inserted) == 1
    assert len(database.events()) == 1
    assert database.row("missions", "mission_id", "mission-1") is not None


def test_invalid_hash_is_rejected_before_write(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    corrupt = replace(mission_created(), payload_hash="0" * 64)

    with pytest.raises(EventIntegrityError):
        database.append(corrupt)
    assert database.events() == []


def test_projection_failure_rolls_back_event(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(mission_created())
    invalid = Event.create(
        "mission.state_changed",
        {"mission_id": "mission-1", "state": "COMPLETE"},
        source_type="test",
        source_id="test-suite",
    )

    with pytest.raises(ValueError, match="invalid mission transition"):
        database.append(invalid)
    assert len(database.events()) == 1
    assert database.row("missions", "mission_id", "mission-1")["state"] == "PLANNED"


def test_projection_rebuild_is_deterministic(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    created = mission_created()
    database.append(created)
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-1", "state": "ORIENTING", "next_action": "inspect"},
        source_type="test", source_id="test-suite", causation_id=created.event_id,
    ))
    before = database.row("missions", "mission_id", "mission-1")

    assert database.rebuild_projections() == 2
    assert database.row("missions", "mission_id", "mission-1") == before
    assert database.quick_check() == "ok"


def test_tampered_event_rebuild_rolls_back_without_erasing_projection(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(mission_created())
    with database.transaction() as connection:
        connection.execute(
            "UPDATE events SET payload_json = ? WHERE event_type = 'mission.created'",
            ('{"mission_id":"forged"}',),
        )

    with pytest.raises(EventIntegrityError):
        database.rebuild_projections()
    assert database.row("missions", "mission_id", "mission-1") is not None


def test_verified_backup_restores_ledger_and_projection(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(mission_created("mission-1"))
    backup = database.backup_to(tmp_path / "backup" / "foundry.db")
    database.append(mission_created("mission-2"))
    assert len(database.events()) == 2

    assert database.restore_from(backup) == 1
    assert len(database.events()) == 1
    assert database.row("missions", "mission_id", "mission-1") is not None
    assert database.row("missions", "mission_id", "mission-2") is None


def insert_raw_event(connection: sqlite3.Connection, event: Event) -> None:
    connection.execute(
        """INSERT INTO events(
            event_id, event_type, occurred_at, recorded_at, source_type, source_id,
            correlation_id, causation_id, idempotency_key, schema_version,
            payload_json, payload_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.event_id, event.event_type, event.occurred_at, event.recorded_at,
            event.source_type, event.source_id, event.correlation_id, event.causation_id,
            event.idempotency_key, event.schema_version, canonical_json(event.payload),
            event.payload_hash,
        ),
    )


def test_schema_one_database_migrates_forward_without_data_loss(tmp_path):
    path = tmp_path / "foundry.db"
    connection = sqlite3.connect(path)
    connection.executescript(MIGRATIONS[1])
    event = mission_created()
    insert_raw_event(connection, event)
    connection.execute(
        """INSERT INTO missions(
            mission_id, objective, resource_id, state, acceptance_json, next_action,
            last_event_id, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "mission-1", "Prove durable autonomy", None, "PLANNED",
            '["state survives restart"]', None, event.event_id, event.recorded_at,
        ),
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
        ("2026-08-11T00:00:00Z",),
    )
    connection.commit()
    connection.close()

    database = FoundryDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert len(database.events()) == 1
    assert database.row("missions", "mission_id", "mission-1")["state"] == "PLANNED"
    with database.connect() as migrated:
        assert migrated.execute("SELECT start_active FROM system_state WHERE singleton = 1").fetchone()[0] == 0
        tables = {row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"agent_runs", "evidence_bundles", "verification_verdicts", "budgets"} <= tables
        assert migrated.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
    assert database.quick_check() == "ok"


def test_schema_two_database_migrates_forward_without_data_loss(tmp_path):
    path = tmp_path / "foundry.db"
    connection = sqlite3.connect(path)
    connection.executescript(MIGRATIONS[1])
    connection.executescript(MIGRATIONS[2])
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?), (2, ?)",
        ("2026-08-11T00:00:00Z", "2026-08-11T00:00:01Z"),
    )
    connection.commit()
    created = mission_created()
    old_events = [
        created,
        Event.create(
            "mission.state_changed",
            {"mission_id": "mission-1", "state": "ORIENTING", "next_action": "inspect"},
            source_type="test", source_id="test-suite", causation_id=created.event_id,
            idempotency_key="mission.state_changed:mission-1",
        ),
        Event.create(
            "project.registered",
            {
                "resource_id": "resource-1", "kind": "git_worktree", "root_path": "/repo",
                "resolved_root": "/repo", "baseline": {"commit": "abc"},
                "capabilities": ["read"], "readiness_state": "READY",
            },
            source_type="test", source_id="test-suite",
        ),
        Event.create(
            "owner_directive.recorded",
            {
                "directive_id": "directive-1", "command_type": "ADD_CONSTRAINT",
                "target": None, "exact_text": "no pushes",
            },
            source_type="test", source_id="test-suite",
        ),
        Event.create(
            "action.allowed",
            {
                "action_id": "action-1", "mission_id": "mission-1", "decision": "ALLOWED",
                "reason": "within scope", "target": "/repo/file",
            },
            source_type="test", source_id="test-suite",
        ),
        Event.create(
            "supervisor.started",
            {"instance_id": "instance-1", "trust_state": "LAB"},
            source_type="test", source_id="test-suite",
        ),
    ]
    connection.execute("BEGIN")
    for event in old_events:
        insert_raw_event(connection, event)
        apply_event(connection, event)
    connection.execute(
        "UPDATE system_state SET start_active = 1, start_event_id = ?, start_received_at = ? WHERE singleton = 1",
        (created.event_id, created.recorded_at),
    )
    connection.commit()
    connection.close()

    database = FoundryDatabase(path)
    assert database.initialize() == SCHEMA_VERSION
    assert len(database.events()) == 6
    assert database.row("missions", "mission_id", "mission-1")["state"] == "ORIENTING"
    assert database.row("resources", "resource_id", "resource-1")["readiness_state"] == "READY"
    assert database.row("directives", "directive_id", "directive-1")["active"] == 1
    assert database.row("action_decisions", "action_id", "action-1")["decision"] == "ALLOWED"
    with database.connect() as migrated:
        assert migrated.execute("SELECT start_active FROM system_state WHERE singleton = 1").fetchone()[0] == 1
        assert migrated.execute("SELECT status FROM supervisor_state WHERE singleton = 1").fetchone()[0] == "RUNNING"
        assert migrated.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM evidence_bundles").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM verification_verdicts").fetchone()[0] == 0
        assert migrated.execute("SELECT COUNT(*) FROM budgets").fetchone()[0] == 0
        tables = {row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"agent_runs", "evidence_bundles", "verification_verdicts", "budgets"} <= tables
    assert database.quick_check() == "ok"


def test_fresh_database_initializes_to_schema_three(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    assert database.initialize() == SCHEMA_VERSION
    with database.connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {
            "agent_runs", "evidence_bundles", "verification_verdicts", "budgets",
            "events", "missions", "system_state",
        } <= tables


def agent_run_started(run_id: str = "run-1", mission_id: str = "mission-1") -> Event:
    return Event.create(
        "agent_run.started",
        {
            "run_id": run_id, "mission_id": mission_id, "step_id": "step-1",
            "role": "builder", "provider": "opencode_go", "model": "kimi-k2.7-code",
            "context_manifest_hash": "c" * 64,
            "tool_policy": {"mutate": ["registered_worktree"]},
        },
        source_type="test", source_id="test-suite",
        idempotency_key=f"agent_run.started:{run_id}",
    )


def test_agent_run_projection_lifecycle_and_rebuild(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(agent_run_started())
    database.append(agent_run_started())
    database.append(Event.create(
        "agent_run.heartbeat", {"run_id": "run-1"},
        source_type="test", source_id="test-suite",
        idempotency_key="agent_run.heartbeat:run-1",
    ))
    database.append(Event.create(
        "agent_run.completed",
        {"run_id": "run-1", "result": {"status": "ok"}, "cost": {"usd": 0.05}},
        source_type="test", source_id="test-suite",
        idempotency_key="agent_run.completed:run-1",
    ))

    run = database.row("agent_runs", "run_id", "run-1")
    assert run["status"] == "COMPLETED"
    assert run["role"] == "builder"
    assert run["provider"] == "opencode_go"
    assert run["context_manifest_hash"] == "c" * 64
    assert run["heartbeat_at"] is not None
    assert run["finished_at"] is not None
    assert '"ok"' in run["result_json"]
    assert "0.05" in run["cost_json"]

    assert database.rebuild_projections() == 3  # event count
    assert database.row("agent_runs", "run_id", "run-1") == run
    assert database.quick_check() == "ok"


def test_agent_run_failure_projection(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(agent_run_started())
    database.append(Event.create(
        "agent_run.failed",
        {"run_id": "run-1", "failure_class": "provider_outage"},
        source_type="test", source_id="test-suite",
        idempotency_key="agent_run.failed:run-1",
    ))
    run = database.row("agent_runs", "run_id", "run-1")
    assert run["status"] == "FAILED"
    assert run["failure_class"] == "provider_outage"


def test_evidence_bundle_projection_and_rebuild(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "evidence.created",
        {
            "bundle_id": "bundle-1", "mission_id": "mission-1", "step_id": "step-1",
            "objective": "prove durable event store",
            "criteria": ["state survives restart"],
            "before_state": {"head": "abc123"},
            "baseline_commit": "abc123",
            "final_diff": {"files": ["src/database.py"]},
            "files_changed": ["src/database.py"],
            "commands_run": ["pytest tests/foundry -q"],
            "artifact_hashes": {"test-report": "f" * 64},
            "after_state": {"head": "def456"},
            "uncertainty": {"none": True},
            "rollback_proof": {"restore": "tested"},
            "builder_claim": {"summary": "implemented"},
            "verifier_verdict_id": None,
        },
        source_type="test", source_id="test-suite",
        idempotency_key="evidence.created:bundle-1",
    ))

    bundle = database.row("evidence_bundles", "bundle_id", "bundle-1")
    assert bundle["objective"] == "prove durable event store"
    assert bundle["baseline_commit"] == "abc123"
    assert "src/database.py" in bundle["files_changed_json"]
    assert "f" * 64 in bundle["artifact_hashes_json"]
    assert bundle["criteria_json"] == canonical_json(["state survives restart"])

    assert database.rebuild_projections() == 1
    assert database.row("evidence_bundles", "bundle_id", "bundle-1") == bundle


def test_verification_verdict_projection_and_rebuild(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "verification.started",
        {
            "verdict_id": "verdict-1", "mission_id": "mission-1",
            "subject_type": "STEP", "subject_id": "step-1",
            "verifier_run_id": "run-2", "builder_run_id": "run-1",
            "independence": {"same_agent": False, "same_session": False, "same_provider": True, "limitations": []},
            "criteria": ["criterion A"],
        },
        source_type="test", source_id="test-suite",
        idempotency_key="verification.started:verdict-1",
    ))
    database.append(Event.create(
        "verification.passed",
        {
            "verdict_id": "verdict-1",
            "findings": [{"criterion": "criterion A", "verdict": "PASS", "evidence": ["artifact-1"]}],
            "evidence": {"artifacts": ["artifact-1"]},
            "missing_proof": [], "scope_violations": [],
        },
        source_type="test", source_id="test-suite",
        idempotency_key="verification.passed:verdict-1",
    ))
    database.append(Event.create(
        "verification.started",
        {
            "verdict_id": "verdict-2", "mission_id": "mission-1",
            "subject_type": "STEP", "subject_id": "step-2",
            "verifier_run_id": "run-4", "builder_run_id": "run-3",
            "independence": {"same_agent": False, "same_session": False, "same_provider": False, "limitations": []},
            "criteria": ["criterion B"],
        },
        source_type="test", source_id="test-suite",
        idempotency_key="verification.started:verdict-2",
    ))
    database.append(Event.create(
        "verification.failed",
        {
            "verdict_id": "verdict-2",
            "findings": [{"criterion": "criterion B", "verdict": "FAIL", "evidence": []}],
            "repair_requirements": ["fix state transition"],
            "scope_violations": [],
        },
        source_type="test", source_id="test-suite",
        idempotency_key="verification.failed:verdict-2",
    ))
    database.append(Event.create(
        "verification.started",
        {
            "verdict_id": "verdict-3", "mission_id": "mission-1",
            "subject_type": "ARTIFACT", "subject_id": "artifact-1",
            "verifier_run_id": "run-6", "builder_run_id": "run-5",
            "independence": {"same_agent": False, "same_session": False, "same_provider": True, "limitations": []},
            "criteria": ["exists"],
        },
        source_type="test", source_id="test-suite",
        idempotency_key="verification.started:verdict-3",
    ))
    database.append(Event.create(
        "verification.blocked",
        {
            "verdict_id": "verdict-3",
            "blocking_dependency": "missing credential", "reason": "cannot verify without auth",
        },
        source_type="test", source_id="test-suite",
        idempotency_key="verification.blocked:verdict-3",
    ))

    passed = database.row("verification_verdicts", "verdict_id", "verdict-1")
    assert passed["status"] == "PASS"
    assert passed["verdict"] == "PASS"
    assert "criterion A" in passed["findings_json"]
    assert passed["verifier_run_id"] == "run-2"
    assert passed["issued_at"] is not None

    failed = database.row("verification_verdicts", "verdict_id", "verdict-2")
    assert failed["status"] == "FAIL"
    assert "fix state transition" in failed["repair_requirements_json"]

    blocked = database.row("verification_verdicts", "verdict_id", "verdict-3")
    assert blocked["status"] == "BLOCKED"
    assert blocked["blocking_dependency"] == "missing credential"

    assert database.rebuild_projections() == 6
    assert database.row("verification_verdicts", "verdict_id", "verdict-1") == passed
    assert database.row("verification_verdicts", "verdict_id", "verdict-2") == failed
    assert database.row("verification_verdicts", "verdict_id", "verdict-3") == blocked


def test_budget_projection_and_rebuild(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "budget.allocation_recorded",
        {"provider": "vertex_ai", "lifetime_budget_usd": 50.0, "daily_soft_limit_usd": 5.0, "billing_verified": False},
        source_type="test", source_id="test-suite",
        idempotency_key="budget.allocation_recorded:vertex_ai",
    ))
    database.append(Event.create(
        "budget.usage_recorded", {"provider": "vertex_ai", "amount_usd": 0.40},
        source_type="test", source_id="test-suite",
        idempotency_key="budget.usage_recorded:vertex_ai:1",
    ))
    database.append(Event.create(
        "budget.usage_recorded", {"provider": "vertex_ai", "amount_usd": 0.10},
        source_type="test", source_id="test-suite",
        idempotency_key="budget.usage_recorded:vertex_ai:2",
    ))
    database.append(Event.create(
        "budget.allocation_recorded",
        {"provider": "vertex_ai", "lifetime_budget_usd": 100.0, "daily_soft_limit_usd": 10.0, "billing_verified": True},
        source_type="test", source_id="test-suite",
        idempotency_key="budget.allocation_recorded:vertex_ai:2",
    ))

    budget = database.row("budgets", "provider", "vertex_ai")
    assert budget["lifetime_budget_usd"] == 100.0
    assert budget["daily_soft_limit_usd"] == 10.0
    assert budget["billing_verified"] == 1
    assert budget["spent_usd"] == pytest.approx(0.5)

    assert database.rebuild_projections() == 4
    rebuilt = database.row("budgets", "provider", "vertex_ai")
    assert rebuilt["spent_usd"] == pytest.approx(0.5)
    assert rebuilt["lifetime_budget_usd"] == 100.0


def test_budget_usage_without_prior_allocation_creates_row(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "budget.usage_recorded", {"provider": "opencode_go", "amount_usd": 1.25},
        source_type="test", source_id="test-suite",
        idempotency_key="budget.usage_recorded:opencode_go:1",
    ))
    budget = database.row("budgets", "provider", "opencode_go")
    assert budget["spent_usd"] == pytest.approx(1.25)
    assert budget["lifetime_budget_usd"] is None
