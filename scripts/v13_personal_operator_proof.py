"""Run the disposable V13 personal-operator product proof.

The proof root is a fresh, explicitly named local directory. User-facing
artifacts and compact evidence are copied to a separate fresh evidence
directory; databases, journals, and process registries remain disposable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from jarvis_foundry.connectors.local_artifacts import (
    ArtifactParseError,
    InjectedArtifactFailure,
    LocalArtifactConnector,
    installed_library_versions,
)
from jarvis_foundry.connectors.personal_operator import (
    InjectedOperatorFailure,
    PersonalOperatorConnector,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    execute_action,
    seed_builtins,
)
from jarvis_foundry.platform.windows_subprocess import (
    headless_popen_kwargs,
    pid_alive,
)
from jarvis_foundry.supervisor.worker import (
    process_creation_identity,
)


BASE_SHA = "afddb8d5ca3d5c3459a4f5394e84affb92d13738"
BRANCH = "candidate/v13-personal-operator-20260903"
SCHEMA = "jarvis.v13.personal_operator.product_proof.v1"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prefixed_sha256(path: Path) -> str:
    return f"sha256:{_sha256(path)}"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _require_receipt(
    receipt: Mapping[str, Any],
    *,
    status: str = "COMPLETED",
    label: str,
) -> dict[str, Any]:
    if receipt.get("status") != status:
        raise RuntimeError(f"{label} returned {receipt.get('status')}: {receipt}")
    return dict(receipt)


def _wait_identity(pid: int, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    identity: dict[str, Any] = {}
    while time.monotonic() < deadline:
        identity = process_creation_identity(pid)
        if identity.get("create_time") is not None and identity.get("exe"):
            return identity
        if not pid_alive(pid):
            break
        time.sleep(0.02)
    raise RuntimeError(f"complete process identity unavailable for PID {pid}: {identity}")


def _same_live_identity(expected: Mapping[str, Any]) -> bool:
    pid = int(expected.get("pid") or 0)
    if pid <= 0 or not pid_alive(pid):
        return False
    actual = process_creation_identity(pid)
    return (
        actual.get("create_time") == expected.get("create_time")
        and str(actual.get("exe") or "").casefold()
        == str(expected.get("exe") or "").casefold()
    )


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        **headless_popen_kwargs(),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _source_state(
    repo_root: Path,
    expected_source_sha: str,
    *,
    expected_branch: str = BRANCH,
) -> dict[str, Any]:
    head = _git(repo_root, "rev-parse", "HEAD")
    branch = _git(repo_root, "branch", "--show-current")
    status = _git(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if head != expected_source_sha:
        raise RuntimeError(
            f"proof source mismatch: HEAD {head}, expected {expected_source_sha}"
        )
    if head == BASE_SHA:
        raise RuntimeError("proof source must be a committed V13 descendant")
    if branch != expected_branch:
        raise RuntimeError(
            f"proof branch mismatch: {branch!r}, expected {expected_branch!r}"
        )
    ancestor = subprocess.run(
        ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", BASE_SHA, head],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
        **headless_popen_kwargs(),
    )
    if ancestor.returncode != 0:
        raise RuntimeError(f"required base {BASE_SHA} is not an ancestor of {head}")
    if status:
        raise RuntimeError(
            "proof requires a clean committed source checkout; dirty entries: "
            + status.replace("\n", " | ")
        )
    verifier = Path(__file__).with_name("v13_independent_verify.py").resolve()
    return {
        "repo_root": str(repo_root),
        "head": head,
        "branch": branch,
        "base_sha": BASE_SHA,
        "tree_sha": _git(repo_root, "rev-parse", "HEAD^{tree}"),
        "proof_script_sha256": _sha256(Path(__file__).resolve()),
        "independent_verifier_sha256": _sha256(verifier),
        "clean_at_start": True,
    }


def _paginate_text(
    text: str,
    *,
    width: int = 88,
    lines_per_page: int = 34,
) -> tuple[str, ...]:
    lines: list[str] = []
    for raw_line in text.splitlines() or [""]:
        wrapped = textwrap.wrap(
            raw_line,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
        )
        lines.extend(wrapped or [""])
    if not lines:
        lines = [""]
    return tuple(
        "\n".join(lines[index:index + lines_per_page])
        for index in range(0, len(lines), lines_per_page)
    )


def _create_chart(path: Path, rows: Sequence[tuple[str, float]]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    if not rows:
        raise ValueError("chart requires at least one derived row")
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1200, 675), "#F7F9FC")
    draw = ImageDraw.Draw(image)
    regular_path = Path("C:/Windows/Fonts/arial.ttf")
    bold_path = Path("C:/Windows/Fonts/arialbd.ttf")
    regular = ImageFont.truetype(str(regular_path), 32)
    small = ImageFont.truetype(str(regular_path), 25)
    bold = ImageFont.truetype(str(bold_path), 46)
    draw.text((72, 54), "Verified open work totals", fill="#17324D", font=bold)
    draw.text(
        (72, 118),
        "Transformed from source.csv with deterministic calculations",
        fill="#4A6073",
        font=small,
    )
    baseline = 560
    colors = ("#2374AB", "#2A9D6F", "#C35D3A", "#6B5CA5")
    max_value = max(value for _, value in rows)
    spacing = 880 / max(1, len(rows))
    bar_width = min(270, int(spacing * 0.62))
    for index, (label, value) in enumerate(rows):
        color = colors[index % len(colors)]
        x = int(150 + index * spacing + (spacing - bar_width) / 2)
        height = int(340 * (value / max_value)) if max_value else 0
        draw.rounded_rectangle(
            (x, baseline - height, x + bar_width, baseline),
            radius=10,
            fill=color,
        )
        draw.text(
            (x + max(8, bar_width // 3), baseline - height - 55),
            f"{value:g}",
            fill="#17324D",
            font=bold,
        )
        draw.text(
            (x + max(4, bar_width // 5), baseline + 18),
            label,
            fill="#17324D",
            font=regular,
        )
    draw.line((120, baseline, 1080, baseline), fill="#93A4B3", width=3)
    image.save(path, format="PNG")


def _terminate_popen(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def _action_runner(
    database: FoundryDatabase,
    context: ExecutorContext,
    records: list[dict[str, Any]],
):
    def run(action_kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = execute_action(
            database,
            context,
            TaskIntent(
                task_id="v13-personal-operator-product-proof",
                required_actions=(action_kind,),
            ),
            action_kind,
            payload,
        )
        record = result.as_dict()
        records.append(record)
        if not result.success:
            raise RuntimeError(
                f"Fabric action {action_kind} failed: "
                f"{result.claimed.value}/{result.verified.value} {result.note}"
            )
        return dict(result.observed)

    return run


def _copy_evidence(
    owned_root: Path,
    evidence_root: Path,
    references: list[str],
) -> None:
    for reference in references:
        source = (owned_root / reference).resolve()
        target = (evidence_root / reference).resolve()
        source.relative_to(owned_root)
        target.relative_to(evidence_root)
        if not source.is_file():
            raise FileNotFoundError(f"evidence source is missing: {reference}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if _sha256(source) != _sha256(target):
            raise RuntimeError(f"evidence copy hash mismatch: {reference}")


def _run_independent(
    verifier: Path,
    manifest: Path,
    output: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(verifier),
            "--manifest",
            str(manifest),
            "--output",
            str(output),
        ],
        cwd=verifier.parent.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        **headless_popen_kwargs(),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"independent verifier failed for {manifest.name}: "
            f"{completed.stdout}\n{completed.stderr}"
        )
    result = json.loads(output.read_text(encoding="utf-8"))
    if result.get("verdict") != "PASS":
        raise RuntimeError(f"independent verifier did not pass: {result}")
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    started_at = _now()
    repo_root = Path(__file__).resolve().parents[1]
    source_state = _source_state(
        repo_root,
        args.source_sha,
        expected_branch=args.expected_branch,
    )
    proof_root = args.root.expanduser().resolve()
    evidence_root = args.evidence_dir.expanduser().resolve()
    if proof_root.exists():
        raise FileExistsError(f"refusing to reuse proof root: {proof_root}")
    if evidence_root.exists():
        raise FileExistsError(f"refusing to overwrite evidence root: {evidence_root}")
    proof_root.mkdir(parents=True)
    owned_root = proof_root / "owned"
    owned_root.mkdir()

    database = FoundryDatabase(proof_root / "foundry.db")
    database.initialize()
    python = Path(sys.executable).resolve()
    base_python = Path(getattr(sys, "_base_executable", python)).resolve()
    pythonw = base_python.with_name("pythonw.exe")
    if os.name == "nt" and not pythonw.is_file():
        raise FileNotFoundError(f"pythonw is required for desktop proof: {pythonw}")
    application = pythonw if pythonw.is_file() else python
    context = ExecutorContext(
        database=database,
        owned_root=owned_root,
        operator_executable_allowlist=(python, application),
        operator_environment_allowlist=("V13_OPERATOR_PROOF_VALUE",),
    )
    seed_builtins(database)

    os.environ["V13_OPERATOR_PROOF_VALUE"] = "bounded-visible"
    operator = PersonalOperatorConnector(
        owned_root,
        executable_allowlist=(python, application),
        environment_allowlist=("V13_OPERATOR_PROOF_VALUE",),
    )
    artifact = LocalArtifactConnector(owned_root)
    fabric_records: list[dict[str, Any]] = []
    run_action = _action_runner(database, context, fabric_records)
    operator_records: dict[str, Any] = {}
    artifact_records: dict[str, Any] = {}
    assertions: dict[str, bool] = {}
    owned_pid: int | None = None
    desktop_pid: int | None = None
    owned_identity: dict[str, Any] = {}
    desktop_identity: dict[str, Any] = {}
    unrelated_identity: dict[str, Any] = {}
    unrelated: subprocess.Popen[bytes] | None = None

    try:
        source_csv = (
            "Name,Status,Quantity,UnitPrice\n"
            "Alpha,open,2,10\n"
            "Beta,closed,3,7.5\n"
            "Gamma,open,4,5\n"
        )
        source_json = {
            "batch": "v13-proof",
            "records": [
                {"Name": "Alpha", "Owner": "Operator Team", "Priority": 1},
                {"Name": "Gamma", "Owner": "Operator Team", "Priority": 2},
            ],
        }
        operator_records["create_csv"] = _require_receipt(
            operator.create_file(
                "inputs/source.csv",
                source_csv,
                idempotency_key="proof:create:source-csv",
            ),
            label="create source CSV",
        )
        operator_records["create_json"] = _require_receipt(
            operator.create_file(
                "inputs/context.json",
                json.dumps(source_json, ensure_ascii=True, indent=2) + "\n",
                idempotency_key="proof:create:context-json",
            ),
            label="create source JSON",
        )
        source_csv_path = owned_root / "inputs" / "source.csv"
        source_json_path = owned_root / "inputs" / "context.json"
        source_csv_sha = _sha256(source_csv_path)
        source_json_sha = _sha256(source_json_path)

        operator_records["inspect_csv"] = _require_receipt(
            operator.inspect_file("inputs/source.csv", preview_bytes=128),
            label="inspect source CSV",
        )
        operator_records["search"] = _require_receipt(
            operator.search(
                name_pattern="*.csv",
                content_query="Alpha",
                start="inputs",
            ),
            label="search source files",
        )
        invalid_path = operator.inspect_file("../outside.txt")
        operator_records["invalid_path"] = invalid_path
        assertions["path_escape_blocked"] = (
            invalid_path.get("status") == "FAILED"
            and (invalid_path.get("error") or {}).get("classification")
            == "path_outside_root"
        )

        operator_records["create_note"] = _require_receipt(
            operator.create_file(
                "notes/operator.txt",
                "state=before\n",
                idempotency_key="proof:create:operator-note",
            ),
            label="create operator note",
        )
        note_path = owned_root / "notes" / "operator.txt"
        note_before = _prefixed_sha256(note_path)
        operator_records["safe_edit"] = _require_receipt(
            operator.safe_text_edit(
                "notes/operator.txt",
                "state=verified\n",
                expected_source_hash=note_before,
                idempotency_key="proof:edit:operator-note",
            ),
            label="safe edit operator note",
        )
        operator_records["copy_note"] = _require_receipt(
            operator.copy_file(
                "notes/operator.txt",
                "staging/operator-copy.txt",
                idempotency_key="proof:copy:operator-note",
            ),
            label="copy operator note",
        )
        operator_records["move_note"] = _require_receipt(
            operator.move_file(
                "staging/operator-copy.txt",
                "organized/operator-moved.txt",
                idempotency_key="proof:move:operator-note",
            ),
            label="move operator note",
        )
        operator_records["rename_note"] = _require_receipt(
            operator.rename_file(
                "organized/operator-moved.txt",
                "organized/operator-status.txt",
                idempotency_key="proof:rename:operator-note",
            ),
            label="rename operator note",
        )

        recovery_path = owned_root / "notes" / "recovery.txt"
        operator_records["create_recovery"] = _require_receipt(
            operator.create_file(
                "notes/recovery.txt",
                "before-recovery\n",
                idempotency_key="proof:create:recovery-note",
            ),
            label="create recovery note",
        )
        recovery_before = _prefixed_sha256(recovery_path)

        def fail_after_replace(stage: str) -> None:
            if stage == "safe_edit.after_replace":
                raise InjectedOperatorFailure("proof crash after atomic replacement")

        interrupted_operator = PersonalOperatorConnector(
            owned_root,
            executable_allowlist=(python, application),
            environment_allowlist=("V13_OPERATOR_PROOF_VALUE",),
            fault_injector=fail_after_replace,
        )
        partial_edit = interrupted_operator.safe_text_edit(
            "notes/recovery.txt",
            "after-recovery\n",
            expected_source_hash=recovery_before,
            idempotency_key="proof:edit:recovery-note",
        )
        operator_records["partial_safe_edit"] = partial_edit
        if partial_edit.get("status") != "UNKNOWN":
            raise RuntimeError(f"safe edit fault was not UNKNOWN: {partial_edit}")
        recovered_edit = PersonalOperatorConnector(
            owned_root,
            executable_allowlist=(python, application),
            environment_allowlist=("V13_OPERATOR_PROOF_VALUE",),
        ).safe_text_edit(
            "notes/recovery.txt",
            "after-recovery\n",
            expected_source_hash=recovery_before,
            idempotency_key="proof:edit:recovery-note",
        )
        operator_records["recovered_safe_edit"] = _require_receipt(
            recovered_edit,
            label="recover safe edit",
        )
        assertions["safe_edit_reconciled"] = bool(
            recovered_edit.get("replayed") and recovered_edit.get("reconciled")
        )

        parsed_json = artifact.parse(
            "inputs/context.json",
            expected_columns=("Name", "Owner", "Priority"),
        )
        if parsed_json.table is None:
            raise RuntimeError("context JSON did not reopen as a table")
        artifact_records["json_parse"] = parsed_json.as_dict()
        transform_payload = {
            "source": "inputs/source.csv",
            "select_columns": ["Name", "Total", "Label"],
            "filters": [{"column": "Status", "op": "eq", "value": "open"}],
            "computed_columns": {
                "Total": {
                    "op": "multiply",
                    "left": {"column": "Quantity"},
                    "right": {"column": "UnitPrice"},
                },
                "Label": {
                    "op": "concat",
                    "values": [
                        {"column": "Name"},
                        {"value": " / "},
                        {"column": "Status"},
                    ],
                },
            },
            "group_by": "Name",
            "sum_column": "Total",
        }
        transformed = run_action("artifact.table.transform", transform_payload)
        if transformed.get("summary", {}).get("sum_value") != "40":
            raise RuntimeError(f"unexpected transformed sum: {transformed}")
        parsed_source = artifact.parse("inputs/source.csv")
        transformed_direct = artifact.transform_table(
            parsed_source,
            select_columns=transform_payload["select_columns"],
            filters=transform_payload["filters"],
            computed_columns=transform_payload["computed_columns"],
        )
        artifact_records["json_export"] = artifact.export_table(
            transformed_direct,
            target_ref="data/transformed.json",
            idempotency_key="proof:export:transformed-json",
        ).as_dict()

        workbook = run_action(
            "artifact.xlsx.create",
            {
                **transform_payload,
                "target": "xlsx/analysis.xlsx",
                "sheet_name": "Work",
                "idempotency_key": "proof:xlsx:create",
            },
        )
        analysis_path = owned_root / "xlsx" / "analysis.xlsx"
        analysis_before_update = _sha256(analysis_path)
        workbook_updated = run_action(
            "artifact.xlsx.update",
            {
                "source": "xlsx/analysis.xlsx",
                "target": "xlsx/analysis-formulas.xlsx",
                "sheet_name": "Work",
                "cells": {
                    "D1": "DoubleTotal",
                },
                "formulas": {
                    "D2": "=B2*2",
                    "D3": "=B3*2",
                },
                "idempotency_key": "proof:xlsx:update",
            },
        )

        report = run_action(
            "artifact.docx.create",
            {
                "source": "xlsx/analysis-formulas.xlsx",
                "target": "docx/report.docx",
                "expected_columns": [
                    "Name",
                    "Total",
                    "Label",
                    "DoubleTotal",
                ],
                "allow_formulas": True,
                "group_by": "Name",
                "sum_column": "Total",
                "title": "V13 Personal Operator Report",
                "idempotency_key": "proof:docx:create",
            },
        )
        report_path = owned_root / "docx" / "report.docx"
        report_before_edit = _sha256(report_path)
        report_final = run_action(
            "artifact.docx.edit",
            {
                "source": "docx/report.docx",
                "target": "docx/report-final.docx",
                "replacements": {
                    "V13 Personal Operator Report":
                    "V13 Personal Operator Verified Report"
                },
                "append_paragraphs": [
                    "Independent readback is required before acceptance."
                ],
                "idempotency_key": "proof:docx:edit",
            },
        )

        formula_workbook = artifact.parse(
            "xlsx/analysis-formulas.xlsx",
            allow_formulas=True,
            expected_columns=("Name", "Total", "Label", "DoubleTotal"),
        )
        if formula_workbook.table is None:
            raise RuntimeError("updated workbook did not reopen as a table")
        derived_rows = tuple(
            (str(row[0]), float(row[1]))
            for row in formula_workbook.table.rows
        )
        report_extracted = artifact.parse("docx/report-final.docx")
        report_text = str(report_extracted.details.get("text") or "")
        for name, total in derived_rows:
            expected_line = f"{name} | {total:g}"
            if expected_line not in report_text:
                raise RuntimeError(
                    f"DOCX report lost derived workbook row {expected_line!r}"
                )
        pdf_pages = _paginate_text(report_text)
        main_pdf_payload = {
            "pages": pdf_pages,
            "target_ref": "pdf/report.pdf",
            "idempotency_key": "proof:pdf:report",
            "title": "V13 Personal Operator Work Packet",
            "source": "docx/report-final.docx",
        }
        try:
            artifact.create_pdf(
                **main_pdf_payload,
                fault_after_write=True,
            )
        except InjectedArtifactFailure as error:
            artifact_records["partial_pdf"] = {
                "status": "UNKNOWN",
                "error_code": "partial_failure",
                "error": str(error),
            }
        else:  # pragma: no cover - the injected boundary must fire
            raise RuntimeError("PDF fault injection did not interrupt verification")
        recovered_pdf = LocalArtifactConnector(owned_root).create_pdf(
            **main_pdf_payload
        )
        artifact_records["recovered_pdf"] = recovered_pdf.as_dict()
        assertions["pdf_reconciled"] = bool(
            recovered_pdf.replayed and recovered_pdf.reconciled
        )

        appendix = run_action(
            "artifact.pdf.create",
            {
                "source": "inputs/context.json",
                "target": "pdf/appendix.pdf",
                "title": "V13 Context Appendix",
                "pages": [
                    "\n".join(
                        " | ".join(
                            str(row.get(header, ""))
                            for header in parsed_json.table.headers
                        )
                        for row in parsed_json.table.row_dicts()
                    )
                ],
                "idempotency_key": "proof:pdf:appendix",
            },
        )
        combined = run_action(
            "artifact.pdf.combine",
            {
                "sources": ["pdf/report.pdf", "pdf/appendix.pdf"],
                "target": "pdf/combined.pdf",
                "idempotency_key": "proof:pdf:combine",
            },
        )
        split = run_action(
            "artifact.pdf.split",
            {
                "source": "pdf/combined.pdf",
                "target": "pdf/split-appendix.pdf",
                "start_page": len(pdf_pages) + 1,
                "end_page": len(pdf_pages) + 1,
                "idempotency_key": "proof:pdf:split",
            },
        )

        parsed_pdf = artifact.parse("pdf/report.pdf")
        pdf_text = str(parsed_pdf.details.get("text") or "")
        pdf_derived_lines: list[str] = []
        for name, total in derived_rows:
            expected_line = f"{name} | {total:g}"
            matched = next(
                (
                    line.strip()
                    for line in pdf_text.splitlines()
                    if expected_line in line
                ),
                None,
            )
            if matched is None:
                raise RuntimeError(
                    f"PDF lost DOCX-derived row {expected_line!r}"
                )
            pdf_derived_lines.append(matched)
        chart_path = owned_root / "assets" / "totals.png"
        _create_chart(chart_path, derived_rows)
        presentation = run_action(
            "artifact.pptx.create",
            {
                "source": "pdf/report.pdf",
                "target": "pptx/briefing.pptx",
                "slides": [
                    {
                        "layout": "title",
                        "title": "V13 Personal Operator",
                        "body": [
                            "Cross-format work packet",
                            f"PDF text SHA-256: {parsed_pdf.details['text_sha256']}",
                        ],
                    },
                    {
                        "layout": "title_and_content",
                        "title": "Verified totals",
                        "body": pdf_derived_lines,
                        "image_ref": "assets/totals.png",
                        "image_box": {
                            "left": 6.2,
                            "top": 2.0,
                            "width": 3.2,
                        },
                    },
                    {
                        "layout": "blank",
                        "title": "Source lineage",
                        "body": [
                            f"PDF SHA-256: {recovered_pdf.sha256}",
                            "Original CSV and intermediate sources preserved.",
                        ],
                    },
                ],
                "idempotency_key": "proof:pptx:create",
            },
        )
        presentation_final = run_action(
            "artifact.pptx.update",
            {
                "source": "pptx/briefing.pptx",
                "target": "pptx/briefing-final.pptx",
                "replacements": {
                    "Cross-format work packet":
                    "Cross-format work packet - independently verified"
                },
                "append_slides": [
                    {
                        "layout": "blank",
                        "title": "Acceptance",
                        "body": [
                            "Artifact contents reopened successfully.",
                            "No source overwrite or path escape observed.",
                        ],
                    }
                ],
                "idempotency_key": "proof:pptx:update",
            },
        )

        organized_sources = {
            "xlsx/analysis-formulas.xlsx": "organized/analysis.xlsx",
            "docx/report-final.docx": "organized/report.docx",
            "pdf/combined.pdf": "organized/report.pdf",
            "pptx/briefing-final.pptx": "organized/briefing.pptx",
        }
        for source_ref, target_ref in organized_sources.items():
            operator_records[f"organize:{target_ref}"] = _require_receipt(
                operator.copy_file(
                    source_ref,
                    target_ref,
                    idempotency_key=f"proof:organize:{target_ref}",
                ),
                label=f"organize {target_ref}",
            )

        command = operator.run_command(
            python,
            ("-c", "print('bounded-system-action')"),
            timeout_seconds=2.0,
            output_limit_bytes=4096,
        )
        operator_records["command"] = _require_receipt(
            command, label="bounded command"
        )
        operator_records["environment"] = _require_receipt(
            operator.environment_info(("V13_OPERATOR_PROOF_VALUE",)),
            label="environment inspection",
        )

        unrelated = subprocess.Popen(
            [str(python), "-c", "import time; time.sleep(120)"],
            cwd=owned_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **headless_popen_kwargs(),
        )
        unrelated_identity = _wait_identity(unrelated.pid)
        launched = run_action(
            "local.process.launch",
            {
                "process_id": "proof-owned-sleeper",
                "executable": str(python),
                "args": ["-c", "import time; time.sleep(120)"],
                "idempotency_key": "proof:process:launch",
            },
        )
        owned_pid = int((launched.get("result") or {})["pid"])
        owned_identity = dict(
            (launched.get("target_identity") or {}).get("creation_identity") or {}
        )
        inspected = run_action(
            "local.process.inspect",
            {"process_id": "proof-owned-sleeper"},
        )
        if (inspected.get("result") or {}).get("matches") is not True:
            raise RuntimeError("owned process identity did not reconcile")
        run_action(
            "local.process.close",
            {
                "process_id": "proof-owned-sleeper",
                "idempotency_key": "proof:process:close",
            },
        )
        assertions["unrelated_process_untouched"] = (
            not _same_live_identity(owned_identity)
            and _same_live_identity(unrelated_identity)
        )

        viewer_script = (
            "import pathlib, sys, tkinter as tk\n"
            "target = pathlib.Path(sys.argv[-1])\n"
            "root = tk.Tk()\n"
            "root.title('JARVIS V13 Owned Viewer')\n"
            "root.geometry('620x260')\n"
            "label = tk.Label(root, text='Owned target opened:\\n' + str(target), "
            "font=('Segoe UI', 15), padx=24, pady=36)\n"
            "label.pack(fill='both', expand=True)\n"
            "root.attributes('-topmost', True)\n"
            "root.after(150, lambda: (root.lift(), root.focus_force()))\n"
            "root.after(600, lambda: root.attributes('-topmost', False))\n"
            "root.after(120000, root.destroy)\n"
            "root.mainloop()\n"
        )
        operator_records["create_viewer"] = _require_receipt(
            operator.create_file(
                "tools/owned_viewer.py",
                viewer_script,
                idempotency_key="proof:create:viewer",
            ),
            label="create owned desktop viewer",
        )
        opened = operator.desktop_open(
            "proof-owned-viewer",
            application,
            "organized",
            args=(str(owned_root / "tools" / "owned_viewer.py"),),
            idempotency_key="proof:desktop:open",
            show_window=True,
        )
        operator_records["desktop_open"] = _require_receipt(
            opened, label="open owned desktop viewer"
        )
        desktop_pid = int((opened.get("result") or {})["pid"])
        desktop_identity = dict(
            (opened.get("target_identity") or {}).get("creation_identity") or {}
        )
        focus: dict[str, Any] | None = None
        focus_deadline = time.monotonic() + 8.0
        while time.monotonic() < focus_deadline:
            focus = operator.focus_process("proof-owned-viewer")
            if focus.get("status") == "COMPLETED":
                break
            time.sleep(0.2)
        if focus is None:
            raise RuntimeError("desktop focus returned no receipt")
        operator_records["desktop_focus"] = _require_receipt(
            focus, label="focus owned desktop viewer"
        )
        closed_viewer = operator.close_process(
            "proof-owned-viewer",
            idempotency_key="proof:desktop:close",
        )
        operator_records["desktop_close"] = _require_receipt(
            closed_viewer, label="close owned desktop viewer"
        )

        system = run_action(
            "local.system.inspect",
            {
                "environment_names": ["V13_OPERATOR_PROOF_VALUE"],
                "process_ids": [os.getpid(), unrelated.pid],
                "applications": [str(python), str(application)],
            },
        )
        if system.get("readback_verified") is not True:
            raise RuntimeError("local system inspection did not verify")

        invalid_json_path = owned_root / "inputs" / "invalid.json"
        invalid_json_path.write_text("{not-json", encoding="utf-8")
        try:
            artifact.parse("inputs/invalid.json")
        except ArtifactParseError as error:
            artifact_records["invalid_json"] = {
                "status": "BLOCKED",
                "error_type": type(error).__name__,
                "error": str(error),
            }
        else:  # pragma: no cover - invalid JSON must not parse
            raise RuntimeError("invalid JSON unexpectedly parsed")

        assertions.update({
            "source_csv_preserved": _sha256(source_csv_path) == source_csv_sha,
            "source_json_preserved": _sha256(source_json_path) == source_json_sha,
            "xlsx_source_preserved": _sha256(analysis_path)
            == analysis_before_update,
            "docx_source_preserved": _sha256(report_path) == report_before_edit,
            "organized_hashes_match": all(
                _sha256(owned_root / source_ref)
                == _sha256(owned_root / target_ref)
                for source_ref, target_ref in organized_sources.items()
            ),
            "no_unexplained_tmp_files": not any(
                path.is_file()
                for path in owned_root.rglob("*.tmp")
            ),
            "desktop_closed": desktop_pid is not None
            and not _same_live_identity(desktop_identity),
            "command_verified": (
                (command.get("result") or {}).get("stdout", "").strip()
                == "bounded-system-action"
                and (command.get("readback") or {}).get("alive") is False
            ),
        })
        if not all(assertions.values()):
            failed = sorted(name for name, passed in assertions.items() if not passed)
            raise RuntimeError(f"product proof assertions failed: {failed}")

        evidence_files = sorted(
            str(path.relative_to(owned_root)).replace("\\", "/")
            for path in owned_root.rglob("*")
            if path.is_file()
        )
        evidence_root.mkdir(parents=True)
        _copy_evidence(owned_root, evidence_root, evidence_files)
        state_root = evidence_root / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        foundry_copy = state_root / "foundry.db"
        shutil.copy2(proof_root / "foundry.db", foundry_copy)
        if _sha256(proof_root / "foundry.db") != _sha256(foundry_copy):
            raise RuntimeError("Foundry database evidence copy hash mismatch")
        verifier = Path(__file__).with_name("v13_independent_verify.py").resolve()
        shutil.copy2(verifier, evidence_root / "tools" / verifier.name)
        shutil.copy2(
            Path(__file__).resolve(),
            evidence_root / "tools" / Path(__file__).name,
        )
        source_state_path = evidence_root / "source-state.json"
        _write_json(source_state_path, source_state)

        operations_path = (
            evidence_root
            / ".jarvis-personal-operator"
            / "operations.json"
        )
        operations_value = json.loads(operations_path.read_text(encoding="utf-8"))
        processes_path = (
            evidence_root
            / ".jarvis-personal-operator"
            / "processes.json"
        )
        safe_backup_ref = str(
            (operator_records["safe_edit"].get("result") or {})["backup_ref"]
        )
        recovery_backup_ref = str(
            (operator_records["recovered_safe_edit"].get("result") or {})[
                "backup_ref"
            ]
        )
        xlsx_cells = {
            "A1": "Name",
            "B1": "Total",
            "C1": "Label",
        }
        for row_index, (name, total) in enumerate(derived_rows, start=2):
            xlsx_cells[f"A{row_index}"] = name
            xlsx_cells[f"B{row_index}"] = total

        artifact_manifest = {
            "root": ".",
            "checks": [
                {
                    "name": "portable-proof-tree",
                    "kind": "tree",
                    "path": ".",
                    "forbidden_globs": ["*.tmp"],
                    "required_paths": [
                        "source-state.json",
                        "state/foundry.db",
                        ".jarvis-fs-idempotency.json",
                        ".jarvis-personal-operator/operations.json",
                        ".jarvis-personal-operator/processes.json",
                        safe_backup_ref,
                        recovery_backup_ref,
                        "tools/v13_independent_verify.py",
                        "tools/v13_personal_operator_proof.py",
                    ],
                },
                {
                    "name": "source-revision",
                    "kind": "json",
                    "path": "source-state.json",
                    "expected_values": {
                        "/head": source_state["head"],
                        "/branch": source_state["branch"],
                        "/base_sha": BASE_SHA,
                        "/tree_sha": source_state["tree_sha"],
                        "/clean_at_start": True,
                    },
                },
                {
                    "name": "verifier-source-hash",
                    "kind": "file_hash",
                    "path": "tools/v13_independent_verify.py",
                    "sha256": source_state["independent_verifier_sha256"],
                },
                {
                    "name": "source-csv-hash",
                    "kind": "file_hash",
                    "path": "inputs/source.csv",
                    "sha256": source_csv_sha,
                },
                {
                    "name": "source-json-hash",
                    "kind": "file_hash",
                    "path": "inputs/context.json",
                    "sha256": source_json_sha,
                },
                {
                    "name": "safe-edit-original",
                    "kind": "file_hash",
                    "path": safe_backup_ref,
                    "sha256": note_before.removeprefix("sha256:"),
                },
                {
                    "name": "restart-recovery-original",
                    "kind": "file_hash",
                    "path": recovery_backup_ref,
                    "sha256": recovery_before.removeprefix("sha256:"),
                },
                {
                    "name": "operation-ledger",
                    "kind": "json",
                    "path": ".jarvis-personal-operator/operations.json",
                    "expected_values": {
                        "/schema": 1,
                        "/operations/proof:edit:operator-note/status": "COMPLETED",
                        "/operations/proof:edit:recovery-note/status": "COMPLETED",
                        "/operations/proof:edit:recovery-note/result/backup_ref":
                            recovery_backup_ref,
                    },
                    "object_key_counts": {
                        "/operations": len(operations_value["operations"]),
                    },
                },
                {
                    "name": "process-registry",
                    "kind": "json",
                    "path": ".jarvis-personal-operator/processes.json",
                    "expected_values": {
                        "/schema": 1,
                        "/processes/proof-owned-sleeper/state": "exited",
                        "/processes/proof-owned-viewer/state": "exited",
                        "/processes/proof-owned-viewer/opened_identity/kind":
                            "directory",
                    },
                    "object_key_counts": {"/processes": 2},
                },
                {
                    "name": "transformed-json",
                    "kind": "json",
                    "path": "data/transformed.json",
                    "required_keys": ["records"],
                    "row_count": 2,
                    "expected_values": {
                        "/records/0/Name": derived_rows[0][0],
                        "/records/0/Total": derived_rows[0][1],
                        "/records/1/Name": derived_rows[1][0],
                        "/records/1/Total": derived_rows[1][1],
                    },
                },
                {
                    "name": "formatted-workbook",
                    "kind": "xlsx",
                    "path": "xlsx/analysis.xlsx",
                    "sheet": "Work",
                    "cells": xlsx_cells,
                    "freeze_panes": "A2",
                    "auto_filter": "A1:C3",
                    "header_bold": True,
                },
                {
                    "name": "formula-workbook",
                    "kind": "xlsx",
                    "path": "xlsx/analysis-formulas.xlsx",
                    "sheet": "Work",
                    "cells": {"D1": "DoubleTotal"},
                    "formulas": {"D2": "=B2*2", "D3": "=B3*2"},
                    "freeze_panes": "A2",
                    "auto_filter": "A1:D3",
                    "header_bold": True,
                },
                {
                    "name": "xlsx-source-preserved",
                    "kind": "file_hash",
                    "path": "xlsx/analysis.xlsx",
                    "sha256": analysis_before_update,
                },
                {
                    "name": "final-docx",
                    "kind": "docx",
                    "path": "docx/report-final.docx",
                    "required_text": [
                        "V13 Personal Operator Verified Report",
                        "Alpha",
                        "Gamma",
                        "Independent readback is required before acceptance.",
                        *[
                            f"{name} | {total:g}"
                            for name, total in derived_rows
                        ],
                    ],
                    "min_paragraphs": 8,
                    "min_tables": 4,
                },
                {
                    "name": "docx-source-preserved",
                    "kind": "file_hash",
                    "path": "docx/report.docx",
                    "sha256": report_before_edit,
                },
                {
                    "name": "main-pdf",
                    "kind": "pdf",
                    "path": "pdf/report.pdf",
                    "page_count": len(pdf_pages),
                    "required_text": [
                        "V13 Personal Operator Work Packet",
                        report_final["output_sha256"],
                        *pdf_derived_lines,
                    ],
                },
                {
                    "name": "combined-pdf",
                    "kind": "pdf",
                    "path": "pdf/combined.pdf",
                    "page_count": len(pdf_pages) + 1,
                    "required_text": [
                        *pdf_derived_lines,
                        derived_rows[0][0],
                        derived_rows[1][0],
                    ],
                },
                {
                    "name": "split-pdf",
                    "kind": "pdf",
                    "path": "pdf/split-appendix.pdf",
                    "page_count": 1,
                    "required_text": [
                        derived_rows[0][0],
                        derived_rows[1][0],
                        "Operator Team",
                    ],
                },
                {
                    "name": "final-presentation",
                    "kind": "pptx",
                    "path": "pptx/briefing-final.pptx",
                    "slide_count": 4,
                    "min_pictures": 1,
                    "required_text": [
                        "V13 Personal Operator",
                        "independently verified",
                        parsed_pdf.details["text_sha256"],
                        recovered_pdf.sha256,
                        *pdf_derived_lines,
                        "Acceptance",
                        "No source overwrite or path escape observed.",
                    ],
                },
                {
                    "name": "organized-xlsx-hash",
                    "kind": "file_hash",
                    "path": "organized/analysis.xlsx",
                    "sha256": workbook_updated["output_sha256"],
                },
                {
                    "name": "organized-docx-hash",
                    "kind": "file_hash",
                    "path": "organized/report.docx",
                    "sha256": report_final["output_sha256"],
                },
                {
                    "name": "organized-pdf-hash",
                    "kind": "file_hash",
                    "path": "organized/report.pdf",
                    "sha256": combined["output_sha256"],
                },
                {
                    "name": "organized-pptx-hash",
                    "kind": "file_hash",
                    "path": "organized/briefing.pptx",
                    "sha256": presentation_final["output_sha256"],
                },
                {
                    "name": "foundry-database",
                    "kind": "sqlite",
                    "path": "state/foundry.db",
                },
            ],
        }
        artifact_manifest_path = evidence_root / "artifact-manifest.json"
        _write_json(artifact_manifest_path, artifact_manifest)

        runtime_manifest = {
            "root": ".",
            "checks": [
                {
                    "name": "owned-process-closed",
                    "kind": "process",
                    "identity": owned_identity,
                    "live": False,
                },
                {
                    "name": "owned-desktop-closed",
                    "kind": "process",
                    "identity": desktop_identity,
                    "live": False,
                },
                {
                    "name": "unrelated-similar-process-untouched",
                    "kind": "process",
                    "identity": unrelated_identity,
                    "live": True,
                },
            ],
        }
        runtime_manifest_path = evidence_root / "runtime-manifest.json"
        _write_json(runtime_manifest_path, runtime_manifest)
        verifier = Path(__file__).with_name("v13_independent_verify.py").resolve()
        artifact_verification = _run_independent(
            verifier,
            artifact_manifest_path,
            evidence_root / "independent-artifact-verification.json",
        )
        runtime_verification = _run_independent(
            verifier,
            runtime_manifest_path,
            evidence_root / "independent-runtime-verification.json",
        )

        _terminate_popen(unrelated)
        assertions["unrelated_cleanup_complete"] = not _same_live_identity(
            unrelated_identity
        )
        cleanup_manifest = {
            "root": ".",
            "checks": [
                {
                    "name": "owned-process-closed",
                    "kind": "process",
                    "identity": owned_identity,
                    "live": False,
                },
                {
                    "name": "owned-desktop-closed",
                    "kind": "process",
                    "identity": desktop_identity,
                    "live": False,
                },
                {
                    "name": "unrelated-process-cleaned",
                    "kind": "process",
                    "identity": unrelated_identity,
                    "live": False,
                },
            ],
        }
        cleanup_manifest_path = evidence_root / "runtime-cleanup-manifest.json"
        _write_json(cleanup_manifest_path, cleanup_manifest)
        cleanup_verification = _run_independent(
            verifier,
            cleanup_manifest_path,
            evidence_root / "independent-runtime-cleanup-verification.json",
        )
        if not assertions["unrelated_cleanup_complete"]:
            raise RuntimeError("unrelated proof process did not clean up exactly")

        if (
            _git(repo_root, "rev-parse", "HEAD") != source_state["head"]
            or subprocess.run(
                ["git", "-C", str(repo_root), "diff", "--quiet", "HEAD", "--"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
                **headless_popen_kwargs(),
            ).returncode != 0
        ):
            raise RuntimeError("tracked source changed during product proof")

        result = {
            "schema": SCHEMA,
            "verdict": "PASS",
            "final_status": "V13_PERSONAL_OPERATOR_READY_CANDIDATE",
            "started_at": started_at,
            "finished_at": _now(),
            "base_sha": source_state["base_sha"],
            "branch": source_state["branch"],
            "source_sha": source_state["head"],
            "source_tree_sha": source_state["tree_sha"],
            "source_clean_at_start": source_state["clean_at_start"],
            "proof_script_sha256": source_state["proof_script_sha256"],
            "independent_verifier_sha256":
                source_state["independent_verifier_sha256"],
            "proof_root": str(proof_root),
            "evidence_root": str(evidence_root),
            "input_sha256": source_csv_sha,
            "library_versions": installed_library_versions(),
            "files": {
                "source_count": 2,
                "organized_outputs": sorted(organized_sources.values()),
                "safe_backup_ref": (
                    operator_records["safe_edit"].get("result") or {}
                ).get("backup_ref"),
                "recovery_backup_ref": recovery_backup_ref,
                "copy_move_rename_verified": True,
            },
            "data": {
                "rows_in": 3,
                "rows_out": transformed["row_count"],
                "sum_total": transformed["summary"]["sum_value"],
                "json_export": artifact_records["json_export"]["target_ref"],
            },
            "xlsx": {
                "created": workbook["target_ref"],
                "updated": workbook_updated["target_ref"],
                "formula_count": workbook_updated["readback"]["formula_count"],
            },
            "docx": {
                "created": report["target_ref"],
                "edited": report_final["target_ref"],
                "extracted_text_sha256":
                    report_extracted.details["text_sha256"],
            },
            "pdf": {
                "created": recovered_pdf.target_ref,
                "combined": combined["target_ref"],
                "split": split["target_ref"],
                "retry_replayed": recovered_pdf.replayed,
                "retry_reconciled": recovered_pdf.reconciled,
                "derived_from_docx_sha256":
                    report_final["output_sha256"],
            },
            "pptx": {
                "created": presentation["target_ref"],
                "updated": presentation_final["target_ref"],
                "slide_count": presentation_final["readback"]["slide_count"],
                "image_count": presentation_final["readback"]["image_count"],
                "derived_from_pdf_sha256": recovered_pdf.sha256,
            },
            "local_system": {
                "bounded_command": command["result"]["stdout"].strip(),
                "owned_pid": owned_pid,
                "unrelated_pid": unrelated.pid,
                "unrelated_untouched": assertions["unrelated_process_untouched"],
            },
            "desktop": {
                "application": str(application),
                "opened_pid": desktop_pid,
                "focused": (
                    operator_records["desktop_focus"].get("result") or {}
                ).get("focused"),
                "closed": assertions["desktop_closed"],
            },
            "assertions": assertions,
            "fabric_actions": fabric_records,
            "operator_receipts": operator_records,
            "artifact_receipts": artifact_records,
            "independent_verifier": {
                "artifact": {
                    "verdict": artifact_verification["verdict"],
                    "checks_passed": artifact_verification["checks_passed"],
                    "checks_total": artifact_verification["checks_total"],
                },
                "runtime": {
                    "verdict": runtime_verification["verdict"],
                    "checks_passed": runtime_verification["checks_passed"],
                    "checks_total": runtime_verification["checks_total"],
                    "verified_at": runtime_verification["verified_at"],
                    "time_bound": True,
                },
                "runtime_cleanup": {
                    "verdict": cleanup_verification["verdict"],
                    "checks_passed": cleanup_verification["checks_passed"],
                    "checks_total": cleanup_verification["checks_total"],
                    "verified_at": cleanup_verification["verified_at"],
                },
            },
        }
        _write_json(evidence_root / "V13_PERSONAL_OPERATOR_PROOF.json", result)
        return result
    finally:
        os.environ.pop("V13_OPERATOR_PROOF_VALUE", None)
        if desktop_identity and _same_live_identity(desktop_identity):
            operator.close_process(
                "proof-owned-viewer",
                idempotency_key="proof:desktop:close",
            )
        if owned_identity and _same_live_identity(owned_identity):
            operator.close_process(
                "proof-owned-sleeper",
                idempotency_key="proof:process:close",
            )
        _terminate_popen(unrelated)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--expected-branch", default=BRANCH)
    return parser.parse_args()


def main() -> int:
    try:
        result = run(_args())
    except Exception as error:  # noqa: BLE001 - proof must fail closed
        sys.stderr.write(f"{type(error).__name__}: {error}\n")
        return 1
    sys.stdout.write(
        json.dumps(
            {
                "verdict": result["verdict"],
                "final_status": result["final_status"],
                "evidence_root": result["evidence_root"],
                "independent_verifier": result["independent_verifier"],
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
