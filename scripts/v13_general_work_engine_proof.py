"""Run disposable V13 General Work Engine assignments and evidence gates.

The proof is deliberately split into three operator-visible phases:

1. ``prepare`` builds every artifact through the existing Campaign,
   MissionLoop, Fabric, and effect journal, but leaves package verifier
   missions pending.
2. ``attest-visual`` validates a reviewer-supplied render manifest and writes
   hash-bound visual evidence beside each artifact.
3. ``finalize`` runs the independent verifier missions and a fresh-process,
   read-only package verification pass.

All assignments live below an explicitly supplied disposable root. The proof
never deploys, promotes, schedules, or touches the live JARVIS installation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_foundry.clock import isoformat
from jarvis_foundry.connectors.local_artifacts import LocalArtifactConnector
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json
from jarvis_foundry.executive.autonomy import (
    create_objective,
    rebuild_executive_state,
)
from jarvis_foundry.executive.campaigns import (
    campaign_dispatchable_mission_ids,
    rebuild_campaign_state,
    record_child_state,
    resume_campaign,
    startup_resume_pass,
)
from jarvis_foundry.fabric.adapter import FabricRoleAdapter
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.general_work import (
    GeneralWorkPlan,
    GeneralWorkPlanner,
    GeneralWorkRequest,
    load_recorded_plan,
    materialize_general_work_plan,
    verify_recorded_package,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.mission.contracts import latest_contract
from jarvis_foundry.mission.loop import (
    BLOCKED_OUTCOME,
    PASSED,
    MissionLoop,
)
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.router import RoleRouter


BRANCH = "candidate/v13-general-work-engine-20260903"
BASE_SHA = "51d2cfcce1be847fd252d1c857105f09f5f2c0e4"
PERSONAL_OPERATOR_INPUT_SHA = "db4ad1d11af14a666ad5b1b6b1be0059d6950da1"
HARD_EXIT = 86
STATE_SCHEMA = "jarvis.v13.general-work-proof-state.v1"
REVIEW_SCHEMA = "jarvis.v13.general-work-visual-review.v1"
EVIDENCE_SCHEMA = "jarvis.v13.general-work-visual-evidence.v1"
REPORT_SCHEMA = "jarvis.v13.general-work-engine-proof.v1"
PROOF_DATE = "2026-09-04"

CAPABILITIES = {
    "cap.file.inspect": "READY",
    "cap.dir.list": "READY",
    "cap.system.diagnose": "READY",
    "cap.system.runtime": "READY",
    "cap.artifact.table.merge": "READY",
    "cap.artifact.xlsx.create": "READY",
    "cap.artifact.docx.compose": "READY",
    "cap.artifact.pdf.create": "READY",
    "cap.artifact.pptx.create": "READY",
    "cap.general_work.verify": "READY",
}


class ProofFailure(RuntimeError):
    """A disposable assignment or proof invariant failed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProofFailure(message)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProofFailure(f"JSON payload must be an object: {path}")
    return value


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        raise ProofFailure(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _source_identity(*, allow_dirty: bool) -> dict[str, Any]:
    source_sha = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    merge_base = _git("merge-base", BASE_SHA, source_sha)
    status = _git("status", "--short")
    _git("cat-file", "-e", f"{PERSONAL_OPERATOR_INPUT_SHA}^{{commit}}")
    _require(branch == BRANCH, f"unexpected branch: {branch}")
    _require(
        merge_base == BASE_SHA,
        f"candidate is not based on exact V12 source {BASE_SHA}",
    )
    _require(SCHEMA_VERSION == 40, f"expected schema 40, got {SCHEMA_VERSION}")
    if not allow_dirty:
        _require(not status, "worktree must be clean for source-bound proof")

    lineage_text = "PDF SHA-256: " + ("a" * 64)
    wrapped = LocalArtifactConnector._pptx_body_lines(lineage_text)
    personal_operator_compatible = (
        ("a" * 64) in wrapped
        and "".join(wrapped)
        .replace("PDF SHA-256:", "")
        .replace(" ", "")
        == ("a" * 64)
    )
    _require(
        personal_operator_compatible,
        "Personal Operator PPTX lineage digest was split or altered",
    )
    return {
        "branch": branch,
        "worktree": str(ROOT),
        "source_sha": source_sha,
        "base_sha": BASE_SHA,
        "merge_base": merge_base,
        "schema_version": SCHEMA_VERSION,
        "worktree_clean_before": not bool(status),
        "main_sha_before": _git("rev-parse", "main"),
        "origin_main_sha_before": _git("rev-parse", "origin/main"),
        "personal_operator_input_sha": PERSONAL_OPERATOR_INPUT_SHA,
        "personal_operator_semantics": {
            "intact_pptx_lineage_digest": personal_operator_compatible,
            "wrapped_lines": wrapped,
        },
    }


def _assert_disposable_root(proof_root: Path) -> None:
    resolved = proof_root.expanduser().resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return
    raise ProofFailure("proof root must be outside the source worktree")


def _assignment_root(proof_root: Path, assignment_id: str) -> Path:
    return proof_root / "assignments" / assignment_id.casefold()


def _owned_root(assignment_root: Path) -> Path:
    return assignment_root / "owned"


def _database_path(assignment_root: Path) -> Path:
    return assignment_root / "foundry.db"


def _open_database(assignment_root: Path) -> FoundryDatabase:
    return FoundryDatabase(_database_path(assignment_root))


def _initialize_assignment(
    assignment_root: Path,
    *,
    assignment_id: str,
    desired_outcome: str,
) -> tuple[FoundryDatabase, Path, str, str]:
    owned = _owned_root(assignment_root)
    owned.mkdir(parents=True)
    database = _open_database(assignment_root)
    database.initialize()
    seed_builtins(database)
    resource_id = f"resource-v13-general-work-{assignment_id.casefold()}"
    objective_id = f"objective-v13-general-work-{assignment_id.casefold()}"
    database.append(
        Event.create(
            "project.registered",
            {
                "resource_id": resource_id,
                "kind": "directory",
                "root_path": str(owned),
                "resolved_root": str(owned.resolve()),
                "baseline": {},
                "capabilities": CAPABILITIES,
                "readiness_state": "READY",
            },
            source_type="proof",
            source_id="v13-general-work-engine-proof",
            idempotency_key=f"project.registered:{resource_id}",
        )
    )
    create_objective(
        database,
        desired_outcome,
        objective_id=objective_id,
        resource_id=resource_id,
        success_criteria=(
            "all requested artifacts are produced through the canonical spine",
            "the independent package verifier passes",
        ),
    )
    return database, owned, objective_id, resource_id


def _mission_loop(
    database: FoundryDatabase,
    owned: Path,
    *,
    stop_path: Path,
    max_repairs: int = 1,
) -> tuple[MissionLoop, FakeHermesAdapter]:
    inner = FakeHermesAdapter(working_root=owned)
    loop = MissionLoop(
        database,
        RoleRouter(),
        FabricRoleAdapter(
            database,
            owned_root=owned,
            inner=inner,
            structured_only=True,
        ),
        max_repairs=max_repairs,
        stop_path=stop_path,
    )
    return loop, inner


def _run_contract(
    database: FoundryDatabase,
    loop: MissionLoop,
    *,
    mission_id: str,
    runtime_step_id: str,
) -> Any:
    contract = latest_contract(
        database,
        mission_id,
        step_id=runtime_step_id,
        allow_invalid=False,
    )
    _require(contract is not None, f"missing contract for {mission_id}")
    return loop.run_until_settled(**contract.runtime_kwargs(database))


def _run_builders(
    database: FoundryDatabase,
    owned: Path,
    materialized: Any,
    *,
    stop_path: Path,
) -> dict[str, Any]:
    loop, inner = _mission_loop(
        database,
        owned,
        stop_path=stop_path,
        max_repairs=1,
    )
    builder_by_mission = {
        step.mission_id: step for step in materialized.steps
    }
    completed: list[str] = []
    outcomes: list[dict[str, Any]] = []
    for round_index in range(len(builder_by_mission) + 4):
        dispatchable = campaign_dispatchable_mission_ids(database)
        current = [
            mission_id
            for mission_id in dispatchable
            if mission_id in builder_by_mission
            and mission_id not in completed
        ]
        if not current:
            break
        for mission_id in current:
            step = builder_by_mission[mission_id]
            outcome = _run_contract(
                database,
                loop,
                mission_id=mission_id,
                runtime_step_id=step.runtime_step_id,
            )
            _require(
                outcome.outcome == PASSED,
                (
                    f"{materialized.plan_id} builder {step.source_step_id} "
                    f"did not pass: {outcome.reason}"
                ),
            )
            completed.append(mission_id)
            outcomes.append(
                {
                    "source_step_id": step.source_step_id,
                    "mission_id": mission_id,
                    "outcome": outcome.outcome,
                    "run_id": outcome.run_id,
                    "verdict_id": outcome.verdict_id,
                }
            )
            startup_resume_pass(
                database,
                stop_path=stop_path,
                resume_id=(
                    f"{materialized.plan_id}:builder:"
                    f"{round_index}:{step.source_step_id}"
                ),
            )
    _require(
        set(completed) == set(builder_by_mission),
        (
            f"{materialized.plan_id} did not execute every builder: "
            f"{sorted(set(builder_by_mission).difference(completed))}"
        ),
    )
    dispatchable = campaign_dispatchable_mission_ids(database)
    _require(
        dispatchable == (materialized.verifier.mission_id,),
        (
            f"{materialized.plan_id} should leave only its verifier "
            f"dispatchable, got {dispatchable}"
        ),
    )
    _require(
        inner.received == [],
        f"{materialized.plan_id} escaped the structured Fabric path",
    )
    return {
        "builder_count": len(completed),
        "outcomes": outcomes,
        "verifier_pending": materialized.verifier.mission_id,
    }


def _prove_stop_resume(
    database: FoundryDatabase,
    owned: Path,
    materialized: Any,
    *,
    stop_path: Path,
) -> dict[str, Any]:
    dispatchable = campaign_dispatchable_mission_ids(database)
    _require(bool(dispatchable), "STOP proof has no dispatchable builder")
    step_by_mission = {
        step.mission_id: step for step in materialized.steps
    }
    mission_id = next(
        mission for mission in dispatchable if mission in step_by_mission
    )
    step = step_by_mission[mission_id]
    loop, _inner = _mission_loop(
        database,
        owned,
        stop_path=stop_path,
        max_repairs=1,
    )
    starts_before = database.event_count(event_type="system.action_started")
    stop_path.write_text("owner STOP for disposable proof\n", encoding="utf-8")
    stopped = startup_resume_pass(
        database,
        stop_path=stop_path,
        resume_id=f"{materialized.plan_id}:stop",
    )
    blocked = _run_contract(
        database,
        loop,
        mission_id=mission_id,
        runtime_step_id=step.runtime_step_id,
    )
    starts_after = database.event_count(event_type="system.action_started")
    _require(stopped.status == "STOPPED", "STOP did not park the campaign")
    _require(blocked.outcome == BLOCKED_OUTCOME, "STOP did not block dispatch")
    _require(
        starts_after == starts_before,
        "STOP allowed a General Work effect to start",
    )
    stop_path.unlink()
    resumed = resume_campaign(
        database,
        materialized.campaign_id,
        stop_path=stop_path,
    )
    startup_resume_pass(
        database,
        stop_path=stop_path,
        resume_id=f"{materialized.plan_id}:resume",
    )
    _require(
        mission_id in campaign_dispatchable_mission_ids(database),
        "resumed campaign did not release the exact stopped builder",
    )
    return {
        "stopped_status": stopped.status,
        "blocked_outcome": blocked.outcome,
        "action_starts_during_stop": starts_after - starts_before,
        "resumed_status": resumed.status,
        "resumed_mission_id": mission_id,
    }


def _prove_time_wait(
    database: FoundryDatabase,
    materialized: Any,
    *,
    stop_path: Path,
) -> dict[str, Any]:
    step = materialized.steps[0]
    starts_before = database.event_count(event_type="system.action_started")
    record_child_state(
        database,
        step.child_id,
        "WAITING",
        wait_kind="TIME",
        wait_until="2026-09-04T12:05:00Z",
        reason="disposable proof waits for a declared processing window",
    )
    before_due = startup_resume_pass(
        database,
        stop_path=stop_path,
        now="2026-09-04T12:00:00Z",
        resume_id=f"{materialized.plan_id}:wait-before-due",
    )
    waiting_child = rebuild_campaign_state(database).children[step.child_id]
    _require(
        waiting_child.status == "WAITING",
        f"time wait was released early: {waiting_child.status}",
    )
    _require(
        step.mission_id not in campaign_dispatchable_mission_ids(database),
        "waiting child remained dispatchable before its due time",
    )
    after_due = startup_resume_pass(
        database,
        stop_path=stop_path,
        now="2026-09-04T12:06:00Z",
        resume_id=f"{materialized.plan_id}:wait-after-due",
    )
    released_child = rebuild_campaign_state(database).children[step.child_id]
    _require(
        step.mission_id in campaign_dispatchable_mission_ids(database),
        "due time wait did not resume the exact child",
    )
    starts_after = database.event_count(event_type="system.action_started")
    _require(
        starts_after == starts_before,
        "WAIT recovery dispatched an effect outside MissionLoop",
    )
    return {
        "wait_kind": waiting_child.wait_kind,
        "wait_until": waiting_child.wait_until,
        "before_due_report": before_due.status,
        "before_due_child": waiting_child.status,
        "after_due_report": after_due.status,
        "after_due_child": released_child.status,
        "action_starts_during_wait": starts_after - starts_before,
        "resumed_mission_id": step.mission_id,
    }


def _plan_a(
    owned: Path,
    *,
    objective_id: str,
    resource_id: str,
) -> tuple[GeneralWorkPlan, tuple[str, ...], tuple[str, ...]]:
    inputs = owned / "inputs"
    inputs.mkdir()
    (inputs / "renewals.csv").write_text(
        "Account Name, Stage ,ARR,Owner,Renewal\n"
        " Acme North ,open,125000,Mira,2026-09-18\n"
        "Blue Harbor, at risk ,84000,Noah,2026-09-22\n"
        "Acme North,open,125000,Mira,2026-09-18\n",
        encoding="utf-8",
    )
    _write_json(
        inputs / "renewals.json",
        {
            "records": [
                {
                    "account": "Cedar Labs",
                    "status": "open",
                    "annual_value": 46000,
                    "owner": "Mira",
                    "renewal_date": "2026-09-27",
                },
                {
                    "account": "Delta Works",
                    "status": "closed",
                    "annual_value": 39000,
                    "owner": "Noah",
                    "renewal_date": "2026-09-11",
                },
            ]
        },
    )
    planning = GeneralWorkPlanner(owned).plan(
        GeneralWorkRequest(
            objective_id=objective_id,
            resource_id=resource_id,
            desired_outcome=(
                "Prepare a verified renewal package with an editable workbook, "
                "written report, printable copy, and presentation"
            ),
            source_paths=(
                "inputs/renewals.csv",
                "inputs/renewals.json",
            ),
            requested_formats=("xlsx", "docx", "pdf", "pptx"),
            output_stem="renewal-portfolio",
            title="Renewal Portfolio Review",
            column_aliases={
                "account name": "Account",
                "account": "Account",
                "stage": "Status",
                "status": "Status",
                "arr": "Annual Value",
                "annual_value": "Annual Value",
                "renewal": "Renewal Date",
                "renewal_date": "Renewal Date",
            },
            select_columns=(
                "Account",
                "Status",
                "Annual Value",
                "Owner",
                "Renewal Date",
            ),
            deduplicate_by=("Account",),
            sort_by=("Account",),
            source_column="Source File",
            quality_requirements=(
                "deduplicate repeated accounts",
                "retain renewal value and ownership in every summary",
            ),
            style_requirements={
                "tone": "executive",
                "palette": "navy, teal, and neutral gray",
            },
            visual_verification=True,
        )
    )
    return planning.plan, planning.inferred_formats, planning.rationale


def _plan_b(
    owned: Path,
    *,
    objective_id: str,
    resource_id: str,
) -> tuple[GeneralWorkPlan, tuple[str, ...], tuple[str, ...]]:
    fragments = owned / "fragments"
    fragments.mkdir()
    (fragments / "01_context.txt").write_text(
        "Renewal decision context\n"
        "Portfolio value: 294000\n"
        "Review owner: Mira Chen\n"
        "Decision date: September 18, 2026\n",
        encoding="utf-8",
    )
    (fragments / "02_decisions.md").write_text(
        "# Decisions\n\n"
        "- Prioritize Blue Harbor recovery.\n"
        "- Keep Acme North on the standard renewal path.\n"
        "- Close the Cedar Labs data gap before September 11, 2026.\n",
        encoding="utf-8",
    )
    (fragments / "03_actions.txt").write_text(
        "Actions\n"
        "Noah: schedule Blue Harbor executive call.\n"
        "Mira: validate Cedar Labs commercial owner.\n"
        "Finance: confirm Delta Works booking.\n",
        encoding="utf-8",
    )
    planning = GeneralWorkPlanner(owned).plan(
        GeneralWorkRequest(
            objective_id=objective_id,
            resource_id=resource_id,
            desired_outcome=(
                "Reconstruct the inconsistent text and Markdown fragments "
                "into one structured decision record and printable final copy"
            ),
            source_paths=("fragments",),
            requested_formats=("docx", "pdf"),
            output_stem="renewal-decision-record",
            title="Renewal Decision Record",
            quality_requirements=(
                "retain every named owner, date, decision, and action",
                "keep source fragments unchanged",
            ),
            style_requirements={
                "tone": "operating memo",
                "hierarchy": "title, context, decisions, actions",
            },
            visual_verification=True,
        )
    )
    return planning.plan, planning.inferred_formats, planning.rationale


def _plan_c(
    owned: Path,
    *,
    objective_id: str,
    resource_id: str,
) -> tuple[GeneralWorkPlan, tuple[str, ...], tuple[str, ...]]:
    inputs = owned / "inputs"
    archive = inputs / "archive"
    archive.mkdir(parents=True)
    (inputs / "operations.csv").write_text(
        "Workstream,Owner,Backlog,Risk\n"
        "Billing,Ana,7,medium\n"
        "Onboarding,Leo,3,low\n",
        encoding="utf-8",
    )
    _write_json(
        inputs / "service.json",
        {
            "records": [
                {
                    "workstream": "Renewals",
                    "owner": "Mira",
                    "backlog": 5,
                    "risk": "high",
                }
            ]
        },
    )
    (inputs / "constraints.md").write_text(
        "# Operating constraints\n\n"
        "Billing changes require Finance review.\n"
        "High-risk work must retain an explicit owner.\n",
        encoding="utf-8",
    )
    (archive / "prior-note.txt").write_text(
        "Prior cycle closed four onboarding items.\n",
        encoding="utf-8",
    )
    planning = GeneralWorkPlanner(owned).plan(
        GeneralWorkRequest(
            objective_id=objective_id,
            resource_id=resource_id,
            desired_outcome=(
                "Consolidate the mixed operations directory into a verified "
                "workbook, operating document, and printable control copy"
            ),
            source_paths=("inputs",),
            requested_formats=("xlsx", "docx", "pdf"),
            output_stem="operations-control",
            title="Operations Control Package",
            column_aliases={
                "workstream": "Workstream",
                "owner": "Owner",
                "backlog": "Backlog",
                "risk": "Risk",
            },
            select_columns=("Workstream", "Owner", "Backlog", "Risk"),
            deduplicate_by=("Workstream",),
            sort_by=("Workstream",),
            source_column="Source File",
            quality_requirements=(
                "preserve directory source lineage",
                "surface backlog, owner, and risk consistently",
            ),
            visual_verification=True,
        )
    )
    steps = tuple(
        replace(
            step,
            payload={**dict(step.payload), "fault_after_write": True},
        )
        if step.action_kind == "artifact.pdf.create"
        else step
        for step in planning.plan.steps
    )
    return (
        replace(planning.plan, steps=steps),
        planning.inferred_formats,
        planning.rationale,
    )


def _plan_d(
    owned: Path,
    *,
    objective_id: str,
    resource_id: str,
) -> tuple[GeneralWorkPlan, tuple[str, ...], tuple[str, ...]]:
    inputs = owned / "owner-assignment"
    inputs.mkdir()
    (inputs / "pipeline.csv").write_text(
        "Company,Stage,Value,Next Step,Owner\n"
        "Northstar,Proposal,92000,Send security answers,Mira\n"
        "Orbit Systems,Discovery,61000,Confirm buying committee,Noah\n"
        "Pine Health,Negotiation,148000,Resolve legal redline,Ana\n"
        "Quarry AI,Qualified,43000,Schedule technical workshop,Leo\n",
        encoding="utf-8",
    )
    (inputs / "owner-notes.md").write_text(
        "# Friday owner notes\n\n"
        "Protect Pine Health timing.\n"
        "Northstar is the cleanest near-term close.\n"
        "Do not create slides or a PDF; the operating team needs editable files.\n",
        encoding="utf-8",
    )
    planning = GeneralWorkPlanner(owned).plan(
        GeneralWorkRequest(
            objective_id=objective_id,
            resource_id=resource_id,
            desired_outcome=(
                "Prepare Friday's owner pipeline decision brief as an "
                "editable workbook and written memo, without slides or PDF"
            ),
            source_paths=("owner-assignment",),
            output_stem="friday-pipeline-decision",
            title="Friday Pipeline Decision Brief",
            column_aliases={
                "company": "Company",
                "stage": "Stage",
                "value": "Value",
                "next step": "Next Step",
                "owner": "Owner",
            },
            select_columns=(
                "Company",
                "Stage",
                "Value",
                "Next Step",
                "Owner",
            ),
            sort_by=("Company",),
            quality_requirements=(
                "make the next owner decision visible",
                "retain editable operating detail",
            ),
            style_requirements={
                "tone": "concise owner briefing",
                "density": "compact and scannable",
            },
            visual_verification=True,
        )
    )
    _require(
        planning.inferred_formats == ("xlsx", "docx"),
        (
            "owner-like assignment should infer only XLSX and DOCX, got "
            f"{planning.inferred_formats}"
        ),
    )
    return planning.plan, planning.inferred_formats, planning.rationale


def _event_count_for_target(
    database: FoundryDatabase,
    event_type: str,
    target_ref: str,
) -> int:
    return sum(
        event.event_type == event_type
        and target_ref in canonical_json(event.payload)
        for event in database.events(readonly=True)
    )


def _deliverable_path(owned: Path, relative: str) -> Path:
    return owned / Path(*PurePosixPath(relative).parts)


def _visual_requests(
    assignment_id: str,
    owned: Path,
    plan: GeneralWorkPlan,
) -> list[dict[str, Any]]:
    connector = LocalArtifactConnector(owned)
    requests: list[dict[str, Any]] = []
    for deliverable in plan.deliverables:
        policy = dict(deliverable.metadata).get("visual_verification")
        if not isinstance(policy, Mapping) or not policy.get("required"):
            continue
        evidence_ref = str(policy.get("evidence_path") or "")
        _require(evidence_ref, f"{deliverable.deliverable_id} lacks evidence path")
        artifact_path = _deliverable_path(owned, deliverable.path)
        parsed = connector.parse(deliverable.path, allow_formulas=True)
        expected_frames: int | None = None
        expected_sheet_names: list[str] = []
        frame_kind = "page"
        if deliverable.format == "xlsx":
            from openpyxl import load_workbook

            workbook = load_workbook(
                artifact_path,
                read_only=True,
                data_only=False,
                keep_links=False,
            )
            try:
                expected_sheet_names = list(workbook.sheetnames)
            finally:
                workbook.close()
            expected_frames = len(expected_sheet_names)
            frame_kind = "sheet"
        elif deliverable.format == "pdf":
            expected_frames = int(parsed.details.get("page_count") or 0)
        elif deliverable.format == "pptx":
            expected_frames = int(parsed.details.get("slide_count") or 0)
            frame_kind = "slide"
        requests.append(
            {
                "assignment": assignment_id,
                "deliverable_id": deliverable.deliverable_id,
                "format": deliverable.format,
                "artifact_ref": deliverable.path,
                "artifact_path": str(artifact_path.resolve()),
                "artifact_sha256": _sha256(artifact_path),
                "evidence_ref": evidence_ref,
                "evidence_path": str(
                    _deliverable_path(owned, evidence_ref).resolve()
                ),
                "frame_kind": frame_kind,
                "expected_frames": expected_frames,
                "expected_sheet_names": expected_sheet_names,
            }
        )
    return requests


def _assignment_record(
    assignment_id: str,
    assignment_root: Path,
    plan: GeneralWorkPlan,
    materialized: Any,
    *,
    inferred_formats: Sequence[str],
    rationale: Sequence[str],
    builder: Mapping[str, Any],
    controls: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    database = _open_database(assignment_root)
    owned = _owned_root(assignment_root)
    return {
        "assignment": assignment_id,
        "assignment_root": str(assignment_root.resolve()),
        "owned_root": str(owned.resolve()),
        "database_path": str(_database_path(assignment_root).resolve()),
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "campaign_id": plan.campaign_id,
        "objective_id": plan.objective_id,
        "resource_id": plan.resource_id,
        "inferred_formats": list(inferred_formats),
        "rationale": list(rationale),
        "sources": [
            {
                "source_id": source.source_id,
                "path": source.path,
                "sha256": source.sha256,
                "size_bytes": source.size_bytes,
            }
            for source in plan.sources
        ],
        "deliverables": [
            {
                "deliverable_id": deliverable.deliverable_id,
                "path": deliverable.path,
                "format": deliverable.format,
                "source_ids": list(deliverable.source_ids),
                "producer_step_id": deliverable.producer_step_id,
            }
            for deliverable in plan.deliverables
        ],
        "topological_layers": [
            list(layer) for layer in plan.topological_layers()
        ],
        "builder": dict(builder),
        "verifier": {
            "mission_id": materialized.verifier.mission_id,
            "runtime_step_id": materialized.verifier.runtime_step_id,
        },
        "campaign_status_after_builders": rebuild_campaign_state(
            database
        ).campaigns[plan.campaign_id].status,
        "event_counts": {
            "plan_recorded": database.event_count(
                event_type="general_work.plan_recorded"
            ),
            "targets_claimed": database.event_count(
                event_type="general_work.target_claimed"
            ),
            "actions_started": database.event_count(
                event_type="system.action_started"
            ),
        },
        "controls": dict(controls or {}),
        "visual_requests": _visual_requests(assignment_id, owned, plan),
    }


def _prepare_standard_assignment(
    proof_root: Path,
    assignment_id: str,
    desired_outcome: str,
    planner: Any,
    *,
    prove_stop: bool = False,
) -> dict[str, Any]:
    assignment_root = _assignment_root(proof_root, assignment_id)
    database, owned, objective_id, resource_id = _initialize_assignment(
        assignment_root,
        assignment_id=assignment_id,
        desired_outcome=desired_outcome,
    )
    plan, inferred_formats, rationale = planner(
        owned,
        objective_id=objective_id,
        resource_id=resource_id,
    )
    stop_path = assignment_root / "EMERGENCY_STOP"
    materialized = materialize_general_work_plan(
        database,
        plan,
        stop_path=stop_path,
    )
    controls: dict[str, Any] = {}
    if prove_stop:
        controls["stop_resume"] = _prove_stop_resume(
            database,
            owned,
            materialized,
            stop_path=stop_path,
        )
    builder = _run_builders(
        database,
        owned,
        materialized,
        stop_path=stop_path,
    )
    return _assignment_record(
        assignment_id,
        assignment_root,
        plan,
        materialized,
        inferred_formats=inferred_formats,
        rationale=rationale,
        builder=builder,
        controls=controls,
    )


def _prepare_assignment_c(proof_root: Path) -> dict[str, Any]:
    assignment_id = "C"
    desired_outcome = (
        "Prove mixed directory work, durable restart, WAIT/resume, stale "
        "output repair, and partial-write reconciliation"
    )
    assignment_root = _assignment_root(proof_root, assignment_id)
    database, owned, objective_id, resource_id = _initialize_assignment(
        assignment_root,
        assignment_id=assignment_id,
        desired_outcome=desired_outcome,
    )
    plan, inferred_formats, rationale = _plan_c(
        owned,
        objective_id=objective_id,
        resource_id=resource_id,
    )
    plan_path = assignment_root / "plan.json"
    _write_json(plan_path, plan.to_payload())
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--mode",
        "c-kill",
        "--root",
        str(proof_root),
    ]
    killed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    _require(
        killed.returncode == HARD_EXIT,
        (
            f"assignment C hard-kill returned {killed.returncode}; "
            f"stdout={killed.stdout[-1000:]!r}; "
            f"stderr={killed.stderr[-1000:]!r}"
        ),
    )

    database = _open_database(assignment_root)
    stop_path = assignment_root / "EMERGENCY_STOP"
    materialized = materialize_general_work_plan(
        database,
        plan,
        stop_path=stop_path,
    )
    _require(
        database.event_count(event_type="general_work.plan_recorded") == 1,
        "restart duplicated the recorded General Work plan",
    )
    _require(
        database.event_count(event_type="mission.created")
        == len(plan.steps) + 1,
        "restart duplicated or omitted General Work missions",
    )
    wait = _prove_time_wait(
        database,
        materialized,
        stop_path=stop_path,
    )

    stale = next(
        deliverable
        for deliverable in plan.deliverables
        if deliverable.format == "docx"
    )
    stale_path = _deliverable_path(owned, stale.path)
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_bytes(b"stale owner draft requiring preserved repair")
    stale_hash = _sha256(stale_path)

    builder = _run_builders(
        database,
        owned,
        materialized,
        stop_path=stop_path,
    )
    backups = list(
        (owned / ".jarvis-general-work" / "repairs").rglob("*.bak")
    )
    _require(len(backups) == 1, f"expected one stale backup, got {backups}")
    _require(
        _sha256(backups[0]) == stale_hash,
        "repair backup does not preserve the stale output bytes",
    )
    pdf = next(
        deliverable
        for deliverable in plan.deliverables
        if deliverable.format == "pdf"
    )
    pdf_starts = _event_count_for_target(
        database,
        "system.action_started",
        pdf.path,
    )
    _require(
        pdf_starts == 1,
        f"landed partial PDF was dispatched {pdf_starts} times",
    )
    stale_starts = _event_count_for_target(
        database,
        "system.action_started",
        stale.path,
    )
    _require(
        stale_starts == 2,
        f"stale DOCX repair expected two attempts, got {stale_starts}",
    )
    controls = {
        "restart": {
            "hard_exit": killed.returncode,
            "expected_hard_exit": HARD_EXIT,
            "plan_recorded_events": database.event_count(
                event_type="general_work.plan_recorded"
            ),
            "mission_created_events": database.event_count(
                event_type="mission.created"
            ),
            "materialization_replayed_without_duplicate_missions": True,
        },
        "wait_resume": wait,
        "stale_output_repair": {
            "target": stale.path,
            "stale_sha256": stale_hash,
            "replacement_sha256": _sha256(stale_path),
            "backup_path": str(backups[0].resolve()),
            "backup_sha256": _sha256(backups[0]),
            "action_starts": stale_starts,
        },
        "partial_write_reconciliation": {
            "target": pdf.path,
            "artifact_sha256": _sha256(_deliverable_path(owned, pdf.path)),
            "action_starts": pdf_starts,
            "effect_reconciled_events": database.event_count(
                event_type="effect.reconciled"
            ),
            "duplicate_mutations": 0,
        },
    }
    return _assignment_record(
        assignment_id,
        assignment_root,
        plan,
        materialized,
        inferred_formats=inferred_formats,
        rationale=rationale,
        builder=builder,
        controls=controls,
    )


def _prepare(proof_root: Path, *, allow_dirty: bool) -> dict[str, Any]:
    _assert_disposable_root(proof_root)
    _require(not proof_root.exists(), f"proof root already exists: {proof_root}")
    source = _source_identity(allow_dirty=allow_dirty)
    proof_root.mkdir(parents=True)
    assignments = {
        "A": _prepare_standard_assignment(
            proof_root,
            "A",
            "Messy CSV and JSON to a complete cross-format package",
            _plan_a,
            prove_stop=True,
        ),
        "B": _prepare_standard_assignment(
            proof_root,
            "B",
            "Reconstruct text and Markdown fragments into durable documents",
            _plan_b,
        ),
        "C": _prepare_assignment_c(proof_root),
        "D": _prepare_standard_assignment(
            proof_root,
            "D",
            "Complete a realistic owner pipeline assignment without a template",
            _plan_d,
        ),
    }
    visual_requests = [
        request
        for assignment in assignments.values()
        for request in assignment["visual_requests"]
    ]
    expected_by_format: dict[str, int] = {}
    for request in visual_requests:
        fmt = str(request["format"])
        expected_by_format[fmt] = expected_by_format.get(fmt, 0) + 1
    _require(
        expected_by_format
        == {"docx": 4, "pdf": 3, "pptx": 1, "xlsx": 3},
        f"unexpected visual artifact inventory: {expected_by_format}",
    )
    state = {
        "schema": STATE_SCHEMA,
        "proof_date": PROOF_DATE,
        "phase": "PREPARED_FOR_VISUAL_REVIEW",
        "prepared_at": isoformat(),
        "prepare_pid": os.getpid(),
        "proof_root": str(proof_root.resolve()),
        "source": source,
        "assignments": assignments,
        "visual_review_request": visual_requests,
        "expected_artifact_counts": expected_by_format,
        "gmail": {
            "read": "DEFERRED",
            "send": "DEFERRED",
            "observation": "DISABLED",
        },
        "live_mutated": False,
        "main_or_origin_mutated": False,
    }
    _write_json(proof_root / "proof-state.json", state)
    return state


def _c_kill(proof_root: Path) -> int:
    assignment_root = _assignment_root(proof_root, "C")
    plan = GeneralWorkPlan.from_payload(
        _load_json(assignment_root / "plan.json")
    )
    database = _open_database(assignment_root)
    first_step_id = plan.topological_layers()[0][0]

    def hard_exit(point: str) -> None:
        if point == f"post general_work.step_materialized:{first_step_id}":
            os._exit(HARD_EXIT)

    materialize_general_work_plan(
        database,
        plan,
        stop_path=assignment_root / "EMERGENCY_STOP",
        fault_hook=hard_exit,
    )
    raise ProofFailure("assignment C hard-kill hook was not reached")


def _normalize_review_path(raw: Any, review_root: Path) -> Path:
    _require(isinstance(raw, str) and raw.strip(), "frame path is absent")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = review_root / candidate
    return candidate.expanduser().resolve()


def _attest_visual(
    proof_root: Path,
    *,
    review_manifest_path: Path,
) -> dict[str, Any]:
    state_path = proof_root / "proof-state.json"
    state = _load_json(state_path)
    _require(state.get("schema") == STATE_SCHEMA, "invalid proof state schema")
    _require(
        state.get("phase") == "PREPARED_FOR_VISUAL_REVIEW",
        f"proof is not awaiting visual review: {state.get('phase')}",
    )
    review = _load_json(review_manifest_path)
    _require(review.get("schema") == REVIEW_SCHEMA, "invalid review schema")
    reviewer = review.get("reviewer")
    _require(
        isinstance(reviewer, str) and reviewer.strip(),
        "visual review must identify its reviewer",
    )
    reviewed_at = review.get("reviewed_at")
    _require(
        isinstance(reviewed_at, str) and reviewed_at.strip(),
        "visual review must include reviewed_at",
    )
    raw_items = review.get("items")
    _require(isinstance(raw_items, list), "visual review items must be a list")
    expected = {
        (request["assignment"], request["deliverable_id"]): request
        for request in state["visual_review_request"]
    }
    actual: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw_item in raw_items:
        _require(isinstance(raw_item, Mapping), "review item must be an object")
        key = (raw_item.get("assignment"), raw_item.get("deliverable_id"))
        _require(
            all(isinstance(value, str) and value for value in key),
            "review item identity is invalid",
        )
        _require(key not in actual, f"duplicate visual review item: {key}")
        actual[key] = raw_item
    _require(
        set(actual) == set(expected),
        (
            "visual review item set differs from requested artifacts: "
            f"missing={sorted(set(expected).difference(actual))}, "
            f"extra={sorted(set(actual).difference(expected))}"
        ),
    )

    evidence_records: list[dict[str, Any]] = []
    for key, request in expected.items():
        item = actual[key]
        artifact_path = Path(request["artifact_path"])
        current_hash = _sha256(artifact_path)
        _require(
            current_hash == request["artifact_sha256"],
            f"artifact changed after rendering request: {artifact_path}",
        )
        _require(item.get("status") == "PASS", f"visual review failed: {key}")
        _require(
            item.get("clipping_detected") is False,
            f"visual clipping is not cleared: {key}",
        )
        _require(
            item.get("artifact_sha256") == current_hash,
            f"review is not bound to the current artifact hash: {key}",
        )
        frames = item.get("frames")
        _require(
            isinstance(frames, list) and frames,
            f"visual review has no rendered frames: {key}",
        )
        normalized_frames: list[dict[str, Any]] = []
        labels: list[str] = []
        for index, frame in enumerate(frames, start=1):
            _require(isinstance(frame, Mapping), f"invalid frame in {key}")
            path = _normalize_review_path(
                frame.get("path"),
                review_manifest_path.parent,
            )
            _require(path.is_file(), f"rendered frame is absent: {path}")
            digest = _sha256(path)
            declared_hash = frame.get("sha256")
            if declared_hash is not None:
                _require(
                    declared_hash == digest,
                    f"rendered frame hash mismatch: {path}",
                )
            label = str(frame.get("label") or f"frame:{index}")
            labels.append(label)
            normalized_frames.append(
                {
                    "label": label,
                    "path": str(path),
                    "sha256": digest,
                    "size_bytes": path.stat().st_size,
                }
            )
        expected_frames = request.get("expected_frames")
        if isinstance(expected_frames, int):
            _require(
                len(normalized_frames) == expected_frames,
                (
                    f"{key} reviewed {len(normalized_frames)} frames; "
                    f"expected {expected_frames}"
                ),
            )
        for sheet_name in request.get("expected_sheet_names") or []:
            _require(
                any(f"sheet:{sheet_name}" in label for label in labels),
                f"workbook sheet was not visually reviewed: {sheet_name}",
            )
        evidence = {
            "schema": EVIDENCE_SCHEMA,
            "status": "PASS",
            "assignment": key[0],
            "deliverable_id": key[1],
            "artifact_ref": request["artifact_ref"],
            "artifact_sha256": current_hash,
            "clipping_detected": False,
            "reviewer": reviewer.strip(),
            "reviewed_at": reviewed_at,
            "frames": normalized_frames,
            "review_notes": str(item.get("notes") or ""),
        }
        evidence_path = Path(request["evidence_path"])
        _write_json(evidence_path, evidence)
        evidence_records.append(
            {
                "assignment": key[0],
                "deliverable_id": key[1],
                "evidence_path": str(evidence_path),
                "evidence_sha256": _sha256(evidence_path),
                "reviewed_frames": len(normalized_frames),
            }
        )
    state["phase"] = "VISUALLY_ATTESTED"
    state["visual_attestation"] = {
        "review_manifest_path": str(review_manifest_path.resolve()),
        "review_manifest_sha256": _sha256(review_manifest_path),
        "reviewer": reviewer.strip(),
        "reviewed_at": reviewed_at,
        "evidence": evidence_records,
    }
    _write_json(state_path, state)
    return state


def _independent_verify(
    proof_root: Path,
    *,
    output_path: Path,
) -> dict[str, Any]:
    state = _load_json(proof_root / "proof-state.json")
    results: dict[str, Any] = {}
    for assignment_id, assignment in sorted(state["assignments"].items()):
        database_path = Path(assignment["database_path"])
        database_hash_before = _sha256(database_path)
        database = FoundryDatabase(database_path)
        verification = verify_recorded_package(
            database,
            assignment["plan_id"],
            resource_root=assignment["owned_root"],
        )
        database_hash_after = _sha256(database_path)
        _require(
            verification.status == "PASS",
            (
                f"independent verifier failed assignment {assignment_id}: "
                f"{verification.errors}"
            ),
        )
        _require(
            database_hash_after == database_hash_before,
            f"read-only verifier mutated assignment {assignment_id} database",
        )
        results[assignment_id] = {
            "status": verification.status,
            "verification_hash": verification.verification_hash,
            "source_findings": [
                dict(item) for item in verification.source_findings
            ],
            "artifact_findings": [
                dict(item) for item in verification.artifact_findings
            ],
            "consistency_findings": [
                dict(item) for item in verification.consistency_findings
            ],
            "quality_coverage": [
                dict(item) for item in verification.quality_coverage
            ],
            "visual_status": [
                dict(item) for item in verification.visual_status
            ],
            "artifacts": [dict(item) for item in verification.artifacts],
            "database_sha256_before": database_hash_before,
            "database_sha256_after": database_hash_after,
        }
    payload = {
        "schema": "jarvis.v13.general-work-independent-verifier.v1",
        "verified_at": isoformat(),
        "verifier_pid": os.getpid(),
        "mode": "fresh-process-read-only",
        "status": "PASS",
        "assignments": results,
    }
    _write_json(output_path, payload)
    return payload


def _finalize(proof_root: Path) -> dict[str, Any]:
    state_path = proof_root / "proof-state.json"
    state = _load_json(state_path)
    _require(state.get("schema") == STATE_SCHEMA, "invalid proof state schema")
    _require(
        state.get("phase") == "VISUALLY_ATTESTED",
        f"proof lacks visual attestation: {state.get('phase')}",
    )
    source = state["source"]
    _require(
        _git("rev-parse", "HEAD") == source["source_sha"],
        "source HEAD changed after proof preparation",
    )
    assignment_results: dict[str, Any] = {}
    for assignment_id, assignment in sorted(state["assignments"].items()):
        assignment_root = Path(assignment["assignment_root"])
        owned = Path(assignment["owned_root"])
        database = _open_database(assignment_root)
        plan = load_recorded_plan(database, assignment["plan_id"])
        materialized = materialize_general_work_plan(
            database,
            plan,
            stop_path=assignment_root / "EMERGENCY_STOP",
        )
        _require(
            campaign_dispatchable_mission_ids(database)
            == (materialized.verifier.mission_id,),
            f"assignment {assignment_id} verifier is not the sole ready mission",
        )
        loop, inner = _mission_loop(
            database,
            owned,
            stop_path=assignment_root / "EMERGENCY_STOP",
            max_repairs=0,
        )
        outcome = _run_contract(
            database,
            loop,
            mission_id=materialized.verifier.mission_id,
            runtime_step_id=materialized.verifier.runtime_step_id,
        )
        _require(
            outcome.outcome == PASSED,
            (
                f"assignment {assignment_id} verifier failed: "
                f"{outcome.reason}"
            ),
        )
        startup_resume_pass(
            database,
            stop_path=assignment_root / "EMERGENCY_STOP",
            resume_id=f"{plan.plan_id}:finalize",
        )
        campaign = rebuild_campaign_state(database).campaigns[plan.campaign_id]
        objective = rebuild_executive_state(database).objectives[
            plan.objective_id
        ]
        verification = verify_recorded_package(
            database,
            plan.plan_id,
            resource_root=owned,
        )
        _require(
            verification.status == "PASS",
            (
                f"assignment {assignment_id} final package failed: "
                f"{verification.errors}"
            ),
        )
        _require(
            campaign.status == "COMPLETED",
            f"assignment {assignment_id} campaign is {campaign.status}",
        )
        _require(
            objective.status == "COMPLETED",
            f"assignment {assignment_id} objective is {objective.status}",
        )
        _require(
            inner.received == [],
            f"assignment {assignment_id} verifier escaped structured Fabric",
        )
        assignment_results[assignment_id] = {
            "status": "PASS",
            "campaign_status": campaign.status,
            "objective_status": objective.status,
            "verifier_outcome": outcome.outcome,
            "verification_hash": verification.verification_hash,
            "source_findings": [
                dict(item) for item in verification.source_findings
            ],
            "artifact_findings": [
                dict(item) for item in verification.artifact_findings
            ],
            "consistency_findings": [
                dict(item) for item in verification.consistency_findings
            ],
            "quality_coverage": [
                dict(item) for item in verification.quality_coverage
            ],
            "visual_status": [
                dict(item) for item in verification.visual_status
            ],
            "artifacts": [dict(item) for item in verification.artifacts],
            "controls": assignment["controls"],
        }

    independent_path = proof_root / "independent-verification.json"
    independent = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--mode",
            "independent-verify",
            "--root",
            str(proof_root),
            "--output",
            str(independent_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    _require(
        independent.returncode == 0,
        (
            f"fresh independent verifier failed ({independent.returncode}): "
            f"{independent.stdout[-2000:]}\n{independent.stderr[-2000:]}"
        ),
    )
    independent_payload = _load_json(independent_path)
    _require(
        independent_payload.get("status") == "PASS",
        "fresh independent verifier did not report PASS",
    )
    _require(
        independent_payload.get("verifier_pid") != os.getpid(),
        "independent verifier did not run in a fresh process",
    )

    main_after = _git("rev-parse", "main")
    origin_after = _git("rev-parse", "origin/main")
    worktree_clean_after = not bool(_git("status", "--short"))
    _require(
        main_after == source["main_sha_before"],
        "main changed during disposable proof",
    )
    _require(
        origin_after == source["origin_main_sha_before"],
        "origin/main changed during disposable proof",
    )
    _require(worktree_clean_after, "proof dirtied the source worktree")
    report = {
        "schema": REPORT_SCHEMA,
        "proof_date": PROOF_DATE,
        "verdict": "PASS",
        "final_status": "V13_GENERAL_WORK_ENGINE_READY",
        "finalized_at": isoformat(),
        "finalize_pid": os.getpid(),
        "source": {
            **source,
            "worktree_clean_after": worktree_clean_after,
            "main_sha_after": main_after,
            "origin_main_sha_after": origin_after,
        },
        "general_work_planner": {
            "status": "PASS",
            "assignment_count": len(assignment_results),
            "artifact_counts": state["expected_artifact_counts"],
            "plans": {
                assignment_id: {
                    "plan_id": assignment["plan_id"],
                    "plan_hash": assignment["plan_hash"],
                    "inferred_formats": assignment["inferred_formats"],
                    "topological_layers": assignment["topological_layers"],
                }
                for assignment_id, assignment in state[
                    "assignments"
                ].items()
            },
        },
        "capability_composition": {
            "status": "PASS",
            "authority": (
                "Executive objective/plan -> V12 Campaign -> MissionLoop -> "
                "Capability Fabric -> canonical effect journal"
            ),
            "second_runtime_created": False,
        },
        "artifact_lineage": {
            "status": "PASS",
            "source_preservation": "PRESERVE_ORIGINALS",
            "assignments": {
                assignment_id: assignment["sources"]
                for assignment_id, assignment in state[
                    "assignments"
                ].items()
            },
        },
        "cross_format_consistency": {
            "status": "PASS",
            "assignments": {
                assignment_id: result["consistency_findings"]
                for assignment_id, result in assignment_results.items()
            },
        },
        "quality_verification": {
            "status": "PASS",
            "visual_attestation": state["visual_attestation"],
            "assignments": {
                assignment_id: {
                    "quality_coverage": result["quality_coverage"],
                    "visual_status": result["visual_status"],
                }
                for assignment_id, result in assignment_results.items()
            },
        },
        "repair": assignment_results["C"]["controls"],
        "campaign_integration": {
            "status": "PASS",
            "assignments": {
                assignment_id: {
                    "campaign_status": result["campaign_status"],
                    "objective_status": result["objective_status"],
                    "verifier_outcome": result["verifier_outcome"],
                }
                for assignment_id, result in assignment_results.items()
            },
        },
        "proofs": assignment_results,
        "stop": state["assignments"]["A"]["controls"]["stop_resume"],
        "restart": state["assignments"]["C"]["controls"]["restart"],
        "wait_resume": state["assignments"]["C"]["controls"]["wait_resume"],
        "duplicate_mutations": {
            "status": "PASS",
            "partial_pdf_duplicate_mutations": state["assignments"]["C"][
                "controls"
            ]["partial_write_reconciliation"]["duplicate_mutations"],
            "plan_recorded_events": {
                assignment_id: assignment["event_counts"]["plan_recorded"]
                for assignment_id, assignment in state[
                    "assignments"
                ].items()
            },
        },
        "independent_verifier": {
            "status": independent_payload["status"],
            "path": str(independent_path),
            "sha256": _sha256(independent_path),
            "verifier_pid": independent_payload["verifier_pid"],
            "mode": independent_payload["mode"],
        },
        "gmail": state["gmail"],
        "known_limitations": [
            (
                "Local OOXML generation uses the inherited python-docx, "
                "openpyxl, and python-pptx connectors; visual acceptance is "
                "therefore required and hash-bound."
            ),
            (
                "The engine is bounded to the registered local resource root "
                "and the supported General Work action catalog."
            ),
        ],
        "remaining_blockers": [],
        "live_mutated": False,
        "main_or_origin_mutated": False,
    }
    report_path = proof_root / "final-report.json"
    _write_json(report_path, report)
    state["phase"] = "FINALIZED"
    state["final_report_path"] = str(report_path)
    state["final_report_sha256"] = _sha256(report_path)
    _write_json(state_path, state)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        required=True,
        choices=(
            "prepare",
            "c-kill",
            "attest-visual",
            "finalize",
            "independent-verify",
        ),
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--review-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    proof_root = args.root.expanduser().resolve()

    if args.mode == "prepare":
        payload = _prepare(proof_root, allow_dirty=args.allow_dirty)
    elif args.mode == "c-kill":
        return _c_kill(proof_root)
    elif args.mode == "attest-visual":
        _require(
            args.review_manifest is not None,
            "--review-manifest is required for attest-visual",
        )
        payload = _attest_visual(
            proof_root,
            review_manifest_path=args.review_manifest.expanduser().resolve(),
        )
    elif args.mode == "finalize":
        payload = _finalize(proof_root)
    else:
        _require(
            args.output is not None,
            "--output is required for independent-verify",
        )
        payload = _independent_verify(
            proof_root,
            output_path=args.output.expanduser().resolve(),
        )
    print(
        json.dumps(
            {
                "mode": args.mode,
                "status": payload.get("verdict")
                or payload.get("status")
                or payload.get("phase"),
                "root": str(proof_root),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProofFailure as error:
        print(f"PROOF FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error
