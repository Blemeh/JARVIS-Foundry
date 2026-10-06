"""Owner-facing lineage invariant: causal joins, never nearest-record guesses.

One mission page must derive ONE authoritative lineage (owner directive ->
objective -> plan -> mission -> worker runs -> artifacts -> evidence ->
verifier). The work-hierarchy projection joins only explicit canonical
relationships; the frontend resolves the displayed owner goal through that
same join. This regression locks the backend half: complete per-mission
linkage survives bounded global windows.
"""
from __future__ import annotations

from jarvis_foundry.command_center_work import project_work_hierarchy


def _hierarchy(workers: list[dict]) -> dict:
    return project_work_hierarchy(
        autonomy={
            "objectives": {
                "owner-goal:owner-causal": {
                    "objective_id": "owner-goal:owner-causal",
                    "objective": "Causal owner goal text",
                    "status": "COMPLETED",
                    "mission_ids": ["mission-causal"],
                    "plan_ids": ["plan:owner-goal:owner-causal:abc"],
                    "provenance": {"directive_id": "owner-causal", "event_id": "event-goal"},
                },
                "owner-goal:owner-newest": {
                    "objective_id": "owner-goal:owner-newest",
                    "objective": "Newest unrelated goal text",
                    "status": "ACTIVE",
                    "mission_ids": [],
                    "plan_ids": [],
                    "provenance": {"directive_id": "owner-newest", "event_id": "event-new"},
                },
            },
            "plans": {
                "plan:owner-goal:owner-causal:abc": {
                    "plan_id": "plan:owner-goal:owner-causal:abc",
                    "objective_id": "owner-goal:owner-causal",
                    "steps": [{
                        "step_id": "execute",
                        "description": "Execute the causal goal",
                        "status": "COMPLETED",
                        "mission_id": "mission-causal",
                    }],
                },
            },
            "progress": {},
            "campaigns": {},
        },
        goals=[
            {"directive_id": "owner-causal", "exact_text": "Causal owner goal text", "active": 1, "last_event_id": "event-goal"},
            {"directive_id": "owner-newest", "exact_text": "Newest unrelated goal text", "active": 1, "last_event_id": "event-new"},
        ],
        missions=[{"mission_id": "mission-causal", "state": "COMPLETE", "last_event_id": "event-mission"}],
        workers=workers,
        evidence=[{"bundle_id": "bundle-1", "mission_id": "mission-causal", "step_id": "execute"}],
        verdicts=[{"verdict_id": "verdict-1", "mission_id": "mission-causal"}],
        mission_steps=[{
            "contract_id": "contract-1",
            "mission_id": "mission-causal",
            "step_id": "execute",
            "capability": "write_registered_worktree_files",
        }],
    )


def test_hierarchy_links_mission_to_its_own_objective_and_goal() -> None:
    result = _hierarchy(workers=[{"run_id": "run-1", "mission_id": "mission-causal", "step_id": "execute"}])
    mission = result["missions"][0]
    assert mission["objective_id"] == "owner-goal:owner-causal"
    objective = next(o for o in result["objectives"] if o["objective_id"] == "owner-goal:owner-causal")
    assert objective["goal_id"] == "goal:owner-causal"
    assert objective["mission_ids"] == ["mission-causal"]
    assert objective["plan_ids"] == ["plan:owner-goal:owner-causal:abc"]
    assert mission["worker_ids"] == ["run-1"]
    assert mission["evidence_refs"] == ["bundle-1"]
    assert mission["verdict_ids"] == ["verdict-1"]
    # The unrelated newest goal stays unlinked; a consumer resolving the
    # displayed goal through objective.goal_id can never substitute it.
    newest = next(o for o in result["objectives"] if o["objective_id"] == "owner-goal:owner-newest")
    assert newest["mission_ids"] == []
    newest_goal = next(g for g in result["goals"] if g["goal_id"] == "goal:owner-newest")
    assert newest_goal["objective_ids"] == ["owner-goal:owner-newest"]


def test_complete_per_mission_workers_survive_bounded_windows() -> None:
    aged = [{"run_id": f"run-old-{i}", "mission_id": "mission-causal", "step_id": "execute"} for i in range(150)]
    newest = [{"run_id": f"run-new-{i}", "mission_id": "mission-other", "step_id": "execute"} for i in range(100)]
    # A bounded "newest 100" window over [aged..., newest...] would lose every
    # run for mission-causal. Complete per-mission linkage must not.
    result = _hierarchy(workers=aged[:5])
    assert result["missions"][0]["worker_ids"] == [f"run-old-{i}" for i in range(5)]


def test_unlinked_mission_proves_no_objective_instead_of_guessing() -> None:
    result = project_work_hierarchy(
        autonomy={"objectives": {}, "plans": {}, "progress": {}},
        goals=[],
        missions=[{"mission_id": "mission-system", "state": "COMPLETE"}],
        workers=[],
        evidence=[],
        verdicts=[],
        mission_steps=[],
    )
    assert result["missions"][0]["objective_id"] is None
