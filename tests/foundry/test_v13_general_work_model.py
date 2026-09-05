from __future__ import annotations

import copy

import pytest

from jarvis_foundry.general_work import (
    ArtifactAssertion,
    ConsistencyAssertion,
    DeliverableSpec,
    GeneralWorkModelError,
    GeneralWorkPlan,
    SourceIdentity,
    WorkStep,
)

CAPTURED_AT = "2026-09-03T08:00:00Z"
CREATED_AT = "2026-09-03T08:05:00Z"
SOURCE_SHA = "a" * 64


def _source(
    source_id: str = "source-1",
    *,
    path: str = "inputs/source.csv",
    sha256: str = SOURCE_SHA,
    captured_at: str = CAPTURED_AT,
) -> SourceIdentity:
    return SourceIdentity(
        source_id=source_id,
        path=path,
        sha256=sha256,
        format="csv",
        size_bytes=24,
        captured_at=captured_at,
    )


def _simple_plan(
    *,
    action_kind: str = "artifact.table.export",
    captured_at: str = CAPTURED_AT,
    created_at: str = CREATED_AT,
) -> GeneralWorkPlan:
    assertion = ArtifactAssertion(
        assertion_id="assert-report-exists",
        deliverable_id="report",
        kind="exists",
        expected=True,
        selector="",
        required=True,
    )
    deliverable = DeliverableSpec(
        deliverable_id="report",
        path="outputs/report.xlsx",
        format="xlsx",
        source_ids=("source-1",),
        producer_step_id="build-report",
        verification_assertion_ids=(assertion.assertion_id,),
        metadata={"audience": "owner"},
    )
    step = WorkStep(
        step_id="build-report",
        description="Build the normalized workbook",
        action_kind=action_kind,
        payload={
            "columns": ["name", "amount"],
            "options": {"sheet": "Summary"},
        },
        dependencies=(),
        source_ids=("source-1",),
        output_ids=(deliverable.deliverable_id,),
        verification_assertion_ids=(assertion.assertion_id,),
        max_retries=1,
    )
    return GeneralWorkPlan(
        plan_id="general-work-1",
        objective_id="objective-1",
        campaign_id="campaign-1",
        resource_id="resource-1",
        desired_outcome="Prepare an owner-ready workbook",
        delivery_root="outputs",
        sources=(_source(captured_at=captured_at),),
        deliverables=(deliverable,),
        steps=(step,),
        artifact_assertions=(assertion,),
        consistency_assertions=(),
        created_at=created_at,
        metadata={"priority": 3},
        quality_requirements=("reopen every deliverable",),
    )


def test_valid_round_trip_and_fingerprint_excludes_volatile_timestamps() -> None:
    plan = _simple_plan()

    rebuilt = GeneralWorkPlan.from_payload(plan.to_payload())
    later = _simple_plan(
        captured_at="2026-09-03T09:00:00Z",
        created_at="2026-09-03T09:05:00Z",
    )

    assert rebuilt.to_payload() == plan.to_payload()
    assert rebuilt.fingerprint() == plan.fingerprint()
    assert later.fingerprint() == plan.fingerprint()
    assert len(plan.fingerprint()) == 64


def test_each_record_has_a_deterministic_payload_round_trip() -> None:
    source = _source()
    deliverable = DeliverableSpec(
        "report", "outputs/report.pdf", "pdf", ("source-1",), "render", ("a1",)
    )
    step = WorkStep(
        "render", "Render report", "artifact.pdf.create",
        {"sections": [{"title": "Summary"}]}, (), ("source-1",), ("report",), ("a1",),
    )
    artifact_assertion = ArtifactAssertion(
        "a1", "report", "page_count", 2, "pages", True
    )
    consistency_assertion = ConsistencyAssertion(
        "c1", "report", "title", "equals", "report", "title", True
    )

    records = (
        source,
        deliverable,
        step,
        artifact_assertion,
        consistency_assertion,
    )
    for record in records:
        rebuilt = type(record).from_payload(record.to_payload())
        assert rebuilt.to_payload() == record.to_payload()


def test_topological_layers_group_independent_steps() -> None:
    sources = (
        _source("source-a", path="inputs/a.csv"),
        _source("source-b", path="inputs/b.csv", sha256="b" * 64),
    )
    deliverables = (
        DeliverableSpec(
            "output-a", "outputs/a.json", "json", ("source-a",), "step-a", ()
        ),
        DeliverableSpec(
            "output-b", "outputs/b.json", "json", ("source-b",), "step-b", ()
        ),
        DeliverableSpec(
            "combined",
            "outputs/combined.xlsx",
            "xlsx",
            ("source-a", "source-b"),
            "step-c",
            (),
        ),
    )
    steps = (
        WorkStep(
            "step-c",
            "Combine normalized data",
            "artifact.xlsx.create",
            {},
            ("step-a", "step-b"),
            (),
            ("combined",),
            (),
        ),
        WorkStep(
            "step-b",
            "Normalize source B",
            "artifact.table.transform",
            {},
            (),
            ("source-b",),
            ("output-b",),
            (),
        ),
        WorkStep(
            "step-a",
            "Normalize source A",
            "artifact.table.transform",
            {},
            (),
            ("source-a",),
            ("output-a",),
            (),
        ),
    )

    plan = GeneralWorkPlan(
        plan_id="parallel-plan",
        objective_id="objective-1",
        campaign_id="campaign-1",
        resource_id="resource-1",
        desired_outcome="Prepare three linked outputs",
        delivery_root="outputs",
        sources=sources,
        deliverables=deliverables,
        steps=steps,
        artifact_assertions=(),
        consistency_assertions=(),
        created_at=CREATED_AT,
    )

    assert plan.topological_layers() == (("step-a", "step-b"), ("step-c",))


def test_path_traversal_is_rejected() -> None:
    with pytest.raises(GeneralWorkModelError, match="escape"):
        _source(path="../outside.csv")


def test_dependency_cycle_is_rejected() -> None:
    deliverables = (
        DeliverableSpec("out-a", "outputs/a.csv", "csv", ("source-1",), "a", ()),
        DeliverableSpec("out-b", "outputs/b.csv", "csv", ("source-1",), "b", ()),
    )
    steps = (
        WorkStep(
            "a", "Step A", "artifact.table.export", {}, ("b",),
            ("source-1",), ("out-a",), (),
        ),
        WorkStep(
            "b", "Step B", "artifact.table.export", {}, ("a",),
            ("source-1",), ("out-b",), (),
        ),
    )

    with pytest.raises(GeneralWorkModelError, match="cycle"):
        GeneralWorkPlan(
            plan_id="cyclic",
            objective_id="objective-1",
            campaign_id="campaign-1",
            resource_id="resource-1",
            desired_outcome="Reject the cyclic package",
            delivery_root="outputs",
            sources=(_source(),),
            deliverables=deliverables,
            steps=steps,
            artifact_assertions=(),
            consistency_assertions=(),
            created_at=CREATED_AT,
        )


def test_unknown_reference_is_rejected() -> None:
    plan = _simple_plan().to_payload()
    plan["steps"][0]["source_ids"] = ["missing-source"]

    with pytest.raises(GeneralWorkModelError, match="unknown sources"):
        GeneralWorkPlan.from_payload(plan)


def test_duplicate_output_producer_is_rejected() -> None:
    plan = _simple_plan().to_payload()
    duplicate = copy.deepcopy(plan["steps"][0])
    duplicate["step_id"] = "duplicate-producer"
    duplicate["verification_assertion_ids"] = []
    plan["steps"].append(duplicate)

    with pytest.raises(GeneralWorkModelError, match="exactly one output producer"):
        GeneralWorkPlan.from_payload(plan)


def test_mutable_payload_inputs_and_outputs_do_not_alias_model_state() -> None:
    payload = {"nested": {"values": [1, 2]}}
    step = WorkStep(
        "immutable-step",
        "Keep a private payload snapshot",
        "file.write",
        payload,
        (),
        (),
        (),
        (),
    )

    payload["nested"]["values"].append(3)
    exported = step.to_payload()
    exported["payload"]["nested"]["values"].append(4)

    assert step.to_payload()["payload"] == {"nested": {"values": [1, 2]}}
    with pytest.raises(TypeError):
        step.payload["new"] = "value"


def test_unsupported_action_is_rejected() -> None:
    with pytest.raises(GeneralWorkModelError, match="unsupported General Work action"):
        _simple_plan(action_kind="local.command.run")


def test_malformed_sha_is_rejected() -> None:
    with pytest.raises(GeneralWorkModelError, match="lowercase 64-hex"):
        _source(sha256="A" * 64)


def test_duplicate_case_variant_paths_are_rejected() -> None:
    plan = _simple_plan().to_payload()
    duplicate = copy.deepcopy(plan["deliverables"][0])
    duplicate["deliverable_id"] = "report-copy"
    duplicate["path"] = "OUTPUTS/REPORT.XLSX"
    duplicate["producer_step_id"] = "build-copy"
    plan["deliverables"].append(duplicate)
    step = copy.deepcopy(plan["steps"][0])
    step["step_id"] = "build-copy"
    step["output_ids"] = ["report-copy"]
    plan["steps"].append(step)

    with pytest.raises(GeneralWorkModelError, match="case-insensitive"):
        GeneralWorkPlan.from_payload(plan)


def test_deliverables_must_match_delivery_root_and_format() -> None:
    outside = _simple_plan().to_payload()
    outside["deliverables"][0]["path"] = "elsewhere/report.xlsx"
    with pytest.raises(GeneralWorkModelError, match="outside delivery_root"):
        GeneralWorkPlan.from_payload(outside)

    wrong_format = _simple_plan().to_payload()
    wrong_format["deliverables"][0]["format"] = "pdf"
    with pytest.raises(GeneralWorkModelError, match="format does not match"):
        GeneralWorkPlan.from_payload(wrong_format)
