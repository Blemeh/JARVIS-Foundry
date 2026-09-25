"""Focused truth, optional-bridge, and bounded-read tests for V11 UI data."""
from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from jarvis_foundry.autonomy_projection import build_autonomy_projection
from jarvis_foundry.command_center import CommandCenterServer
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.verification import (
    record_verification_acceptance,
    record_verification_outcome,
    start_verification,
)
from jarvis_foundry.mission.contracts import MissionStepContract, record_step_contract


def _append(database: FoundryDatabase, event_type: str, payload: dict,
            key: str) -> Event:
    event = Event.create(
        event_type, payload, source_type="test",
        source_id="v11-command-center", idempotency_key=key,
    )
    database.append(event)
    return event


def _candidate(database: FoundryDatabase, candidate_id: str, *,
               outcome: str, rank: int, reason: str,
               factors: dict | None = None,
               evidence: list[dict] | None = None) -> None:
    _append(database, "discovery.candidate_generated", {
        "candidate_id": candidate_id,
        "mission_id": "v11-a2-candidate-holding",
        "source": "initiative_observation",
        "objective": f"Investigate {candidate_id} for the owner",
        "objective_canonical": f"investigate {candidate_id}",
        "expected_outcome": f"Resolve {candidate_id}",
        "evidence": evidence or [{"authority": "DATA_ONLY"}],
        "fingerprint": candidate_id.ljust(64, "f")[:64],
        "expires_at": "2026-09-09T00:00:00Z",
    }, f"candidate:{candidate_id}")
    _append(database, "discovery.candidate_source_linked", {
        "link_id": f"link:{candidate_id}",
        "candidate_id": candidate_id,
        "observation_id": f"observation:{candidate_id}",
        "source_kind": "project",
        "source_identity": "JARVIS-Foundry",
        "source_ref": f"repo://JARVIS-Foundry/{candidate_id}",
        "authority": "DATA_ONLY",
        "observed_at": "2026-09-02T09:00:00Z",
        "fresh_until": "2026-09-09T00:00:00Z",
    }, f"candidate-source:{candidate_id}")
    _append(database, "discovery.candidate_judged", {
        "judgment_id": f"judgment:{candidate_id}",
        "candidate_id": candidate_id,
        "decision_id": "decision:cycle-1",
        "cycle_id": "cycle-1",
        "stage": "FINAL",
        "outcome": outcome,
        "reason_code": "SELECTED_SURVIVOR" if outcome == "ELIGIBLE" else "LOW_OWNER_RELEVANCE",
        "reason_detail": reason,
        "score": 0.78 if outcome == "ELIGIBLE" else 0.12,
        "rank": rank,
        "factors": factors or {
            "owner_context_goal_relevance": {"value": 0.9, "kind": "KNOWN", "contribution": 0.07},
            "expected_value": {"value": 0.8, "kind": "KNOWN", "contribution": 0.16},
            "confidence": {"value": 0.75, "kind": "KNOWN", "contribution": 0.0375},
            "effort": {"value": 0.6, "kind": "KNOWN", "contribution": 0.03},
            "fabric_capability_readiness": {"value": 1.0, "kind": "KNOWN", "contribution": 0.04},
            "opportunity_cost_headroom": {"value": 0.7, "kind": "KNOWN", "contribution": 0.012},
            "novelty_history": {"value": 1.0, "kind": "KNOWN", "contribution": 0.025},
        },
        "policy_version": "v11-a2",
        "decided_at": "2026-09-02T09:04:00Z",
    }, f"judgment:{candidate_id}")


def _seed(database: FoundryDatabase) -> None:
    _append(database, "initiative.cycle_started", {
        "cycle_id": "cycle-1", "status": "RUNNING",
        "started_at": "2026-09-02T09:00:00Z",
    }, "cycle-started")
    _append(database, "initiative.source_recorded", {
        "cycle_id": "cycle-1", "source_id": "source-project",
        "source_kind": "project", "source_identity": "JARVIS-Foundry",
        "status": "READY", "observations_recorded": 3,
        "candidates_recorded": 2, "next_due_at": "2026-09-02T10:00:00Z",
    }, "source-project")
    _append(database, "initiative.source_recorded", {
        "cycle_id": "cycle-1", "source_id": "source-gmail",
        "source_kind": "gmail", "source_identity": "owner-inbox",
        "status": "BLOCKED", "observations_recorded": 0,
        "candidates_recorded": 0,
        "reason": "Gmail observation is deferred; no configured read capability",
        "next_due_at": "2026-09-02T10:00:00Z",
    }, "source-gmail")
    _candidate(database, "candidate-eligible", outcome="ELIGIBLE", rank=1,
               reason="Highest surviving score above the do-nothing baseline")
    _candidate(database, "candidate-rejected", outcome="REJECTED", rank=2,
               reason="No evidence-backed owner goal or active-project relevance")
    _append(database, "research.candidate_enqueued", {
        "research_id": "research-eligible", "candidate_id": "candidate-eligible",
        "cycle_id": "cycle-1", "pass_number": 1, "status": "ENQUEUED",
        "budget_used": 0.5, "attempted_at": "2026-09-02T09:02:00Z",
    }, "research-enqueued")
    _append(database, "research.candidate_started", {
        "research_id": "research-eligible", "candidate_id": "candidate-eligible",
        "cycle_id": "cycle-1", "pass_number": 1, "status": "IN_PROGRESS",
        "budget_used": 0.5, "attempted_at": "2026-09-02T09:02:30Z",
    }, "research-started")
    _append(database, "research.candidate_completed", {
        "research_id": "research-eligible", "candidate_id": "candidate-eligible",
        "cycle_id": "cycle-1", "pass_number": 1, "status": "COMPLETED",
        "budget_used": 0.5, "completed_at": "2026-09-02T09:03:00Z",
        "evidence": {"authority": "DATA_ONLY", "finding": "bounded evidence"},
        "content_hash": "a" * 64,
    }, "research-completed")
    _append(database, "initiative.cycle_completed", {
        "cycle_id": "cycle-1", "status": "WAIT",
        "completed_at": "2026-09-02T09:05:00Z",
        "next_due_at": "2026-09-02T10:00:00Z",
        "reason": "A2 stopped at the admission boundary; waiting for the next wake",
        "metrics": {
            "observations_recorded": 3,
            "raw_candidates_generated": 3,
            "unique_candidates": 2,
            "candidate_duplicates_suppressed": 1,
            "candidates_researched": 1,
            "candidates_rejected": 1,
            "candidates_eligible": 1,
        },
    }, "cycle-completed")


def test_projection_exposes_funnel_reasons_sources_wait_and_candidate_details(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    _seed(database)

    projection = build_autonomy_projection(
        database, now="2026-09-02T09:10:00Z",
    )

    assert projection["status"] == "AVAILABLE"
    assert {key: value["value"] for key, value in projection["funnel"].items()} == {
        "observed": 3, "generated": 3, "deduped": 2,
        "duplicates_suppressed": 1, "researched": 1, "rejected": 1,
        "eligible": 1, "executing": None, "verified_outcome": None,
    }
    assert projection["executive"]["state"] == "WAIT"
    assert "admission boundary" in projection["executive"]["wait_reason"]
    assert projection["executive"]["next_wake"] == "2026-09-02T10:00:00Z"
    assert projection["source_digest"]["contract"] == "jarvis.source-digest.v1"
    assert projection["source_digest"]["status"] == "AVAILABLE"
    assert projection["wait"]["status"] == "WAITING"
    assert projection["overview"]["chosen"]["candidate_id"] == "candidate-eligible"
    gmail = next(item for item in projection["sources"] if item["source_kind"] == "gmail")
    assert gmail["state"] == "BLOCKED"
    assert gmail["readiness"] == "DEFERRED"
    assert "no configured read capability" in gmail["last_error"]
    eligible = next(item for item in projection["candidates"] if item["state"] == "ELIGIBLE")
    assert eligible["project"] == "JARVIS-Foundry"
    assert eligible["research"]["status"] == "COMPLETED"
    assert eligible["judgment"]["evidence"]["owner_relevance"][0]["factor"] == "owner_context_goal_relevance"
    rejected = build_autonomy_projection(database, candidate_filter="rejected")
    assert [item["candidate_id"] for item in rejected["candidates"]] == ["candidate-rejected"]
    assert rejected["candidates"][0]["rejection"]["reason_code"] == "LOW_OWNER_RELEVANCE"


def test_projection_surfaces_open_owner_escalations_and_resolutions(tmp_path):
    database = FoundryDatabase(tmp_path / "owner-actions.db")
    database.initialize()
    _seed(database)
    opened = _append(database, "executive.escalation_opened", {
        "escalation_id": "escalation:objective-retry",
        "objective_id": "objective-retry",
        "decision": "retry budget exhausted",
        "why": "The execute step exhausted its bounded retry budget.",
        "options": ["change strategy", "grant capability", "suspend objective"],
        "recommendation": "Suspend until owner direction or a materially different capability is available.",
        "delay_consequence": "Downstream work remains blocked.",
        "created_at": "2026-09-25T06:20:00Z",
        "status": "OPEN",
    }, "escalation-opened:objective-retry")

    projection = build_autonomy_projection(database)
    owner_action = projection["executive"]["owner_action"]
    assert owner_action["status"] == "AVAILABLE"
    assert owner_action["count"] == 1
    assert owner_action["value"] == owner_action["items"][0]
    assert owner_action["items"][0] == {
        "escalation_id": "escalation:objective-retry",
        "objective_id": "objective-retry",
        "decision": "retry budget exhausted",
        "why": "The execute step exhausted its bounded retry budget.",
        "recommendation": "Suspend until owner direction or a materially different capability is available.",
        "options": ["change strategy", "grant capability", "suspend objective"],
        "delay_consequence": "Downstream work remains blocked.",
        "created_at": "2026-09-25T06:20:00Z",
        "event_id": opened.event_id,
    }
    timeline_item = next(
        item for item in projection["timeline"]
        if item["event_id"] == opened.event_id
    )
    assert timeline_item["stage"] == "OWNER_ACTION"

    _append(database, "executive.escalation_resolved", {
        "escalation_id": "escalation:objective-retry",
        "objective_id": "objective-retry",
        "decision": "retry budget exhausted",
        "status": "RESOLVED",
    }, "escalation-resolved:objective-retry")
    resolved = build_autonomy_projection(database)
    assert resolved["executive"]["owner_action"]["items"] == []
    assert resolved["executive"]["owner_action"]["value"] is None


def test_owner_action_projection_marks_item_truncation_and_bounds_text(tmp_path):
    database = FoundryDatabase(tmp_path / "owner-action-limits.db")
    database.initialize()
    _seed(database)
    for index in range(21):
        long_text = "x" * 1_500 if index == 20 else f"decision {index}"
        _append(database, "executive.escalation_opened", {
            "escalation_id": f"escalation:{index}",
            "objective_id": "objective-limits",
            "decision": long_text,
            "why": long_text,
            "recommendation": long_text,
            "options": [long_text for _ in range(16)],
            "delay_consequence": long_text,
            "status": "OPEN",
        }, f"escalation-opened:{index}")

    owner_action = build_autonomy_projection(database)["executive"]["owner_action"]
    assert owner_action["status"] == "PARTIAL"
    assert owner_action["count"] == 20
    assert owner_action["open_count"] == 21
    assert owner_action["items_truncated"] is True
    assert owner_action["scan_truncated"] is False
    assert owner_action["items"][0]["escalation_id"] == "escalation:20"
    assert len(owner_action["items"][0]["decision"]) == 1_200
    assert len(owner_action["items"][0]["why"]) == 1_200
    assert len(owner_action["items"][0]["recommendation"]) == 1_200
    assert len(owner_action["items"][0]["options"]) == 12
    assert all(len(option) == 240 for option in owner_action["items"][0]["options"])
    assert len(owner_action["items"][0]["delay_consequence"]) == 1_200


def test_projection_keeps_optional_research_capability_and_context_contracts_durable(tmp_path):
    database = FoundryDatabase(tmp_path / "contracts.db")
    database.initialize()
    _seed(database)
    _append(database, "research.candidate_enqueued", {
        "research_id": "research-eligible-2", "candidate_id": "candidate-eligible",
        "cycle_id": "cycle-1", "pass_number": 2, "status": "ENQUEUED",
        "budget_used": 0.5,
    }, "research-enqueued-2")
    _append(database, "research.candidate_started", {
        "research_id": "research-eligible-2", "candidate_id": "candidate-eligible",
        "cycle_id": "cycle-1", "pass_number": 2, "status": "IN_PROGRESS",
    }, "research-started-2")
    _append(database, "research.candidate_completed", {
        "research_id": "research-eligible-2", "candidate_id": "candidate-eligible",
        "cycle_id": "cycle-1", "pass_number": 2, "status": "COMPLETED",
        "budget_used": 0.5, "completed_at": "2026-09-02T09:06:00Z",
        "result": {"dossier": {
            "contract": "jarvis.v11.research-dossier.v1",
            "human_summary": "Bounded evidence supports the candidate.",
            "supporting_evidence": [{"evidence_id": "support-1", "statement": "A durable source supports this."}],
            "contradicting_evidence": [{"evidence_id": "contra-1", "statement": "A second source remains uncertain."}],
            "contradictions": [{"claim_key": "cost", "status": "UNRESOLVED"}],
            "freshness": {"status": "FRESH"},
            "confidence": {"score": 0.7},
            "final_research_conclusion": {"verdict": "SUPPORTED"},
        }},
    }, "research-completed-2")
    # A later candidate revision can carry trusted, system-created optional
    # context without changing the scorer or admission authority.
    _candidate(
        database, "candidate-context", outcome="ELIGIBLE", rank=3,
        reason="Context fixture", evidence=[{
            "authority": "DATA_ONLY",
            "capability_requirements": {
                "contract_version": "jarvis.v11.capability-requirements.v1",
                "required_capability_ids": ["cap.repo.read"],
                "readiness": {"state": "PARTIALLY_READY"},
                "missing_capabilities": ["cap.repo.write"],
            },
            "project_profile": {
                "schema": "jarvis.project-profile.v1", "profile_id": "profile-1",
                "project_id": "JARVIS", "stack": {"language": "Python"},
            },
            "project_context": {
                "schema": "jarvis.actionable-project-context.v1",
                "profile_ref": {"profile_id": "profile-1"},
                "context_gaps": ["CI evidence unavailable"],
            },
        }],
    )
    projection = build_autonomy_projection(database)
    eligible = next(item for item in projection["candidates"] if item["candidate_id"] == "candidate-eligible")
    assert eligible["research"]["dossier"]["available"] is True
    assert eligible["research"]["dossier"]["contradictions"][0]["status"] == "UNRESOLVED"
    contextual = next(item for item in projection["candidates"] if item["candidate_id"] == "candidate-context")
    assert contextual["capability_readiness"]["status"] == "PARTIALLY_READY"
    assert contextual["capability_readiness"]["missing_capabilities"] == ["cap.repo.write"]
    assert contextual["project_context"]["profile"]["profile_id"] == "profile-1"
    assert contextual["project_context"]["context_gaps"] == ["CI evidence unavailable"]


def test_source_digest_uses_recorded_meaningful_changes_only(tmp_path):
    database = FoundryDatabase(tmp_path / "digest.db")
    database.initialize()
    _seed(database)
    _append(database, "initiative.source_recorded", {
        "cycle_id": "cycle-1", "source_id": "source-project-refresh",
        "source_kind": "project", "source_identity": "JARVIS-Foundry",
        "status": "READY", "observations_recorded": 1,
        "candidates_recorded": 1, "content_hash": "b" * 64,
        "meaningful_change": True, "noise_suppressed": 2,
        "meaningful_observations": [{
            "source_identity": "repo://JARVIS-Foundry",
            "change_type": "UPDATED", "changed_fields": ["status"],
            "candidate_worthy": True,
        }],
        "candidate_worthy_signals": [{"signal": "status changed", "candidate_worthy": True}],
        "next_due_at": "2026-09-02T10:00:00Z",
    }, "source-project-refresh")
    projection = build_autonomy_projection(database)
    digest = projection["source_digest"]
    assert digest["meaningful_changes"][0]["change_type"] == "UPDATED"
    assert digest["candidate_worthy_signals"][0]["signal"] == "status changed"
    assert digest["noise_suppressed"] == 2
    project = next(item for item in projection["sources"] if item["source_kind"] == "project")
    assert project["last_meaningful_change"]


def test_projection_empty_state_is_honest(tmp_path):
    database = FoundryDatabase(tmp_path / "empty.db")
    database.initialize()
    projection = build_autonomy_projection(database)
    assert projection["status"] == "EMPTY"
    assert projection["candidates"] == []
    assert projection["current_work"] is None
    assert projection["funnel"]["observed"] == {
        "value": None, "status": "UNAVAILABLE",
    }
    unavailable = build_autonomy_projection(FoundryDatabase(tmp_path / "missing.db"))
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["executive"]["owner_action"] == {
        "value": None,
        "status": "UNAVAILABLE",
        "items": [],
        "count": 0,
        "open_count": None,
        "scan_truncated": False,
        "items_truncated": False,
        "scan_limit": 500,
    }


def test_optional_a3_bridge_and_preliminary_pass_stay_distinct(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    _seed(database)
    _append(database, "mission.created", {
        "mission_id": "mission-a3", "objective": "Build the selected candidate",
        "resource_id": None, "state": "PLANNED", "acceptance_criteria": ["verified"],
        "next_action": "run independent verifier",
        "created_from": "candidate:candidate-eligible",
    }, "mission-a3")
    start_verification(
        database, verdict_id="verdict-a3", mission_id="mission-a3",
        step_id="step-a3", builder_run_id="builder-a3",
        verifier_run_id="verifier-a3",
        independence={"session_independent": True}, criteria=["verified"],
    )
    record_verification_outcome(
        database, verdict_id="verdict-a3", outcome="PASS", findings=[],
        evidence={"tests": "pass"},
    )

    preliminary = build_autonomy_projection(database)
    assert preliminary["funnel"]["executing"]["status"] == "AVAILABLE"
    current = next(item for item in preliminary["candidates"]
                   if item["candidate_id"] == "candidate-eligible")
    assert current["current_work"]["mission"]["mission_id"] == "mission-a3"
    assert current["current_work"]["objective"]["status"] == "UNAVAILABLE"
    assert current["current_work"]["final_outcome"]["state"] == "PRELIMINARY_PASS"
    assert current["current_work"]["final_outcome"]["verification"]["acceptance"] == "PENDING"

    record_verification_acceptance(
        database, verdict_id="verdict-a3", mission_id="mission-a3",
        step_id="step-a3", builder_run_id="builder-a3",
        verifier_run_id="verifier-a3", checks={"acceptance": "ok"},
    )
    accepted = build_autonomy_projection(database)
    current = next(item for item in accepted["candidates"]
                   if item["candidate_id"] == "candidate-eligible")
    assert current["current_work"]["final_outcome"]["state"] == "ACCEPTED"
    assert current["current_work"]["final_outcome"]["verification"]["acceptance_event_id"]


def test_projection_surfaces_durable_a3_execution_context_and_partial_plan(tmp_path):
    database = FoundryDatabase(tmp_path / "a3-context.db")
    database.initialize()
    _seed(database)
    _append(database, "project.registered", {
        "resource_id": "resource-a3", "kind": "git",
        "root_path": "C:/work/jarvis", "resolved_root": "C:/work/jarvis",
        "baseline": {"branch": "candidate", "commit": "abc123"},
        "capabilities": {"read": "READY"}, "readiness_state": "READY",
    }, "project-registered-a3")
    _append(database, "mission.created", {
        "mission_id": "mission-a3-context", "objective": "Verify the selected change",
        "resource_id": "resource-a3", "state": "PLANNED",
        "acceptance_criteria": ["focused tests pass"],
        "next_action": "run independent verifier",
        "created_from": "candidate:candidate-eligible",
    }, "mission-a3-context")
    contract = MissionStepContract(
        mission_id="mission-a3-context", step_id="step-a3-context", version=1,
        resource_id="resource-a3", capability="repo.read",
        target_relative_path="src/check.py", instructions="Inspect the bounded change",
        expected_effects=("verification evidence",), rollback={"required": True},
        verification_criteria=("focused tests pass",),
        expected_artifacts=("verification receipt",), forbidden_paths=(".env",),
    )
    record_step_contract(database, contract)
    projection = build_autonomy_projection(database)
    current = next(item for item in projection["candidates"] if item["candidate_id"] == "candidate-eligible")
    work = current["current_work"]
    assert work["worktree"]["path"] == "C:/work/jarvis"
    assert work["current_step"]["step_id"] == "step-a3-context"
    assert work["verification_plan"]["status"] == "PARTIAL"
    assert work["verification_plan"]["acceptance_criteria"] == ["focused tests pass"]


def test_projection_queries_are_read_only_and_limits_are_hard_bounded(tmp_path, monkeypatch):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    _seed(database)
    calls: list[bool] = []
    statements: list[str] = []
    original = database.connect

    def traced_connect(*, readonly=False):
        calls.append(bool(readonly))
        connection = original(readonly=readonly)
        connection.set_trace_callback(statements.append)
        return connection

    with original(readonly=True) as connection:
        before = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    monkeypatch.setattr(database, "connect", traced_connect)
    projection = build_autonomy_projection(
        database, candidate_limit=5000, timeline_limit=5000,
    )
    with original(readonly=True) as connection:
        after = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert calls and all(calls)
    assert before == after
    assert projection["limits"]["candidate_limit"] == 50
    assert projection["limits"]["timeline_limit"] == 100
    assert projection["limits"]["owner_action_scan_limit"] == 500
    assert projection["limits"]["owner_action_limit"] == 20
    assert len(projection["candidates"]) <= 50
    assert len(projection["timeline"]) <= 100
    selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    assert len(selects) <= 17
    assert sum("FROM candidate_sources" in statement for statement in selects) == 1
    assert sum("FROM research_queue" in statement for statement in selects) == 1
    assert sum("FROM verification_verdicts" in statement for statement in selects) <= 1
    assert sum(
        "FROM events" in statement and "ORDER BY sequence DESC LIMIT 501" in statement
        for statement in selects
    ) == 1


def test_autonomy_http_api_applies_filters_and_rejects_invalid_requests(tmp_path):
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    _seed(database)
    server = CommandCenterServer(database, stop_path=tmp_path / "STOP")
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urlopen(
            f"{base}/api/autonomy?filter=rejected&limit=999&timeline_limit=999",
            timeout=3,
        ) as response:
            payload = json.loads(response.read())
        assert payload["schema"] == "v11.command-center-autonomy.v1"
        assert {item["state"] for item in payload["candidates"]} == {"REJECTED"}
        assert payload["current_work"]["candidate"]["candidate_id"] == "candidate-eligible"
        assert payload["limits"]["candidate_limit"] == 50
        assert payload["limits"]["timeline_limit"] == 100
        with pytest.raises(HTTPError) as captured:
            urlopen(f"{base}/api/autonomy?filter=made-up", timeout=3)
        assert captured.value.code == 400
    finally:
        server.close()
