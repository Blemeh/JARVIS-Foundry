"""Focused v38 -> v39 -> v40 persistence and replay coverage for V11."""

from __future__ import annotations

import json

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile


def _event(event_type: str, payload: dict, key: str) -> Event:
    return Event.create(
        event_type,
        payload,
        source_type="test",
        source_id="v11-a2-persistence",
        idempotency_key=key,
    )


def _candidate(candidate_id: str = "candidate-a2") -> Event:
    return _event(
        "discovery.candidate_generated",
        {
            "candidate_id": candidate_id,
            "mission_id": "initiative-observation",
            "source": "initiative_observation",
            "objective": "Investigate the bounded repository issue",
            "objective_canonical": "investigate bounded repository issue",
            "evidence": [{"observation_id": "observation-a2", "authority": "DATA_ONLY"}],
            "duplicate_of": None,
            "fingerprint": "f" * 64,
            "expires_at": "2026-09-09T00:00:00Z",
        },
        f"discovery.candidate_generated:{candidate_id}",
    )


def test_v38_to_v39_to_v40_preserves_queue_and_rejection_history(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry-v38.db")
    assert database.initialize(target=38) == 38
    database.append(_candidate())
    rejected = _event(
        "discovery.candidate_rejected",
        {
            "candidate_id": "candidate-a2",
            "reason_code": "LOW_VALUE",
            "reason": "Doing nothing is better",
        },
        "discovery.candidate_rejected:candidate-a2",
    )
    database.append(rejected)
    database.append(_event(
        "research.queue_item_created",
        {
            "item_id": "legacy-research",
            "claim_id": "claim-a2",
            "reason": "legacy claim research",
            "evidence": {"source": "legacy"},
            "status": "OPEN",
        },
        "research.queue_item_created:legacy-research",
    ))

    # Reproduce the v38 rejection projection that replaced source evidence.
    with database.transaction() as connection:
        connection.execute(
            "UPDATE candidates SET evidence_json = ? WHERE candidate_id = ?",
            ('[{"rejection_reason":"Doing nothing is better"}]', "candidate-a2"),
        )

    # Exercise the integration sequence explicitly. The A2 migration remains
    # the sole v39 authority, then continuous observation advances the same
    # database through additive v40 on reopen.
    assert database.initialize(target=39) == 39
    with database.connect(readonly=True) as connection:
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 39
        assert "candidate_judgments" in {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    database = FoundryDatabase(database.path)
    assert database.initialize() == SCHEMA_VERSION == 40
    candidate = database.row("candidates", "candidate_id", "candidate-a2")
    assert json.loads(str(candidate["evidence_json"])) == [
        {"observation_id": "observation-a2", "authority": "DATA_ONLY"},
        {
            "rejection_reason": "Doing nothing is better",
            "reason_code": "LOW_VALUE",
        },
    ]
    legacy = database.row("research_queue", "item_id", "legacy-research")
    assert legacy["claim_id"] == "claim-a2"
    assert legacy["candidate_id"] is None
    assert legacy["status"] == "OPEN"
    assert legacy["budget_used"] == 0.0

    judgments = database.all("candidate_judgments", candidate_id="candidate-a2")
    assert len(judgments) == 1
    assert judgments[0]["judgment_id"] == (
        "legacy-rejection:discovery.candidate_rejected:candidate-a2"
    )
    assert judgments[0]["outcome"] == "REJECTED"
    assert judgments[0]["reason_code"] == "LOW_VALUE"
    assert judgments[0]["last_event_id"] == rejected.event_id

    with database.connect(readonly=True) as connection:
        assert [row[0] for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )] == list(range(1, 41))
        indexes = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        monitor_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(monitors)")
        }
        research_columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(research_queue)"
            )
        }
    assert {
        "source_id", "config_hash", "readiness_state", "next_due_at",
        "last_content_hash", "consecutive_failures", "backoff_until",
    } <= monitor_columns
    assert "input_hash" in research_columns
    assert {
        "candidates_status_expiry",
        "candidates_fingerprint_status",
        "world_observations_source_processed_time",
        "world_signals_state_due",
        "world_signals_state_stale",
        "world_signals_dedupe",
        "monitors_due_lookup",
        "research_queue_candidate_revision_pass",
        "monitors_durable_due",
    } <= indexes
    # A second reopen is a no-op: no duplicate authority or migration row.
    database = FoundryDatabase(database.path)
    assert database.initialize() == 40
    with database.connect(readonly=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version IN (39, 40)"
        ).fetchone()[0] == 2
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert reconcile(database).consistent is True


def test_v40_projects_candidate_lineage_judgment_research_and_cycle_replay(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(_candidate())
    database.append(_event(
        "initiative.cycle_started",
        {
            "cycle_id": "cycle-a2",
            "status": "RUNNING",
            "started_at": "2026-09-02T10:00:00Z",
            "budgets": {"research_total": 2.0},
            "metrics": {"objective_creations": 0},
        },
        "initiative.cycle_started:cycle-a2",
    ))
    source_payload = {
        "candidate_id": "candidate-a2",
        "observation_id": "observation-a2",
        "source_kind": "github",
        "source_identity": "owner/repository#123",
        "content_hash": f"sha256:{'a' * 64}",
    }
    database.append(_event(
        "discovery.candidate_source_linked",
        source_payload,
        "discovery.candidate_source_linked:candidate-a2:github",
    ))
    # A separately delivered equivalent link stays one durable projection row.
    database.append(_event(
        "discovery.candidate_source_linked",
        source_payload,
        "discovery.candidate_source_linked:candidate-a2:github:replay",
    ))
    database.append(_event(
        "initiative.source_recorded",
        {
            "cycle_id": "cycle-a2",
            "source_id": "source-run-github",
            "source_kind": "github",
            "source_identity": "owner/repository",
            "status": "READY",
            "observations_recorded": 1,
            "candidates_recorded": 1,
            "content_hash": f"sha256:{'a' * 64}",
        },
        "initiative.source_recorded:cycle-a2:github",
    ))
    database.append(_event(
        "research.candidate_enqueued",
        {
            "research_id": "research-a2",
            "candidate_id": "candidate-a2",
            "cycle_id": "cycle-a2",
            "pass_number": 1,
            "status": "ENQUEUED",
            "budget_used": 0.5,
            "source_ref": "https://example.invalid/read-only",
            "attempted_at": "2026-09-02T10:01:00Z",
        },
        "research.candidate_enqueued:research-a2",
    ))
    database.append(_event(
        "research.candidate_started",
        {
            "research_id": "research-a2",
            "candidate_id": "candidate-a2",
            "cycle_id": "cycle-a2",
            "pass_number": 1,
            "status": "IN_PROGRESS",
            "budget_used": 0.5,
            "attempted_at": "2026-09-02T10:01:30Z",
        },
        "research.candidate_started:research-a2",
    ))
    database.append(_event(
        "research.candidate_completed",
        {
            "research_id": "research-a2",
            "candidate_id": "candidate-a2",
            "cycle_id": "cycle-a2",
            "pass_number": 1,
            "status": "COMPLETED",
            "budget_used": 0.5,
            "content_hash": f"sha256:{'b' * 64}",
            "evidence": {"finding": "bounded evidence"},
            "completed_at": "2026-09-02T10:02:00Z",
        },
        "research.candidate_completed:research-a2",
    ))
    database.append(_event(
        "discovery.candidate_judged",
        {
            "judgment_id": "judgment-a2",
            "candidate_id": "candidate-a2",
            "fingerprint": "f" * 64,
            "decision_id": "decision-a2",
            "outcome": "REJECTED",
            "reason_code": "LOW_VALUE",
            "reason_detail": "The do-nothing baseline wins",
            "score": 0.2,
            "factors": {"owner_relevance": 0.3},
            "evidence": [{"research_id": "research-a2"}],
            "decided_at": "2026-09-02T10:03:00Z",
        },
        "discovery.candidate_judged:judgment-a2",
    ))
    database.append(_event(
        "initiative.cycle_stage_changed",
        {"cycle_id": "cycle-a2", "stage": "RANK", "status": "RUNNING"},
        "initiative.cycle_stage_changed:cycle-a2:RANK",
    ))
    database.append(_event(
        "initiative.cycle_completed",
        {
            "cycle_id": "cycle-a2",
            "status": "IDLE",
            "completed_at": "2026-09-02T10:04:00Z",
            "metrics": {"objective_creations": 0, "mutating_effects": 0},
            "budgets": {"research_used": 0.5},
            "next_due_at": "2026-09-02T11:00:00Z",
        },
        "initiative.cycle_completed:cycle-a2",
    ))

    assert database.count("candidate_sources") == 1
    assert database.count("candidate_judgments") == 1
    assert database.count("initiative_source_runs") == 1
    assert database.row(
        "initiative_source_runs", "source_run_id", "source-run-github"
    )["candidates_count"] == 1
    research = database.row("research_queue", "item_id", "research-a2")
    assert research["status"] == "COMPLETED"
    assert research["budget_used"] == 0.5
    assert research["pass_number"] == 1
    assert json.loads(str(research["evidence_json"])) == {
        "finding": "bounded evidence",
    }
    candidate = database.row("candidates", "candidate_id", "candidate-a2")
    assert candidate["status"] == "REJECTED"
    assert json.loads(str(candidate["evidence_json"])) == [
        {"observation_id": "observation-a2", "authority": "DATA_ONLY"},
    ]
    cycle = database.row("initiative_cycles", "cycle_id", "cycle-a2")
    assert cycle["stage"] == "COMPLETED"
    assert cycle["status"] == "IDLE"
    assert json.loads(str(cycle["metrics_json"]))["objective_creations"] == 0
    assert reconcile(database).consistent is True
