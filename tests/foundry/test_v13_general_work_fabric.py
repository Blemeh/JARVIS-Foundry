from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import jarvis_foundry.fabric.general_work as general_work_module
from jarvis_foundry.connectors.general_work_files import (
    GeneralWorkFileConnector,
    InjectedGeneralWorkFileFailure,
)
from jarvis_foundry.connectors.local_artifacts import LocalArtifactConnector
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    WorkflowStep,
    run_workflow,
)
from jarvis_foundry.fabric.adapter import FabricRoleAdapter
from jarvis_foundry.fabric.catalog import get_spec, listed_capabilities
from jarvis_foundry.fabric.loop import execute_action, seed_builtins
from jarvis_foundry.fabric.model import ClaimOutcome, VerifyVerdict
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.manifest import ContextManifest


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_builtin_seed_preserves_one_authority_per_operator_concern(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)

    seed_builtins(database)
    specs = listed_capabilities(database)

    assert get_spec(database, "cap.file.inspect").executor == "general_work_files"
    assert get_spec(database, "cap.file.replace").executor == "filesystem"
    assert get_spec(database, "cap.file.safe_edit") is None
    assert get_spec(database, "cap.local.process.launch").executor == (
        "computer_operator"
    )
    assert sum(spec.action_kind == "file.replace" for spec in specs) == 1
    assert all(spec.action_kind != "file.safe_edit" for spec in specs)


def test_table_transform_and_xlsx_creation_use_canonical_fabric(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    source = root / "inputs" / "pipeline.csv"
    source.parent.mkdir()
    source.write_text(
        "Account,Status,Amount\n"
        "A,open,10\n"
        "B,closed,20\n"
        "C,open,30\n",
        encoding="utf-8",
    )
    source_hash = _sha256(source)
    context = ExecutorContext(database=database, owned_root=root)
    intent = TaskIntent(
        task_id="general-work.fabric.xlsx",
        required_actions=(
            "artifact.table.transform",
            "artifact.xlsx.create",
        ),
        unattended=True,
    )
    transform = {
        "source": "inputs/pipeline.csv",
        "expected_source_sha256": source_hash,
        "expected_columns": ["Account", "Status", "Amount"],
        "filters": [{"column": "Status", "op": "eq", "value": "open"}],
        "select_columns": ["Account", "Amount"],
    }

    result = run_workflow(
        database,
        context,
        intent,
        (
            WorkflowStep("transform", "artifact.table.transform", transform),
            WorkflowStep(
                "workbook",
                "artifact.xlsx.create",
                {
                    **transform,
                    "target": "delivery/open-accounts.xlsx",
                    "sheet_name": "Open Accounts",
                },
            ),
        ),
    )

    assert result["success"] is True
    assert result["context"]["transform"]["row_count"] == 2
    assert result["context"]["workbook"]["readback_verified"] is True
    assert source.read_text(encoding="utf-8").endswith("C,open,30\n")
    from openpyxl import load_workbook

    workbook = load_workbook(
        root / "delivery" / "open-accounts.xlsx",
        data_only=False,
        read_only=True,
    )
    try:
        assert list(workbook["Open Accounts"].values) == [
            ("Account", "Amount"),
            ("A", "10"),
            ("C", "30"),
        ]
    finally:
        workbook.close()


def test_file_copy_replays_through_the_canonical_effect_journal(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    source = root / "inputs" / "brief.txt"
    source.parent.mkdir()
    source.write_text("owner brief", encoding="utf-8")
    payload = {
        "source": "inputs/brief.txt",
        "target": "delivery/brief.txt",
        "expected_source_sha256": _sha256(source),
    }
    context = ExecutorContext(database=database, owned_root=root)
    intent = TaskIntent(
        task_id="general-work.fabric.copy",
        required_actions=("file.copy",),
        unattended=True,
    )
    seed_builtins(database)

    first = execute_action(
        database,
        context,
        intent,
        "file.copy",
        payload,
        workflow_id="workflow:general-work.fabric.copy",
        step_id="copy",
    )
    second = execute_action(
        database,
        context,
        intent,
        "file.copy",
        payload,
        workflow_id="workflow:general-work.fabric.copy",
        step_id="copy",
    )

    assert first.claimed is ClaimOutcome.COMPLETED
    assert first.verified is VerifyVerdict.MATCH
    assert second.claimed is ClaimOutcome.COMPLETED
    assert second.verified is VerifyVerdict.MATCH
    assert second.observed["replayed"] is True
    assert list((root / "delivery").glob("brief*.txt")) == [
        root / "delivery" / "brief.txt"
    ]


def test_direct_fabric_file_action_rejects_ntfs_stream_target(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    source = root / "source.txt"
    source.write_text("bounded", encoding="utf-8")
    seed_builtins(database)

    result = execute_action(
        database,
        ExecutorContext(database=database, owned_root=root),
        TaskIntent(
            task_id="general-work.fabric.stream-refused",
            required_actions=("file.copy",),
            unattended=True,
        ),
        "file.copy",
        {
            "source": "source.txt",
            "target": "delivery.txt:hidden-stream",
            "expected_source_sha256": _sha256(source),
        },
        workflow_id="workflow:general-work.fabric.stream-refused",
        step_id="copy",
    )

    assert result.claimed is ClaimOutcome.FAILED
    assert result.observed["error_code"] == "invalid_input"
    assert not (root / "delivery.txt").exists()


@pytest.mark.parametrize("stream_field", ("source", "target"))
def test_artifact_fabric_rejects_ntfs_stream_refs_without_output(
    tmp_path: Path,
    stream_field: str,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    source = root / "inputs" / "source.csv"
    source.parent.mkdir()
    source.write_text("Name,Value\nvisible,1\n", encoding="utf-8")
    source_ref = "inputs/source.csv"
    expected_source = source
    target_ref = "delivery/output.xlsx"
    if stream_field == "source":
        stream = Path(f"{source}:hidden.csv")
        stream.write_text("Name,Value\nhidden,999\n", encoding="utf-8")
        source_ref = "inputs/source.csv:hidden.csv"
        expected_source = stream
    else:
        target_ref = "delivery/output.xlsx:hidden-stream"
    seed_builtins(database)

    result = execute_action(
        database,
        ExecutorContext(database=database, owned_root=root),
        TaskIntent(
            task_id=f"general-work.fabric.artifact-stream-{stream_field}",
            required_actions=("artifact.xlsx.create",),
            unattended=True,
        ),
        "artifact.xlsx.create",
        {
            "source": source_ref,
            "expected_source_sha256": _sha256(expected_source),
            "target": target_ref,
        },
        workflow_id=f"workflow:general-work.artifact-stream-{stream_field}",
        step_id="workbook",
    )

    assert result.claimed is ClaimOutcome.FAILED
    assert result.verified is VerifyVerdict.MATCH
    assert result.observed["error_code"] == "validation_error"
    assert not (root / "delivery" / "output.xlsx").exists()


def test_source_roots_are_context_policy_not_payload_authority(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    source = external / "source.csv"
    source.write_text("Name,Value\nA,1\n", encoding="utf-8")
    payload = {
        "source": str(source),
        "source_roots": [str(external)],
        "target": "delivery/refused.xlsx",
    }
    context = ExecutorContext(database=database, owned_root=root)
    intent = TaskIntent(
        task_id="general-work.fabric.root-policy",
        required_actions=("artifact.xlsx.create",),
        unattended=True,
    )
    seed_builtins(database)

    refused = execute_action(
        database,
        context,
        intent,
        "artifact.xlsx.create",
        payload,
    )

    assert refused.claimed is ClaimOutcome.FAILED
    assert refused.verified is VerifyVerdict.MATCH
    assert refused.observed["error_code"] == "validation_error"
    assert not (root / "delivery" / "refused.xlsx").exists()

    trusted = ExecutorContext(
        database=database,
        owned_root=root,
        artifact_source_roots=(external,),
    )
    accepted = execute_action(
        database,
        trusted,
        TaskIntent(
            task_id="general-work.fabric.root-policy.trusted",
            required_actions=("artifact.xlsx.create",),
            unattended=True,
        ),
        "artifact.xlsx.create",
        {
            "source": str(source),
            "expected_source_sha256": _sha256(source),
            "target": "delivery/trusted.xlsx",
        },
    )

    assert accepted.claimed is ClaimOutcome.COMPLETED
    assert accepted.verified is VerifyVerdict.MATCH


def test_post_write_fault_freezes_without_duplicate_dispatch(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    context = ExecutorContext(database=database, owned_root=root)
    intent = TaskIntent(
        task_id="general-work.fabric.unknown",
        required_actions=("artifact.pdf.create",),
        unattended=True,
    )
    payload = {
        "target": "delivery/ambiguous.pdf",
        "title": "Ambiguous proof",
        "pages": ["The write landed before the verifier was interrupted."],
        "fault_after_write": True,
    }
    seed_builtins(database)

    first = execute_action(
        database,
        context,
        intent,
        "artifact.pdf.create",
        payload,
        workflow_id="workflow:general-work.fabric.unknown",
        step_id="pdf",
    )
    second = execute_action(
        database,
        context,
        intent,
        "artifact.pdf.create",
        payload,
        workflow_id="workflow:general-work.fabric.unknown",
        step_id="pdf",
    )

    assert first.claimed is ClaimOutcome.UNKNOWN
    assert first.verified is VerifyVerdict.UNKNOWN
    assert first.observed["journal_state"] == "FROZEN"
    assert second.claimed is ClaimOutcome.UNKNOWN
    assert second.verified is VerifyVerdict.UNKNOWN
    assert second.observed["replayed"] is True
    assert list((root / "delivery").glob("ambiguous*.pdf")) == [
        root / "delivery" / "ambiguous.pdf"
    ]


def _artifact_manifest(
    *,
    role: str,
    attempt: int,
    payload: dict[str, object],
) -> ContextManifest:
    return ContextManifest(
        run_id=f"general-work-repair-{role}-{attempt}",
        role=role,
        mission_id="mission-general-work-repair",
        step_id="step-general-work-repair",
        resource_id="resource-general-work-repair",
        capability="cap.artifact.pdf.create",
        provider="fabric",
        model="local",
        isolation_group="general-work-repair",
        instructions=json.dumps(payload, sort_keys=True),
        expected_artifacts=("delivery/repaired.pdf",),
        verification_criteria=("PDF reopens with required content",),
        attempt=attempt,
    )


def _file_manifest(
    *,
    action: str,
    role: str,
    attempt: int,
    payload: dict[str, object],
) -> ContextManifest:
    capabilities = {
        "file.copy": "cap.file.copy",
        "file.move": "cap.file.move",
        "file.rename": "cap.file.rename",
    }
    return ContextManifest(
        run_id=f"general-work-file-{action}-{role}-{attempt}",
        role=role,
        mission_id=f"mission-general-work-{action}",
        step_id=f"step-general-work-{action}",
        resource_id="resource-general-work-file",
        capability=capabilities[action],
        provider="fabric",
        model="local",
        isolation_group=f"general-work-{action}",
        instructions=json.dumps(payload, sort_keys=True),
        expected_artifacts=(str(payload["target"]),),
        verification_criteria=("file transfer readback matches",),
        attempt=attempt,
    )


@pytest.mark.parametrize(
    "action",
    ("file.copy", "file.move", "file.rename"),
)
def test_repair_reconciles_landed_frozen_file_transfer_without_second_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    source = root / "source.txt"
    source.write_text("landed transfer", encoding="utf-8")
    target = root / "delivery" / "landed.txt"
    payload = {
        "source": "source.txt",
        "target": "delivery/landed.txt",
        "expected_source_sha256": _sha256(source),
    }
    publication_stages: list[str] = []
    real_connector = GeneralWorkFileConnector

    def connector_factory(owned_root: str | Path) -> GeneralWorkFileConnector:
        def fail_once(stage: str) -> None:
            if stage == f"{action}.after_publish":
                publication_stages.append(stage)
                if len(publication_stages) == 1:
                    raise InjectedGeneralWorkFileFailure(
                        "simulated post-publication restart"
                    )

        return real_connector(owned_root, fault_injector=fail_once)

    monkeypatch.setattr(
        general_work_module,
        "GeneralWorkFileConnector",
        connector_factory,
    )
    seed_builtins(database)
    adapter = FabricRoleAdapter(
        database,
        owned_root=root,
        inner=FakeHermesAdapter(working_root=root),
        structured_only=True,
    )

    frozen = adapter.dispatch(
        _file_manifest(action=action, role="builder", attempt=1, payload=payload),
        heartbeat=lambda: None,
    )
    landed_hash = _sha256(target)
    repaired = adapter.dispatch(
        _file_manifest(action=action, role="repair", attempt=2, payload=payload),
        heartbeat=lambda: None,
    )
    replayed = adapter.dispatch(
        _file_manifest(action=action, role="repair", attempt=3, payload=payload),
        heartbeat=lambda: None,
    )

    assert frozen.status == "FAILED"
    assert repaired.status == "COMPLETED"
    assert replayed.status == "COMPLETED"
    assert _sha256(target) == landed_hash
    assert publication_stages == [f"{action}.after_publish"]
    assert database.event_count(event_type="system.action_started") == 1
    assert database.event_count(event_type="effect.reconciled") == 1
    if action == "file.copy":
        assert source.read_text(encoding="utf-8") == "landed transfer"
    else:
        assert not source.exists()


def test_repair_refuses_tampered_landed_file_transfer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    source = root / "source.txt"
    source.write_text("captured transfer", encoding="utf-8")
    target = root / "delivery" / "landed.txt"
    payload = {
        "source": "source.txt",
        "target": "delivery/landed.txt",
        "expected_source_sha256": _sha256(source),
    }
    publication_stages: list[str] = []
    real_connector = GeneralWorkFileConnector

    def connector_factory(owned_root: str | Path) -> GeneralWorkFileConnector:
        def fail_once(stage: str) -> None:
            if stage == "file.copy.after_publish":
                publication_stages.append(stage)
                if len(publication_stages) == 1:
                    raise InjectedGeneralWorkFileFailure(
                        "simulated post-publication restart"
                    )

        return real_connector(owned_root, fault_injector=fail_once)

    monkeypatch.setattr(
        general_work_module,
        "GeneralWorkFileConnector",
        connector_factory,
    )
    seed_builtins(database)
    adapter = FabricRoleAdapter(
        database,
        owned_root=root,
        inner=FakeHermesAdapter(working_root=root),
        structured_only=True,
    )

    frozen = adapter.dispatch(
        _file_manifest(
            action="file.copy",
            role="builder",
            attempt=1,
            payload=payload,
        ),
        heartbeat=lambda: None,
    )
    target.write_text("tampered after crash", encoding="utf-8")
    repair = adapter.dispatch(
        _file_manifest(
            action="file.copy",
            role="repair",
            attempt=2,
            payload=payload,
        ),
        heartbeat=lambda: None,
    )

    assert frozen.status == "FAILED"
    assert repair.status == "BLOCKED"
    assert repair.error_class == "fabric_effect_reconciliation_required"
    assert target.read_text(encoding="utf-8") == "tampered after crash"
    assert publication_stages == ["file.copy.after_publish"]
    assert database.event_count(event_type="system.action_started") == 1
    assert database.event_count(event_type="effect.reconciled") == 0


def test_repair_attempt_preserves_stale_output_and_rebuilds_through_fabric(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    target = root / "delivery" / "repaired.pdf"
    target.parent.mkdir()
    target.write_bytes(b"stale prior output")
    stale_hash = _sha256(target)
    seed_builtins(database)
    adapter = FabricRoleAdapter(
        database,
        owned_root=root,
        inner=FakeHermesAdapter(working_root=root),
        structured_only=True,
    )
    payload = {
        "target": "delivery/repaired.pdf",
        "title": "Repair proof",
        "pages": ["The stale output was preserved and replaced."],
    }

    failed = adapter.dispatch(
        _artifact_manifest(role="builder", attempt=1, payload=payload),
        heartbeat=lambda: None,
    )
    repaired = adapter.dispatch(
        _artifact_manifest(role="repair", attempt=2, payload=payload),
        heartbeat=lambda: None,
    )
    verified = adapter.dispatch(
        _artifact_manifest(role="verifier", attempt=2, payload=payload),
        heartbeat=lambda: None,
    )

    assert failed.status == "FAILED"
    assert repaired.status == "COMPLETED"
    assert verified.status == "COMPLETED"
    assert verified.verdict == "PASS"
    assert _sha256(target) != stale_hash
    backups = list(
        (root / ".jarvis-general-work" / "repairs").rglob("*.bak")
    )
    assert len(backups) == 1
    assert _sha256(backups[0]) == stale_hash
    assert database.event_count(event_type="system.action_started") == 2
    assert database.event_count(
        event_type="general_work.output_repaired"
    ) == 1
    assert database.event_count(
        event_type="general_work.artifact_verification_observed"
    ) == 1


def test_repair_reconciles_landed_frozen_output_without_second_write(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    seed_builtins(database)
    adapter = FabricRoleAdapter(
        database,
        owned_root=root,
        inner=FakeHermesAdapter(working_root=root),
        structured_only=True,
    )
    payload = {
        "target": "delivery/repaired.pdf",
        "title": "Reconciliation proof",
        "pages": ["The artifact landed before the injected interruption."],
        "fault_after_write": True,
    }

    frozen = adapter.dispatch(
        _artifact_manifest(role="builder", attempt=1, payload=payload),
        heartbeat=lambda: None,
    )
    target = root / "delivery" / "repaired.pdf"
    landed_hash = _sha256(target)
    reconciled = adapter.dispatch(
        _artifact_manifest(role="repair", attempt=2, payload=payload),
        heartbeat=lambda: None,
    )

    assert frozen.status == "FAILED"
    assert reconciled.status == "COMPLETED"
    assert _sha256(target) == landed_hash
    assert database.event_count(event_type="system.action_started") == 1
    assert database.event_count(event_type="effect.reconciled") == 1
    assert database.event_count(
        event_type="general_work.output_repaired"
    ) == 0


def test_pptx_verification_accepts_layout_line_wrapping(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    seed_builtins(database)
    adapter = FabricRoleAdapter(
        database,
        owned_root=root,
        inner=FakeHermesAdapter(working_root=root),
        structured_only=True,
    )
    body = (
        "Prepare a verified account package with an editable workbook, "
        "written report, printable copy, and presentation"
    )
    payload = {
        "target": "delivery/wrapped-title.pptx",
        "slides": [
            {
                "layout": "title",
                "title": "Account package",
                "body": body,
            }
        ],
    }
    manifest = ContextManifest(
        run_id="general-work-pptx-wrapping",
        role="builder",
        mission_id="mission-general-work-pptx-wrapping",
        step_id="step-general-work-pptx-wrapping",
        resource_id="resource-general-work-repair",
        capability="cap.artifact.pptx.create",
        provider="fabric",
        model="local",
        isolation_group="general-work-pptx-wrapping",
        instructions=json.dumps(payload, sort_keys=True),
        expected_artifacts=("delivery/wrapped-title.pptx",),
        verification_criteria=("PPTX retains semantically identical text",),
    )

    result = adapter.dispatch(manifest, heartbeat=lambda: None)

    assert result.status == "COMPLETED"
    assert result.verdict == "PASS"


def test_title_and_content_pptx_uses_safe_explicit_geometry(
    tmp_path: Path,
) -> None:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    root = tmp_path / "owned"
    root.mkdir()
    connector = LocalArtifactConnector(root)
    connector.create_pptx(
        slides=[
            {
                "layout": "title_and_content",
                "title": "Work package summary",
                "body": ["Records: 4", "Captured sources: 2"],
            }
        ],
        target_ref="delivery/summary.pptx",
        idempotency_key="general-work:pptx-safe-geometry",
    )

    presentation = Presentation(root / "delivery" / "summary.pptx")
    slide = presentation.slides[0]
    title_shape = slide.shapes.title
    body_shape = slide.placeholders[1]

    assert title_shape is not None
    assert title_shape.top == Inches(0.45)
    assert title_shape.height == Inches(0.75)
    assert title_shape.text_frame.paragraphs[0].font.size == Pt(28)
    assert body_shape.top == Inches(1.55)
    assert body_shape.height == Inches(4.9)


def test_xlsx_normalizes_only_proven_mixed_numeric_columns(
    tmp_path: Path,
) -> None:
    from openpyxl import load_workbook

    database = _database(tmp_path)
    root = tmp_path / "owned"
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    source = inputs / "mixed.json"
    source.write_text(
        json.dumps(
            {
                "records": [
                    {"Account": "0012", "Value": "125000"},
                    {"Account": "34", "Value": 46000},
                ]
            }
        ),
        encoding="utf-8",
    )
    result = run_workflow(
        database,
        ExecutorContext(database=database, owned_root=root),
        TaskIntent(
            task_id="general-work.fabric.mixed-numeric-xlsx",
            required_actions=("artifact.xlsx.create",),
            unattended=True,
        ),
        (
            WorkflowStep(
                "workbook",
                "artifact.xlsx.create",
                {
                    "source": "inputs/mixed.json",
                    "expected_source_sha256": _sha256(source),
                    "target": "delivery/mixed.xlsx",
                },
            ),
        ),
    )

    assert result["success"] is True
    assert result["context"]["workbook"]["readback_verified"] is True
    workbook = load_workbook(
        root / "delivery" / "mixed.xlsx",
        read_only=False,
        data_only=False,
        keep_links=False,
    )
    try:
        worksheet = workbook["Data"]
        assert worksheet["A2"].value == "0012"
        assert worksheet["A3"].value == "34"
        assert worksheet["B2"].value == 125000
        assert worksheet["B3"].value == 46000
        assert worksheet["B2"].number_format == "#,##0"
        assert worksheet["B3"].number_format == "#,##0"
    finally:
        workbook.close()


def test_personal_operator_pptx_lineage_digest_remains_intact() -> None:
    """Preserve semantic input db4ad1d without blindly merging its branch."""

    digest = "a" * 64
    wrapped = LocalArtifactConnector._pptx_body_lines(
        f"PDF SHA-256: {digest}"
    )

    assert all(len(line) <= 54 or line == digest for line in wrapped)
    assert digest in wrapped
    assert "".join(wrapped).replace("PDF SHA-256:", "").replace(" ", "") == digest
