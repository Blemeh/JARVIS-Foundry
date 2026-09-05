"""V13 bounded local artifact capability expansion."""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from pathlib import Path

import pytest

from jarvis_foundry.connectors.local_artifacts import (
    ArtifactLimits,
    ArtifactParseError,
    ArtifactSchemaError,
    ArtifactValidationError,
    ArtifactWriteError,
    InjectedArtifactFailure,
    LocalArtifactConnector,
)


def _csv(path: Path) -> bytes:
    data = (
        "Name,Status,Quantity,UnitPrice\n"
        "Alpha,open,2,10\n"
        "Beta,closed,3,7.5\n"
        "Gamma,open,4,5\n"
    ).encode("utf-8")
    path.write_bytes(data)
    return data


def _docx(path: Path) -> bytes:
    from docx import Document

    document = Document()
    document.add_heading("Quarterly draft", level=1)
    document.add_paragraph("Owner: Old Team")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "State"
    table.rows[0].cells[1].text = "Old Value"
    document.save(path)
    return path.read_bytes()


def _xlsx(path: Path) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Data"
    worksheet.append(["Name", "Amount", "Double"])
    worksheet.append(["Alpha", 10, "=B2*2"])
    worksheet.append(["Beta", 20, "=B3*2"])
    worksheet.append(["Gamma", 30, "=B4*2"])
    workbook.save(path)
    workbook.close()
    return path.read_bytes()


def _image(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (120, 80), color=(31, 78, 120)).save(path, format="PNG")


def test_json_object_record_lists_and_records_container(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    (root / "object.json").write_text(
        json.dumps({"owner": "local", "enabled": True, "count": 3}),
        encoding="utf-8",
    )
    (root / "list.json").write_text(
        json.dumps([
            {"Name": "Alpha", "Amount": 10},
            {"Name": "Beta", "Amount": 20, "Status": "open"},
        ]),
        encoding="utf-8",
    )
    (root / "records.json").write_text(
        json.dumps({
            "batch": "v13",
            "records": [
                {"Name": "Alpha", "Amount": 10},
                {"Name": "Beta", "Amount": 20},
            ],
        }),
        encoding="utf-8",
    )
    connector = LocalArtifactConnector(root)

    object_result = connector.parse("object.json")
    assert object_result.kind == "json"
    assert object_result.details["json_type"] == "object"
    assert object_result.details["value"]["owner"] == "local"

    list_result = connector.parse(
        "list.json", expected_columns=("Name", "Amount", "Status")
    )
    assert list_result.kind == "table"
    assert list_result.table is not None
    assert list_result.table.rows == (
        ("Alpha", 10, None),
        ("Beta", 20, "open"),
    )
    assert list_result.details["records_path"] == "$"

    records_result = connector.parse(
        "records.json", expected_columns=("Name", "Amount")
    )
    assert records_result.table is not None
    assert records_result.details["records_path"] == "$.records"
    assert records_result.details["container_keys"] == ["batch", "records"]

    (root / "invalid.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(ArtifactParseError, match="JSON parser rejected"):
        connector.parse("invalid.json")


def test_table_transform_is_deterministic_and_keeps_source_lineage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    source_before = _csv(root / "source.csv")
    connector = LocalArtifactConnector(root)
    parsed = connector.parse("source.csv")
    transform_args = {
        "select_columns": ("Name", "Total", "Label"),
        "filters": ({"column": "Status", "op": "eq", "value": "open"},),
        "computed_columns": {
            "Total": {
                "op": "multiply",
                "left": {"column": "Quantity"},
                "right": {"column": "UnitPrice"},
            },
            "Label": {
                "op": "concat",
                "values": (
                    {"column": "Name"},
                    {"value": " / "},
                    {"column": "Status"},
                ),
            },
        },
    }

    first = connector.transform_table(parsed, **transform_args)
    second = connector.transform_table(parsed, **transform_args)

    assert first.table is not None
    assert first.table.rows == (
        ("Alpha", 20, "Alpha / open"),
        ("Gamma", 20, "Gamma / open"),
    )
    assert first.table == second.table
    assert first.details == second.details
    assert first.source.sha256 == hashlib.sha256(source_before).hexdigest()
    assert first.details["source_sha256"] == first.source.sha256
    assert (root / "source.csv").read_bytes() == source_before

    with pytest.raises(ArtifactSchemaError, match="divides by zero"):
        connector.transform_table(
            parsed,
            computed_columns={
                "Broken": {
                    "op": "divide",
                    "left": {"column": "Quantity"},
                    "right": {"value": 0},
                }
            },
        )

    exported = connector.export_table(
        first,
        target_ref="outputs/transformed.json",
        idempotency_key="v13:json:export",
    )
    assert exported.source_sha256 == first.source.sha256
    assert exported.readback["verified"] is True
    reopened = connector.parse(
        "outputs/transformed.json",
        expected_columns=("Name", "Total", "Label"),
    )
    assert reopened.table == first.table


def test_xlsx_create_format_formula_replay_and_exact_source_preserving_update(
    tmp_path: Path,
) -> None:
    from openpyxl import load_workbook

    root = tmp_path / "owned"
    root.mkdir()
    csv_before = _csv(root / "source.csv")
    source_xlsx_before = _xlsx(root / "source.xlsx")
    connector = LocalArtifactConnector(root)
    table = connector.parse("source.csv")

    created = connector.create_xlsx(
        table,
        target_ref="outputs/formatted.xlsx",
        idempotency_key="v13:xlsx:create",
        sheet_name="Work",
        formulas={"E2": "=C2*D2"},
    )
    assert created.readback["verified"] is True
    assert created.readback["formula_count"] == 1
    assert created.readback["freeze_panes"] == "A2"
    assert created.readback["styled_header"] is True
    assert (root / "source.csv").read_bytes() == csv_before

    time.sleep(1.05)
    replay = connector.create_xlsx(
        table,
        target_ref="outputs/formatted.xlsx",
        idempotency_key="v13:xlsx:create",
        sheet_name="Work",
        formulas={"E2": "=C2*D2"},
    )
    assert replay.replayed is True
    assert replay.sha256 == created.sha256

    updated = connector.update_xlsx(
        "source.xlsx",
        target_ref="outputs/updated.xlsx",
        idempotency_key="v13:xlsx:update",
        sheet_name="Data",
        cells={"B2": 99},
        ranges={"A3:B4": [["Beta revised", 25], ["Gamma revised", 35]]},
        formulas={"C2": "=B2*3"},
    )
    assert updated.source_sha256 == hashlib.sha256(source_xlsx_before).hexdigest()
    assert updated.readback["verified"] is True
    assert updated.readback["formula_count"] == 3
    assert (root / "source.xlsx").read_bytes() == source_xlsx_before

    workbook = load_workbook(root / "outputs" / "updated.xlsx", data_only=False)
    worksheet = workbook["Data"]
    assert worksheet["B2"].value == 99
    assert worksheet["C2"].value == "=B2*3"
    assert worksheet["A3"].value == "Beta revised"
    assert worksheet["B4"].value == 35
    workbook.close()

    with pytest.raises(ArtifactValidationError, match="target must differ"):
        connector.update_xlsx(
            "source.xlsx",
            target_ref="source.xlsx",
            idempotency_key="v13:xlsx:same-source",
            sheet_name="Data",
            cells={"B2": 1},
        )
    with pytest.raises(ArtifactWriteError, match="target_exists"):
        connector.update_xlsx(
            "source.xlsx",
            target_ref="outputs/updated.xlsx",
            idempotency_key="v13:xlsx:different-write",
            sheet_name="Data",
            cells={"B2": 1},
        )
    assert (root / "source.xlsx").read_bytes() == source_xlsx_before


def test_xlsx_source_formula_like_text_stays_literal(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    root = tmp_path / "owned"
    root.mkdir()
    (root / "source.csv").write_text(
        "Name,Value\nAlpha,=1+1\n",
        encoding="utf-8",
    )
    connector = LocalArtifactConnector(root)
    parsed = connector.parse("source.csv")
    created = connector.create_xlsx(
        parsed,
        target_ref="created.xlsx",
        idempotency_key="literal:create",
        formulas={"C2": "=1+1"},
    )
    workbook = load_workbook(root / created.target_ref, data_only=False)
    try:
        worksheet = workbook["Data"]
        assert worksheet["B2"].value == "=1+1"
        assert worksheet["B2"].data_type == "s"
        assert worksheet["C2"].value == "=1+1"
        assert worksheet["C2"].data_type == "f"
    finally:
        workbook.close()

    exported = connector.export_table(
        parsed,
        target_ref="exported.xlsx",
        idempotency_key="literal:export",
    )
    workbook = load_workbook(root / exported.target_ref, data_only=False)
    try:
        assert workbook["Data"]["B2"].value == "=1+1"
        assert workbook["Data"]["B2"].data_type == "s"
    finally:
        workbook.close()


def test_docx_edit_and_structured_report_preserve_sources(tmp_path: Path) -> None:
    from docx import Document

    root = tmp_path / "owned"
    root.mkdir()
    docx_before = _docx(root / "draft.docx")
    csv_before = _csv(root / "source.csv")
    connector = LocalArtifactConnector(root)

    edited = connector.edit_docx(
        "draft.docx",
        target_ref="outputs/revised.docx",
        idempotency_key="v13:docx:edit",
        replacements={"Old Team": "Operator Team", "Old Value": "Verified"},
        append_paragraphs=("Readback complete.",),
    )
    assert edited.readback["verified"] is True
    assert edited.readback["replacements_applied"] == {
        "Old Team": 1,
        "Old Value": 1,
    }
    edited_parsed = connector.parse("outputs/revised.docx")
    assert "Operator Team" in edited_parsed.details["text"]
    assert edited_parsed.details["paragraphs"]
    assert edited_parsed.details["table_text_rows"]
    assert (root / "draft.docx").read_bytes() == docx_before

    parsed = connector.parse("source.csv")
    summary = connector.summarize_table(parsed, group_by="Status", sum_column="Quantity")
    report = connector.create_docx_report(
        parsed,
        summary,
        target_ref="outputs/report.docx",
        idempotency_key="v13:docx:report",
        title="V13 Local Artifact Report",
    )
    assert report.readback["verified"] is True
    document = Document(root / "outputs" / "report.docx")
    assert len(document.tables) >= 4
    assert any(
        paragraph.text == "Data preview" for paragraph in document.paragraphs
    )
    assert any(
        "Alpha" in cell.text
        for table in document.tables
        for row in table.rows
        for cell in row.cells
    )
    assert (root / "source.csv").read_bytes() == csv_before


def test_pdf_create_fault_replay_combine_and_split_with_text_readback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    _csv(root / "source.csv")
    connector = LocalArtifactConnector(root)

    with pytest.raises(InjectedArtifactFailure):
        connector.create_pdf(
            pages=("Alpha source page.",),
            target_ref="pdf/alpha.pdf",
            idempotency_key="v13:pdf:alpha",
            title="Alpha packet",
            fault_after_write=True,
        )
    alpha = connector.create_pdf(
        pages=("Alpha source page.",),
        target_ref="pdf/alpha.pdf",
        idempotency_key="v13:pdf:alpha",
        title="Alpha packet",
    )
    assert alpha.replayed is True
    assert alpha.readback["verified"] is True
    alpha_parsed = connector.parse("pdf/alpha.pdf")
    assert "Alpha source page." in alpha_parsed.details["text"]
    assert len(alpha_parsed.details["pages"]) == 1

    beta = connector.create_pdf(
        pages=("Beta source page.",),
        target_ref="pdf/beta.pdf",
        idempotency_key="v13:pdf:beta",
        title="Beta packet",
        source="source.csv",
    )
    assert beta.source_sha256 == hashlib.sha256(
        (root / "source.csv").read_bytes()
    ).hexdigest()
    assert beta.readback["source_sha256"] == beta.source_sha256
    alpha_before = (root / "pdf" / "alpha.pdf").read_bytes()
    beta_before = (root / "pdf" / "beta.pdf").read_bytes()
    combined = connector.combine_pdfs(
        ("pdf/alpha.pdf", "pdf/beta.pdf"),
        target_ref="pdf/combined.pdf",
        idempotency_key="v13:pdf:combined",
    )
    assert combined.readback["verified"] is True
    assert combined.readback["page_count"] == 2
    assert len(combined.readback["source_sha256s"]) == 2

    split = connector.split_pdf(
        "pdf/combined.pdf",
        target_ref="pdf/second.pdf",
        idempotency_key="v13:pdf:split",
        start_page=2,
        end_page=2,
    )
    assert split.readback["verified"] is True
    assert split.readback["page_count"] == 1
    assert connector.verify_pdf(
        "pdf/second.pdf",
        expected_sha256=split.sha256,
        expected_page_count=1,
        required_text=("Beta source page.",),
    )["verified"] is True
    assert (root / "pdf" / "alpha.pdf").read_bytes() == alpha_before
    assert (root / "pdf" / "beta.pdf").read_bytes() == beta_before


def test_pptx_create_with_owned_image_and_source_preserving_update(
    tmp_path: Path,
) -> None:
    from pptx import Presentation

    lineage_token = "a" * 64
    root = tmp_path / "owned"
    root.mkdir()
    _image(root / "figure.png")
    image_before = (root / "figure.png").read_bytes()
    connector = LocalArtifactConnector(root)

    created = connector.create_pptx(
        slides=(
            {
                "layout": "title",
                "title": "V13 Local Artifacts",
                "body": (
                    "Verified capability pack",
                    f"Lineage SHA-256: {lineage_token}",
                ),
            },
            {
                "layout": "title_and_content",
                "title": "Evidence",
                "body": ("Source preserved", "Readback verified"),
                "image_ref": "figure.png",
            },
        ),
        target_ref="slides/source.pptx",
        idempotency_key="v13:pptx:create",
        source="figure.png",
    )
    assert created.readback["verified"] is True
    assert created.readback["slide_count"] == 2
    assert created.readback["image_count"] == 1
    assert created.readback["source_matches"] is True
    assert created.source_sha256 == hashlib.sha256(image_before).hexdigest()
    assert (root / "figure.png").read_bytes() == image_before
    source_before = (root / "slides" / "source.pptx").read_bytes()

    updated = connector.update_pptx(
        "slides/source.pptx",
        target_ref="slides/updated.pptx",
        idempotency_key="v13:pptx:update",
        replacements={"Evidence": "Independent Evidence"},
        append_slides=(
            {
                "layout": "blank",
                "title": "Completion",
                "body": ("All requested formats reopened.",),
            },
        ),
    )
    assert updated.source_sha256 == hashlib.sha256(source_before).hexdigest()
    assert updated.readback["verified"] is True
    assert updated.readback["slide_count"] == 3
    assert updated.readback["replacements_applied"] == {"Evidence": 1}
    assert (root / "slides" / "source.pptx").read_bytes() == source_before
    parsed = connector.parse("slides/updated.pptx")
    assert "Independent Evidence" in parsed.details["text"]
    assert lineage_token in parsed.details["text"]
    assert parsed.details["text_blocks"]

    presentation = Presentation(root / "slides" / "updated.pptx")
    text = "\n".join(
        str(shape.text)
        for slide in presentation.slides
        for shape in slide.shapes
        if hasattr(shape, "text")
    )
    assert "Independent Evidence" in text
    assert "All requested formats reopened." in text
    subtitle_sizes = [
        paragraph.font.size.pt
        for paragraph in presentation.slides[0].placeholders[1].text_frame.paragraphs
        if paragraph.font.size is not None
    ]
    assert subtitle_sizes
    assert max(subtitle_sizes) <= 18

    outside = tmp_path / "outside.png"
    _image(outside)
    with pytest.raises(
        ArtifactValidationError, match="outside the configured source roots"
    ):
        connector.create_pptx(
            slides=({"title": "Blocked", "image_ref": str(outside)},),
            target_ref="slides/blocked.pptx",
            idempotency_key="v13:pptx:outside",
        )


def test_new_capabilities_enforce_size_and_count_bounds(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    (root / "records.json").write_text(
        json.dumps([{"Name": "A"}, {"Name": "B"}]),
        encoding="utf-8",
    )
    bounded = LocalArtifactConnector(root, limits=ArtifactLimits(max_rows=1))
    with pytest.raises(ArtifactSchemaError, match="max_rows"):
        bounded.parse("records.json")

    connector = LocalArtifactConnector(root)
    with pytest.raises(ArtifactValidationError, match="single-page layout"):
        connector.create_pdf(
            pages=("\n".join(f"line {index}" for index in range(60)),),
            target_ref="too-long.pdf",
            idempotency_key="v13:pdf:too-long",
        )
    assert not (root / "too-long.pdf").exists()

    long_identity = "PDF SHA-256: " + ("a" * 64)
    wrapped = connector._pptx_body_lines(long_identity)
    assert all(len(line) <= 54 or line == ("a" * 64) for line in wrapped)
    assert "a" * 64 in wrapped
    assert "".join(wrapped).replace("PDF SHA-256:", "").replace(" ", "") == (
        "a" * 64
    )


def test_ooxml_updates_enforce_archive_bounds_and_refuse_macros(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    for suffix in ("xlsx", "docx", "pptx"):
        with zipfile.ZipFile(
            root / f"bomb.{suffix}",
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr("[Content_Types].xml", "x" * 2_000)
    bounded = LocalArtifactConnector(
        root,
        limits=ArtifactLimits(max_zip_expanded_bytes=256),
    )
    with pytest.raises(ArtifactValidationError, match="expanded-byte"):
        bounded.update_xlsx(
            "bomb.xlsx",
            target_ref="out.xlsx",
            idempotency_key="bomb:xlsx",
            sheet_name="Data",
            cells={"A1": "safe"},
        )
    with pytest.raises(ArtifactValidationError, match="expanded-byte"):
        bounded.edit_docx(
            "bomb.docx",
            target_ref="out.docx",
            idempotency_key="bomb:docx",
            append_paragraphs=("safe",),
        )
    with pytest.raises(ArtifactValidationError, match="expanded-byte"):
        bounded.update_pptx(
            "bomb.pptx",
            target_ref="out.pptx",
            idempotency_key="bomb:pptx",
            append_slides=({"title": "Safe"},),
        )

    source = root / "source.xlsx"
    _xlsx(source)
    macro = root / "source.xlsm"
    macro.write_bytes(source.read_bytes())
    with pytest.raises(ArtifactSchemaError, match="source must use one of xlsx"):
        LocalArtifactConnector(root).update_xlsx(
            "source.xlsm",
            target_ref="macro-output.xlsm",
            idempotency_key="macro:refused",
            sheet_name="Data",
            cells={"A1": "safe"},
        )
