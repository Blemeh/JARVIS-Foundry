from __future__ import annotations

from jarvis_foundry.command_center_work import project_work_hierarchy


def test_work_hierarchy_joins_only_explicit_canonical_relationships() -> None:
    result = project_work_hierarchy(
        autonomy={
            "objectives": {
                "objective-1": {
                    "objective_id": "objective-1",
                    "objective": "Ship the owner proof",
                    "status": "ACTIVE",
                    "mission_ids": ["mission-1"],
                    "plan_ids": ["plan-1"],
                    "provenance": {
                        "directive_id": "directive-1",
                        "event_id": "event-goal",
                    },
                    "metadata": {
                        "project_id": "project-1",
                        "project": "Command Center",
                    },
                },
            },
            "plans": {
                "plan-1": {
                    "plan_id": "plan-1",
                    "objective_id": "objective-1",
                    "steps": [{
                        "step_id": "step-1",
                        "description": "Verify the live readback",
                        "status": "RUNNING",
                        "mission_id": "mission-1",
                        "evidence_refs": ["evidence-plan"],
                    }],
                },
            },
            "progress": {
                "objective-1": {
                    "status": "ON_TRACK",
                    "confidence": 0.8,
                    "evidence_refs": ["evidence-progress"],
                },
            },
            "campaigns": {
                "schema": "v12.campaign-runtime.v1",
                "campaigns": {
                    "campaign-1": {
                        "campaign_id": "campaign-1",
                        "objective_id": "objective-1",
                        "status": "EXECUTING",
                        "children": [{
                            "child_id": "child-1",
                            "mission_id": "mission-1",
                            "step_id": "step-1",
                            "status": "WAITING",
                            "wait_kind": "PROVIDER",
                        }],
                        "pending_work": [{"child_id": "child-1"}],
                    },
                },
            },
        },
        goals=[{
            "directive_id": "directive-1",
            "exact_text": "Ship the owner proof",
            "active": 1,
            "last_event_id": "event-goal",
        }],
        missions=[{
            "mission_id": "mission-1",
            "state": "EXECUTING",
            "last_event_id": "event-mission",
        }],
        workers=[{
            "run_id": "worker-1",
            "mission_id": "mission-1",
            "step_id": "step-1",
        }],
        evidence=[{
            "bundle_id": "evidence-1",
            "mission_id": "mission-1",
            "step_id": "step-1",
        }],
        verdicts=[{
            "verdict_id": "verdict-1",
            "mission_id": "mission-1",
        }],
        mission_steps=[],
    )

    assert result["schema"] == "v13.command-center-work.v1"
    assert result["status"] == "PARTIAL"
    assert result["goals"][0]["objective_ids"] == ["objective-1"]
    assert result["projects"][0]["project_id"] == "project-1"
    assert result["objectives"][0]["campaign_ids"] == ["campaign-1"]
    assert result["campaigns"][0]["wait_kinds"] == ["PROVIDER"]
    assert result["missions"][0] == {
        "mission_id": "mission-1",
        "objective_id": "objective-1",
        "campaign_id": "campaign-1",
        "status": "EXECUTING",
        "action_ids": ["plan-1:step-1"],
        "worker_ids": ["worker-1"],
        "evidence_refs": ["evidence-1"],
        "verdict_ids": ["verdict-1"],
        "source_event_id": "event-mission",
    }
    assert result["actions"][0]["evidence_refs"] == [
        "evidence-plan",
        "evidence-1",
    ]


def test_work_hierarchy_does_not_infer_an_objective_from_mission_text() -> None:
    result = project_work_hierarchy(
        autonomy={"objectives": {}, "plans": {}, "progress": {}},
        goals=[],
        missions=[{
            "mission_id": "mission-unlinked",
            "objective": "Text matches nothing authoritative",
            "state": "PLANNED",
        }],
        workers=[],
        evidence=[],
        verdicts=[],
        mission_steps=[],
    )

    assert result["missions"][0]["objective_id"] is None
    assert result["missions"][0]["campaign_id"] is None
    assert result["levels"]["campaigns"] == "NOT_YET_INTEGRATED"
