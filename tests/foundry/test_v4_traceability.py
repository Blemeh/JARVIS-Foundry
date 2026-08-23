from __future__ import annotations

import json
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.result_traceability import build_result_traceability


def _append(database: FoundryDatabase, event_type: str, payload: dict, key: str) -> None:
    database.append(Event.create(
        event_type,
        payload,
        source_type="test",
        source_id="v4-traceability",
        idempotency_key=key,
    ))


def _seed_rows(database: FoundryDatabase) -> None:
    now = "2026-08-23T10:00:00+00:00"
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO candidates (candidate_id, mission_id, source, objective, "
            "objective_canonical, evidence_json, status, fingerprint, candidate_type, "
            "expected_outcome, scoring_policy_version, last_event_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("cand-trace", "mission-trace", "test", "trace result", "trace result",
             json.dumps([{"evidence_id": "bundle-trace"}]), "EXECUTED", "fp-trace",
             "repair", "measured result", None, "seed:cand-trace", now),
        )
        connection.execute(
            "INSERT INTO missions (mission_id, objective, resource_id, state, "
            "acceptance_json, next_action, created_from, last_event_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("mission-trace", "trace result", "resource-trace", "COMPLETE", "[]",
             "review measured outcome", "candidate:cand-trace", "seed:mission-trace", now),
        )


def test_result_traceability_links_durable_lineage_and_owner_decision(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "trace.db")
    database.initialize()
    _seed_rows(database)
    _append(database, "executive.decision_recorded", {
        "decision_id": "decision-trace", "decision": "SELECT",
        "candidate_id": "cand-trace", "mission_id": "mission-trace",
        "reason": "best evidenced candidate",
    }, "decision-trace")
    _append(database, "executive.decision_materialized", {
        "decision_id": "decision-trace", "decision": "SELECT",
        "candidate_id": "cand-trace", "mission_id": "mission-trace",
    }, "decision-materialized-trace")
    _append(database, "agent_run.started", {
        "run_id": "builder-trace", "mission_id": "mission-trace", "step_id": "step-1",
        "role": "builder", "provider": "opencode_go", "model": "deepseek-v4-flash",
    }, "builder-started-trace")
    _append(database, "agent_run.completed", {"run_id": "builder-trace"}, "builder-completed-trace")
    _append(database, "verification.started", {
        "verdict_id": "verdict-trace", "mission_id": "mission-trace",
        "subject_type": "MISSION", "subject_id": "mission-trace",
        "verifier_run_id": "verifier-trace",
    }, "verification-started-trace")
    _append(database, "verification.passed", {
        "verdict_id": "verdict-trace", "verdict": "PASS",
        "verifier_run_id": "verifier-trace", "findings": [],
        "missing_proof": [], "scope_violations": [],
    }, "verification-passed-trace")
    _append(database, "evidence.created", {
        "bundle_id": "bundle-trace", "mission_id": "mission-trace", "step_id": "step-1",
        "objective": "trace result", "criteria": ["lineage"], "before_state": {},
        "baseline_commit": "base", "final_diff": {}, "files_changed": [],
        "commands_run": ["pytest"], "artifact_hashes": {}, "after_state": {},
        "uncertainty": {}, "rollback_proof": {}, "builder_claim": {},
        "verifier_verdict_id": "verdict-trace",
    }, "evidence-created-trace")
    _append(database, "verification.failed", {
        "verdict_id": "verdict-old", "mission_id": "mission-trace",
        "failure_class": "evidence_mismatch", "repair_requirements": ["rebuild proof"],
    }, "verification-failed-trace")
    _append(database, "system.action_started", {
        "action_key": "action-trace", "system_id": "system-trace",
        "target_ref": "target-trace", "payload_hash": "hash-trace", "kind": "file.write",
        "mission_id": "mission-trace",
    }, "effect-started-trace")
    _append(database, "system.action_completed", {
        "action_key": "action-trace", "mission_id": "mission-trace",
    }, "effect-completed-trace")
    _append(database, "discovery.candidate_outcome_recorded", {
        "outcome_id": "cand-trace:immediate", "candidate_id": "cand-trace",
        "mission_id": "mission-trace", "window": "immediate",
        "predicted_value": {"score": 0.7}, "actual_value": {"score": 0.9},
        "status": "RECORDED", "outcome": {"status": "RECORDED"},
    }, "outcome-trace")
    service = OwnerControlService(database, secret=b"trace-owner")
    text = "review mission-trace outcome"
    nonce = "owner-trace"
    service.apply(
        "CONSTRAINT", target="mission-trace", exact_text=text, nonce=nonce,
        signature=sign_command(b"trace-owner", "CONSTRAINT", "mission-trace", text, nonce),
        directive_id="owner-trace-directive",
    )

    projection = build_result_traceability(database, now="2026-08-23T12:00:00+00:00")
    result = projection["results"][0]

    assert projection["schema"] == "v4.result-traceability.v1"
    assert result["decision"]["decision_id"] == "decision-trace"
    assert result["evidence"][0]["bundle_id"] == "bundle-trace"
    assert result["workers"][0]["run_id"] == "builder-trace"
    assert result["verifiers"][0]["verdict_id"] == "verdict-trace"
    assert result["failures_repairs"][0]["event_type"] == "verification.failed"
    assert result["external_changes"][0]["event_type"] == "system.action_started"
    assert result["actual_outcome"]["status"] == "EVIDENCED"
    assert result["next_action"] == {"status": "EVIDENCED", "value": "review measured outcome", "source": "missions.next_action"}
    assert result["owner_decisions"][0]["directive_id"] == "owner-trace-directive"
    assert result["actual_outcome"]["source_event_id"] in result["evidence_refs"]


def test_result_traceability_is_honest_when_actual_and_external_change_are_missing(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "unknown.db")
    database.initialize()
    _seed_rows(database)
    _append(database, "discovery.candidate_outcome_recorded", {
        "outcome_id": "cand-trace:immediate", "candidate_id": "cand-trace",
        "mission_id": "mission-trace", "window": "immediate",
        "predicted_value": {"score": 0.7}, "actual_value": {},
        "status": "UNKNOWN", "outcome": {"status": "UNKNOWN"},
    }, "outcome-unknown")
    with database.connect() as connection:
        connection.execute("UPDATE missions SET next_action = NULL WHERE mission_id = 'mission-trace'")

    result = build_result_traceability(database)["results"][0]

    assert result["actual_outcome"]["status"] == "UNKNOWN"
    assert result["actual_outcome"]["value"] is None
    assert result["external_changes"]["status"] == "UNKNOWN"
    assert result["next_action"]["value"] == "RECONCILE_OUTCOME"
