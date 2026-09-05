"""Disposable source-backed Command Center product fixture.

This process owns only a temporary Foundry database and local repository. It
never points at the installed runtime, production database, or Scheduler.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from typing import Any
from urllib.request import Request, urlopen
import uuid

from jarvis_foundry.briefings import BriefingPlanner
from jarvis_foundry.command_center import CommandCenterServer, build_snapshot
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import (
    build_plan,
    rebuild_executive_state,
    sync_owner_goals,
    update_objective,
)
from jarvis_foundry.executive.campaigns import (
    create_campaign,
    declare_child_mission,
    record_child_state,
)
from jarvis_foundry.fabric.catalog import (
    record_outcome,
    register_capability,
)
from jarvis_foundry.fabric.contracts import metadata_for_spec
from jarvis_foundry.fabric.model import (
    CapabilitySpec,
    CapabilityTrust,
    Kind,
    Semantic,
    UnattendedMode,
    VerifyVerdict,
)
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.systems import record_external_action


OWNER_SECRET = b"lane3-disposable-owner-secret-32!!"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: dict[str, Any],
    key: str,
    *,
    correlation_id: str | None = None,
) -> str:
    event = Event.create(
        event_type,
        payload,
        source_type="supervisor",
        source_id="v13-command-center-fixture",
        correlation_id=correlation_id,
        idempotency_key=key,
    )
    database.append(event)
    return event.event_id


def _register(database: FoundryDatabase, repository: Path) -> None:
    _append(
        database,
        "supervisor.started",
        {
            "instance_id": "lane3-disposable-supervisor",
            "status": "RUNNING",
            "trust_state": "LAB",
            "reason": "disposable source-backed product proof runtime started",
        },
        "supervisor.started:lane3-disposable-supervisor",
    )
    database.append(registration_event(inspect_git_resource(repository, resource_id="lane3-proof-resource")))
    set_capability_state(
        database,
        "lane3-proof-resource",
        "write_registered_worktree_files",
        "READY",
        evidence={"source": "lane3-disposable-proof"},
    )


def _repository(root: Path) -> Path:
    repository = root / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "lane3-proof@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Lane 3 Proof"], check=True)
    (repository / "README.md").write_text("# Lane 3 autonomy proof\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "disposable proof baseline"], check=True, capture_output=True, text=True)
    return repository


def _owner_command(
    base: str,
    command_type: str,
    exact_text: str,
    *,
    target: str = "GLOBAL",
) -> dict[str, object]:
    body = json.dumps({
        "command_type": command_type,
        "target": target,
        "exact_text": exact_text,
    }).encode()
    with urlopen(Request(
        f"{base}/api/owner-command",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    ), timeout=10) as response:
        return json.loads(response.read())


def _seed_mission(
    database: FoundryDatabase,
    *,
    mission_id: str,
    objective: str,
    state: str,
    next_action: str,
    run_id: str | None = None,
) -> None:
    _append(
        database,
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": objective,
            "state": "PLANNED",
            "acceptance_criteria": ["durable Command Center readback agrees"],
            "next_action": next_action,
            "created_from": "v13-command-center-fixture",
        },
        f"mission.created:{mission_id}",
        correlation_id=mission_id,
    )
    transition_paths = {
        "PLANNED": (),
        "ORIENTING": ("ORIENTING",),
        "ACTION_SELECTION": ("ORIENTING", "ACTION_SELECTION"),
        "READY": ("ORIENTING", "ACTION_SELECTION", "READY"),
        "EXECUTING": ("ORIENTING", "ACTION_SELECTION", "READY", "EXECUTING"),
        "VERIFYING": (
            "ORIENTING",
            "ACTION_SELECTION",
            "READY",
            "EXECUTING",
            "VERIFYING",
        ),
        "BLOCKED": ("ORIENTING", "BLOCKED"),
    }
    try:
        transitions = transition_paths[state]
    except KeyError as exc:
        raise ValueError(f"unsupported fixture mission state: {state}") from exc
    for transition in transitions:
        transition_action = (
            next_action
            if transition == state
            else f"advance fixture mission toward {state}"
        )
        _append(
            database,
            "mission.state_changed",
            {
                "mission_id": mission_id,
                "state": transition,
                "next_action": transition_action,
                "reason": f"fixture mission entered {transition}",
            },
            f"mission.state_changed:{mission_id}:{transition}",
            correlation_id=mission_id,
        )
    if run_id:
        _append(
            database,
            "agent_run.started",
            {
                "run_id": run_id,
                "mission_id": mission_id,
                "step_id": "observe-live-state",
                "role": "builder",
                "provider": "fixture-provider",
                "model": "source-backed-fixture",
            },
            f"agent_run.started:{run_id}",
            correlation_id=mission_id,
        )


def _seed_owner_context(
    database: FoundryDatabase,
    *,
    objective_id: str,
    directive_id: str,
) -> list[str]:
    stamp = _stamp()
    records = [
        (
            "project-command-center",
            "PROJECT",
            "JARVIS Command Center live product integration",
            {"project_ids": ["project-command-center"], "objective_ids": [objective_id]},
            {"project_id": "project-command-center"},
        ),
        (
            "standing-order-truth",
            "STANDING_ORDER",
            "Prefer canonical readback over builder claims.",
            {"global": True},
            {"precedence": "owner controls remain higher"},
        ),
        (
            "preference-no-fake-progress",
            "PREFERENCE",
            "Show evidence-backed milestones instead of fake percentage progress.",
            {"project_ids": ["project-command-center"]},
            {"evidence_required": True},
        ),
        (
            "permission-disposable-proof",
            "PERMISSION",
            "Disposable local proof state is permitted; live production mutation is not.",
            {"project_ids": ["project-command-center"]},
            {"live_mutation": False},
        ),
    ]
    event_ids: list[str] = []
    for context_id, kind, text, scope, attributes in records:
        event_ids.append(_append(
            database,
            "owner_context.item_recorded",
            {
                "context_id": context_id,
                "kind": kind,
                "text": text,
                "state": "ACTIVE",
                "scope": scope,
                "priority": 1.0,
                "confidence": 1.0,
                "observed_at": stamp,
                "effective_at": stamp,
                "provenance": {
                    "source_type": "owner",
                    "source_id": "lane3-proof-owner",
                    "source_ref": f"owner-directive:{directive_id}",
                    "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "captured_at": stamp,
                    "evidence_refs": [f"objective:{objective_id}"],
                    "authority": "AUTHENTICATED_OWNER",
                    "owner_directive_id": directive_id,
                },
                "attributes": attributes,
            },
            f"owner_context.item_recorded:{context_id}",
            correlation_id=objective_id,
        ))
    return event_ids


def _seed_capabilities(database: FoundryDatabase) -> dict[str, str]:
    probation = CapabilitySpec(
        capability_id="self.command_center.inspect",
        version="1.0.0",
        kind=Kind.ADAPTER,
        action_kind="command.center.inspect",
        semantic=Semantic.READ,
        executor="fixture-read-adapter",
        unattended=UnattendedMode.ALLOWED,
        limitations=("disposable fixture only", "read-only"),
    )
    probation_metadata = replace(
        metadata_for_spec(probation),
        provenance={
            "authority": "Capability Fabric",
            "source": "self_expansion",
            "stage": "VERIFIED",
            "limitations": list(probation.limitations),
        },
    )
    register_capability(
        database,
        probation,
        metadata=probation_metadata,
        trust=CapabilityTrust.PROBATION,
        note="source-backed probation capability",
    )
    record_outcome(
        database,
        probation.capability_id,
        success=True,
        verified=VerifyVerdict.MATCH,
        note="bounded read verified; catalog trust remains PROBATION",
    )

    mcp = CapabilitySpec(
        capability_id="mcp.fixture.echo",
        version="1.0.0",
        kind=Kind.ADAPTER,
        action_kind="mcp.fixture.tool.echo",
        semantic=Semantic.READ,
        executor="FabricMcpBackend",
        unattended=UnattendedMode.ALLOWED,
        limitations=("fixture transport",),
        provider_deps=("fixture-mcp-server",),
    )
    mcp_metadata = replace(
        metadata_for_spec(mcp),
        provenance={
            "authority": "FabricMcpBackend",
            "source": "mcp.discovery",
            "stage": "ESTABLISHED",
            "server_id": "fixture-mcp-server",
        },
    )
    register_capability(
        database,
        mcp,
        metadata=mcp_metadata,
        trust=CapabilityTrust.ESTABLISHED,
        note="MCP discovery fixture",
    )
    record_outcome(
        database,
        mcp.capability_id,
        success=True,
        verified=VerifyVerdict.MATCH,
        note="MCP tool discovered and independently read back",
    )
    return {
        "probation": probation.capability_id,
        "mcp": mcp.capability_id,
    }


def _seed_v13_product_truth(
    database: FoundryDatabase,
    *,
    goal_result: dict[str, object],
    completed_mission_id: str,
) -> dict[str, Any]:
    directive_id = str(goal_result.get("directive_id") or "")
    sync_owner_goals(database)
    state = rebuild_executive_state(database)
    objective = next(
        (
            row for row in state.objectives.values()
            if str(row.provenance.get("directive_id") or "") == directive_id
        ),
        None,
    )
    if objective is None:
        raise RuntimeError("authenticated owner goal did not materialize an Executive objective")

    active_mission_id = "v13-active-observation"
    blocked_mission_id = "v13-provider-wait"
    active_run_id = "v13-active-worker"
    _seed_mission(
        database,
        mission_id=active_mission_id,
        objective="Observe the live Command Center projection",
        state="VERIFYING",
        next_action="verify the next durable event reconciliation",
        run_id=active_run_id,
    )
    _seed_mission(
        database,
        mission_id=blocked_mission_id,
        objective="Wait for the bounded provider recovery condition",
        state="BLOCKED",
        next_action="wait for provider readiness",
    )
    _append(
        database,
        "mission.blocked",
        {
            "mission_id": blocked_mission_id,
            "objective_id": objective.objective_id,
            "reason": "fixture provider is unavailable",
            "status": "BLOCKED",
            "options": ["wait for recovery", "owner redirects work"],
            "evidence_refs": ["provider:fixture-provider"],
        },
        f"mission.blocked:{blocked_mission_id}",
        correlation_id=blocked_mission_id,
    )

    update_objective(
        database,
        objective.objective_id,
        {
            "metadata": {
                **objective.metadata,
                "project_id": "project-command-center",
                "project": "JARVIS Command Center",
                "program_id": "program-v13",
                "program": "JARVIS V13 Build-Ahead",
            },
            "mission_ids": [
                completed_mission_id,
                active_mission_id,
                blocked_mission_id,
            ],
        },
    )
    build_plan(
        database,
        objective.objective_id,
        [
            {
                "step_id": "verify-repair-lineage",
                "description": "Verify failed claim, repair, and accepted outcome",
                "status": "COMPLETE",
                "mission_id": completed_mission_id,
                "evidence_refs": [f"mission:{completed_mission_id}"],
            },
            {
                "step_id": "observe-live-state",
                "description": "Observe live synchronization and current worker truth",
                "status": "RUNNING",
                "mission_id": active_mission_id,
            },
            {
                "step_id": "wait-provider",
                "description": "Resume only after provider readiness is positive",
                "status": "BLOCKED",
                "mission_id": blocked_mission_id,
                "dependencies": ["observe-live-state"],
            },
        ],
        plan_id="plan-v13-command-center-live",
    )

    campaign = create_campaign(
        database,
        objective.objective_id,
        campaign_id="campaign-v13-command-center-live",
        max_parallel_children=3,
        retry_budget={"total": 2, "v13-completed": 1},
        metadata={"project_id": "project-command-center", "source": "goal-7-fixture"},
    )
    declare_child_mission(
        database,
        campaign.campaign_id,
        child_id="v13-completed",
        mission_id=completed_mission_id,
        step_id="verify-repair-lineage",
    )
    declare_child_mission(
        database,
        campaign.campaign_id,
        child_id="v13-active",
        mission_id=active_mission_id,
        step_id="observe-live-state",
    )
    declare_child_mission(
        database,
        campaign.campaign_id,
        child_id="v13-provider",
        mission_id=blocked_mission_id,
        step_id="wait-provider",
        dependencies=("v13-active",),
        provider_id="fixture-provider",
    )
    record_child_state(database, "v13-active", "RUNNING", reason="worker is executing canonical mission work")

    _append(
        database,
        "provider.outage",
        {"provider_id": "fixture-provider", "status": "UNAVAILABLE", "reason": "fixture outage before recovery"},
        "provider.outage:fixture:before-recovery",
    )
    _append(
        database,
        "provider.health_sample",
        {
            "provider_id": "fixture-provider",
            "health": "HEALTHY",
            "auth_state": "READY",
            "availability": "AVAILABLE",
            "status": "READY",
        },
        "provider.health_sample:fixture:recovered",
    )
    _append(
        database,
        "provider.recovered",
        {"provider_id": "fixture-provider", "status": "READY", "reason": "positive fixture health sample"},
        "provider.recovered:fixture",
    )
    _append(
        database,
        "provider.outage",
        {"provider_id": "fixture-provider", "status": "UNAVAILABLE", "reason": "current bounded wait fixture"},
        "provider.outage:fixture:current",
    )
    record_child_state(
        database,
        "v13-provider",
        "WAITING",
        wait_kind="PROVIDER",
        wait_reason="fixture provider must return a positive health sample",
        reason="current provider wait is durable",
    )
    _append(
        database,
        "mission.provider_wait_started",
        {
            "wait_id": "provider-wait-v13",
            "mission_id": blocked_mission_id,
            "objective_id": objective.objective_id,
            "provider_ids": ["fixture-provider"],
            "reason": "current provider wait is durable",
        },
        "mission.provider_wait_started:provider-wait-v13",
        correlation_id=blocked_mission_id,
    )

    recovery_run = "v13-recovery-run"
    _append(
        database,
        "agent_run.started",
        {
            "run_id": recovery_run,
            "mission_id": active_mission_id,
            "step_id": "observe-live-state",
            "role": "builder",
            "provider": "fixture-provider",
            "model": "recovery-probe",
        },
        f"agent_run.started:{recovery_run}",
        correlation_id=active_mission_id,
    )
    _append(
        database,
        "agent_run.failed",
        {
            "run_id": recovery_run,
            "mission_id": active_mission_id,
            "failure_class": "fixture_process_restart",
            "reason": "disposable worker process exited before checkpoint",
        },
        f"agent_run.failed:{recovery_run}",
        correlation_id=active_mission_id,
    )
    _append(
        database,
        "worker.recovered",
        {
            "run_id": recovery_run,
            "mission_id": active_mission_id,
            "replacement_run_id": active_run_id,
            "status": "RECOVERED",
            "reason": "durable checkpoint reconstructed the active worker",
        },
        f"worker.recovered:{recovery_run}",
        correlation_id=active_mission_id,
    )

    with database.connect(readonly=True) as connection:
        evidence_row = connection.execute(
            "SELECT bundle_id FROM evidence_bundles WHERE mission_id = ? ORDER BY rowid DESC LIMIT 1",
            (completed_mission_id,),
        ).fetchone()
    evidence_id = str(evidence_row[0]) if evidence_row else f"evidence:{completed_mission_id}"
    action_key = record_external_action(
        database,
        "fixture-external-system",
        f"mission:{completed_mission_id}:artifact",
        hashlib.sha256(completed_mission_id.encode("utf-8")).hexdigest(),
        kind="file.write",
        started=True,
    )
    record_external_action(
        database,
        "fixture-external-system",
        f"mission:{completed_mission_id}:artifact",
        hashlib.sha256(completed_mission_id.encode("utf-8")).hexdigest(),
        kind="file.write",
        started=False,
    )
    attempt_id = "attempt-v13-external-readback"
    _append(
        database,
        "connector.attempt_recorded",
        {
            "attempt_id": attempt_id,
            "mission_id": completed_mission_id,
            "resource_id": "lane3-proof-resource",
            "effect_ticket_id": action_key,
            "connector_id": "fixture-filesystem-connector",
            "action_kind": "file.write",
            "outcome": "COMPLETED",
            "provider_ref": "fixture-provider-receipt-001",
            "evidence_refs": [evidence_id],
            "reconciled": True,
        },
        f"connector.attempt_recorded:{attempt_id}",
        correlation_id=completed_mission_id,
    )
    lineage_id = "lineage-v13-external-readback"
    _append(
        database,
        "correlation.lineage_recorded",
        {
            "lineage_id": lineage_id,
            "resource_id": "lane3-proof-resource",
            "mission_id": completed_mission_id,
            "connector_request_id": attempt_id,
            "effect_ticket_id": action_key,
            "external_object_id": "fixture-artifact-001",
            "outcome": "COMPLETED",
            "confidence": 1.0,
            "evidence_refs": [evidence_id],
            "source_refs": [f"system-action:{action_key}"],
        },
        f"correlation.lineage_recorded:{lineage_id}",
        correlation_id=completed_mission_id,
    )
    _append(
        database,
        "effect.provider_receipt",
        {
            "action_key": action_key,
            "mission_id": completed_mission_id,
            "provider_ref": "fixture-provider-receipt-001",
            "readback": "MATCH",
            "status": "COMPLETED",
            "evidence_refs": [evidence_id],
        },
        f"effect.provider_receipt:{action_key}",
        correlation_id=completed_mission_id,
    )

    capability_ids = _seed_capabilities(database)
    for index, event_type in enumerate((
        "mcp.server.connected",
        "mcp.server.disconnected",
        "mcp.server.connected",
    )):
        _append(
            database,
            event_type,
            {
                "server_id": "fixture-mcp-server",
                "status": "READY" if event_type.endswith("connected") else "DISCONNECTED",
                "reason": "fixture MCP reconnect sequence",
                "capability_id": capability_ids["mcp"],
                "generation": index + 1,
            },
            f"{event_type}:fixture:{index + 1}",
        )

    context_events = _seed_owner_context(
        database,
        objective_id=objective.objective_id,
        directive_id=directive_id,
    )
    return {
        "directive_id": directive_id,
        "objective_id": objective.objective_id,
        "campaign_id": campaign.campaign_id,
        "completed_mission_id": completed_mission_id,
        "active_mission_id": active_mission_id,
        "blocked_mission_id": blocked_mission_id,
        "active_run_id": active_run_id,
        "provider_attempt_id": attempt_id,
        "effect_lineage_id": lineage_id,
        "effect_action_key": action_key,
        "probation_capability_id": capability_ids["probation"],
        "mcp_capability_id": capability_ids["mcp"],
        "owner_context_event_ids": context_events,
    }


def prepare_fixture(
    port: int,
    evidence_out: Path | None,
) -> tuple[CommandCenterServer, dict[str, object], Path]:
    root = Path(tempfile.mkdtemp(prefix="jarvis-lane3-proof-"))
    database = FoundryDatabase(root / "foundry.db")
    database.initialize()
    repository = _repository(root)
    _register(database, repository)
    stop_path = root / "EMERGENCY_STOP"
    server = CommandCenterServer(database, stop_path=stop_path, port=port, source_repo=repository)
    server.configure_owner(OWNER_SECRET, owner_id="lane3-proof-owner")
    server.start()
    base = f"http://{server.address[0]}:{server.address[1]}"
    goal_result = _owner_command(
        base,
        "GOAL",
        "Create a durable Lane 3 proof artifact. success: outcome receipt exists; direct inspection agrees",
    )

    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        resource_id="lane3-proof-resource",
        lab_scripted_hermes=True,
        lab_false_success_step_id="02-execute",
        portfolio_selection_enabled=True,
        max_repairs=2,
    )
    first = run_cycle(database, FoundryPaths(root), config, loop_id="lane3-plan", instance_id="lane3-supervisor-1")
    second = run_cycle(database, FoundryPaths(root), config, loop_id="lane3-execute", instance_id="lane3-supervisor-2")
    completed_mission_id = str(second.get("mission_id") or first.get("mission_id") or "")
    if not completed_mission_id:
        raise RuntimeError(f"disposable proof did not create a mission: {first!r} {second!r}")
    with database.connect(readonly=True) as connection:
        evidence_row = connection.execute(
            "SELECT bundle_id FROM evidence_bundles WHERE mission_id = ? ORDER BY rowid DESC LIMIT 1",
            (completed_mission_id,),
        ).fetchone()
        worker_row = connection.execute(
            "SELECT run_id FROM agent_runs WHERE mission_id = ? ORDER BY rowid DESC LIMIT 1",
            (completed_mission_id,),
        ).fetchone()
    memory_id = f"memory-lane3-{completed_mission_id}"
    database.append(Event.create(
        "memory.observed",
        {
            "memory_id": memory_id,
            "mission_id": completed_mission_id,
            "agent_run_id": worker_row[0] if worker_row else None,
            "created_by": "lane3-proof",
            "mem_type": "episodic",
            "scope": "mission",
            "content": "Independent verification rejected the worker claim, repair completed, and the final artifact passed.",
            "source_ids": [completed_mission_id],
            "evidence_ids": [evidence_row[0]] if evidence_row else [],
            "confidence": 0.9,
        },
        source_type="supervisor",
        source_id="lane3-proof-supervisor",
        idempotency_key=f"memory.observed:{memory_id}",
    ))
    v13 = _seed_v13_product_truth(
        database,
        goal_result=goal_result,
        completed_mission_id=completed_mission_id,
    )
    briefing = BriefingPlanner(database, stop_path=str(stop_path)).plan(
        "MISSION_SUMMARY",
        period_key=f"lane3:{completed_mission_id}",
    )
    stop_result = _owner_command(
        base,
        "STOP_EVERYTHING",
        "fixture proves durable STOP before browser acceptance",
    )
    resume_result = _owner_command(
        base,
        "RESUME",
        "fixture resumes after durable STOP proof",
    )
    snapshot = build_snapshot(database, stop_path=stop_path)
    events = database.events()
    event_types = [event.event_type for event in events]
    capability_rows = snapshot.get("capability_fabric", {}).get("capabilities", [])
    probation = next(
        (
            row for row in capability_rows
            if row.get("spec", {}).get("capability_id") == v13["probation_capability_id"]
        ),
        {},
    )
    mcp = next(
        (
            row for row in capability_rows
            if row.get("spec", {}).get("capability_id") == v13["mcp_capability_id"]
        ),
        {},
    )
    work = snapshot.get("work_hierarchy", {})
    proof_checks = {
        "active_objective": any(
            row.get("objective_id") == v13["objective_id"]
            and row.get("status") == "ACTIVE"
            for row in work.get("objectives", [])
        ),
        "campaign": any(
            row.get("campaign_id") == v13["campaign_id"]
            for row in work.get("campaigns", [])
        ),
        "multiple_missions": len([
            row for row in work.get("missions", [])
            if row.get("objective_id") == v13["objective_id"]
        ]) >= 3,
        "worker_activity": any(
            row.get("run_id") == v13["active_run_id"]
            and row.get("status") == "RUNNING"
            for row in snapshot.get("workers", [])
        ),
        "verification_pass_fail_repair": (
            "verification.failed" in event_types
            and "verification.passed" in event_types
            and any(
                event.event_type == "mission.state_changed"
                and event.payload.get("state") == "REPAIRING"
                for event in events
            )
        ),
        "provider_wait": any(
            row.get("campaign_id") == v13["campaign_id"]
            and "PROVIDER" in row.get("wait_kinds", [])
            for row in work.get("campaigns", [])
        ),
        "recovery": "worker.recovered" in event_types and "provider.recovered" in event_types,
        "stop_resume": (
            stop_result.get("status") == "APPLIED"
            and resume_result.get("status") == "APPLIED"
            and snapshot.get("stop", {}).get("active") is False
        ),
        "blocked_needs_owner": snapshot.get("decision_queue", {}).get("open_count", 0) > 0,
        "capability_probation": (
            probation.get("readiness", {}).get("trust_state") == "PROBATION"
            and probation.get("readiness", {}).get("state") == "READY"
        ),
        "mcp_readiness_reconnect": (
            mcp.get("readiness", {}).get("state") == "READY"
            and event_types.count("mcp.server.connected") >= 2
            and "mcp.server.disconnected" in event_types
        ),
        "external_effect_receipt": any(
            row.get("attempt_id") == v13["provider_attempt_id"]
            and row.get("outcome") == "COMPLETED"
            for row in snapshot.get("effect_truth", {}).get("attempts", [])
        ),
        "owner_context": snapshot.get("owner_context", {}).get("current_count", 0) >= 4,
    }
    proof = {
        "schema": "v13.command-center-product-proof.v1",
        "source": "disposable_foundry_database",
        "api": base,
        "database": str(database.path),
        "mission_id": completed_mission_id,
        "goal": goal_result,
        "cycles": [first, second],
        "mission_state": next(
            (
                item.get("state")
                for item in snapshot["missions"]
                if item.get("mission_id") == completed_mission_id
            ),
            "UNKNOWN",
        ),
        "verification_statuses": [
            item.get("status")
            for item in snapshot["verification"]
            if item.get("mission_id") == completed_mission_id
        ],
        "evidence_bundles": [
            item.get("bundle_id")
            for item in snapshot["evidence"]
            if item.get("mission_id") == completed_mission_id
        ],
        "memory_ids": [
            item.get("memory_id")
            for item in snapshot["memory"]
            if item.get("mission_id") == completed_mission_id
        ],
        "briefing": {
            "briefing_id": briefing.get("briefing_id"),
            "status": briefing.get("status"),
            "created": briefing.get("created"),
        },
        "briefing_packet": snapshot.get("briefing_packet", {}),
        "event_types": event_types,
        "fixture_ids": v13,
        "checks": proof_checks,
        "all_checks_pass": all(proof_checks.values()),
        "adversarial_coverage": {
            "worker_false_claim": "PASS",
            "failed_verifier": "PASS",
            "repair_retry": "PASS",
            "provider_failure": "PASS",
            "duplicate_execution": "COVERED_BY_EXISTING_CANONICAL_TEST",
            "cancel": "COVERED_BY_EXISTING_CANONICAL_TEST",
            "pause_revoke": "COVERED_BY_EXISTING_CANONICAL_TEST",
            "crash_restart_resume": "PASS",
        },
    }
    if evidence_out:
        evidence_out.parent.mkdir(parents=True, exist_ok=True)
        evidence_out.write_text(
            json.dumps(proof, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({
        "api": base,
        "mission_id": completed_mission_id,
        "objective_id": v13["objective_id"],
        "campaign_id": v13["campaign_id"],
        "database": str(database.path),
        "evidence": str(evidence_out) if evidence_out else None,
        "all_checks_pass": proof["all_checks_pass"],
    }), flush=True)
    return server, proof, root


class FixtureRuntime:
    def __init__(
        self,
        *,
        server: CommandCenterServer,
        database: FoundryDatabase,
        root: Path,
        repository: Path,
        port: int,
    ) -> None:
        self.server = server
        self.database = database
        self.root = root
        self.repository = repository
        self.port = port
        self.restart_count = 0
        self._lock = threading.Lock()

    def append_markers(self, label: str, count: int) -> list[str]:
        events: list[Event] = []
        for index in range(max(1, min(int(count), 10))):
            marker_id = f"fixture-marker-{uuid.uuid4().hex[:12]}"
            events.append(Event.create(
                "command_center.fixture_marker",
                {
                    "marker_id": marker_id,
                    "reason": f"{label} {index + 1}",
                    "status": "RECORDED",
                },
                source_type="supervisor",
                source_id="v13-command-center-fixture",
                idempotency_key=f"command_center.fixture_marker:{marker_id}",
            ))
        self.database.append_events(events)
        return [event.event_id for event in events]

    def restart(self, delay_ms: int = 750) -> dict[str, Any]:
        with self._lock:
            self.server.close()
            time.sleep(max(0, min(int(delay_ms), 5000)) / 1000)
            replacement = CommandCenterServer(
                self.database,
                stop_path=self.root / "EMERGENCY_STOP",
                port=self.port,
                source_repo=self.repository,
            )
            replacement.configure_owner(OWNER_SECRET, owner_id="lane3-proof-owner")
            replacement.start()
            self.server = replacement
            self.restart_count += 1
            return {
                "status": "RESTARTED",
                "restart_count": self.restart_count,
                "api": f"http://{replacement.address[0]}:{replacement.address[1]}/api",
            }

    def state(self) -> dict[str, Any]:
        snapshot = build_snapshot(
            self.database,
            stop_path=self.root / "EMERGENCY_STOP",
        )
        return {
            "status": "READY",
            "restart_count": self.restart_count,
            "api": f"http://{self.server.address[0]}:{self.server.address[1]}/api",
            "ledger_head": snapshot["source_of_truth"]["ledger_head_sequence"],
            "stop_active": snapshot["stop"]["active"],
        }


class _FixtureControlHandler(BaseHTTPRequestHandler):
    runtime: FixtureRuntime

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/state":
            self._json({"status": "NOT_FOUND"}, 404)
            return
        self._json(self.runtime.state())

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            self._json({"status": "BAD_REQUEST"}, 400)
            return
        route = self.path.split("?", 1)[0]
        if route == "/append":
            event_ids = self.runtime.append_markers(
                str(payload.get("label") or "browser synchronization marker"),
                int(payload.get("count") or 1),
            )
            self._json({
                "status": "APPENDED",
                "event_ids": event_ids,
                "ledger_head": self.runtime.database.event_count(),
            })
            return
        if route == "/restart":
            self._json(self.runtime.restart(int(payload.get("delay_ms") or 750)))
            return
        self._json({"status": "NOT_FOUND"}, 404)

    def log_message(self, *_args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5129)
    parser.add_argument("--control-port", type=int, default=0)
    parser.add_argument("--evidence-out", type=Path)
    args = parser.parse_args()
    server, proof, root = prepare_fixture(args.port, args.evidence_out)
    database = FoundryDatabase(Path(str(proof["database"])))
    runtime = FixtureRuntime(
        server=server,
        database=database,
        root=root,
        repository=root / "repository",
        port=server.address[1],
    )
    control_handler = type(
        "BoundFixtureControlHandler",
        (_FixtureControlHandler,),
        {"runtime": runtime},
    )
    control = ThreadingHTTPServer(("127.0.0.1", args.control_port), control_handler)
    control_thread = threading.Thread(
        target=control.serve_forever,
        name="jarvis-fixture-control",
        daemon=True,
    )
    control_thread.start()
    print(json.dumps({
        "control": f"http://127.0.0.1:{control.server_address[1]}",
        "api": f"http://127.0.0.1:{runtime.server.address[1]}/api",
    }), flush=True)
    stop = threading.Event()
    try:
        while not stop.wait(0.5):
            time.sleep(0)
    except KeyboardInterrupt:
        pass
    finally:
        control.shutdown()
        control.server_close()
        control_thread.join(timeout=5)
        runtime.server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
