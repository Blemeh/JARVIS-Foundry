"""Fabric integration proof for the V13 personal-operator hands."""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.connectors.local_artifacts import LocalArtifactConnector
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    execute_action,
    get_spec,
    seed_builtins,
)
from jarvis_foundry.platform.windows_subprocess import pid_alive


def _setup(tmp_path: Path) -> tuple[FoundryDatabase, ExecutorContext]:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    context = ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned",
        operator_executable_allowlist=(Path(sys.executable).resolve(),),
        operator_environment_allowlist=("V13_OPERATOR_TEST_VALUE",),
    )
    seed_builtins(database)
    return database, context


def _intent(*actions: str) -> TaskIntent:
    return TaskIntent(task_id="v13-personal-operator-fabric", required_actions=actions)


def _hash(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _terminate_popen(process) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except __import__("subprocess").TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def test_v13_specs_are_additive_and_file_effects_verify(tmp_path: Path) -> None:
    database, context = _setup(tmp_path)
    for capability_id in (
        "cap.file.inspect",
        "cap.file.copy",
        "cap.file.safe_edit",
        "cap.artifact.xlsx.create",
        "cap.artifact.pdf.create",
        "cap.local.process.launch",
        "cap.desktop.open",
    ):
        assert get_spec(database, capability_id) is not None

    created = execute_action(
        database,
        context,
        _intent("file.write"),
        "file.write",
        {"target": "source.txt", "content": "before\n"},
    )
    assert created.success is True
    inspected = execute_action(
        database,
        context,
        _intent("file.inspect"),
        "file.inspect",
        {"target": "source.txt", "preview_bytes": 64},
    )
    assert inspected.success is True
    assert inspected.observed["target_identity"]["sha256"] == _hash(b"before\n")
    searched = execute_action(
        database,
        context,
        _intent("file.search"),
        "file.search",
        {"name_pattern": "*.txt", "content_query": "before"},
    )
    assert searched.success is True
    assert searched.observed["readback"]["match_count"] == 1

    copied = execute_action(
        database,
        context,
        _intent("file.copy"),
        "file.copy",
        {"source": "source.txt", "target": "work/copied.txt"},
    )
    assert copied.success is True
    assert copied.receipt["state"] == "VERIFIED"
    assert copied.observed["target_identity"]["sha256"] == _hash(b"before\n")

    moved = execute_action(
        database,
        context,
        _intent("file.move"),
        "file.move",
        {"source": "work/copied.txt", "target": "work/moved.txt"},
    )
    assert moved.success is True
    renamed = execute_action(
        database,
        context,
        _intent("file.rename"),
        "file.rename",
        {"source": "work/moved.txt", "target": "work/final.txt"},
    )
    assert renamed.success is True

    edited = execute_action(
        database,
        context,
        _intent("file.safe_edit"),
        "file.safe_edit",
        {
            "target": "work/final.txt",
            "new_text": "after\n",
            "expected_source_hash": _hash(b"before\n"),
        },
    )
    assert edited.success is True
    assert edited.observed["target_identity"]["sha256"] == _hash(b"after\n")
    backup = context.owned_root / edited.observed["result"]["backup_ref"]
    assert backup.read_bytes() == b"before\n"

    replay = execute_action(
        database,
        context,
        _intent("file.safe_edit"),
        "file.safe_edit",
        {
            "target": "work/final.txt",
            "new_text": "after\n",
            "expected_source_hash": _hash(b"before\n"),
        },
    )
    assert replay.success is True
    assert replay.receipt["effect_id"] == edited.receipt["effect_id"]
    assert list(
        (context.owned_root / ".jarvis-personal-operator" / "backups").rglob(
            "*.original"
        )
    ) == [backup]


def test_bounded_command_and_invalid_payload_stay_in_fabric_contract(
    tmp_path: Path,
) -> None:
    database, context = _setup(tmp_path)
    command = execute_action(
        database,
        context,
        _intent("local.command.run"),
        "local.command.run",
        {
            "executable": str(Path(sys.executable).resolve()),
            "args": ["-c", "print('fabric-command')"],
            "timeout_seconds": 2.0,
            "output_limit_bytes": 4096,
        },
    )
    assert command.success is True
    assert command.observed["result"]["stdout"].strip() == "fabric-command"
    assert command.observed["readback"]["alive"] is False

    blocked = execute_action(
        database,
        context,
        _intent("file.copy"),
        "file.copy",
        {"source": "missing-target.txt"},
    )
    assert blocked.claimed.value == "BLOCKED"
    assert blocked.observed["error_code"] == "invalid_input"
    assert not blocked.success

    (context.owned_root / "table.csv").write_text(
        "Name,Amount\nAlpha,1\n",
        encoding="utf-8",
    )
    invalid_boolean = execute_action(
        database,
        context,
        _intent("artifact.table.transform"),
        "artifact.table.transform",
        {"source": "table.csv", "allow_formulas": "false"},
    )
    assert invalid_boolean.claimed.value == "BLOCKED"
    assert invalid_boolean.observed["error_code"] == "invalid_input"

    policy_override = execute_action(
        database,
        context,
        _intent("local.command.run"),
        "local.command.run",
        {
            "executable": str(Path(sys.executable).resolve()),
            "args": ["-c", "print('refused')"],
            "executable_allowlist": [str(Path(sys.executable).resolve())],
        },
    )
    assert policy_override.claimed.value == "BLOCKED"
    assert policy_override.observed["error_code"] == "policy_override_refused"

    environment_override = execute_action(
        database,
        context,
        _intent("local.system.inspect"),
        "local.system.inspect",
        {
            "environment_names": ["PATH"],
            "environment_allowlist": ["PATH"],
        },
    )
    assert environment_override.claimed.value == "BLOCKED"
    assert environment_override.observed["error_code"] == "policy_override_refused"

    external = tmp_path / "external"
    external.mkdir()
    (external / "data.csv").write_text("Name,Amount\nA,1\n", encoding="utf-8")
    source_override = execute_action(
        database,
        context,
        _intent("artifact.table.transform"),
        "artifact.table.transform",
        {
            "source": str(external / "data.csv"),
            "source_roots": [str(external)],
        },
    )
    assert source_override.claimed.value == "BLOCKED"
    assert source_override.observed["error_code"] == "policy_override_refused"


def test_v13_artifact_actions_chain_through_fabric_and_reopen(
    tmp_path: Path,
) -> None:
    database, context = _setup(tmp_path)
    source = context.owned_root / "source.csv"
    source.write_text(
        "Name,Status,Quantity,UnitPrice\n"
        "Alpha,open,2,10\n"
        "Beta,closed,3,7.5\n"
        "Gamma,open,4,5\n",
        encoding="utf-8",
    )
    transform = {
        "source": "source.csv",
        "select_columns": ["Name", "Total"],
        "filters": [{"column": "Status", "op": "eq", "value": "open"}],
        "computed_columns": {
            "Total": {
                "op": "multiply",
                "left": {"column": "Quantity"},
                "right": {"column": "UnitPrice"},
            }
        },
        "group_by": "Name",
        "sum_column": "Total",
    }
    transformed = execute_action(
        database,
        context,
        _intent("artifact.table.transform"),
        "artifact.table.transform",
        transform,
    )
    assert transformed.success is True
    assert transformed.receipt["state"] == "VERIFIED"
    assert transformed.observed["row_count"] == 2
    assert transformed.observed["summary"]["sum_value"] == "40"

    workbook = execute_action(
        database,
        context,
        _intent("artifact.xlsx.create"),
        "artifact.xlsx.create",
        {
                **transform,
                "target": "outputs/work.xlsx",
                "sheet_name": "Work",
                "idempotency_key": "fabric-v13-xlsx",
            },
    )
    assert workbook.success is True
    assert workbook.receipt["state"] == "VERIFIED"
    assert workbook.observed["source_sha256"] == transformed.observed[
        "source_sha256"
    ]

    report = execute_action(
        database,
        context,
        _intent("artifact.docx.create"),
        "artifact.docx.create",
        {
            "source": "outputs/work.xlsx",
            "target": "outputs/work.docx",
            "expected_columns": ["Name", "Total"],
            "group_by": "Name",
            "sum_column": "Total",
            "title": "V13 Work Packet",
            "idempotency_key": "fabric-v13-docx",
        },
    )
    assert report.success is True
    report_parsed = LocalArtifactConnector(context.owned_root).parse(
        "outputs/work.docx"
    )
    report_text = str(report_parsed.details["text"])
    assert "Alpha | 20" in report_text
    assert "Gamma | 20" in report_text

    pdf = execute_action(
        database,
        context,
        _intent("artifact.pdf.create"),
        "artifact.pdf.create",
        {
            "source": "outputs/work.docx",
            "target": "outputs/work.pdf",
            "title": "V13 Work Packet",
            "pages": [report_text],
            "idempotency_key": "fabric-v13-pdf",
        },
    )
    assert pdf.success is True
    assert pdf.receipt["state"] == "VERIFIED"
    assert pdf.observed["source_sha256"] == report.observed["output_sha256"]

    appendix = execute_action(
        database,
        context,
        _intent("artifact.pdf.create"),
        "artifact.pdf.create",
        {
            "target": "outputs/appendix.pdf",
            "title": "Appendix",
            "pages": ["Additional evidence"],
            "idempotency_key": "fabric-v13-pdf-appendix",
        },
    )
    assert appendix.success is True
    combined = execute_action(
        database,
        context,
        _intent("artifact.pdf.combine"),
        "artifact.pdf.combine",
        {
            "sources": ["outputs/work.pdf", "outputs/appendix.pdf"],
            "target": "outputs/combined.pdf",
            "idempotency_key": "fabric-v13-pdf-combine",
        },
    )
    assert combined.success is True
    assert combined.receipt["state"] == "VERIFIED"
    split = execute_action(
        database,
        context,
        _intent("artifact.pdf.split"),
        "artifact.pdf.split",
        {
            "source": "outputs/combined.pdf",
            "target": "outputs/split.pdf",
            "start_page": 2,
            "end_page": 2,
            "idempotency_key": "fabric-v13-pdf-split",
        },
    )
    assert split.success is True
    assert split.receipt["state"] == "VERIFIED"
    invalid_split = execute_action(
        database,
        context,
        _intent("artifact.pdf.split"),
        "artifact.pdf.split",
        {
            "source": "outputs/combined.pdf",
            "target": "outputs/invalid-split.pdf",
            "start_page": 1.5,
            "end_page": 2,
        },
    )
    assert invalid_split.claimed.value == "BLOCKED"
    assert not (context.owned_root / "outputs" / "invalid-split.pdf").exists()

    pdf_parsed = LocalArtifactConnector(context.owned_root).parse(
        "outputs/work.pdf"
    )
    pdf_text = str(pdf_parsed.details["text"])
    pdf_rows = [
        line.strip()
        for line in pdf_text.splitlines()
        if "Alpha | 20" in line or "Gamma | 20" in line
    ]
    assert len(pdf_rows) == 2
    presentation = execute_action(
        database,
        context,
        _intent("artifact.pptx.create"),
        "artifact.pptx.create",
        {
            "source": "outputs/work.pdf",
            "target": "outputs/work.pptx",
            "slides": [
                {
                    "layout": "title",
                    "title": "V13 Work Packet",
                    "body": "Verified cross-format chain",
                },
                {
                    "layout": "title_and_content",
                    "title": "Totals",
                    "body": [
                        *pdf_rows,
                        "PDF SHA-256: " + pdf.observed["output_sha256"],
                    ],
                },
            ],
            "idempotency_key": "fabric-v13-pptx",
        },
    )
    assert presentation.success is True
    assert presentation.receipt["state"] == "VERIFIED"
    assert presentation.observed["source_sha256"] == pdf.observed[
        "output_sha256"
    ]

    from docx import Document

    draft = Document()
    draft.add_heading("Draft", level=1)
    draft.add_paragraph("Owner: Old Team")
    draft.save(context.owned_root / "draft.docx")
    edited = execute_action(
        database,
        context,
        _intent("artifact.docx.edit"),
        "artifact.docx.edit",
        {
            "source": "draft.docx",
            "target": "outputs/edited.docx",
            "replacements": {"Old Team": "Operator Team"},
            "append_paragraphs": ["Readback verified."],
            "idempotency_key": "fabric-v13-docx-edit",
        },
    )
    assert edited.success is True
    assert edited.receipt["state"] == "VERIFIED"

    system = execute_action(
        database,
        context,
        _intent("local.system.inspect"),
        "local.system.inspect",
        {
            "process_ids": [os.getpid()],
            "applications": [str(Path(sys.executable).resolve())],
        },
    )
    assert system.success is True
    assert system.observed["processes"]["result"]["processes"][0]["alive"] is True
    assert system.observed["applications"]["result"]["applications"][0][
        "path"
    ] == str(Path(sys.executable).resolve())


def test_post_write_artifact_failure_is_unknown_with_landed_evidence(
    tmp_path: Path,
) -> None:
    database, context = _setup(tmp_path)
    (context.owned_root / "source.csv").write_text(
        "Name,Amount\nAlpha,1\n",
        encoding="utf-8",
    )
    interrupted = execute_action(
        database,
        context,
        _intent("artifact.xlsx.create"),
        "artifact.xlsx.create",
        {
            "source": "source.csv",
            "target": "outputs/interrupted.xlsx",
            "fault_after_write": True,
            "idempotency_key": "fabric-v13-interrupted-xlsx",
        },
    )
    assert interrupted.claimed.value == "UNKNOWN"
    assert interrupted.observed["error_code"] == "partial_failure"
    assert interrupted.observed["target_ref"] == "outputs/interrupted.xlsx"
    assert interrupted.observed["output_sha256"]
    assert interrupted.observed["readback"]["exists"] is True
    assert (context.owned_root / "outputs" / "interrupted.xlsx").is_file()


def test_artifact_verifier_reconstructs_payload_and_rejects_lied_workbook(
    tmp_path: Path,
) -> None:
    from openpyxl import Workbook

    from jarvis_foundry.fabric.personal_operator import _table_digest

    database, context = _setup(tmp_path)
    source = context.owned_root / "source.csv"
    source.write_text(
        "Name,Status,Quantity,UnitPrice\n"
        "Alpha,open,2,10\n"
        "Gamma,open,4,5\n",
        encoding="utf-8",
    )
    payload = {
        "source": "source.csv",
        "select_columns": ["Name", "Total"],
        "computed_columns": {
            "Total": {
                "op": "multiply",
                "left": {"column": "Quantity"},
                "right": {"column": "UnitPrice"},
            }
        },
        "target": "outputs/claimed.xlsx",
        "sheet_name": "Work",
        "idempotency_key": "fabric-v13-lied-workbook",
    }
    connector = LocalArtifactConnector(context.owned_root)
    expected = connector.transform_table(
        connector.parse("source.csv"),
        select_columns=payload["select_columns"],
        computed_columns=payload["computed_columns"],
    )

    target = context.owned_root / "outputs" / "claimed.xlsx"
    target.parent.mkdir(parents=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Work"
    worksheet.append(("Name", "Total"))
    worksheet.append(("Mallory", 999))
    workbook.save(target)
    workbook.close()
    output_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    claimed = {
        "claimed": "COMPLETED",
        "action_kind": "artifact.xlsx.create",
        "target_ref": "outputs/claimed.xlsx",
        "path": str(target),
        "format": "xlsx",
        "sha256": output_sha256,
        "output_sha256": output_sha256,
        "size_bytes": target.stat().st_size,
        "source_ref": expected.source.source_ref,
        "source_sha256": expected.source.sha256,
        "table_sha256": _table_digest(expected),
        "idempotency_key": payload["idempotency_key"],
        "readback": {"verified": True, "sha256": output_sha256},
        "readback_verified": True,
    }

    result = execute_action(
        database,
        context,
        _intent("artifact.xlsx.create"),
        "artifact.xlsx.create",
        payload,
        claim_override=claimed,
    )

    assert result.claimed.value == "COMPLETED"
    assert result.verified.value == "MISMATCH"
    assert result.success is False
    assert result.note == "worker claim diverged from observed effect"


@pytest.mark.skipif(os.name != "nt", reason="exact creation identity is Windows-only")
def test_fabric_owned_process_lifecycle_closes_only_registered_pid(
    tmp_path: Path,
) -> None:
    database, context = _setup(tmp_path)
    executable = Path(sys.executable).resolve()
    unrelated = __import__("subprocess").Popen(
        [str(executable), "-c", "import time; time.sleep(60)"],
        stdin=__import__("subprocess").DEVNULL,
        stdout=__import__("subprocess").DEVNULL,
        stderr=__import__("subprocess").DEVNULL,
    )
    owned_pid = None
    try:
        launched = execute_action(
            database,
            context,
            _intent("local.process.launch"),
            "local.process.launch",
            {
                "process_id": "fabric-owned-sleeper",
                "target": "fabric-owned-sleeper",
                "executable": str(executable),
                "args": ["-c", "import time; time.sleep(60)"],
            },
        )
        assert launched.success is True
        owned_pid = int(launched.observed["result"]["pid"])
        assert owned_pid != unrelated.pid
        assert pid_alive(owned_pid)
        assert pid_alive(unrelated.pid)

        inspected = execute_action(
            database,
            context,
            _intent("local.process.inspect"),
            "local.process.inspect",
            {"process_id": "fabric-owned-sleeper", "target": "fabric-owned-sleeper"},
        )
        assert inspected.success is True
        assert inspected.observed["result"]["matches"] is True

        closed = execute_action(
            database,
            context,
            _intent("local.process.close"),
            "local.process.close",
            {"process_id": "fabric-owned-sleeper", "target": "fabric-owned-sleeper"},
        )
        assert closed.success is True
        assert not pid_alive(owned_pid)
        assert pid_alive(unrelated.pid)
    finally:
        if owned_pid is not None and pid_alive(owned_pid):
            execute_action(
                database,
                context,
                _intent("local.process.close"),
                "local.process.close",
                {
                    "process_id": "fabric-owned-sleeper",
                    "target": "fabric-owned-sleeper",
                },
            )
        _terminate_popen(unrelated)


@pytest.mark.skipif(os.name != "nt", reason="desktop focus is Windows-only")
def test_fabric_desktop_open_focus_and_close_owned_folder(
    tmp_path: Path,
) -> None:
    base_python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    pythonw = base_python.with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("pythonw is unavailable")
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    context = ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned",
        operator_executable_allowlist=(pythonw,),
    )
    seed_builtins(database)
    folder = context.owned_root / "folder"
    folder.mkdir()
    viewer = context.owned_root / "viewer.py"
    viewer.write_text(
        "import pathlib, sys, tkinter as tk\n"
        "target = pathlib.Path(sys.argv[-1])\n"
        "root = tk.Tk()\n"
        "root.title('JARVIS V13 Fabric Viewer')\n"
        "tk.Label(root, text=str(target)).pack(padx=30, pady=30)\n"
        "root.after(30000, root.destroy)\n"
        "root.mainloop()\n",
        encoding="utf-8",
    )
    opened_pid = None
    try:
        opened = execute_action(
            database,
            context,
            _intent("desktop.open"),
            "desktop.open",
            {
                "process_id": "fabric-folder-viewer",
                "application": str(pythonw),
                "target": "folder",
                "args": [str(viewer)],
                "show_window": True,
            },
        )
        assert opened.success is True
        assert opened.observed["source_identity"]["kind"] == "directory"
        opened_pid = int(opened.observed["result"]["pid"])

        time.sleep(1.0)
        focused = execute_action(
            database,
            context,
            _intent("desktop.focus"),
            "desktop.focus",
            {"process_id": "fabric-folder-viewer"},
        )
        assert focused.success is True
        assert focused.observed["result"]["focused"] is True

        closed = execute_action(
            database,
            context,
            _intent("desktop.close"),
            "desktop.close",
            {"process_id": "fabric-folder-viewer"},
        )
        assert closed.success is True
        assert not pid_alive(opened_pid)
    finally:
        if opened_pid is not None and pid_alive(opened_pid):
            execute_action(
                database,
                context,
                _intent("desktop.close"),
                "desktop.close",
                {"process_id": "fabric-folder-viewer"},
            )
