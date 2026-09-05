from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.general_work import (
    GeneralWorkPlanner,
    GeneralWorkPlanningError,
    GeneralWorkRequest,
)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "owned"
    root.mkdir()
    return root


def test_planner_compiles_multi_source_data_package_deterministically(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    inputs = root / "inputs"
    inputs.mkdir()
    (inputs / "orders.csv").write_text(
        "Account,Status,Amount\n"
        " A ,open,10\n"
        " B ,closed,20\n",
        encoding="utf-8",
    )
    (inputs / "orders.json").write_text(
        '{"records": ['
        '{"account": "A", "status": "open", "amount": 10},'
        '{"account": "C", "status": "open", "amount": 30}'
        "]}\n",
        encoding="utf-8",
    )
    request = GeneralWorkRequest(
        objective_id="objective-data-package",
        resource_id="resource-data-package",
        desired_outcome=(
            "Prepare a verified data and report package with an editable "
            "workbook, written report, printable copy, and presentation"
        ),
        source_paths=("inputs/orders.csv", "inputs/orders.json"),
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
    planner = GeneralWorkPlanner(root)

    first = planner.plan(request)
    second = planner.plan(request)

    assert first.plan.plan_id == second.plan.plan_id
    assert first.plan.plan_hash == second.plan.plan_hash
    assert first.inferred_formats == ("xlsx", "docx", "pdf", "pptx")
    assert len(first.plan.sources) == 2
    assert first.plan.source_preservation == "PRESERVE_ORIGINALS"
    by_id = {
        deliverable.deliverable_id: deliverable
        for deliverable in first.plan.deliverables
    }
    assert set(by_id) == {
        "normalized-data",
        "workbook",
        "document",
        "pdf",
        "presentation",
    }
    normalized = next(
        assertion
        for assertion in first.plan.artifact_assertions
        if assertion.assertion_id == "normalized-row-count"
    )
    assert normalized.expected == 3
    layers = first.plan.topological_layers()
    assert set(layers[0]) == {"inspect-001", "inspect-002"}
    assert layers[1] == ("normalize-data",)
    assert set(layers[2]) == {
        "build-document",
        "build-pdf",
        "build-presentation",
        "build-workbook",
    }
    assert first.plan.metadata["organization"]["delivery_root"] == "delivery"


def test_planner_chooses_owner_workbook_and_memo_without_fixed_template(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    inputs = root / "inputs"
    inputs.mkdir()
    (inputs / "pipeline.csv").write_text(
        "Account,Owner,Amount\n"
        "North,Alex,100\n"
        "South,Sam,80\n",
        encoding="utf-8",
    )

    result = GeneralWorkPlanner(root).plan(
        GeneralWorkRequest(
            objective_id="objective-owner-review",
            resource_id="resource-owner-review",
            desired_outcome=(
                "Prepare the September sales operating view and a short "
                "written decision memo for the owner"
            ),
            source_paths=("inputs/pipeline.csv",),
            excluded_formats=("pdf", "pptx"),
        )
    )

    assert result.inferred_formats == ("xlsx", "docx")
    assert {
        deliverable.format for deliverable in result.plan.deliverables
    } == {"xlsx", "docx"}
    assert any(
        "editable workbook" in reason for reason in result.rationale
    )


@pytest.mark.parametrize(
    "negative_clause",
    (
        "without slides or PDF",
        "without PDF and slides",
    ),
)
def test_planner_honors_coordinated_negative_formats(
    tmp_path: Path,
    negative_clause: str,
) -> None:
    root = _root(tmp_path)
    inputs = root / "inputs"
    inputs.mkdir()
    (inputs / "pipeline.csv").write_text(
        "Account,Owner,Amount\nNorth,Alex,100\n",
        encoding="utf-8",
    )
    (inputs / "notes.md").write_text(
        "# Owner note\nKeep the output editable.\n",
        encoding="utf-8",
    )

    result = GeneralWorkPlanner(root).plan(
        GeneralWorkRequest(
            objective_id="objective-negative-formats",
            resource_id="resource-negative-formats",
            desired_outcome=(
                "Prepare an editable workbook and written memo "
                f"{negative_clause}"
            ),
            source_paths=("inputs",),
        )
    )

    assert result.inferred_formats == ("xlsx", "docx")
    assert {
        deliverable.format for deliverable in result.plan.deliverables
    } == {"xlsx", "docx"}


def test_planner_expands_directory_for_document_reconstruction(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    inputs = root / "inputs"
    inputs.mkdir()
    (inputs / "policy.txt").write_text(
        "Approvals must be recorded before publication.\n",
        encoding="utf-8",
    )
    (inputs / "notes.md").write_text(
        "# Operating notes\nPublication follows the final review.\n",
        encoding="utf-8",
    )

    result = GeneralWorkPlanner(root).plan(
        GeneralWorkRequest(
            objective_id="objective-reconstruction",
            resource_id="resource-reconstruction",
            desired_outcome=(
                "Reconstruct and consolidate the source material into a "
                "structured document and final printable copy"
            ),
            source_paths=("inputs",),
        )
    )

    assert result.inferred_formats == ("docx", "pdf")
    assert len(result.plan.sources) == 2
    assert all(
        set(deliverable.source_ids)
        == {source.source_id for source in result.plan.sources}
        for deliverable in result.plan.deliverables
    )
    assert result.plan.ambiguities == ()


def test_planner_records_ambiguity_without_hiding_it(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    source = root / "source.csv"
    source.write_text("Metric,Value\nTotal,4\n", encoding="utf-8")

    result = GeneralWorkPlanner(root).plan(
        GeneralWorkRequest(
            objective_id="objective-ambiguous",
            resource_id="resource-ambiguous",
            desired_outcome="Prepare the requested report",
            source_paths=("source.csv",),
            ambiguities=("reporting period is not specified",),
        )
    )

    assert result.plan.ambiguities == (
        "reporting period is not specified",
    )


def test_planner_rejects_malformed_and_unsupported_sources(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    (root / "broken.json").write_text('{"records": [', encoding="utf-8")
    (root / "unknown.bin").write_bytes(b"\x00\x01")
    planner = GeneralWorkPlanner(root)

    with pytest.raises(GeneralWorkPlanningError, match="inspection failed"):
        planner.plan(
            GeneralWorkRequest(
                objective_id="objective-broken",
                resource_id="resource-broken",
                desired_outcome="Prepare a report",
                source_paths=("broken.json",),
            )
        )
    with pytest.raises(GeneralWorkPlanningError, match="inspection failed"):
        planner.plan(
            GeneralWorkRequest(
                objective_id="objective-unsupported",
                resource_id="resource-unsupported",
                desired_outcome="Prepare a report",
                source_paths=("unknown.bin",),
            )
        )


@pytest.mark.parametrize("suffix", ("xlsx", "docx"))
def test_planner_rejects_malformed_ooxml_sources(
    tmp_path: Path,
    suffix: str,
) -> None:
    root = _root(tmp_path)
    source = root / f"broken.{suffix}"
    source.write_bytes(b"not an OOXML package")

    with pytest.raises(
        GeneralWorkPlanningError,
        match=r"source inspection failed.*ArtifactParseError",
    ):
        GeneralWorkPlanner(root).plan(
            GeneralWorkRequest(
                objective_id=f"objective-broken-{suffix}",
                resource_id=f"resource-broken-{suffix}",
                desired_outcome="Prepare a reviewed document package",
                source_paths=(source.name,),
            )
        )


def test_planner_reports_a_locked_source_without_partial_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    source = root / "locked.csv"
    source.write_text("Metric,Value\nTotal,4\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path.resolve() == source.resolve():
            raise PermissionError("source file is locked")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    with pytest.raises(
        GeneralWorkPlanningError,
        match=r"source inspection failed.*PermissionError.*locked",
    ):
        GeneralWorkPlanner(root).plan(
            GeneralWorkRequest(
                objective_id="objective-locked",
                resource_id="resource-locked",
                desired_outcome="Prepare a workbook from the locked source",
                source_paths=(source.name,),
            )
        )


def test_planner_rejects_formula_workbook_without_explicit_formula_policy(
    tmp_path: Path,
) -> None:
    from openpyxl import Workbook

    root = _root(tmp_path)
    source = root / "formula.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(("Metric", "Value"))
    sheet.append(("Total", "=SUM(1,3)"))
    workbook.save(source)
    workbook.close()

    with pytest.raises(
        GeneralWorkPlanningError,
        match=r"source inspection failed.*formula policy violation",
    ):
        GeneralWorkPlanner(root).plan(
            GeneralWorkRequest(
                objective_id="objective-formula",
                resource_id="resource-formula",
                desired_outcome="Prepare a verified workbook report",
                source_paths=(source.name,),
            )
        )


def test_planner_rejects_symbolic_links_inside_source_directories(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    source_dir = root / "inputs"
    source_dir.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("Metric,Value\nTotal,9\n", encoding="utf-8")
    linked = source_dir / "linked.csv"
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(
        GeneralWorkPlanningError,
        match="contains a symbolic link",
    ):
        GeneralWorkPlanner(root).plan(
            GeneralWorkRequest(
                objective_id="objective-symlink",
                resource_id="resource-symlink",
                desired_outcome="Prepare a workbook",
                source_paths=("inputs",),
            )
        )
