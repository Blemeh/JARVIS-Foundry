"""Purpose Recovery II Lane A — executive intelligence product path."""
from __future__ import annotations

import subprocess
from pathlib import Path

from jarvis_foundry.command_center import _executive_projection
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import rebuild_executive_state, sync_owner_goals
from jarvis_foundry.executive.calibration import rebuild_calibration
from jarvis_foundry.executive.goals import active_goals, goal_alignment_score
from jarvis_foundry.executive.intelligence import (
    assemble_project_inventory,
    measure_outcome,
    run_executive_intelligence_cycle,
    schedule_next_action_after_outcome,
)
from jarvis_foundry.executive.owner_context import seed_owner_context
from jarvis_foundry.executive.standing_intent import is_discovery_mandate
from jarvis_foundry.fabric.catalog import record_outcome as record_fabric_outcome
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.initiative_a3 import A3AdmissionPolicy
from jarvis_foundry.owner_controls import OwnerControlService, sign_command


STANDING_GOAL = (
    "Keep improving my active projects while I'm away. "
    "Find what is worth improving among my registered projects and take the best next step."
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(root: Path, *, name: str, note: str, extra_note: str = "") -> None:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "lane-a@example.invalid")
    _git(root, "config", "user.name", "Lane A")
    (root / "README.md").write_text(f"# {name}\nOwner-valued project.\n", encoding="utf-8")
    (root / "work.py").write_text(f"# TODO: {note}\nVALUE = 1\n", encoding="utf-8")
    extra = extra_note or ""
    if extra:
        (root / "style.py").write_text(f"# TODO: {extra}\nSTYLE = 0\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", f"seed {name}")


def _goal(database: FoundryDatabase, secret: bytes, text: str, nonce: str) -> None:
    svc = OwnerControlService(database, secret=secret, owner_id="owner")
    sig = sign_command(secret, "GOAL", "GLOBAL", text, nonce)
    svc.apply("GOAL", target="GLOBAL", exact_text=text, nonce=nonce, signature=sig)


def _ready(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-09-17T00:00:00Z", "standing_mandate": True},
        source_type="owner",
        source_id="lane-a-owner",
        idempotency_key="system.start-build",
    ))
    seed_builtins(database)
    for capability in A3AdmissionPolicy().required_fabric_capabilities:
        record_fabric_outcome(
            database,
            capability,
            success=True,
            verified=VerifyVerdict.MATCH,
            note="Lane A fabric readiness",
            at="2026-09-17T00:00:00Z",
        )
    return database


def test_discovery_mandate_is_not_an_executable_recipe() -> None:
    assert is_discovery_mandate(STANDING_GOAL)
    assert not is_discovery_mandate(
        "Improve gateway reliability and reduce customer outage risk today. "
        "priority: 3 success: latency under 200ms"
    )


def test_standing_goal_aligns_with_project_improvement_without_token_overlap(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "align.db")
    database.initialize()
    secret = b"test-secret-32-bytes-minimum!!"
    _goal(database, secret, STANDING_GOAL, "standing-1")
    goals = active_goals(database)
    assert goals
    score = goal_alignment_score(
        goals[0],
        "Resolve tracked TODO in work.py: improve reliability and fix regression",
        candidate_type="tracked_work",
        target_project="alpha",
    )
    weak = goal_alignment_score(
        goals[0],
        "Resolve tracked TODO in work.py: rename variable for style",
        candidate_type="tracked_work",
        target_project="beta",
    )
    assert score >= 0.62
    assert weak < score
    assert weak <= 0.20


def test_sync_owner_goals_records_standing_intent_instead_of_mission_recipe(tmp_path: Path) -> None:
    database = _ready(tmp_path)
    secret = b"test-secret-32-bytes-minimum!!"
    _goal(database, secret, STANDING_GOAL, "standing-sync")
    created = sync_owner_goals(database)
    assert created == []
    state = rebuild_executive_state(database)
    assert not any(oid.startswith("owner-goal:") for oid in state.objectives)
    intents = [
        event for event in database.events(event_types=("executive.standing_intent_recorded",))
    ]
    assert intents
    assert intents[0].payload.get("kind") == "DISCOVERY_MANDATE"


def _two_projects(tmp_path: Path) -> tuple[FoundryDatabase, Path, Path]:
    database = _ready(tmp_path)
    secret = b"test-secret-32-bytes-minimum!!"
    _goal(database, secret, STANDING_GOAL, "standing-cycle")
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    _repo(
        alpha,
        name="Alpha Reliability",
        note="improve reliability and fix regression with a bounded reversible local test",
        extra_note="rename variable for style",
    )
    _repo(
        beta,
        name="Beta Maintenance",
        note="fix failing local checks and reduce regression risk today",
    )
    database.append(registration_event(inspect_git_resource(alpha, resource_id="resource-alpha")))
    database.append(registration_event(inspect_git_resource(beta, resource_id="resource-beta")))
    seed_owner_context(
        database,
        goals=[{
            "text": STANDING_GOAL,
            "source_ref": "IDEA.md",
            "source_hash": "a" * 64,
        }],
        projects=[
            {"text": "Alpha Reliability active project", "source_ref": "alpha/README.md", "source_hash": "b" * 64},
            {"text": "Beta Cosmetic active project", "source_ref": "beta/README.md", "source_hash": "c" * 64},
        ],
        standing_rejections=[{
            "text": "cosmetic rename and style-only churn",
            "source_ref": "docs/governance/AUTONOMY_MANDATE.md",
            "source_hash": "d" * 64,
        }],
    )
    return database, alpha, beta


def test_unscripted_cycle_discovers_rejects_and_selects_across_projects(tmp_path: Path) -> None:
    database, _alpha, _beta = _two_projects(tmp_path)
    inventory = assemble_project_inventory(database)
    assert {row["resource_id"] for row in inventory} >= {"resource-alpha", "resource-beta"}

    cycle = run_executive_intelligence_cycle(
        database,
        cycle_id="lane-a-unscripted",
        resource_id="resource-alpha",
        required_capabilities=tuple(A3AdmissionPolicy().required_fabric_capabilities),
    )
    assert cycle.standing_intents
    assert cycle.decision is not None
    selected = (
        database.row("candidates", "candidate_id", cycle.decision.candidate_id)
        if cycle.decision.candidate_id else None
    )
    rejected = list(cycle.decision.rejected) + list(cycle.decision.deferred)
    assert cycle.decision.kind == "SELECT"
    assert selected is not None
    objective = str(selected.get("objective") or "").lower()
    assert "rename variable for style" not in objective
    assert rejected, cycle.as_dict()["decision"]
    assert cycle.decision_truth.get("why_chosen")
    assert cycle.wait_diagnosis.get("classification") in {"ACTION", "JUSTIFIED_WAIT"}
    projection = _executive_projection(database)
    latest = projection["latest_decision"]
    assert latest is not None
    assert latest.get("why_chosen") or latest.get("reason")
    assert latest.get("next_action")


def test_research_findings_change_a_later_decision(tmp_path: Path) -> None:
    database, _alpha, _beta = _two_projects(tmp_path)
    first = run_executive_intelligence_cycle(
        database,
        cycle_id="lane-a-research-1",
        resource_id="resource-alpha",
        required_capabilities=tuple(A3AdmissionPolicy().required_fabric_capabilities),
    )
    assert first.decision is not None
    target = str(first.decision.candidate_id)
    before = first.decision.candidate_id
    second = run_executive_intelligence_cycle(
        database,
        cycle_id="lane-a-research-2",
        resource_id="resource-alpha",
        required_capabilities=tuple(A3AdmissionPolicy().required_fabric_capabilities),
        research_findings={
            target: {
                "stance": "CONTRADICTING",
                "summary": "Local evidence shows this opportunity is already solved and would be performative churn.",
                "provenance": "repo-readback:README.md",
                "contradicting": True,
                "supporting": False,
                "confidence": 0.9,
            }
        },
    )
    assert second.decision is not None
    assert second.research.get("applied")
    changed = (
        second.decision.kind != first.decision.kind
        or second.decision.candidate_id != before
        or any(str(item.get("candidate_id")) == target for item in second.decision.rejected)
    )
    assert changed
    assert second.decision_truth.get("learned", {}).get("research_findings_consumed") is True


def test_low_value_outcome_reduces_later_similar_score(tmp_path: Path) -> None:
    database, _alpha, _beta = _two_projects(tmp_path)
    first = run_executive_intelligence_cycle(
        database,
        cycle_id="lane-a-learn-1",
        resource_id="resource-alpha",
        required_capabilities=tuple(A3AdmissionPolicy().required_fabric_capabilities),
    )
    assert first.decision is not None
    candidate_id = first.decision.candidate_id or "candidate-none"
    row = database.row("candidates", "candidate_id", candidate_id) or {}
    source = str(row.get("source") or "initiative_observation")
    objective = str(row.get("objective") or "Resolve tracked TODO")
    before = rebuild_calibration(database).adjustment_for(source=source, objective=objective)
    actual = measure_outcome(
        database,
        decision_id=first.decision.decision_id,
        candidate_id=candidate_id,
        mission_id="mission-learn-stub",
        value_predicted=0.82,
        value_realized=0.10,
        effort_predicted=0.20,
        effort_actual=0.55,
        risk_predicted="LOW",
        risk_actual="MEDIUM",
        outcome="PASSED",
        source=source,
        objective=objective,
    )
    assert actual["value_realized"] == 0.10
    after = rebuild_calibration(database).adjustment_for(source=source, objective=objective)
    assert after < before
    wake = schedule_next_action_after_outcome(
        database, mission_id="mission-learn-stub", outcome="PASSED",
    )
    assert wake["wake_id"]
    second = run_executive_intelligence_cycle(
        database,
        cycle_id="lane-a-learn-2",
        resource_id="resource-alpha",
        required_capabilities=tuple(A3AdmissionPolicy().required_fabric_capabilities),
    )
    assert second.decision is not None
    assert second.standing_intents
    assert second.decision.kind in {"SELECT", "WAIT"}
    if second.decision.kind == "WAIT":
        assert second.wait_diagnosis["classification"] == "JUSTIFIED_WAIT"


def test_new_project_speculative_work_is_judged_not_forced(tmp_path: Path) -> None:
    database, _alpha, _beta = _two_projects(tmp_path)
    cycle = run_executive_intelligence_cycle(
        database,
        cycle_id="lane-a-new-project",
        resource_id="resource-alpha",
        required_capabilities=tuple(A3AdmissionPolicy().required_fabric_capabilities),
    )
    assert cycle.new_project_judgment.get("judgment") in {
        "NONE", "REJECT / DEFER", "BUILD",
    }
    if cycle.new_project_judgment.get("judgment") == "BUILD":
        assert cycle.decision and cycle.decision.kind == "SELECT"


def test_empty_a3_sources_bind_registered_projects(tmp_path: Path, monkeypatch) -> None:
    from jarvis_foundry.paths import FoundryPaths
    from jarvis_foundry.runtime.config import WorkerRuntimeConfig
    from jarvis_foundry.runtime.worker_loop import run_cycle

    database, _alpha, _beta = _two_projects(tmp_path)
    captured: dict[str, object] = {}

    def fake_observe(database, *, sources, **kwargs):
        captured["sources"] = list(sources)
        return {"outcome": "WAIT", "reason": "no eligible candidate", "decision": None}

    monkeypatch.setattr(
        "jarvis_foundry.initiative_a2.run_continuous_observation_cycle",
        fake_observe,
    )
    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        lab_scripted_hermes=True,
        runtime_state_root=str(tmp_path / "runtime"),
        initiative_a3_enabled=True,
        initiative_a3={
            "enabled": True,
            "sources": [],
            "bounds": {"do_nothing_score": 0.10},
            "worktree_parent": str(tmp_path / "worktrees"),
        },
    )
    config.validate()
    report = run_cycle(
        database,
        FoundryPaths(database.path.parent),
        config,
        loop_id="lane-a-empty-src",
        instance_id="worker-lane-a",
    )
    sources = list(captured.get("sources") or ())
    assert sources, report
    source_ids = {
        getattr(item, "source_id", None) or (item.get("source_id") if isinstance(item, dict) else None)
        for item in sources
    }
    assert source_ids >= {"project:resource-alpha", "project:resource-beta"}
    assert report["outcome"] == "WAITING"
    assert "no registered project sources" not in str(report.get("reason") or "")


def test_evaluate_records_wait_diagnosis_and_prediction_merge(tmp_path: Path) -> None:
    from jarvis_foundry.executive.calibration import merge_predicted_vs_actual
    from jarvis_foundry.executive.decisions import ExecutiveDecisionService
    from jarvis_foundry.executive.predictions import record_prediction

    database = _ready(tmp_path)
    decision = ExecutiveDecisionService(database, min_action_score=0.40).evaluate_a2(
        resource_id="resource-alpha",
        loop_id="lane-a-wait-diag",
        capability="write_registered_worktree_files",
    )
    assert decision.kind == "WAIT"
    recorded = [
        event for event in database.events(event_types=("executive.wait_diagnosis_recorded",))
    ]
    assert recorded
    assert recorded[-1].payload.get("classification") == "JUSTIFIED_WAIT"
    projection = _executive_projection(database)
    latest = projection["latest_decision"]
    assert latest is not None
    assert latest.get("wait_diagnosis", {}).get("classification") == "JUSTIFIED_WAIT"
    record_prediction(
        database,
        decision_id=decision.decision_id,
        candidate_id=decision.candidate_id or "none",
        prediction={"expected_value": 0.7, "expected_effort": 0.2, "expected_risk": "LOW"},
    )
    merged = merge_predicted_vs_actual(
        database,
        {"outcome": "FAILED", "repair_count": 2},
        decision_id=decision.decision_id,
        candidate_id=decision.candidate_id or "none",
    )
    assert merged["value_predicted"] == 0.7
    assert merged["value_realized"] == 0.0
    assert merged["risk_actual"] == "HIGH"
    assert merged["effort_actual"] == 2.0
    wake = schedule_next_action_after_outcome(
        database, mission_id="mission-next-action-proof", outcome="PASSED",
    )
    assert wake["wake_id"]
    wakes = [
        event for event in database.events(event_types=("executive.wake_created", "executive.next_action_scheduled"))
    ]
    assert any(event.event_type == "executive.next_action_scheduled" for event in wakes)
