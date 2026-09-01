"""Run the bounded V10-B Golden Journey 2 against one registered local source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jarvis_foundry.connectors.filesystem import FilesystemConnector
from jarvis_foundry.connectors.local_artifacts import (
    LocalArtifactError,
    installed_library_versions,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric import ExecutorContext, TaskIntent, WorkflowStep, run_workflow
from jarvis_foundry.world_model.facade import WorldModel


EXPECTED_COLUMNS = (
    "Priority",
    "Lead Readiness",
    "Company",
    "Category",
    "City",
    "State",
    "Website",
    "Phone",
    "Email",
    "Contactability",
    "Deterministic Score",
    "Recommended Service",
    "Why This Business Is An Opportunity",
    "Best Sales Angle",
    "How to Approach",
    "What to Say",
    "Key Talking Points",
    "Evidence Supporting the Pitch",
    "Recommended Next Action",
    "Suggested Call Opening",
    "Suggested Email Subject",
    "Suggested Email Message",
    "Likely Objection",
    "Suggested Objection Response",
    "Missing or Uncertain Information",
    "AI Confidence",
    "Review Reason",
    "Contact Status",
    "Follow-up Date",
    "Lead ID",
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--sheet", default="Sales Worklist")
    return parser.parse_args()


def _public_result(result: dict[str, Any], observation: dict[str, Any] | None) -> dict[str, Any]:
    summary = result.get("context", {}).get("summary", {})
    report = result.get("context", {}).get("report", {})
    return {
        "verdict": "PASS" if result.get("success") else "BLOCKED",
        "task_id": result.get("task_id"),
        "success": bool(result.get("success")),
        "source": {
            "source_ref": summary.get("source_ref"),
            "source_sha256": summary.get("source_sha256"),
            "format": summary.get("format"),
            "sheet": summary.get("sheet_name"),
            "headers": summary.get("headers"),
            "row_count": summary.get("row_count"),
            "column_count": summary.get("column_count"),
        },
        "calculation": {
            "group_by": summary.get("group_by"),
            "group_counts": summary.get("group_counts"),
            "sum_column": summary.get("sum_column"),
            "sum_value": summary.get("sum_value"),
            "sum_value_count": summary.get("sum_value_count"),
            "missing_by_column": summary.get("missing_by_column"),
        },
        "artifact": {
            "target_ref": report.get("target_ref"),
            "output_sha256": report.get("output_sha256"),
            "size_bytes": report.get("size_bytes"),
            "readback_verified": report.get("readback_verified"),
            "reconciled": report.get("reconciled"),
            "replayed": report.get("replayed"),
        },
        "world_report": observation or {"status": "NOT_EMITTED"},
        "failure": [
            {
                "step_id": item.get("step_id"),
                "claimed": item.get("claimed"),
                "verified": item.get("verified"),
                "note": item.get("note"),
            }
            for item in result.get("results", [])
            if item.get("claimed") != "COMPLETED" or item.get("verified") != "MATCH"
        ],
    }


def main() -> int:
    args = _args()
    source = args.source.expanduser().resolve()
    source_root = args.source_root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    database_path = args.database.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    database = FoundryDatabase(database_path)
    database.initialize()
    context = ExecutorContext(database=database, owned_root=output)
    roots = [str(source_root)]
    intent = TaskIntent(
        task_id="journey-2.real-sales-worklist",
        required_actions=(
            "artifact.table.summary",
            "artifact.docx.create",
            "artifact.docx.verify",
        ),
    )
    steps = (
        WorkflowStep(
            "summary",
            "artifact.table.summary",
            {
                "source": str(source),
                "source_roots": roots,
                "sheet": args.sheet,
                "expected_columns": list(EXPECTED_COLUMNS),
                "group_by": "Lead Readiness",
                "sum_column": "Deterministic Score",
            },
        ),
        WorkflowStep(
            "report",
            "artifact.docx.create",
            {
                "source": str(source),
                "source_roots": roots,
                "sheet": args.sheet,
                "expected_columns": list(EXPECTED_COLUMNS),
                "group_by": "Lead Readiness",
                "sum_column": "Deterministic Score",
                "target": "reports/journey-2-real.docx",
                "idempotency_key": "journey-2:real-sales-worklist",
                "title": "JARVIS V10-B bounded sales worklist report",
            },
        ),
        WorkflowStep(
            "verify",
            "artifact.docx.verify",
            {
                "target": "$report.target_ref",
                "expected_sha256": "$report.output_sha256",
                "required_values": ["$summary.source_sha256", "$summary.row_count"],
                "source_roots": roots,
            },
        ),
    )

    if not source.is_file() or not source_root.is_dir():
        evidence = {
            "verdict": "NOT_TESTED",
            "task_id": intent.task_id,
            "success": False,
            "core_libraries": installed_library_versions(),
            "reason": "owner-designated real input is missing; no synthetic input was created",
            "source": {
                "source_ref": str(source),
                "source_root": str(source_root),
                "source_exists": source.is_file(),
                "source_root_exists": source_root.is_dir(),
            },
            "world_report": {"status": "NOT_EMITTED"},
        }
        proof = FilesystemConnector(output).request(
            "file.write",
            "journey-2-proof.json",
            json.dumps(evidence, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            idempotency_key="journey-2:real-sales-worklist:proof",
        )
        evidence["proof_write"] = {
            "outcome": proof.outcome,
            "target_ref": proof.target_ref,
            "content_hash": (proof.after_state or {}).get("content_hash"),
        }
        print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
        return 0 if proof.outcome == "COMPLETED" else 1

    try:
        result = run_workflow(database, context, intent, steps)
    except LocalArtifactError as error:
        result = {
            "task_id": intent.task_id,
            "success": False,
            "results": [{"step_id": "setup", "claimed": "BLOCKED", "verified": "UNKNOWN", "note": str(error)}],
            "context": {},
        }

    observation: dict[str, Any] | None = None
    if result.get("success"):
        summary = result["context"]["summary"]
        report = result["context"]["report"]
        observation = WorldModel(database).observe(
            "artifact.journey_2",
            intent.task_id,
            {
                "status": "COMPLETED",
                "source_sha256": summary["source_sha256"],
                "output_sha256": report["output_sha256"],
                "target_ref": report["target_ref"],
                "row_count": summary["row_count"],
                "next_action": "review the verified local report",
            },
            provenance={"task_id": intent.task_id, "source_root": str(source_root)},
            observation_id="observation:journey-2:real-sales-worklist",
            idempotency_key="journey-2:real-sales-worklist:world-report",
        )

    evidence = _public_result(result, observation)
    evidence["core_libraries"] = installed_library_versions()
    proof_text = json.dumps(evidence, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    proof = FilesystemConnector(output).request(
        "file.write",
        "journey-2-proof.json",
        proof_text,
        idempotency_key="journey-2:real-sales-worklist:proof",
    )
    evidence["proof_write"] = {
        "outcome": proof.outcome,
        "target_ref": proof.target_ref,
        "content_hash": (proof.after_state or {}).get("content_hash"),
    }
    print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
    return 0 if result.get("success") and proof.outcome == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
