"""Disposable multi-project proof for the V13 Chief of Staff layer.

The proof uses only an isolated schema-v40 database. It exercises the real
Owner Context, Executive objective/campaign, Continuous Executive wake,
candidate-ingress, Command Center, briefing, and reconciliation authorities.
It never reads from or writes to the live installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

from jarvis_foundry.briefing_items import project_briefing_items
from jarvis_foundry.chief_of_staff import (
    ChiefOfStaffService,
    project_chief_of_staff,
)
from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json
from jarvis_foundry.executive.autonomy import (
    create_objective,
    set_objective_status,
)
from jarvis_foundry.executive.campaigns import (
    create_campaign,
    declare_child_mission,
)
from jarvis_foundry.executive.continuous import (
    rebuild_wake_state,
    tick_wakes,
)
from jarvis_foundry.owner_context import (
    ContextProvenance,
    ContextScope,
    OwnerContextStore,
    compile_standing_order,
    project_standing_orders,
)
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.reconcile import reconcile


NOW = "2026-09-04T18:00:00Z"
BASE_SHA = "7f153851517b5529dd1e582725a0c2a6d96ddc9b"
V12_FINAL_CANDIDATE = "51d2cfcce1be847fd252d1c857105f09f5f2c0e4"
OWNER_CONTEXT_SOURCE = "41dfcd285e3fb407237a43b9c2caba47d9d6ab1b"
OWNER_CONTEXT_FINAL = "ca3e9adb6a559f507253bdf7b70854424bc8b35d"
EXPECTED_BRANCH = "candidate/v13-chief-of-staff-20260903"
INITIAL_MAIN_SHA = "2905c206fed0461bf3085bc0cb43773be913300a"
SECRET = b"v13-chief-of-staff-proof-secret"
REPO_ROOT = Path(__file__).resolve().parents[1]

JARVIS_ORDER = "directive:standing-jarvis-blocked"
LEADGEN_ORDER = "directive:standing-leadgen-recovery"
OPERATOR_ORDER = "directive:standing-operator-clean"


def _git(*args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _source_identity() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    head = _git("rev-parse", "HEAD")
    return {
        "worktree": str(REPO_ROOT),
        "branch": _git("branch", "--show-current"),
        "head_sha": head,
        "source_sha": head,
        "source_tree": _git("rev-parse", "HEAD^{tree}"),
        "base_sha": BASE_SHA,
        "merge_base": _git("merge-base", "HEAD", BASE_SHA),
        "base_is_ancestor": subprocess.run(
            ["git", "merge-base", "--is-ancestor", BASE_SHA, "HEAD"],
            cwd=REPO_ROOT,
            check=False,
        ).returncode == 0,
        "main_sha": _git("rev-parse", "main"),
        "origin_main_sha": _git("rev-parse", "origin/main"),
        "dirty": bool(status),
        "dirty_paths": status.splitlines(),
    }


def _provenance(text: str, ref: str) -> ContextProvenance:
    return ContextProvenance.from_content(
        source_type="repository",
        source_id="v13-chief-of-staff-proof",
        source_ref=ref,
        content=text,
        captured_at="2026-09-04T08:00:00Z",
        evidence_refs=(f"proof:{ref}",),
        authority="REPOSITORY_AUTHORITY",
    )


def _record(
    store: OwnerContextStore,
    context_id: str,
    kind: str,
    text: str,
    *,
    project_id: str | None = None,
    program_id: str | None = None,
    objective_id: str | None = None,
    priority: float = 0.0,
    attributes: Mapping[str, Any] | None = None,
    related_context_ids: tuple[str, ...] = (),
) -> str:
    store.record(
        kind,
        text,
        context_id=context_id,
        scope=ContextScope(
            global_scope=not any((project_id, program_id, objective_id)),
            program_ids=(program_id,) if program_id else (),
            project_ids=(project_id,) if project_id else (),
            objective_ids=(objective_id,) if objective_id else (),
        ),
        provenance=_provenance(text, f"fixture/{context_id}"),
        observed_at="2026-09-04T08:00:00Z",
        effective_at="2026-09-04T08:00:00Z",
        priority=priority,
        attributes=attributes,
        related_context_ids=related_context_ids,
    )
    return context_id


def _add_project(
    store: OwnerContextStore,
    *,
    project_id: str,
    title: str,
    program_id: str,
    program_text: str,
    objective_id: str,
    objective_text: str,
    owner_priority: float,
) -> None:
    _record(
        store,
        program_id,
        "PROGRAM",
        program_text,
        project_id=project_id,
    )
    _record(
        store,
        f"project:{project_id}",
        "PROJECT",
        title,
        project_id=project_id,
        program_id=program_id,
        attributes={"title": title},
        related_context_ids=("goal:owner-portfolio", program_id),
    )
    _record(
        store,
        f"context-objective:{objective_id}",
        "ACTIVE_OBJECTIVE",
        objective_text,
        project_id=project_id,
        objective_id=objective_id,
        priority=owner_priority,
    )
    _record(
        store,
        f"priority:{project_id}",
        "PRIORITY",
        f"{title} owner priority",
        project_id=project_id,
        priority=owner_priority,
        attributes={"value": owner_priority},
    )


def _apply_standing_order(
    controls: OwnerControlService,
    *,
    project_id: str,
    directive_id: str,
    text: str,
) -> str:
    draft = compile_standing_order(
        text,
        scope=ContextScope(project_ids=(project_id,)),
    )
    metadata = draft.as_metadata()
    controls.apply(
        "STANDING_ORDER",
        target=project_id,
        exact_text=text,
        nonce=directive_id,
        signature=sign_command(
            SECRET,
            "STANDING_ORDER",
            project_id,
            text,
            directive_id,
            metadata=metadata,
        ),
        directive_id=directive_id,
        metadata=metadata,
    )
    return f"directive:{directive_id}"


def _revoke(
    controls: OwnerControlService,
    *,
    directive_id: str,
    nonce: str,
) -> None:
    text = f"Owner revoked {directive_id}"
    controls.apply(
        "REVOKE",
        target=directive_id,
        exact_text=text,
        nonce=nonce,
        signature=sign_command(
            SECRET,
            "REVOKE",
            directive_id,
            text,
            nonce,
        ),
    )


def _apply_veto(controls: OwnerControlService) -> None:
    text = "Veto further JARVIS Chief of Staff project action"
    controls.apply(
        "VETO_ACTION",
        target="jarvis",
        exact_text=text,
        nonce="v13-proof-veto",
        signature=sign_command(
            SECRET,
            "VETO_ACTION",
            "jarvis",
            text,
            "v13-proof-veto",
        ),
        directive_id="v13-proof-veto",
    )


def _append_hierarchy(database: FoundryDatabase) -> None:
    create_objective(
        database,
        "Complete the dependency prerequisite",
        objective_id="objective-jarvis-dependency",
        expected_impact=0.90,
        urgency=0.10,
        risk=0.20,
        progress=0.70,
        provenance={"project_id": "jarvis"},
    )
    create_objective(
        database,
        "Continue V13 Chief of Staff after the dependency",
        objective_id="objective-jarvis-v13",
        dependencies=("objective-jarvis-dependency",),
        expected_impact=0.95,
        urgency=0.20,
        blocking_value=0.95,
        risk=0.45,
        progress=0.25,
        status="BLOCKED",
        provenance={"project_id": "jarvis"},
    )
    create_campaign(
        database,
        "objective-jarvis-v13",
        campaign_id="campaign-jarvis-v13",
        metadata={"project_id": "jarvis"},
    )
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": "mission-jarvis-v13",
            "objective": "Prove the bounded multi-project Chief of Staff",
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": ["independent verifier accepts"],
            "next_action": "run the disposable proof",
        },
        source_type="supervisor",
        source_id="v13-chief-of-staff-proof",
        idempotency_key="mission.created:mission-jarvis-v13",
    ))
    declare_child_mission(
        database,
        "campaign-jarvis-v13",
        child_id="child-jarvis-v13",
        mission_id="mission-jarvis-v13",
        effect_action_key="action-jarvis-v13",
        effect_keys=("action-jarvis-v13",),
    )
    database.append(Event.create(
        "action.allowed",
        {
            "action_id": "action-jarvis-v13",
            "mission_id": "mission-jarvis-v13",
            "decision": "ALLOW",
            "reason": "bounded disposable proof action",
            "target": "disposable-v13-proof",
        },
        source_type="supervisor",
        source_id="v13-chief-of-staff-proof",
        idempotency_key="action.allowed:action-jarvis-v13",
    ))


def _event_count(database: FoundryDatabase, event_type: str) -> int:
    return database.event_count(event_type=event_type)


def _prefix_count(database: FoundryDatabase, prefix: str) -> int:
    return len(database.events(event_type_prefixes=(prefix,)))


def _table_count(database: FoundryDatabase, table: str) -> int:
    with database.connect(readonly=True) as connection:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table}",
            ).fetchone()[0]
        )


def _ledger_digest(database: FoundryDatabase) -> str:
    rows = [
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "source_type": event.source_type,
            "source_id": event.source_id,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "idempotency_key": event.idempotency_key,
            "payload_hash": event.payload_hash,
        }
        for event in database.events()
    ]
    return hashlib.sha256(
        canonical_json(rows).encode("utf-8"),
    ).hexdigest()


def _fresh_process_snapshot(
    database_path: Path,
    *,
    now: str,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--snapshot-db",
            str(database_path),
            "--now",
            now,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _find_project(
    projection: Mapping[str, Any],
    project_id: str,
) -> dict[str, Any]:
    return next(
        dict(item)
        for item in projection.get("projects", ())
        if item.get("project_id") == project_id
    )


def _priority(
    projection: Mapping[str, Any],
    project_id: str,
) -> dict[str, Any]:
    return next(
        dict(item)
        for item in projection.get("priorities", ())
        if item.get("project_id") == project_id
    )


def run_proof(
    runtime_root: Path,
    *,
    now: str = NOW,
    enforce_source: bool = True,
) -> dict[str, Any]:
    source = _source_identity()
    proof_root = runtime_root.resolve() / f"run-{uuid.uuid4().hex[:12]}"
    proof_root.mkdir(parents=True, exist_ok=False)
    database = FoundryDatabase(proof_root / "foundry.db")
    schema = database.initialize()
    store = OwnerContextStore(database)
    _record(
        store,
        "goal:owner-portfolio",
        "GOAL",
        "Advance the owner's active portfolio by strategic value",
    )
    _add_project(
        store,
        project_id="jarvis",
        title="JARVIS Foundry",
        program_id="program:personal-operator",
        program_text="Personal operator reliability program",
        objective_id="objective-jarvis-v13",
        objective_text="Prove a bounded multi-project Chief of Staff",
        owner_priority=95,
    )
    _add_project(
        store,
        project_id="leadgen",
        title="LeadGen",
        program_id="program:revenue-quality",
        program_text="Revenue and contact quality program",
        objective_id="objective-leadgen-quality",
        objective_text="Improve contact evidence without noisy recovery",
        owner_priority=82,
    )
    _add_project(
        store,
        project_id="operatoros",
        title="OperatorOS",
        program_id="program:operator-systems",
        program_text="Owner operating system program",
        objective_id="objective-operatoros-hygiene",
        objective_text="Keep the planning system coherent and intentionally idle",
        owner_priority=60,
    )
    _append_hierarchy(database)

    controls = OwnerControlService(database, secret=SECRET)
    assert _apply_standing_order(
        controls,
        project_id="jarvis",
        directive_id="standing-jarvis-blocked",
        text="If a project becomes blocked, investigate it.",
    ) == JARVIS_ORDER
    assert _apply_standing_order(
        controls,
        project_id="leadgen",
        directive_id="standing-leadgen-recovery",
        text="Do not bother me with small recoverable errors.",
    ) == LEADGEN_ORDER
    assert _apply_standing_order(
        controls,
        project_id="operatoros",
        directive_id="standing-operator-clean",
        text="Keep my projects clean.",
    ) == OPERATOR_ORDER

    fixture_counts = {
        "objective_created": _event_count(
            database, "executive.objective_created",
        ),
        "mission_created": _event_count(database, "mission.created"),
        "effect_events": _prefix_count(database, "effect."),
    }
    stop_path = proof_root / "EMERGENCY_STOP"
    service = ChiefOfStaffService(database, stop_path=stop_path)
    cycles: list[dict[str, Any]] = []

    def consider(
        name: str,
        change: Mapping[str, Any],
        *,
        expected_orders: tuple[str, ...],
    ):
        before = database.event_count()
        result = service.consider(change, now=now)
        cycles.append({
            "ordinal": len(cycles) + 1,
            "name": name,
            "kind": "CHIEF_OF_STAFF_CONSIDER",
            "input": dict(change),
            "expected_standing_order_ids": list(expected_orders),
            "result": result.as_dict(),
            "event_delta": database.event_count() - before,
        })
        return result

    dependency_change = {
        "change_id": "change-jarvis-dependency-wait",
        "project_id": "jarvis",
        "program_id": "program:personal-operator",
        "objective_id": "objective-jarvis-v13",
        "change_type": "DEPENDENCY_WAIT",
        "summary": "The JARVIS project is blocked by its dependency objective",
        "occurred_at": "2026-09-04T09:00:00Z",
        "situation_state": "BLOCKED",
        "source_event_refs": ["source:jarvis-dependency-graph"],
        "evidence_refs": ["evidence:jarvis-dependency-block"],
        "attributes": {
            "recoverable": True,
            "wait_kind": "DEPENDENCY",
            "dependency_id": "objective-jarvis-dependency",
            "waiting_condition": "wait for objective-jarvis-dependency",
            "value": 0.95,
            "urgency": 0.25,
            "blocking_value": 0.95,
            "risk": 0.45,
            "dependencies": ["objective-jarvis-dependency"],
        },
    }
    dependency_wait = consider(
        "jarvis_dependency_wait",
        dependency_change,
        expected_orders=(JARVIS_ORDER,),
    )
    initial_projection = service.snapshot(now=now).as_dict()
    active_wakes = rebuild_wake_state(database)
    dependency_replay = consider(
        "jarvis_dependency_wait_replay",
        dependency_change,
        expected_orders=(JARVIS_ORDER,),
    )

    quiet_recovery = consider(
        "leadgen_quiet_recoverable_error",
        {
            "change_id": "change-leadgen-recoverable",
            "project_id": "leadgen",
            "program_id": "program:revenue-quality",
            "change_type": "RECOVERABLE_ERROR",
            "summary": (
                "A small recoverable error interrupted the LeadGen project"
            ),
            "occurred_at": "2026-09-04T09:10:00Z",
            "situation_state": "WAITING",
            "severity": "SMALL",
            "evidence_refs": ["evidence:leadgen-retryable-error"],
            "attributes": {
                "recoverable": True,
                "waiting_condition": "retry after the local backoff window",
                "value": 0.78,
                "urgency": 0.15,
                "blocking_value": 0.35,
                "risk": 0.20,
            },
        },
        expected_orders=(LEADGEN_ORDER,),
    )
    hygiene_wait = consider(
        "operatoros_standing_order_trigger",
        {
            "change_id": "change-operatoros-dirty",
            "project_id": "operatoros",
            "program_id": "program:operator-systems",
            "change_type": "WAITING",
            "summary": (
                "The OperatorOS project is dirty after generated cache drift"
            ),
            "occurred_at": "2026-09-04T09:20:00Z",
            "situation_state": "DIRTY",
            "evidence_refs": ["evidence:operatoros-dirty-tree"],
            "attributes": {
                "recoverable": True,
                "waiting_condition": "perform bounded local project cleanup",
                "value": 0.40,
                "urgency": 0.30,
                "blocking_value": 0.25,
                "risk": 0.10,
            },
        },
        expected_orders=(OPERATOR_ORDER,),
    )
    low_value = consider(
        "operatoros_urgent_low_value_rejected",
        {
            "change_id": "change-operatoros-urgent-cosmetic",
            "project_id": "operatoros",
            "program_id": "program:operator-systems",
            "change_type": "OPPORTUNITY",
            "summary": "An urgent cosmetic label cleanup is available",
            "occurred_at": "2026-09-04T09:25:00Z",
            "evidence_refs": ["evidence:operatoros-cosmetic-label"],
            "attributes": {
                "proposed_objective": "Polish one cosmetic planning label",
                "expected_outcome": "One label is visually cleaner",
                "value": 0.20,
                "urgency": 1.00,
                "confidence": 0.95,
                "reversible": True,
                "risk": 0.05,
            },
        },
        expected_orders=(),
    )
    priority_before_revocation = service.snapshot(now=now).as_dict()

    before_revoke = database.event_count()
    _revoke(
        controls,
        directive_id="standing-operator-clean",
        nonce="revoke-standing-operator-clean",
    )
    cycles.append({
        "ordinal": len(cycles) + 1,
        "name": "operatoros_standing_order_revoked",
        "kind": "OWNER_CONTROL",
        "directive_id": "standing-operator-clean",
        "event_delta": database.event_count() - before_revoke,
    })
    revoked_order = consider(
        "operatoros_revoked_order_cannot_route",
        {
            "change_id": "change-operatoros-after-revoke",
            "project_id": "operatoros",
            "program_id": "program:operator-systems",
            "change_type": "OPPORTUNITY",
            "summary": "The OperatorOS project is dirty and needs cleanup",
            "occurred_at": "2026-09-04T09:30:00Z",
            "situation_state": "DIRTY",
            "evidence_refs": ["evidence:operatoros-post-revoke-drift"],
            "attributes": {
                "proposed_objective": "Restore bounded OperatorOS hygiene",
                "expected_outcome": "The planning worktree is clean",
                "value": 0.80,
                "urgency": 0.40,
                "confidence": 0.90,
                "reversible": True,
            },
        },
        expected_orders=(),
    )
    adjacent = consider(
        "leadgen_adjacent_opportunity_waits_for_authority",
        {
            "change_id": "change-leadgen-adjacent-opportunity",
            "project_id": "leadgen",
            "program_id": "program:revenue-quality",
            "change_type": "OPPORTUNITY",
            "summary": "An adjacent contact-evidence repair is available",
            "occurred_at": "2026-09-04T09:40:00Z",
            "evidence_refs": ["evidence:leadgen-adjacent-quality-gap"],
            "attributes": {
                "proposed_objective": "Repair adjacent contact evidence lineage",
                "expected_outcome": "Contact evidence is more trustworthy",
                "value": 0.88,
                "urgency": 0.20,
                "confidence": 0.85,
                "reversible": True,
            },
        },
        expected_orders=(),
    )
    owner_decision = consider(
        "leadgen_material_owner_decision",
        {
            "change_id": "change-leadgen-owner-decision",
            "project_id": "leadgen",
            "program_id": "program:revenue-quality",
            "change_type": "OWNER_DECISION",
            "summary": "The owner must choose between two irreversible data buys",
            "occurred_at": "2026-09-04T09:50:00Z",
            "evidence_refs": ["evidence:leadgen-purchase-options"],
            "attributes": {
                "owner_decision_needed": True,
                "owner_decision_reason": (
                    "no standing authority selects an irreversible purchase"
                ),
            },
        },
        expected_orders=(),
    )
    intentional_idle = consider(
        "operatoros_intentional_idle",
        {
            "change_id": "change-operatoros-intentional-idle",
            "project_id": "operatoros",
            "program_id": "program:operator-systems",
            "change_type": "WAITING",
            "summary": "OperatorOS is deliberately idle until its review window",
            "occurred_at": "2026-09-04T10:00:00Z",
            "evidence_refs": ["evidence:operatoros-review-window"],
            "attributes": {
                "intentional_idle": True,
                "waiting_condition": "wait for the scheduled portfolio review",
            },
        },
        expected_orders=(),
    )

    before_unlock = database.event_count()
    set_objective_status(
        database,
        "objective-jarvis-dependency",
        "COMPLETED",
        reason="dependency proof completed",
        evidence_refs=("evidence:jarvis-dependency-complete",),
    )
    triggered_wakes = tick_wakes(database)
    set_objective_status(
        database,
        "objective-jarvis-v13",
        "ACTIVE",
        reason="dependency wake released the objective",
        evidence_refs=("evidence:jarvis-dependency-wake",),
    )
    cycles.append({
        "ordinal": len(cycles) + 1,
        "name": "jarvis_dependency_unlock_and_wake",
        "kind": "EXISTING_EXECUTIVE_AUTHORITIES",
        "triggered_wake_ids": triggered_wakes,
        "event_delta": database.event_count() - before_unlock,
    })
    continuation_change = {
        "change_id": "change-jarvis-after-dependency",
        "project_id": "jarvis",
        "program_id": "program:personal-operator",
        "objective_id": "objective-jarvis-v13",
        "change_type": "BLOCKED",
        "summary": (
            "The JARVIS project is blocked by a bounded reliability regression"
        ),
        "occurred_at": "2026-09-04T10:10:00Z",
        "situation_state": "BLOCKED",
        "source_event_refs": ["source:jarvis-wake-trigger"],
        "evidence_refs": ["evidence:jarvis-dependency-wake"],
        "attributes": {
            "proposed_objective": (
                "Investigate and repair the bounded V13 reliability blocker"
            ),
            "expected_outcome": "The focused V13 reliability proof passes",
            "candidate_type": "project_reliability",
            "value": 0.95,
            "urgency": 0.30,
            "confidence": 0.92,
            "reversible": True,
            "blocking_value": 0.90,
            "risk": 0.45,
            "health": "GREEN",
            "next_action": "review the admitted candidate",
        },
    }
    continuation = consider(
        "jarvis_continuation_routes_candidate",
        continuation_change,
        expected_orders=(JARVIS_ORDER,),
    )
    continuation_replay = consider(
        "jarvis_continuation_replay",
        continuation_change,
        expected_orders=(JARVIS_ORDER,),
    )

    stop_path.write_text("owner stop proof\n", encoding="utf-8")
    stopped = consider(
        "owner_stop_zero_write",
        {
            **continuation_change,
            "change_id": "change-jarvis-stop",
            "occurred_at": "2026-09-04T10:20:00Z",
        },
        expected_orders=(),
    )
    stop_path.unlink()
    before_veto = database.event_count()
    _apply_veto(controls)
    cycles.append({
        "ordinal": len(cycles) + 1,
        "name": "owner_veto_activated",
        "kind": "OWNER_CONTROL",
        "directive_id": "v13-proof-veto",
        "event_delta": database.event_count() - before_veto,
    })
    vetoed = consider(
        "owner_veto_zero_write",
        {
            **continuation_change,
            "change_id": "change-jarvis-veto",
            "occurred_at": "2026-09-04T10:30:00Z",
        },
        expected_orders=(),
    )

    before_reads = database.event_count()
    direct_projection = service.snapshot(now=now).as_dict()
    reopened_projection = project_chief_of_staff(
        FoundryDatabase(database.path),
        now=now,
    ).as_dict()
    fresh_process_projection = _fresh_process_snapshot(
        database.path,
        now=now,
    )
    command_center = build_snapshot(database, now=now)
    command_center_projection = command_center["executive"]["chief_of_staff"]
    packet = command_center["briefing_packet"]
    briefing_items = project_briefing_items(database, now=now, limit=200)
    after_reads = database.event_count()
    database.rebuild_projections()
    rebuilt_projection = _fresh_process_snapshot(database.path, now=now)

    final_wakes = rebuild_wake_state(database)
    active_orders = project_standing_orders(
        database,
        now=now,
        include_history=False,
    )
    active_order_ids = {
        str(item["standing_order_id"]) for item in active_orders
    }
    hierarchy = _find_project(direct_projection, "jarvis")
    hierarchy_edges = {
        (
            item["parent_kind"],
            item["parent_id"],
            item["child_kind"],
            item["child_id"],
        )
        for item in hierarchy["hierarchy_links"]
    }
    required_hierarchy = {
        (
            "OWNER_GOAL",
            "goal:owner-portfolio",
            "PROGRAM",
            "program:personal-operator",
        ),
        (
            "PROGRAM",
            "program:personal-operator",
            "PROJECT",
            "jarvis",
        ),
        (
            "PROJECT",
            "jarvis",
            "OBJECTIVE",
            "objective-jarvis-v13",
        ),
        (
            "OBJECTIVE",
            "objective-jarvis-v13",
            "CAMPAIGN",
            "campaign-jarvis-v13",
        ),
        (
            "CAMPAIGN",
            "campaign-jarvis-v13",
            "MISSION",
            "mission-jarvis-v13",
        ),
        (
            "MISSION",
            "mission-jarvis-v13",
            "ACTION",
            "action-jarvis-v13",
        ),
    }
    initial_jarvis = _find_project(initial_projection, "jarvis")
    final_jarvis = _find_project(direct_projection, "jarvis")
    final_leadgen = _find_project(direct_projection, "leadgen")
    final_operator = _find_project(direct_projection, "operatoros")
    leadgen_priority = _priority(priority_before_revocation, "leadgen")
    operator_priority = _priority(priority_before_revocation, "operatoros")
    final_operator_priority = _priority(direct_projection, "operatoros")
    matched_attention_items = [
        item
        for item in briefing_items
        if item.source_event_type
        == "chief_of_staff.owner_attention_required"
    ]
    queue_attention = [
        item
        for item in command_center["decision_queue"]["open"]
        if item.get("source_event_id")
        in {
            attention.source_event_id
            for attention in matched_attention_items
        }
    ]
    standing_order_mismatches = [
        cycle["name"]
        for cycle in cycles
        if cycle["kind"] == "CHIEF_OF_STAFF_CONSIDER"
        and sorted(cycle["result"]["standing_order_ids"])
        != sorted(cycle["expected_standing_order_ids"])
    ]
    counts = {
        "ledger_events": database.event_count(),
        "projects": len(direct_projection["projects"]),
        "chief_changes": _event_count(
            database, "chief_of_staff.change_observed",
        ),
        "candidate_routed": _event_count(
            database, "chief_of_staff.candidate_routed",
        ),
        "recommendations_rejected": _event_count(
            database, "chief_of_staff.recommendation_rejected",
        ),
        "waits_recorded": _event_count(
            database, "chief_of_staff.wait_recorded",
        ),
        "owner_attention": _event_count(
            database, "chief_of_staff.owner_attention_required",
        ),
        "candidates": _table_count(database, "candidates"),
        "objective_created": _event_count(
            database, "executive.objective_created",
        ),
        "mission_created": _event_count(database, "mission.created"),
        "wake_created": _event_count(
            database, "executive.wake_created",
        ),
        "wake_triggered": _event_count(
            database, "executive.wake_triggered",
        ),
        "effect_events": _prefix_count(database, "effect."),
        "decision_queue_matches": len(queue_attention),
        "false_standing_order_triggers": len(standing_order_mismatches),
    }
    source_attestations = {
        "branch_matches_candidate": source["branch"] == EXPECTED_BRANCH,
        "base_is_exact_merge_base": source["merge_base"] == BASE_SHA,
        "base_is_ancestor": source["base_is_ancestor"] is True,
        "main_unchanged": source["main_sha"] == INITIAL_MAIN_SHA,
        "origin_main_unchanged": (
            source["origin_main_sha"] == INITIAL_MAIN_SHA
        ),
        "worktree_clean_before_output": source["dirty"] is False,
    }
    checks = {
        "01_schema_v40": schema == 40,
        "02_three_real_projects_projected": {
            item["project_id"] for item in direct_projection["projects"]
        } == {"jarvis", "leadgen", "operatoros"},
        "03_full_owner_to_action_hierarchy": (
            required_hierarchy.issubset(hierarchy_edges)
        ),
        "04_blocked_project_and_active_dependency_wait_observed": (
            initial_jarvis["status"] == "BLOCKED"
            and initial_jarvis["health"] == "RED"
            and dependency_wait.wait_id in active_wakes
            and active_wakes[dependency_wait.wait_id]["status"] == "ACTIVE"
        ),
        "05_strategic_value_beats_raw_urgency": (
            leadgen_priority["score"] > operator_priority["score"]
            and leadgen_priority["factors"]["strategic_value"][
                "normalized_value"
            ] > operator_priority["factors"]["strategic_value"][
                "normalized_value"
            ]
            and operator_priority["factors"]["urgency"]["normalized_value"]
            == 1.0
        ),
        "06_standing_orders_match_and_revocation_is_effective": (
            {JARVIS_ORDER, LEADGEN_ORDER}.issubset(active_order_ids)
            and OPERATOR_ORDER not in active_order_ids
            and hygiene_wait.standing_order_ids == (OPERATOR_ORDER,)
            and revoked_order.standing_order_ids == ()
            and revoked_order.status == "WAITING"
        ),
        "07_quiet_recovery_does_not_interrupt_owner": (
            quiet_recovery.status == "WAITING"
            and quiet_recovery.standing_order_ids == (LEADGEN_ORDER,)
            and owner_decision.owner_attention_id
            and len(direct_projection["owner_attention"]) == 1
        ),
        "08_low_value_urgent_work_is_rejected": (
            low_value.status == "REJECTED"
            and low_value.rejection_reason == "LOW_STRATEGIC_VALUE"
        ),
        "09_adjacent_opportunity_waits_for_authority": (
            adjacent.status == "WAITING"
            and adjacent.rejection_reason
            == "PROACTIVE_AUTHORITY_NOT_ESTABLISHED"
            and adjacent.candidate_id is None
        ),
        "10_material_owner_decision_uses_existing_queue": (
            owner_decision.status == "OWNER_ATTENTION_REQUIRED"
            and counts["owner_attention"] == 1
            and counts["decision_queue_matches"] == 1
        ),
        "11_intentional_idle_is_healthy_and_unranked": (
            intentional_idle.status == "WAITING"
            and final_operator["status"] == "IDLE"
            and final_operator["health"] == "GREEN"
            and final_operator_priority["score"] == 0.0
        ),
        "12_wait_wake_unlock_and_continuation": (
            dependency_replay.wait_id == dependency_wait.wait_id
            and cycles[1]["event_delta"] == 0
            and triggered_wakes == [dependency_wait.wait_id]
            and final_wakes[dependency_wait.wait_id]["status"] == "TRIGGERED"
            and continuation.status
            == "ROUTED_TO_EXISTING_CANDIDATE_PATH"
            and final_jarvis["waiting_condition"] is None
            and final_jarvis["status"] == "ACTIVE"
        ),
        "13_one_candidate_and_zero_delta_replay": (
            counts["candidates"] == 1
            and counts["candidate_routed"] == 1
            and continuation.candidate_id == continuation_replay.candidate_id
            and continuation_replay.duplicate is True
            and next(
                cycle["event_delta"]
                for cycle in cycles
                if cycle["name"] == "jarvis_continuation_replay"
            ) == 0
        ),
        "14_stop_and_veto_each_add_zero_service_events": (
            stopped.status == "BLOCKED_BY_OWNER_CONTROL"
            and vetoed.status == "BLOCKED_BY_OWNER_CONTROL"
            and next(
                cycle["event_delta"]
                for cycle in cycles
                if cycle["name"] == "owner_stop_zero_write"
            ) == 0
            and next(
                cycle["event_delta"]
                for cycle in cycles
                if cycle["name"] == "owner_veto_zero_write"
            ) == 0
        ),
        "15_no_false_standing_order_triggers": (
            not standing_order_mismatches
        ),
        "16_no_second_objective_mission_or_effect_authority": (
            counts["objective_created"] == fixture_counts["objective_created"]
            and counts["mission_created"] == fixture_counts["mission_created"]
            and counts["effect_events"] == fixture_counts["effect_events"] == 0
        ),
        "17_command_center_packet_match_and_gets_are_read_only": (
            command_center_projection == direct_projection
            and packet["chief_of_staff"] == direct_projection
            and bool(packet["content_digest"])
            and after_reads == before_reads
        ),
        "18_restart_and_projection_rebuild_are_identical": (
            direct_projection
            == reopened_projection
            == fresh_process_projection
            == rebuilt_projection
        ),
        "19_briefing_has_source_backed_decision_and_next_actions": (
            len(matched_attention_items) == 1
            and bool(packet["source_refs"])
            and any(
                item.get("project_id") == "jarvis"
                for item in packet["next_action_summary"]
            )
            and final_leadgen["owner_decision_needed"] is True
        ),
        "20_event_integrity_quick_check_and_reconciliation": (
            database.verify_event_integrity() == database.event_count()
            and database.quick_check() == "ok"
            and reconcile(database, readonly=True).consistent
        ),
    }
    result = {
        "contract": "jarvis.v13.chief-of-staff-proof.v1",
        "generated_at": now,
        "scope": "DISPOSABLE_SCHEMA_V40_DATABASE_ONLY",
        "live_mutated": False,
        "main_or_origin_mutated": False,
        "schema": schema,
        "database": str(database.path),
        "ledger_digest": _ledger_digest(database),
        "source": source,
        "source_enforced": enforce_source,
        "source_attestations": source_attestations,
        "inputs": {
            "v12_final_candidate": V12_FINAL_CANDIDATE,
            "v12_source": BASE_SHA,
            "owner_context_final": OWNER_CONTEXT_FINAL,
            "owner_context_source": OWNER_CONTEXT_SOURCE,
        },
        "checks": checks,
        "passed": (
            all(checks.values())
            and (
                not enforce_source
                or all(source_attestations.values())
            )
        ),
        "counts": counts,
        "cycles": cycles,
        "artifacts": {
            "initial_projection": initial_projection,
            "priority_before_revocation": priority_before_revocation,
            "final_projection": direct_projection,
            "active_standing_order_ids": sorted(active_order_ids),
            "standing_order_mismatches": standing_order_mismatches,
            "triggered_wake_ids": triggered_wakes,
            "final_wakes": final_wakes,
            "candidate_id": continuation.candidate_id,
            "owner_attention_id": owner_decision.owner_attention_id,
            "decision_queue_source_event_ids": [
                item["source_event_id"] for item in queue_attention
            ],
            "briefing_content_digest": packet["content_digest"],
            "briefing_source_refs": packet["source_refs"],
            "project_health": {
                item["project_id"]: {
                    "status": item["status"],
                    "health": item["health"],
                    "waiting_condition": item["waiting_condition"],
                    "owner_decision_needed": item["owner_decision_needed"],
                    "intentional_idle": item["intentional_idle"],
                }
                for item in direct_projection["projects"]
            },
        },
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--snapshot-db", type=Path)
    parser.add_argument("--now", default=NOW)
    args = parser.parse_args()
    if args.snapshot_db is not None:
        snapshot = project_chief_of_staff(
            FoundryDatabase(args.snapshot_db),
            now=args.now,
        ).as_dict()
        print(json.dumps(snapshot, sort_keys=True))
        return 0
    if args.runtime_root is None:
        parser.error("--runtime-root is required")
    result = run_proof(args.runtime_root, now=args.now)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps({
        "passed": result["passed"],
        "database": result["database"],
        "output": str(args.output) if args.output else None,
        "source": result["source"],
        "source_attestations": result["source_attestations"],
        "counts": result["counts"],
        "failed_checks": [
            name for name, passed in result["checks"].items() if not passed
        ],
    }, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
