from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.connectors.local_artifacts import LocalArtifactConnector
from jarvis_foundry.executive.autonomy import (
    build_plan,
    create_objective,
    rebuild_executive_state,
)
from jarvis_foundry.executive.campaigns import (
    campaign_dispatchable_mission_ids,
    create_campaign,
    rebuild_campaign_state,
    record_child_state,
    resume_campaign,
    startup_resume_pass,
)
from jarvis_foundry.fabric.adapter import FabricRoleAdapter
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.general_work import (
    ArtifactAssertion,
    DeliverableSpec,
    GeneralWorkMaterializationError,
    GeneralWorkPlan,
    GeneralWorkPlanner,
    GeneralWorkRequest,
    SourceIdentity,
    WorkStep,
    general_work_campaign_contract,
    materialize_general_work_plan,
    verify_recorded_package,
)
from jarvis_foundry.mission.contracts import latest_contract
from jarvis_foundry.mission.loop import BLOCKED_OUTCOME, MissionLoop, PASSED
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.router import RoleRouter


EXECUTIVE_PLAN_ID = "executive-general-work-plan"
EXECUTIVE_STEP_ID = "executive-general-work-step"
MATERIALIZATION_MODULE = importlib.import_module(
    "jarvis_foundry.general_work.materialization"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _database_and_root(
    tmp_path: Path,
    *,
    capabilities: dict[str, str] | None = None,
) -> tuple[FoundryDatabase, Path]:
    root = tmp_path / "owned"
    root.mkdir()
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    capabilities = capabilities or {
        "cap.file.copy": "READY",
        "cap.general_work.verify": "READY",
    }
    database.append(
        Event.create(
            "project.registered",
            {
                "resource_id": "resource-general-work",
                "kind": "directory",
                "root_path": str(root),
                "resolved_root": str(root.resolve()),
                "baseline": {},
                "capabilities": capabilities,
                "readiness_state": "READY",
            },
            source_type="test",
            source_id="v13-general-work-test",
            idempotency_key="project.registered:resource-general-work",
        )
    )
    create_objective(
        database,
        "Deliver a bounded General Work package",
        objective_id="objective-general-work",
        resource_id="resource-general-work",
        success_criteria=("the package verifier passes",),
    )
    return database, root


def _plan(root: Path, *, ambiguities: tuple[str, ...] = ()) -> GeneralWorkPlan:
    source = root / "inputs" / "owner.txt"
    source.parent.mkdir(exist_ok=True)
    if not source.exists():
        source.write_text("owner metric: 42\n", encoding="utf-8")
    assertions = (
        ArtifactAssertion(
            "copy-a-content",
            "copy-a",
            "text_contains",
            "owner metric: 42",
            "",
        ),
        ArtifactAssertion(
            "copy-b-content",
            "copy-b",
            "text_contains",
            "owner metric: 42",
            "",
        ),
    )
    return GeneralWorkPlan(
        plan_id="general-work-materialization",
        objective_id="objective-general-work",
        campaign_id="campaign-general-work-materialization",
        resource_id="resource-general-work",
        desired_outcome="Deliver two independently verified owner copies",
        delivery_root="delivery",
        sources=(
            SourceIdentity(
                "owner-source",
                "inputs/owner.txt",
                _sha256(source),
                "txt",
                source.stat().st_size,
                "2026-09-04T10:00:00Z",
            ),
        ),
        deliverables=(
            DeliverableSpec(
                "copy-a",
                "delivery/a.txt",
                "txt",
                ("owner-source",),
                "copy-a",
                ("copy-a-content",),
                quality_requirements=("owner metric is retained",),
            ),
            DeliverableSpec(
                "copy-b",
                "delivery/b.txt",
                "txt",
                ("owner-source",),
                "copy-b",
                ("copy-b-content",),
                quality_requirements=("owner metric is retained",),
            ),
        ),
        steps=(
            WorkStep(
                "copy-a",
                "Copy the owner source to delivery A",
                "file.copy",
                {
                    "source": "inputs/owner.txt",
                    "target": "delivery/a.txt",
                },
                (),
                ("owner-source",),
                ("copy-a",),
                ("copy-a-content",),
                max_retries=1,
            ),
            WorkStep(
                "copy-b",
                "Copy the owner source to delivery B",
                "file.copy",
                {
                    "source": "inputs/owner.txt",
                    "target": "delivery/b.txt",
                },
                (),
                ("owner-source",),
                ("copy-b",),
                ("copy-b-content",),
                max_retries=1,
            ),
        ),
        artifact_assertions=assertions,
        consistency_assertions=(),
        created_at="2026-09-04T10:05:00Z",
        quality_requirements=("both copies reopen",),
        ambiguities=ambiguities,
    )


def _authorize_executive_plan(
    database: FoundryDatabase,
    plan: GeneralWorkPlan,
) -> None:
    required_capabilities = tuple(dict.fromkeys((
        *(f"cap.{step.action_kind}" for step in plan.steps),
        "cap.general_work.verify",
    )))
    build_plan(
        database,
        plan.objective_id,
        (
            {
                "step_id": EXECUTIVE_STEP_ID,
                "description": plan.desired_outcome,
                "resource_id": plan.resource_id,
                "required_capabilities": required_capabilities,
                "output_contract": tuple(
                    deliverable.path for deliverable in plan.deliverables
                ),
                "verification_contract": (
                    "the complete General Work package passes verification",
                ),
            },
        ),
        plan_id=EXECUTIVE_PLAN_ID,
        source_id="v13-general-work-executive-test",
    )


def _authorize_general_work(
    database: FoundryDatabase,
    plan: GeneralWorkPlan,
) -> None:
    _authorize_executive_plan(database, plan)
    contract = general_work_campaign_contract(
        database,
        plan,
        executive_step_id=EXECUTIVE_STEP_ID,
    )
    create_campaign(
        database,
        plan.objective_id,
        **contract.create_kwargs(),
    )


def _materialize(
    database: FoundryDatabase,
    plan: GeneralWorkPlan,
    **kwargs: object,
):
    _authorize_general_work(database, plan)
    return materialize_general_work_plan(
        database,
        plan,
        executive_step_id=EXECUTIVE_STEP_ID,
        **kwargs,
    )


def test_materialization_builds_one_existing_spine_dag_and_replays(
    tmp_path: Path,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)

    first = _materialize(database, plan)
    replay = _materialize(database, plan)

    assert first.inserted is True
    assert replay.replayed is True
    assert database.event_count(event_type="general_work.plan_recorded") == 1
    assert database.event_count(event_type="general_work.target_claimed") == 2
    assert database.event_count(event_type="general_work.plan_materialized") == 1
    executive = rebuild_executive_state(database)
    assert first.executive_plan_id in executive.plans
    assert first.executive_plan_id == EXECUTIVE_PLAN_ID
    assert first.executive_step_id == EXECUTIVE_STEP_ID
    assert database.event_count(event_type="executive.plan_created") == 1
    assert {
        step.step_id for step in executive.plans[EXECUTIVE_PLAN_ID].steps
    } == {EXECUTIVE_STEP_ID}
    campaign = rebuild_campaign_state(database)
    children = {
        child.child_id: child
        for child in campaign.children.values()
        if child.campaign_id == plan.campaign_id
    }
    assert set(children) == {
        *(step.child_id for step in first.steps),
        first.verifier.child_id,
    }
    assert children[first.verifier.child_id].dependencies == tuple(
        step.child_id for step in first.steps
    )
    assert set(campaign_dispatchable_mission_ids(database)) == {
        step.mission_id for step in first.steps
    }
    assert latest_contract(
        database,
        first.verifier.mission_id,
        step_id=first.verifier.runtime_step_id,
    ).capability == "cap.general_work.verify"
    producer_contract = latest_contract(
        database,
        first.steps[0].mission_id,
        step_id=first.steps[0].runtime_step_id,
    )
    assert producer_contract is not None
    assert producer_contract.forbidden_paths == (".git",)


def test_restart_after_partial_materialization_releases_no_half_built_mission(
    tmp_path: Path,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)

    def interrupt(point: str) -> None:
        if point == "post general_work.step_materialized:copy-a":
            raise RuntimeError("deliberate materialization interruption")

    with pytest.raises(RuntimeError, match="deliberate"):
        _materialize(
            database,
            plan,
            fault_hook=interrupt,
        )

    assert campaign_dispatchable_mission_ids(database) == ()
    assert database.event_count(event_type="general_work.plan_recorded") == 1
    assert database.event_count(event_type="general_work.target_claimed") == 2

    resumed = _materialize(database, plan)

    assert resumed.inserted is True
    assert set(campaign_dispatchable_mission_ids(database)) == {
        step.mission_id for step in resumed.steps
    }
    assert database.event_count(event_type="mission.created") == 3


def test_stop_after_campaign_boundary_blocks_later_materialization_and_replays(
    tmp_path: Path,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)
    stop_path = tmp_path / "STOP"

    def stop_after_campaign(point: str) -> None:
        if point == "post general_work.campaign_authority":
            stop_path.write_text("owner stop at campaign boundary\n", encoding="utf-8")

    with pytest.raises(
        GeneralWorkMaterializationError,
        match="materialization is stopped",
    ):
        _materialize(
            database,
            plan,
            stop_path=stop_path,
            fault_hook=stop_after_campaign,
        )

    assert database.event_count(event_type="campaign.created") == 1
    assert database.event_count(event_type="mission.created") == 0
    assert database.event_count(event_type="mission.step_contract_recorded") == 0
    assert database.event_count(event_type="campaign.child_declared") == 0
    assert database.event_count(event_type="mission.state_changed") == 0
    assert database.event_count(event_type="general_work.plan_materialized") == 0
    assert rebuild_campaign_state(database).children == {}
    assert campaign_dispatchable_mission_ids(database) == ()

    stop_path.unlink()
    resumed = _materialize(
        database,
        plan,
        stop_path=stop_path,
    )

    assert resumed.inserted is True
    assert database.event_count(event_type="campaign.created") == 1
    assert database.event_count(event_type="mission.created") == 3
    assert database.event_count(event_type="mission.step_contract_recorded") == 3
    assert database.event_count(event_type="campaign.child_declared") == 3
    assert database.event_count(event_type="general_work.plan_materialized") == 1
    assert set(campaign_dispatchable_mission_ids(database)) == {
        step.mission_id for step in resumed.steps
    }


def test_stale_source_and_ambiguity_fail_before_output_claims(
    tmp_path: Path,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)
    (root / "inputs" / "owner.txt").write_text(
        "owner metric: 99\n",
        encoding="utf-8",
    )

    with pytest.raises(
        GeneralWorkMaterializationError,
        match="source identity is stale",
    ):
        _materialize(database, plan)

    assert database.event_count(event_type="general_work.plan_recorded") == 0
    assert database.event_count(event_type="general_work.target_claimed") == 0

    fresh = _plan(root, ambiguities=("which owner period applies",))
    with pytest.raises(
        GeneralWorkMaterializationError,
        match="unresolved ambiguities",
    ):
        materialize_general_work_plan(
            database,
            fresh,
            executive_step_id=EXECUTIVE_STEP_ID,
        )
    assert database.event_count(event_type="general_work.plan_recorded") == 0


@pytest.mark.parametrize("control_kind", ("marker", "veto"))
def test_stop_and_veto_fail_before_any_materialization_claim(
    tmp_path: Path,
    control_kind: str,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)
    stop_path = tmp_path / "STOP"
    _authorize_general_work(database, plan)
    if control_kind == "marker":
        stop_path.write_text("owner stop\n", encoding="utf-8")
    else:
        database.append(
            Event.create(
                "owner_directive.recorded",
                {
                    "directive_id": "veto-general-work",
                    "command_type": "VETO_ACTION",
                    "target": plan.campaign_id,
                    "exact_text": "do not materialize this package",
                    "reason": "owner veto",
                },
                source_type="test",
                source_id="v13-general-work-test",
                idempotency_key=(
                    "owner_directive.recorded:veto-general-work"
                ),
            )
        )

    with pytest.raises(
        GeneralWorkMaterializationError,
        match="materialization is stopped",
    ):
        materialize_general_work_plan(
            database,
            plan,
            executive_step_id=EXECUTIVE_STEP_ID,
            stop_path=stop_path,
        )

    assert database.event_count(event_type="general_work.plan_recorded") == 0
    assert database.event_count(event_type="general_work.target_claimed") == 0
    assert database.event_count(event_type="executive.plan_created") == 1
    assert database.event_count(event_type="campaign.created") == 1
    assert database.event_count(event_type="mission.created") == 0


def test_existing_campaign_contract_conflict_fails_before_output_claims(
    tmp_path: Path,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)
    _authorize_executive_plan(database, plan)
    create_campaign(
        database,
        plan.objective_id,
        campaign_id=plan.campaign_id,
        metadata={"different": "contract"},
    )

    with pytest.raises(
        GeneralWorkMaterializationError,
        match="different immutable contract",
    ):
        materialize_general_work_plan(
            database,
            plan,
            executive_step_id=EXECUTIVE_STEP_ID,
        )

    assert database.event_count(event_type="general_work.plan_recorded") == 0
    assert database.event_count(event_type="general_work.target_claimed") == 0
    assert database.event_count(event_type="executive.plan_created") == 1
    assert database.event_count(event_type="mission.created") == 0


def test_materialization_requires_executive_plan_and_campaign_before_claims(
    tmp_path: Path,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)

    with pytest.raises(
        GeneralWorkMaterializationError,
        match="existing Executive-authored active plan",
    ):
        materialize_general_work_plan(database, plan)

    assert database.event_count(event_type="general_work.plan_recorded") == 0
    assert database.event_count(event_type="executive.plan_created") == 0
    assert database.event_count(event_type="campaign.created") == 0

    _authorize_executive_plan(database, plan)
    with pytest.raises(
        GeneralWorkMaterializationError,
        match="existing Executive-authorized campaign",
    ):
        materialize_general_work_plan(
            database,
            plan,
            executive_step_id=EXECUTIVE_STEP_ID,
        )

    assert database.event_count(event_type="general_work.plan_recorded") == 0
    assert database.event_count(event_type="campaign.created") == 0


def test_materialization_has_no_executive_planner_authority() -> None:
    source = Path(MATERIALIZATION_MODULE.__file__).read_text(encoding="utf-8")

    assert "PlanStep" not in source
    assert "build_plan(" not in source
    assert "create_campaign(" not in source


def test_payload_lineage_and_unknown_executive_step_fail_closed(
    tmp_path: Path,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)
    bad_step = replace(
        plan.steps[0],
        payload={
            "source": "inputs/not-declared.txt",
            "target": "delivery/a.txt",
        },
    )
    bad_plan = replace(
        plan,
        plan_id="general-work-bad-lineage",
        campaign_id="campaign-general-work-bad-lineage",
        steps=(bad_step, plan.steps[1]),
    )

    _authorize_general_work(database, bad_plan)
    with pytest.raises(
        GeneralWorkMaterializationError,
        match="outside declared lineage",
    ):
        materialize_general_work_plan(
            database,
            bad_plan,
            executive_step_id=EXECUTIVE_STEP_ID,
        )
    assert database.event_count(event_type="general_work.plan_recorded") == 0

    with pytest.raises(
        GeneralWorkMaterializationError,
        match="does not contain step",
    ):
        materialize_general_work_plan(
            database,
            plan,
            executive_step_id="not-executive-authored",
        )
    assert database.event_count(event_type="general_work.plan_recorded") == 0


def test_materialized_plan_runs_through_mission_loop_and_fresh_verifier(
    tmp_path: Path,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)
    materialized = _materialize(database, plan)
    inner = FakeHermesAdapter(working_root=root)
    adapter = FabricRoleAdapter(
        database,
        owned_root=root,
        inner=inner,
        structured_only=True,
    )
    loop = MissionLoop(
        database,
        RoleRouter(),
        adapter,
        max_repairs=0,
        stop_path=tmp_path / "STOP",
    )

    for step in materialized.steps:
        contract = latest_contract(
            database,
            step.mission_id,
            step_id=step.runtime_step_id,
            allow_invalid=False,
        )
        assert contract is not None
        outcome = loop.run_until_settled(**contract.runtime_kwargs(database))
        assert outcome.outcome == PASSED, outcome
        startup_resume_pass(database, stop_path=tmp_path / "STOP")

    assert campaign_dispatchable_mission_ids(database) == (
        materialized.verifier.mission_id,
    )
    verifier_contract = latest_contract(
        database,
        materialized.verifier.mission_id,
        step_id=materialized.verifier.runtime_step_id,
        allow_invalid=False,
    )
    assert verifier_contract is not None
    verifier_outcome = loop.run_until_settled(
        **verifier_contract.runtime_kwargs(database)
    )
    assert verifier_outcome.outcome == PASSED, verifier_outcome.reason
    startup_resume_pass(database, stop_path=tmp_path / "STOP")

    campaign = rebuild_campaign_state(database).campaigns[plan.campaign_id]
    assert campaign.status == "COMPLETED"
    assert rebuild_executive_state(database).objectives[
        plan.objective_id
    ].status == "COMPLETED"
    assert verify_recorded_package(
        database,
        plan.plan_id,
        resource_root=root,
    ).status == "PASS"
    assert inner.received == []


def test_local_general_work_fabric_respects_denied_provider(
    tmp_path: Path,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)
    materialized = _materialize(database, plan)
    first = materialized.steps[0]
    contract = latest_contract(
        database,
        first.mission_id,
        step_id=first.runtime_step_id,
        allow_invalid=False,
    )
    assert contract is not None
    inner = FakeHermesAdapter(working_root=root)
    loop = MissionLoop(
        database,
        RoleRouter(),
        FabricRoleAdapter(
            database,
            owned_root=root,
            inner=inner,
            structured_only=True,
        ),
        max_repairs=0,
        stop_path=tmp_path / "STOP",
        denied_providers=("fabric",),
    )

    outcome = loop.run_until_settled(**contract.runtime_kwargs(database))

    assert outcome.outcome == BLOCKED_OUTCOME
    assert not (root / first.target_relative_path).exists()
    assert database.event_count(event_type="system.action_started") == 0
    assert inner.received == []
    decisions = [
        event.payload
        for event in database.events(event_types=("provider.route_decided",))
    ]
    assert decisions[-1]["status"] == "BLOCKED"
    assert decisions[-1]["failure_class"] == "OWNER_RESTRICTED"
    assert decisions[-1]["selected"] is None
    assert decisions[-1]["rejected"][0]["provider"] == "fabric"


def test_materialized_campaign_stops_before_effect_and_resumes_exact_step(
    tmp_path: Path,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)
    stop_path = tmp_path / "STOP"
    materialized = _materialize(
        database,
        plan,
        stop_path=stop_path,
    )
    first = materialized.steps[0]
    contract = latest_contract(
        database,
        first.mission_id,
        step_id=first.runtime_step_id,
        allow_invalid=False,
    )
    assert contract is not None
    loop = MissionLoop(
        database,
        RoleRouter(),
        FabricRoleAdapter(
            database,
            owned_root=root,
            inner=FakeHermesAdapter(working_root=root),
            structured_only=True,
        ),
        max_repairs=0,
        stop_path=stop_path,
    )

    stop_path.write_text("owner stop\n", encoding="utf-8")
    stopped = startup_resume_pass(
        database,
        stop_path=stop_path,
        resume_id="general-work-stop",
    )
    blocked = loop.run_until_settled(**contract.runtime_kwargs(database))

    assert stopped.status == "STOPPED"
    assert blocked.outcome == BLOCKED_OUTCOME
    assert database.event_count(event_type="system.action_started") == 0
    assert not (root / first.target_relative_path).exists()

    stop_path.unlink()
    resumed = resume_campaign(
        database,
        plan.campaign_id,
        stop_path=stop_path,
    )
    startup_resume_pass(
        database,
        stop_path=stop_path,
        resume_id="general-work-resume",
    )
    passed = loop.run_until_settled(**contract.runtime_kwargs(database))

    assert resumed.status == "EXECUTING"
    assert passed.outcome == PASSED
    assert (root / first.target_relative_path).is_file()
    assert database.event_count(event_type="system.action_started") == 1


def test_materialized_campaign_preserves_time_wait_until_due(
    tmp_path: Path,
) -> None:
    database, root = _database_and_root(tmp_path)
    plan = _plan(root)
    stop_path = tmp_path / "STOP"
    materialized = _materialize(
        database,
        plan,
        stop_path=stop_path,
    )
    first = materialized.steps[0]
    record_child_state(
        database,
        first.child_id,
        "WAITING",
        wait_kind="TIME",
        wait_until="2026-09-04T12:05:00Z",
        reason="wait for the declared processing window",
    )

    before_due = startup_resume_pass(
        database,
        stop_path=stop_path,
        now="2026-09-04T12:00:00Z",
        resume_id="general-work-wait-before-due",
    )
    waiting = rebuild_campaign_state(database).children[first.child_id]

    assert before_due.status == "READY"
    assert waiting.status == "WAITING"
    assert waiting.wait_kind == "TIME"
    assert first.mission_id not in campaign_dispatchable_mission_ids(database)
    assert database.event_count(event_type="system.action_started") == 0

    after_due = startup_resume_pass(
        database,
        stop_path=stop_path,
        now="2026-09-04T12:06:00Z",
        resume_id="general-work-wait-after-due",
    )
    released = rebuild_campaign_state(database).children[first.child_id]

    assert after_due.status == "READY"
    assert released.status == "READY"
    assert first.mission_id in campaign_dispatchable_mission_ids(database)
    assert database.event_count(event_type="system.action_started") == 0


def test_generated_intermediate_uses_producer_lineage_not_original_hash(
    tmp_path: Path,
) -> None:
    capabilities = {
        "cap.file.inspect": "READY",
        "cap.artifact.table.merge": "READY",
        "cap.artifact.xlsx.create": "READY",
        "cap.general_work.verify": "READY",
    }
    database, root = _database_and_root(
        tmp_path,
        capabilities=capabilities,
    )
    source = root / "inputs" / "pipeline.csv"
    source.parent.mkdir()
    source.write_text(
        "Account,Amount\nB,20\nA,10\n",
        encoding="utf-8",
    )
    plan = GeneralWorkPlanner(root).plan(
        GeneralWorkRequest(
            objective_id="objective-general-work",
            resource_id="resource-general-work",
            desired_outcome="Prepare an editable sorted workbook",
            source_paths=("inputs/pipeline.csv",),
            requested_formats=("xlsx",),
            sort_by=("Account",),
        )
    ).plan
    materialized = _materialize(database, plan)
    workbook_step = next(
        step
        for step in materialized.steps
        if step.source_step_id == "build-workbook"
    )
    contract = latest_contract(
        database,
        workbook_step.mission_id,
        step_id=workbook_step.runtime_step_id,
        allow_invalid=False,
    )
    assert contract is not None
    payload = json.loads(contract.instructions)

    assert payload["source"].endswith("-normalized.json")
    assert "expected_source_sha256" not in payload
    assert payload["expected_source_sha256s"] == {
        "inputs/pipeline.csv": _sha256(source)
    }


def test_planner_package_runs_through_existing_spine_and_repairs_stale_output(
    tmp_path: Path,
) -> None:
    capabilities = {
        "cap.file.inspect": "READY",
        "cap.artifact.table.merge": "READY",
        "cap.artifact.xlsx.create": "READY",
        "cap.artifact.docx.compose": "READY",
        "cap.artifact.pdf.create": "READY",
        "cap.artifact.pptx.create": "READY",
        "cap.general_work.verify": "READY",
    }
    database, root = _database_and_root(
        tmp_path,
        capabilities=capabilities,
    )
    inputs = root / "inputs"
    inputs.mkdir()
    csv_source = inputs / "accounts.csv"
    csv_source.write_text(
        "Account,Status,Amount\n"
        " A ,open,10\n"
        " B ,closed,20\n",
        encoding="utf-8",
    )
    json_source = inputs / "accounts.json"
    json_source.write_text(
        '{"records": ['
        '{"account": "A", "status": "open", "amount": 10},'
        '{"account": "C", "status": "open", "amount": 30}'
        "]}\n",
        encoding="utf-8",
    )
    source_hashes = {
        path.name: _sha256(path)
        for path in (csv_source, json_source)
    }
    planning = GeneralWorkPlanner(root).plan(
        GeneralWorkRequest(
            objective_id="objective-general-work",
            resource_id="resource-general-work",
            desired_outcome=(
                "Prepare a verified account package with an editable "
                "workbook, written report, printable copy, and presentation"
            ),
            source_paths=(
                "inputs/accounts.csv",
                "inputs/accounts.json",
            ),
            requested_formats=("xlsx", "docx", "pdf", "pptx"),
            column_aliases={
                "account": "Account",
                "status": "Status",
                "amount": "Amount",
            },
            select_columns=("Account", "Status", "Amount"),
            deduplicate_by=("Account",),
            sort_by=("Account",),
            source_column="Source",
        )
    )
    plan = planning.plan
    pdf = next(
        deliverable
        for deliverable in plan.deliverables
        if deliverable.format == "pdf"
    )
    stale_output = root / Path(*PurePosixPath(pdf.path).parts)

    materialized = _materialize(
        database,
        plan,
        stop_path=tmp_path / "STOP",
    )
    stale_output.parent.mkdir(parents=True)
    stale_output.write_bytes(b"stale owner draft")
    stale_hash = _sha256(stale_output)
    inner = FakeHermesAdapter(working_root=root)
    loop = MissionLoop(
        database,
        RoleRouter(),
        FabricRoleAdapter(
            database,
            owned_root=root,
            inner=inner,
            structured_only=True,
        ),
        max_repairs=1,
        stop_path=tmp_path / "STOP",
    )
    mission_by_id = {
        step.mission_id: step
        for step in (*materialized.steps, materialized.verifier)
    }
    completed: set[str] = set()
    for _round in range(len(mission_by_id) + 2):
        dispatchable = campaign_dispatchable_mission_ids(database)
        if not dispatchable:
            break
        for mission_id in dispatchable:
            if mission_id in completed:
                continue
            step = mission_by_id[mission_id]
            contract = latest_contract(
                database,
                mission_id,
                step_id=step.runtime_step_id,
                allow_invalid=False,
            )
            assert contract is not None
            outcome = loop.run_until_settled(
                **contract.runtime_kwargs(database)
            )
            assert outcome.outcome == PASSED, (
                f"{mission_id} ({step.source_step_id}): {outcome.reason}"
            )
            completed.add(mission_id)
            startup_resume_pass(
                database,
                stop_path=tmp_path / "STOP",
            )

    verification = verify_recorded_package(
        database,
        plan.plan_id,
        resource_root=root,
    )
    assert verification.status == "PASS", verification.errors
    assert rebuild_campaign_state(database).campaigns[
        plan.campaign_id
    ].status == "COMPLETED"
    normalized = next(
        deliverable
        for deliverable in plan.deliverables
        if deliverable.deliverable_id == "normalized-data"
    )
    parsed = LocalArtifactConnector(root).parse(normalized.path)
    assert parsed.table is not None
    assert parsed.table.row_count == 3
    assert _sha256(stale_output) != stale_hash
    backups = list(
        (root / ".jarvis-general-work" / "repairs").rglob("*.bak")
    )
    assert len(backups) == 1
    assert _sha256(backups[0]) == stale_hash
    assert {
        path.name: _sha256(path)
        for path in (csv_source, json_source)
    } == source_hashes
    assert inner.received == []
