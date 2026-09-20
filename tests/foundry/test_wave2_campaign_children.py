"""Wave 2 P0-1 pins — a warranted campaign's ACTIVE plan becomes child work.

Discriminating by construction.  On the pre-fix tree:

* ``materialize_campaign_children`` does not exist, so the direct pins fail with
  AttributeError rather than passing vacuously;
* no worker-loop cycle turns an eligible 2-step campaign plan into children, so
  the production-seam pin observes ZERO ``campaign.child_declared`` events.

Every pin below therefore fails on the pre-fix tree and passes at the fixed head.
"""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.executive.autonomy import PlanStep, build_plan, create_objective
from jarvis_foundry.executive.campaigns import (
    CAMPAIGN_CHILD_MATERIALIZATION_VERSION,
    create_campaign,
    materialize_campaign_children,
    rebuild_campaign_state,
)
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.provider_config import default_provider_settings
from jarvis_foundry.runtime.worker_loop import run_cycle

OBJECTIVE = "objective-wave2-campaign-children"
PLAN_ID = "plan-wave2-campaign-children"
CHILD_DECLARED = "campaign.child_declared"


def _objective_with_two_step_plan(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    create_objective(
        database,
        "Bring the registered campaign project to a verified releasable state",
        objective_id=OBJECTIVE,
        success_criteria=("each child mission is independently verified",),
    )
    build_plan(
        database,
        OBJECTIVE,
        [
            PlanStep(step_id="01-plan", objective_id=OBJECTIVE,
                     description="plan the decomposition",
                     required_capabilities=("cap.file.write",)),
            PlanStep(step_id="02-execute", objective_id=OBJECTIVE,
                     description="execute the decomposition",
                     dependencies=("01-plan",),
                     required_capabilities=("cap.file.write",)),
        ],
        plan_id=PLAN_ID,
    )
    return database


def _child_id(campaign_id: str, step_id: str) -> str:
    return f"child:{campaign_id}:{step_id}"


def test_an_eligible_two_step_plan_materializes_deterministic_children(tmp_path: Path) -> None:
    database = _objective_with_two_step_plan(tmp_path)
    campaign = create_campaign(database, OBJECTIVE)

    summaries = materialize_campaign_children(database)

    assert len(summaries) == 1, summaries
    summary = summaries[0]
    assert summary["campaign_id"] == campaign.campaign_id
    assert summary["plan_id"] == PLAN_ID
    assert summary["materialization_version"] == CAMPAIGN_CHILD_MATERIALIZATION_VERSION
    assert summary["declared"] == [
        _child_id(campaign.campaign_id, "01-plan"),
        _child_id(campaign.campaign_id, "02-execute"),
    ], summary
    assert summary["refused"] == []
    assert database.event_count(event_type=CHILD_DECLARED) == 2

    state = rebuild_campaign_state(database)
    assert set(state.children) == {
        _child_id(campaign.campaign_id, "01-plan"),
        _child_id(campaign.campaign_id, "02-execute"),
    }
    # the plan's dependency edge survives as the campaign's own child dependency
    assert state.children[_child_id(campaign.campaign_id, "02-execute")].dependencies == (
        _child_id(campaign.campaign_id, "01-plan"),
    )
    assert state.children[_child_id(campaign.campaign_id, "01-plan")].dependencies == ()


def test_a_repeat_pass_never_duplicates_a_child(tmp_path: Path) -> None:
    database = _objective_with_two_step_plan(tmp_path)
    campaign = create_campaign(database, OBJECTIVE)
    materialize_campaign_children(database)

    again = materialize_campaign_children(database)

    assert again[0]["declared"] == []
    assert sorted(again[0]["already_declared"]) == [
        _child_id(campaign.campaign_id, "01-plan"),
        _child_id(campaign.campaign_id, "02-execute"),
    ]
    assert database.event_count(event_type=CHILD_DECLARED) == 2
    assert len(rebuild_campaign_state(database).children) == 2


def test_child_identity_does_not_depend_on_the_loop_or_a_restart(tmp_path: Path) -> None:
    """A rebuilt state must derive the SAME children, so a restart cannot fork them."""

    database = _objective_with_two_step_plan(tmp_path)
    campaign = create_campaign(database, OBJECTIVE)
    materialize_campaign_children(database)
    before = sorted(rebuild_campaign_state(database).children)

    # a fresh handle over the same durable ledger, exactly as a restarted
    # Supervisor sees it, must resolve the same identities
    restarted = FoundryDatabase(database.path)
    restarted_state = rebuild_campaign_state(restarted)
    assert sorted(restarted_state.children) == before
    for child_id, record in restarted_state.children.items():
        assert record.child_id == child_id
        assert record.campaign_id == campaign.campaign_id
        assert child_id == _child_id(campaign.campaign_id, record.step_id or "")

    replay = materialize_campaign_children(restarted)
    assert replay[0]["declared"] == []
    assert restarted.event_count(event_type=CHILD_DECLARED) == 2


def test_a_worker_loop_cycle_materializes_children_without_an_outer_caller(tmp_path: Path) -> None:
    """The production seam: no driver, no test-only materializer call."""

    database = _objective_with_two_step_plan(tmp_path)
    settings = default_provider_settings()
    config = WorkerRuntimeConfig(
        enabled=True,
        campaign_runtime_enabled=True,
        provider_settings=settings,
        provider_settings_pool=(settings,),
    )

    run_cycle(database, FoundryPaths(tmp_path), config,
              loop_id="loop-wave2-campaign", instance_id="instance-wave2-campaign")

    assert database.event_count(event_type=CHILD_DECLARED) == 2, (
        "a warranted campaign with an eligible 2-step plan produced no child work")
    campaign = next(iter(rebuild_campaign_state(database).campaigns.values()))
    assert sorted(rebuild_campaign_state(database).children) == [
        _child_id(campaign.campaign_id, "01-plan"),
        _child_id(campaign.campaign_id, "02-execute"),
    ]

    # a second cycle with a DIFFERENT loop id must not duplicate anything
    run_cycle(database, FoundryPaths(tmp_path), config,
              loop_id="loop-wave2-campaign-2", instance_id="instance-wave2-campaign-2")
    assert database.event_count(event_type=CHILD_DECLARED) == 2
    assert len(rebuild_campaign_state(database).children) == 2


def test_a_terminal_campaign_is_never_given_new_children(tmp_path: Path) -> None:
    from jarvis_foundry.executive.campaigns import cancel_campaign

    database = _objective_with_two_step_plan(tmp_path)
    campaign = create_campaign(database, OBJECTIVE)
    cancel_campaign(database, campaign.campaign_id)

    summaries = materialize_campaign_children(database)

    assert summaries == []
    assert database.event_count(event_type=CHILD_DECLARED) == 0
