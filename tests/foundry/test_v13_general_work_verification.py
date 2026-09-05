from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jarvis_foundry.connectors.local_artifacts import LocalArtifactConnector
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.general_work import (
    ArtifactAssertion,
    ConsistencyAssertion,
    DeliverableSpec,
    GeneralWorkPlan,
    SourceIdentity,
    WorkStep,
    record_plan_and_claim_targets,
    verify_recorded_package,
)


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _plan(root: Path, *, visual_required: bool = False) -> GeneralWorkPlan:
    source = root / "inputs" / "metric.csv"
    source.parent.mkdir()
    source.write_text("Metric,Value\nTotal,40\n", encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    assertions = (
        ArtifactAssertion(
            "xlsx-value",
            "workbook",
            "cell_equals",
            "40",
            "Data!B2",
        ),
        ArtifactAssertion(
            "docx-value",
            "document",
            "text_contains",
            "40",
            "",
        ),
        ArtifactAssertion(
            "pdf-pages",
            "pdf",
            "page_count",
            1,
            "",
        ),
        ArtifactAssertion(
            "pptx-slides",
            "slides",
            "slide_count",
            1,
            "",
        ),
    )
    deliverables = (
        DeliverableSpec(
            "workbook",
            "delivery/metric.xlsx",
            "xlsx",
            ("metric",),
            "xlsx",
            ("xlsx-value",),
            quality_requirements=("metric cell matches source",),
        ),
        DeliverableSpec(
            "document",
            "delivery/metric.docx",
            "docx",
            ("metric",),
            "docx",
            ("docx-value",),
            quality_requirements=("metric appears in the report",),
        ),
        DeliverableSpec(
            "pdf",
            "delivery/metric.pdf",
            "pdf",
            ("metric",),
            "pdf",
            ("pdf-pages",),
            quality_requirements=("single-page owner brief",),
        ),
        DeliverableSpec(
            "slides",
            "delivery/metric.pptx",
            "pptx",
            ("metric",),
            "pptx",
            ("pptx-slides",),
            metadata=(
                {
                    "visual_verification": {
                        "required": True,
                        "evidence_path": "delivery/evidence/slides.json",
                    }
                }
                if visual_required
                else {}
            ),
            quality_requirements=("single-slide owner summary",),
        ),
    )
    steps = tuple(
        WorkStep(
            deliverable.producer_step_id,
            f"Produce {deliverable.format}",
            {
                "xlsx": "artifact.xlsx.create",
                "docx": "artifact.docx.create",
                "pdf": "artifact.pdf.create",
                "pptx": "artifact.pptx.create",
            }[deliverable.format],
            {
                "source": "inputs/metric.csv",
                "target": deliverable.path,
            },
            (),
            ("metric",),
            (deliverable.deliverable_id,),
            deliverable.verification_assertion_ids,
        )
        for deliverable in deliverables
    )
    return GeneralWorkPlan(
        plan_id=(
            "general-work-verification-visual"
            if visual_required
            else "general-work-verification"
        ),
        objective_id="objective-verification",
        campaign_id=(
            "campaign-verification-visual"
            if visual_required
            else "campaign-verification"
        ),
        resource_id="resource-verification",
        desired_outcome="Deliver the same owner metric in four usable formats",
        delivery_root="delivery",
        sources=(
            SourceIdentity(
                "metric",
                "inputs/metric.csv",
                source_hash,
                "csv",
                source.stat().st_size,
                "2026-09-04T09:00:00Z",
            ),
        ),
        deliverables=deliverables,
        steps=steps,
        artifact_assertions=assertions,
        consistency_assertions=(
            ConsistencyAssertion(
                "xlsx-docx",
                "workbook",
                "cell:Data!B2",
                "equals",
                "document",
                r"regex:Total \| ([0-9]+)",
            ),
            ConsistencyAssertion(
                "docx-pdf",
                "document",
                r"regex:Total \| ([0-9]+)",
                "equals",
                "pdf",
                r"regex:Total value: ([0-9]+)",
            ),
            ConsistencyAssertion(
                "pdf-pptx",
                "pdf",
                r"regex:Total value: ([0-9]+)",
                "equals",
                "slides",
                r"regex:Total value: ([0-9]+)",
            ),
        ),
        created_at="2026-09-04T09:05:00Z",
        quality_requirements=(
            "all deliverables reopen",
            "the owner metric agrees across formats",
        ),
    )


def _materialize_outputs(root: Path) -> None:
    connector = LocalArtifactConnector(root)
    parsed = connector.parse(
        "inputs/metric.csv",
        expected_columns=("Metric", "Value"),
    )
    summary = connector.summarize_table(parsed)
    connector.create_xlsx(
        parsed,
        target_ref="delivery/metric.xlsx",
        idempotency_key="verification:xlsx",
    )
    connector.create_docx_report(
        parsed,
        summary,
        target_ref="delivery/metric.docx",
        idempotency_key="verification:docx",
        title="Metric report",
    )
    connector.create_pdf(
        pages=("Total value: 40",),
        target_ref="delivery/metric.pdf",
        idempotency_key="verification:pdf",
        title="Metric brief",
        source=parsed,
    )
    connector.create_pptx(
        slides=(
            {
                "layout": "title_and_content",
                "title": "Metric summary",
                "body": "Total value: 40",
            },
        ),
        target_ref="delivery/metric.pptx",
        idempotency_key="verification:pptx",
        source=parsed,
    )


def test_four_format_package_passes_readback_and_consistency(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    plan = _plan(root)
    record_plan_and_claim_targets(database, plan, resource_root=root)
    _materialize_outputs(root)

    verification = verify_recorded_package(
        database,
        plan.plan_id,
        resource_root=root,
    )

    assert verification.status == "PASS"
    assert all(
        finding["status"] == "PASS"
        for finding in verification.source_findings
    )
    assert all(
        finding["status"] == "PASS"
        for finding in verification.artifact_findings
    )
    assert all(
        finding["status"] == "PASS"
        for finding in verification.consistency_findings
    )
    assert len(verification.verification_hash) == 64
    assert {
        item["status"] for item in verification.visual_status
    } == {"NOT_TESTED"}


def test_source_drift_and_cross_format_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    plan = _plan(root)
    record_plan_and_claim_targets(database, plan, resource_root=root)
    _materialize_outputs(root)
    (root / "inputs" / "metric.csv").write_text(
        "Metric,Value\nTotal,99\n",
        encoding="utf-8",
    )

    verification = verify_recorded_package(
        database,
        plan.plan_id,
        resource_root=root,
    )

    assert verification.status == "FAIL"
    assert verification.source_findings[0]["status"] == "FAIL"
    assert "one or more source identities changed" in verification.errors


def test_cross_format_mismatch_fails_while_sources_remain_stable(
    tmp_path: Path,
) -> None:
    from pptx import Presentation

    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    plan = _plan(root)
    record_plan_and_claim_targets(database, plan, resource_root=root)
    _materialize_outputs(root)
    presentation_path = root / "delivery" / "metric.pptx"
    presentation = Presentation(presentation_path)
    for slide in presentation.slides:
        for shape in slide.shapes:
            if hasattr(shape, "text") and "Total value: 40" in str(shape.text):
                shape.text = str(shape.text).replace(
                    "Total value: 40",
                    "Total value: 41",
                )
    presentation.save(presentation_path)

    verification = verify_recorded_package(
        database,
        plan.plan_id,
        resource_root=root,
    )

    assert verification.status == "FAIL"
    assert all(
        finding["status"] == "PASS"
        for finding in verification.source_findings
    )
    mismatch = next(
        finding
        for finding in verification.consistency_findings
        if finding["assertion_id"] == "pdf-pptx"
    )
    assert mismatch["status"] == "FAIL"
    assert mismatch["left"] == "40"
    assert mismatch["right"] == "41"
    assert "one or more cross-format consistency checks failed" in (
        verification.errors
    )


def test_required_visual_evidence_is_hash_bound_to_the_artifact(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    plan = _plan(root, visual_required=True)
    record_plan_and_claim_targets(database, plan, resource_root=root)
    _materialize_outputs(root)

    missing = verify_recorded_package(
        database,
        plan.plan_id,
        resource_root=root,
    )
    assert missing.status == "FAIL"
    missing_visual = {
        finding["deliverable_id"]: finding
        for finding in missing.visual_status
    }
    assert missing_visual["slides"]["status"] == "FAIL"

    evidence = root / "delivery" / "evidence" / "slides.json"
    evidence.parent.mkdir()
    artifact_sha256 = hashlib.sha256(
        (root / "delivery" / "metric.pptx").read_bytes()
    ).hexdigest()
    evidence.write_text(
        json.dumps(
            {
                "status": "PASS",
                "artifact_sha256": artifact_sha256,
                "clipping_detected": True,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    clipped = verify_recorded_package(
        database,
        plan.plan_id,
        resource_root=root,
    )
    assert clipped.status == "FAIL"
    clipped_visual = {
        finding["deliverable_id"]: finding
        for finding in clipped.visual_status
    }
    assert clipped_visual["slides"]["status"] == "FAIL"

    evidence.write_text(
        json.dumps(
            {
                "status": "PASS",
                "artifact_sha256": artifact_sha256,
                "clipping_detected": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    passed = verify_recorded_package(
        database,
        plan.plan_id,
        resource_root=root,
    )

    assert passed.status == "PASS"
    passed_visual = {
        finding["deliverable_id"]: finding
        for finding in passed.visual_status
    }
    assert passed_visual["slides"]["status"] == "PASS"
