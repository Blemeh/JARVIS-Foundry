"""V10-B bounded local artifact adapters and Golden Journey 2 proof."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
import zipfile

import pytest

from jarvis_foundry.connectors.local_artifacts import (
    ArtifactLimits,
    ArtifactSchemaError,
    ArtifactValidationError,
    ArtifactWriteError,
    CORE_DISTRIBUTIONS,
    InjectedArtifactFailure,
    LocalArtifactConnector,
    SourceChangedError,
    installed_library_versions,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    WorkflowStep,
    run_workflow,
)
from jarvis_foundry.fabric.loop import _verify
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.world_model.facade import WorldModel


def _xlsx(path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(["Status", "Amount"])
    sheet.append(["open", 10])
    sheet.append(["closed", 20])
    sheet.append(["open", 5])
    workbook.save(path)


def _docx(path: Path) -> None:
    from docx import Document

    document = Document()
    document.add_heading("Fixture document", level=1)
    document.add_paragraph("bounded fixture")
    document.save(path)


def _pdf(path: Path) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


def _pptx(path: Path) -> None:
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Fixture presentation"
    slide.placeholders[1].text = "bounded fixture"
    presentation.save(path)


def _image(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (8, 4), color=(30, 80, 120)).save(path, format="PNG")


def _csv(path: Path) -> None:
    path.write_text(
        "Status,Amount\nopen,10\nclosed,20\nopen,5\n",
        encoding="utf-8",
    )


def _journey_source(root: Path) -> Path:
    source = root / "input.csv"
    _csv(source)
    return source


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_core_parsers_cover_all_lane_b_formats(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    _csv(root / "data.csv")
    _xlsx(root / "data.xlsx")
    _docx(root / "data.docx")
    _pdf(root / "data.pdf")
    _pptx(root / "data.pptx")
    _image(root / "data.png")
    connector = LocalArtifactConnector(root)

    table = connector.parse("data.csv", expected_columns=("Status", "Amount"))
    assert table.kind == "table"
    assert table.table is not None
    assert table.table.row_count == 3
    assert table.source.sha256 == hashlib.sha256((root / "data.csv").read_bytes()).hexdigest()

    workbook = connector.parse("data.xlsx", expected_columns=("Status", "Amount"))
    assert workbook.details["workbook_format"] == "xlsx"
    assert workbook.table is not None
    assert workbook.table.rows == (("open", 10), ("closed", 20), ("open", 5))

    document = connector.parse("data.docx")
    assert document.kind == "docx"
    assert document.details["paragraph_count"] >= 2

    pdf = connector.parse("data.pdf")
    assert pdf.kind == "pdf"
    assert pdf.details["page_count"] == 1

    presentation = connector.parse("data.pptx")
    assert presentation.kind == "pptx"
    assert presentation.details["slide_count"] == 1

    image = connector.parse("data.png")
    assert image.kind == "image"
    assert image.details["width"] == 8
    assert image.details["height"] == 4


def test_golden_journey_2_fabric_chain_and_world_report(tmp_path: Path) -> None:
    database = _database(tmp_path)
    owned = tmp_path / "owned"
    owned.mkdir()
    source = _journey_source(owned)
    context = ExecutorContext(database=database, owned_root=owned)
    intent = TaskIntent(
        task_id="journey-2.fixture",
        required_actions=(
            "artifact.table.summary",
            "artifact.docx.create",
            "artifact.docx.verify",
        ),
    )

    result = run_workflow(
        database,
        context,
        intent,
        (
            WorkflowStep(
                "summary",
                "artifact.table.summary",
                {
                    "source": source.name,
                    "expected_columns": ["Status", "Amount"],
                    "group_by": "Status",
                    "sum_column": "Amount",
                },
            ),
            WorkflowStep(
                "report",
                "artifact.docx.create",
                {
                    "source": source.name,
                    "expected_columns": ["Status", "Amount"],
                    "group_by": "Status",
                    "sum_column": "Amount",
                    "target": "reports/journey-2.docx",
                    "idempotency_key": "journey-2:report",
                },
            ),
            WorkflowStep(
                "verify",
                "artifact.docx.verify",
                {
                    "target": "$report.target_ref",
                    "expected_sha256": "$report.output_sha256",
                    "required_values": [
                        "$summary.source_sha256",
                        "$summary.row_count",
                    ],
                },
            ),
        ),
    )

    assert result["success"] is True
    summary = result["context"]["summary"]
    report = result["context"]["report"]
    assert summary["row_count"] == 3
    assert summary["group_counts"] == {"closed": 1, "open": 2}
    assert summary["sum_value"] == "35"
    assert report["readback_verified"] is True
    assert (owned / "reports" / "journey-2.docx").is_file()

    observation = WorldModel(database).observe(
        "artifact.journey_2",
        "journey-2.fixture",
        {
            "status": "COMPLETED",
            "source_sha256": summary["source_sha256"],
            "output_sha256": report["output_sha256"],
            "next_action": "review the verified local report",
        },
        provenance={"task_id": intent.task_id, "source": source.name},
        observation_id="observation:journey-2:fixture",
    )
    assert observation["observation_id"] == "observation:journey-2:fixture"
    assert observation["deduplicated"] is False


def test_source_drift_and_schema_mismatch_are_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    source = _journey_source(root)
    connector = LocalArtifactConnector(root)
    captured = connector.capture(source.name)
    source.write_text("Status,Amount\nopen,999\n", encoding="utf-8")
    with pytest.raises(SourceChangedError, match="changed after capture"):
        connector.parse(captured)
    assert source.read_text(encoding="utf-8") == "Status,Amount\nopen,999\n"

    with pytest.raises(ArtifactSchemaError, match="schema mismatch"):
        connector.parse(source.name, expected_columns=("Wrong", "Amount"))
    assert source.read_text(encoding="utf-8") == "Status,Amount\nopen,999\n"


def test_schema_mismatch_is_blocked_at_fabric_boundary_without_source_write(
    tmp_path: Path,
) -> None:
    from jarvis_foundry.fabric.executors import artifact_table_summary

    database = _database(tmp_path)
    owned = tmp_path / "owned"
    owned.mkdir()
    source = _journey_source(owned)
    before = source.read_bytes()
    result = artifact_table_summary(
        ExecutorContext(database=database, owned_root=owned),
        {"source": source.name, "expected_columns": ["Wrong", "Amount"]},
    )

    assert result["claimed"] == "BLOCKED"
    assert result["error_code"] == "schema_mismatch"
    assert source.read_bytes() == before
    assert not (owned / "Wrong").exists()


def test_external_source_root_keeps_real_input_read_only_and_output_owned(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "registered-source"
    owned = tmp_path / "owned-output"
    source_root.mkdir()
    owned.mkdir()
    source = _journey_source(source_root)
    source_before = source.read_bytes()

    connector = LocalArtifactConnector(owned, source_roots=[source_root])
    parsed = connector.parse(str(source), expected_columns=("Status", "Amount"))
    summary = connector.summarize_table(parsed, group_by="Status", sum_column="Amount")
    report = connector.create_docx_report(
        parsed,
        summary,
        target_ref="reports/external-source.docx",
        idempotency_key="external-source:report",
    )

    assert parsed.source.source_ref == str(source.resolve())
    assert parsed.source.sha256 == hashlib.sha256(source_before).hexdigest()
    assert source.read_bytes() == source_before
    assert report.path == str((owned / "reports" / "external-source.docx").resolve())
    assert Path(report.path).is_file()
    assert Path(report.path).is_relative_to(owned.resolve())


def test_external_source_root_is_preserved_through_fabric_verification(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    source_root = tmp_path / "registered-source"
    owned = tmp_path / "owned-output"
    source_root.mkdir()
    owned.mkdir()
    source = _journey_source(source_root)
    context = ExecutorContext(
        database=database,
        owned_root=owned,
        artifact_source_roots=(source_root,),
    )
    intent = TaskIntent(
        task_id="journey-2.external",
        required_actions=(
            "artifact.table.summary",
            "artifact.docx.create",
            "artifact.docx.verify",
        ),
    )

    result = run_workflow(
        database,
        context,
        intent,
        (
            WorkflowStep(
                "summary",
                "artifact.table.summary",
                {
                    "source": str(source),
                    "expected_columns": ["Status", "Amount"],
                    "group_by": "Status",
                    "sum_column": "Amount",
                },
            ),
            WorkflowStep(
                "report",
                "artifact.docx.create",
                {
                    "source": str(source),
                    "expected_columns": ["Status", "Amount"],
                    "group_by": "Status",
                    "sum_column": "Amount",
                    "target": "reports/external-fabric.docx",
                    "idempotency_key": "journey-2:external-fabric",
                },
            ),
            WorkflowStep(
                "verify",
                "artifact.docx.verify",
                {
                    "target": "$report.target_ref",
                    "expected_sha256": "$report.output_sha256",
                    "required_values": [
                        "$summary.source_sha256",
                        "$summary.row_count",
                    ],
                },
            ),
        ),
    )

    assert result["success"] is True
    assert result["context"]["report"]["readback_verified"] is True


def test_docx_fault_reconciles_same_output_without_duplicate(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    source = _journey_source(root)
    connector = LocalArtifactConnector(root)
    parsed = connector.parse(source.name, expected_columns=("Status", "Amount"))
    summary = connector.summarize_table(parsed, group_by="Status", sum_column="Amount")
    with pytest.raises(InjectedArtifactFailure):
        connector.create_docx_report(
            parsed,
            summary,
            target_ref="reports/reconciled.docx",
            idempotency_key="journey-2:fault",
            fault_after_write=True,
        )

    target = root / "reports" / "reconciled.docx"
    expected_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    reconciled = connector.reconcile_docx(
        "reports/reconciled.docx",
        expected_sha256=expected_hash,
        required_values=(parsed.source.sha256, "3"),
    )
    assert reconciled["reconciled"] is True

    retry = connector.create_docx_report(
        parsed,
        summary,
        target_ref="reports/reconciled.docx",
        idempotency_key="journey-2:fault",
    )
    assert retry.replayed is True
    assert retry.readback["verified"] is True
    assert len(list((root / "reports").glob("reconciled*.docx"))) == 1


def test_independent_docx_verifier_rejects_lied_readback(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    source = _journey_source(root)
    connector = LocalArtifactConnector(root)
    parsed = connector.parse(source.name, expected_columns=("Status", "Amount"))
    summary = connector.summarize_table(parsed, group_by="Status", sum_column="Amount")
    report = connector.create_docx_report(
        parsed, summary, target_ref="reports/independent.docx", idempotency_key="independent"
    )

    claimed = {
        "claimed": "COMPLETED",
        "target_ref": report.target_ref,
        "output_sha256": report.sha256,
        "readback_verified": True,
    }
    verdict = _verify(
        "artifact.docx.verify",
        claimed,
        root,
        {
            "target": report.target_ref,
            "expected_sha256": report.sha256,
            "required_values": ["value-not-in-report"],
        },
    )
    assert verdict is VerifyVerdict.MISMATCH


def test_outputs_are_new_targets_and_binary_adapters_read_back(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    _csv(root / "data.csv")
    _xlsx(root / "data.xlsx")
    _pdf(root / "data.pdf")
    _pptx(root / "data.pptx")
    _image(root / "data.png")
    connector = LocalArtifactConnector(root)

    table = connector.parse("data.xlsx", expected_columns=("Status", "Amount"))
    exported = connector.export_table(
        table, target_ref="out/data.xlsx", idempotency_key="out:xlsx"
    )
    assert exported.readback["verified"] is True
    assert exported.sha256 == hashlib.sha256((root / "out" / "data.xlsx").read_bytes()).hexdigest()

    pdf = connector.parse("data.pdf")
    pdf_copy = connector.copy_pdf(
        pdf, target_ref="out/data.pdf", idempotency_key="out:pdf"
    )
    assert pdf_copy.readback["verified"] is True

    presentation = connector.parse("data.pptx")
    pptx_copy = connector.copy_pptx(
        presentation, target_ref="out/data.pptx", idempotency_key="out:pptx"
    )
    assert pptx_copy.readback["verified"] is True

    image = connector.parse("data.png")
    transformed = connector.transform_image(
        image, target_ref="out/data.png", idempotency_key="out:image",
        width=4, height=2,
    )
    assert transformed.readback["verified"] is True
    assert transformed.readback["width"] == 4
    assert transformed.readback["height"] == 2

    with pytest.raises(ArtifactValidationError, match="target must differ from source"):
        connector.copy_pdf(pdf, target_ref="data.pdf", idempotency_key="out:source")


def test_existing_output_is_not_silently_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    source = _journey_source(root)
    connector = LocalArtifactConnector(root)
    parsed = connector.parse(source.name, expected_columns=("Status", "Amount"))
    summary = connector.summarize_table(parsed, sum_column="Amount")
    connector.create_docx_report(
        parsed,
        summary,
        target_ref="reports/existing.docx",
        idempotency_key="existing:first",
    )
    target_before = (root / "reports" / "existing.docx").read_bytes()

    with pytest.raises(ArtifactWriteError, match="target_exists"):
        connector.create_docx_report(
            parsed,
            {**summary, "title": "not used"},
            target_ref="reports/existing.docx",
            idempotency_key="existing:second",
        )
    assert (root / "reports" / "existing.docx").read_bytes() == target_before


def test_ooxml_package_path_traversal_is_rejected_before_parser(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    source = root / "unsafe.docx"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("../outside.txt", "blocked")
    source.write_bytes(output.getvalue())

    with pytest.raises(ArtifactValidationError, match="unsafe package member"):
        LocalArtifactConnector(root).parse(source.name)
    assert not (tmp_path / "outside.txt").exists()


def test_artifact_paths_reject_ntfs_streams_before_read_or_write(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    source = _journey_source(root)
    stream = Path(f"{source}:hidden.csv")
    stream.write_text("Status,Amount\nhidden,999\n", encoding="utf-8")
    connector = LocalArtifactConnector(root)

    with pytest.raises(
        ArtifactValidationError,
        match="cannot contain a drive or stream",
    ):
        connector.capture("input.csv:hidden.csv")

    parsed = connector.parse("input.csv")
    with pytest.raises(
        ArtifactValidationError,
        match="cannot contain a drive or stream",
    ):
        connector.create_xlsx(
            parsed,
            target_ref="out.xlsx:hidden-stream",
            idempotency_key="artifact:stream-target",
        )

    assert not (root / "out.xlsx").exists()


def test_core_library_versions_are_visible_for_lane_evidence() -> None:
    versions = installed_library_versions()
    assert tuple(versions) == CORE_DISTRIBUTIONS
    assert all(value != "MISSING" for value in versions.values())


def test_unsupported_and_oversized_inputs_have_useful_errors(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    (root / "data.exe").write_bytes(b"not an artifact")
    connector = LocalArtifactConnector(root)
    with pytest.raises(ArtifactValidationError):
        connector.parse("data.exe")

    source = root / "data.csv"
    source.write_text("Column\nvalue\n", encoding="utf-8")
    bounded = LocalArtifactConnector(
        root,
        limits=ArtifactLimits(max_source_bytes=1),
    )
    with pytest.raises(ArtifactValidationError, match="max_source_bytes"):
        bounded.capture(source.name)
