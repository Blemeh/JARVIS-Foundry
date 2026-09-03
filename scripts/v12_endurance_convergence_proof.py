"""Run one disposable V12 campaign through the converged runtime.

The proof uses the canonical Executive campaign projection, MissionLoop,
RoleDispatcher/provider router, effect journal, wake authority, verifier
acceptance, and SQLite ledger.  Providers are deterministic local adapters;
no live provider credentials, installation, scheduler, or production state is
read or mutated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import execute_fabric_effect, reconcile_effect
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import create_objective, rebuild_executive_state
from jarvis_foundry.executive.campaigns import (
    apply_replan,
    campaign_dispatchable_mission_ids,
    campaign_snapshot,
    create_campaign,
    declare_child_mission,
    evaluate_joins,
    rebuild_campaign_state,
    resume_campaign,
    startup_resume_pass,
)
from jarvis_foundry.executive.continuous import resume_mission_provider_waits
from jarvis_foundry.mission.loop import BLOCKED_OUTCOME, PASSED, MissionLoop
from jarvis_foundry.mission.contracts import MissionStepContract, record_step_contract
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.roles.adapter import ProviderFailure
from jarvis_foundry.roles.results import COMPLETED, RoleResult
from jarvis_foundry.roles.router import ProviderRouteCandidate, RoleRouter, RoutingRequest
from jarvis_foundry.runtime.provider_registry import ProviderReadiness, record_provider_readiness

HARD_EXIT = 86
PROOF_NOW = "2026-09-03T12:00:00Z"
FUTURE_DEADLINE = "2099-09-03T12:00:00Z"
CAMPAIGN_ID = "campaign-v12-endurance-disposable"
OBJECTIVE_ID = "objective-v12-endurance-disposable"
REPO_ROOT = SOURCE_ROOT.parent
BASE_V11_SHA = "ae9ac06b6f9174c810b0e658daaf81c7b683ae8b"
CAMPAIGN_INPUT_SHA = "db685fe0bd0bc22b25a1f717bafdd8612cdfd19d"
PROVIDER_INPUT_SHA = "eedc4b293daa6400907f3196924514b21a61010c"

CHILDREN = {
    "ambiguous-child": {
        "mission_id": "mission-v12-ambiguous",
        "step_id": "step-v12-ambiguous",
        "providers": ("ambiguous-primary", "ambiguous-alternate"),
        "effect": "effect:v12:ambiguous",
    },
    "fallback-child": {
        "mission_id": "mission-v12-fallback",
        "step_id": "step-v12-fallback",
        "providers": ("fallback-primary", "fallback-alternate"),
        "effect": "effect:v12:fallback",
    },
    "wait-child": {
        "mission_id": "mission-v12-wait",
        "step_id": "step-v12-wait",
        "providers": ("wait-primary", "wait-alternate"),
        "effect": "effect:v12:wait",
    },
}


def _open(root: Path) -> FoundryDatabase:
    database = FoundryDatabase(root / "foundry.db")
    database.initialize()
    return database


def _git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_identity() -> dict[str, Any]:
    return {
        "branch": _git_output("branch", "--show-current"),
        "implementation_sha": _git_output("rev-parse", "HEAD"),
        "base_v11_sha": BASE_V11_SHA,
        "campaign_input_sha": CAMPAIGN_INPUT_SHA,
        "provider_resilience_input_sha": PROVIDER_INPUT_SHA,
        "schema_version": SCHEMA_VERSION,
        "worktree_clean_before_proof": not bool(_git_output("status", "--short")),
        "activation_authorized": False,
    }


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: dict[str, Any],
    key: str,
) -> None:
    database.append(Event.create(
        event_type,
        payload,
        source_type="test",
        source_id="v12-endurance-proof",
        idempotency_key=key,
    ))


def _git_repository(root: Path, child_id: str) -> Path:
    repository = root / "resources" / child_id
    repository.mkdir(parents=True, exist_ok=True)
    if not (repository / ".git").exists():
        subprocess.run(
            ["git", "init"], cwd=repository, check=True,
            capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "v12@example.invalid"],
            cwd=repository, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "V12 Endurance Proof"],
            cwd=repository, check=True, capture_output=True, text=True,
        )
        (repository / "README.md").write_text(
            f"disposable {child_id}\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "add", "README.md"], cwd=repository, check=True,
            capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "disposable baseline"],
            cwd=repository, check=True, capture_output=True, text=True,
        )
    return repository


def _ensure_fixture(root: Path) -> dict[str, str]:
    database = _open(root)
    if database.event_count(event_type="system.start_build_recorded") == 0:
        _append(
            database,
            "system.start_build_recorded",
            {"received_at": PROOF_NOW, "standing_mandate": True},
            "system.start-build:v12-endurance-proof",
        )
    create_objective(
        database,
        "V12 provider-aware long-horizon disposable campaign",
        objective_id=OBJECTIVE_ID,
        success_criteria=("all required child mission outcomes are accepted",),
    )
    create_campaign(
        database,
        OBJECTIVE_ID,
        campaign_id=CAMPAIGN_ID,
        deadline="2020-01-01T00:00:00Z",
        max_replans=1,
        max_parallel_children=2,
        join_conditions={
            "any-provider-child": {
                "children": list(CHILDREN),
                "mode": "ANY",
                "required": True,
            },
            "all-provider-children": {
                "children": list(CHILDREN),
                "mode": "ALL",
                "required": True,
            },
        },
        metadata={"proof": "disposable", "live_activation": False},
    )
    for child_id, spec in CHILDREN.items():
        repository = _git_repository(root, child_id)
        resource_id = f"resource-{child_id}"
        mission_id = str(spec["mission_id"])
        if database.row("resources", "resource_id", resource_id) is None:
            database.append(registration_event(
                inspect_git_resource(repository, resource_id=resource_id)
            ))
            set_capability_state(
                database,
                resource_id,
                "write_registered_worktree_files",
                "READY",
                evidence={"source": "v12-endurance-disposable"},
            )
        if database.row("missions", "mission_id", mission_id) is None:
            _append(
                database,
                "mission.created",
                {
                    "mission_id": mission_id,
                    "objective": f"complete {child_id}",
                    "resource_id": resource_id,
                    "state": "PLANNED",
                    "acceptance_criteria": ["result.txt exists and verifier passes"],
                    "next_action": f"execute {child_id}",
                },
                f"mission.created:{mission_id}",
            )
            for index, state in enumerate(("ORIENTING", "ACTION_SELECTION")):
                _append(
                    database,
                    "mission.state_changed",
                    {
                        "mission_id": mission_id,
                        "state": state,
                        "next_action": f"execute {child_id}",
                    },
                    f"mission.state_changed:{mission_id}:fixture:{index}",
                )
        record_step_contract(
            database,
            MissionStepContract(
                mission_id=mission_id,
                step_id=str(spec["step_id"]),
                version=1,
                resource_id=resource_id,
                capability="write_registered_worktree_files",
                target_relative_path="result.txt",
                instructions=f"create the isolated result for {child_id}",
                expected_effects=("create result.txt",),
                rollback={"kind": "delete_created_file"},
                verification_criteria=(
                    "result.txt exists and verifier passes",
                ),
                expected_artifacts=("result.txt",),
                provenance={
                    "objective_id": OBJECTIVE_ID,
                    "campaign_id": CAMPAIGN_ID,
                    "child_id": child_id,
                },
            ),
        )
        providers = tuple(str(item) for item in spec["providers"])
        request = RoutingRequest(
            task_type="builder",
            required_capabilities=("code", "tests"),
            required_tools=("hermes_oneshot",),
            preferred_provider=providers[0],
            allowed_providers=providers,
            allowed_data_use=("NO_TRAINING",),
        )
        declare_child_mission(
            database,
            CAMPAIGN_ID,
            child_id=child_id,
            mission_id=mission_id,
            step_id=str(spec["step_id"]),
            sibling_group="provider-workers",
            join_ids=("any-provider-child", "all-provider-children"),
            routing_requirements=request,
            isolation_key=str(repository.resolve()),
            effect_action_key=str(spec["effect"]),
            effect_keys=(str(spec["effect"]),),
        )
    return {
        child_id: str((root / "resources" / child_id).resolve())
        for child_id in CHILDREN
    }


def _candidate(provider: str, *, readiness: str = "READY") -> ProviderRouteCandidate:
    available = readiness in {"READY", "DEGRADED"}
    return ProviderRouteCandidate(
        provider=provider,
        model=f"{provider}-model",
        capabilities=(
            "structured_reasoning", "code", "tools", "tests",
            "artifact_inspection", "adversarial_verification",
        ),
        task_classes=("builder", "verifier", "repair"),
        tool_support=("hermes_oneshot",),
        readiness=readiness,
        health="READY" if available else readiness,
        auth_state="READY" if available else "UNKNOWN",
        availability="AVAILABLE" if available else readiness,
        data_use_classification="NO_TRAINING",
        noninteractive_allowed=True,
        owner_allowed=True,
        cost_usd=0.01,
        evidence_refs=(f"disposable-probe:{provider}:{readiness}",),
    )


def _router(providers: tuple[str, str]) -> RoleRouter:
    primary, alternate = providers
    return RoleRouter().clone_with({
        "builder": {
            "preferred_provider": primary,
            "preferred_model": f"{primary}-model",
            "fallbacks": (alternate,),
        },
        "repair": {
            "preferred_provider": primary,
            "preferred_model": f"{primary}-model",
            "fallbacks": (alternate,),
        },
        "verifier": {
            "preferred_provider": alternate,
            "preferred_model": f"{alternate}-model",
            "fallbacks": (primary,),
        },
    })


class DisposableProviderAdapter:
    """Deterministic provider boundary with real local effect-journal calls."""

    def __init__(
        self,
        database: FoundryDatabase,
        roots: dict[str, Path],
        scripts: dict[str, list[object]] | None = None,
    ) -> None:
        self.database = database
        self.roots = roots
        self.scripts = {
            provider: list(values)
            for provider, values in (scripts or {}).items()
        }
        self.received = []
        self.running_counts: list[int] = []

    def dispatch(self, manifest, *, heartbeat) -> RoleResult:
        self.received.append(manifest)
        state = rebuild_campaign_state(self.database)
        self.running_counts.append(sum(
            child.campaign_id == str(manifest.campaign_id or "")
            and child.status == "RUNNING"
            for child in state.children.values()
        ))
        heartbeat()
        queue = self.scripts.get(manifest.provider, [])
        outcome = queue.pop(0) if queue else "PASS"
        if isinstance(outcome, BaseException):
            raise outcome
        if manifest.role in {"builder", "repair"}:
            root = self.roots[manifest.resource_id]
            target = root / "result.txt"
            action_key = str(manifest.effect_action_key or "")
            if not action_key:
                raise RuntimeError("campaign builder manifest lost effect identity")

            def perform() -> dict[str, Any]:
                target.write_text(
                    f"accepted disposable result from {manifest.provider}\n",
                    encoding="utf-8",
                )
                if outcome == "AMBIGUOUS":
                    raise TimeoutError("response lost after local effect")
                return {
                    "claimed": "COMPLETED",
                    "performed": True,
                    "provider_ref": f"local:{target}",
                    "exists": True,
                }

            effect = execute_fabric_effect(
                self.database,
                action_key=action_key,
                system_id=manifest.provider,
                target_ref=str(target),
                payload_hash=hashlib.sha256(
                    manifest.instructions.encode("utf-8")
                ).hexdigest(),
                kind="disposable_campaign_child",
                connector_id=f"provider:{manifest.provider}",
                perform=perform,
            )
            if outcome == "AMBIGUOUS":
                if effect["journal_state"] != "FROZEN":
                    raise RuntimeError("ambiguous effect did not freeze")
                raise ProviderFailure(
                    "provider timed out after possible mutation",
                    failure_class="OUTAGE",
                    possible_effect=True,
                    effect_action_key=action_key,
                )
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=COMPLETED,
            provider=manifest.provider,
            model=manifest.model,
            verdict="PASS",
            evidence=(f"disposable:{manifest.provider}:{manifest.role}",),
            effect_action_key=manifest.effect_action_key,
        )


def _loop(
    database: FoundryDatabase,
    roots: dict[str, Path],
    child_id: str,
    adapter: DisposableProviderAdapter,
    *,
    readiness: tuple[str, str] = ("READY", "READY"),
) -> MissionLoop:
    providers = tuple(str(item) for item in CHILDREN[child_id]["providers"])
    return MissionLoop(
        database,
        _router(providers),
        adapter,
        provider_candidates=tuple(
            _candidate(provider, readiness=status)
            for provider, status in zip(providers, readiness, strict=True)
        ),
        max_provider_attempts=3,
        max_provider_retries=0,
        allowed_data_use=("NO_TRAINING",),
    )


def _step(root: Path, child_id: str, *, run_id: str | None = None) -> dict[str, Any]:
    spec = CHILDREN[child_id]
    values: dict[str, Any] = {
        "mission_id": spec["mission_id"],
        "step_id": spec["step_id"],
        "capability": "write_registered_worktree_files",
        "target": root / "result.txt",
        "instructions": f"create the isolated result for {child_id}",
        "expected_effects": (f"create {root / 'result.txt'}",),
        "rollback": {"kind": "delete_created_file"},
        "expected_artifacts": ("result.txt",),
        "verification_criteria": ["result.txt exists and verifier passes"],
    }
    if run_id is not None:
        values["run_id"] = run_id
    return values


def _hard_kill(root: Path) -> int:
    database = _open(root)

    def kill_after_checkpoint(phase: str) -> None:
        if "after checkpoint" in phase:
            os._exit(HARD_EXIT)

    startup_resume_pass(
        database,
        now=PROOF_NOW,
        resume_id="v12-endurance-hard-kill",
        fault_hook=kill_after_checkpoint,
    )
    return 0


def _event_duplicates(database: FoundryDatabase, event_type: str, key: str) -> int:
    identities = [
        str(event.payload.get(key) or "")
        for event in database.events()
        if event.event_type == event_type and event.payload.get(key)
    ]
    counts = Counter(identities)
    return sum(count - 1 for count in counts.values() if count > 1)


def _run_full(root: Path) -> dict[str, Any]:
    root_map = {
        child_id: Path(path)
        for child_id, path in _ensure_fixture(root).items()
    }
    database = _open(root)
    resource_roots = {
        f"resource-{child_id}": path for child_id, path in root_map.items()
    }

    # The initial expired deadline becomes a durable REPLANNING decision;
    # applying a bounded future deadline is required before dispatch.
    deadline_report = startup_resume_pass(
        database,
        now=PROOF_NOW,
        resume_id="v12-endurance-deadline",
    )
    if CAMPAIGN_ID not in deadline_report.replan_required:
        raise RuntimeError("expired campaign deadline did not request a replan")
    apply_replan(
        database,
        CAMPAIGN_ID,
        deadline=FUTURE_DEADLINE,
        reason="disposable bounded deadline replan",
    )
    startup_resume_pass(
        database,
        now=PROOF_NOW,
        resume_id="v12-endurance-after-replan",
    )
    initial_dispatchable = campaign_dispatchable_mission_ids(database)
    if len(initial_dispatchable) != 2:
        raise RuntimeError(
            f"bounded parallel admission expected 2, got {initial_dispatchable}"
        )

    # Primary failure -> compatible alternate, with one run/effect identity.
    fallback_primary = CHILDREN["fallback-child"]["providers"][0]
    fallback_adapter = DisposableProviderAdapter(
        database,
        resource_roots,
        scripts={
            str(fallback_primary): [ProviderFailure(
                "disposable primary outage", failure_class="OUTAGE"
            )],
        },
    )
    fallback = _loop(
        database, resource_roots, "fallback-child", fallback_adapter
    ).run_step(**_step(
        root_map["fallback-child"],
        "fallback-child",
        run_id="builder-run-v12-fallback",
    ))
    if fallback.outcome != PASSED:
        raise RuntimeError(f"fallback child did not pass: {fallback}")
    # Ambiguous primary effect freezes.  The same run can reach the alternate
    # only after canonical effect reconciliation releases its exact wait.
    ambiguous_adapter = DisposableProviderAdapter(
        database,
        resource_roots,
        scripts={"ambiguous-primary": ["AMBIGUOUS"]},
    )
    ambiguous_loop = _loop(
        database, resource_roots, "ambiguous-child", ambiguous_adapter
    )
    ambiguous_first = ambiguous_loop.run_step(**_step(
        root_map["ambiguous-child"],
        "ambiguous-child",
        run_id="builder-run-v12-ambiguous",
    ))
    if ambiguous_first.outcome != BLOCKED_OUTCOME:
        raise RuntimeError("ambiguous child was not blocked before fallback")
    parallel_running_peak = max(
        fallback_adapter.running_counts + ambiguous_adapter.running_counts
    )
    if parallel_running_peak != 2:
        raise RuntimeError(
            "two isolated siblings were not durably active inside the bound"
        )
    startup_resume_pass(
        database,
        now=PROOF_NOW,
        resume_id="v12-endurance-after-parallel-children",
    )
    any_join = evaluate_joins(database, CAMPAIGN_ID)
    alternate_attempts_before = sum(
        manifest.provider == "ambiguous-alternate"
        for manifest in ambiguous_adapter.received
    )
    reconcile_effect(
        database,
        str(CHILDREN["ambiguous-child"]["effect"]),
        provider_state={
            "exists": True,
            "provider_ref": "disposable-ambiguous-effect-confirmed",
        },
    )
    ambiguous_resumed = resume_mission_provider_waits(database)
    ambiguous_second = ambiguous_loop.run_step(**_step(
        root_map["ambiguous-child"], "ambiguous-child"
    ))
    if ambiguous_second.outcome != PASSED:
        raise RuntimeError(f"ambiguous child did not pass after reconcile: {ambiguous_second}")
    startup_resume_pass(
        database,
        now=PROOF_NOW,
        resume_id="v12-endurance-after-ambiguous",
    )

    # Every compatible provider is down.  MissionLoop creates the exact
    # provider wait, then a separate process dies during checkpoint rebuild.
    wait_adapter = DisposableProviderAdapter(database, resource_roots)
    wait_loop = _loop(
        database,
        resource_roots,
        "wait-child",
        wait_adapter,
        readiness=("OUTAGE", "OUTAGE"),
    )
    wait_first = wait_loop.run_step(**_step(
        root_map["wait-child"],
        "wait-child",
        run_id="builder-run-v12-wait",
    ))
    if wait_first.outcome != BLOCKED_OUTCOME:
        raise RuntimeError("all-down child did not enter a provider wait")
    proof_script = Path(__file__).resolve()
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(SOURCE_ROOT) + os.pathsep + environment.get(
        "PYTHONPATH", ""
    )
    killed = subprocess.run(
        [
            sys.executable,
            str(proof_script),
            "--mode", "hard-kill",
            "--root", str(root),
        ],
        cwd=SOURCE_ROOT.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if killed.returncode != HARD_EXIT:
        raise RuntimeError(
            f"hard-kill subprocess returned {killed.returncode}: {killed.stderr[-1000:]}"
        )
    restart = startup_resume_pass(
        _open(root),
        now=PROOF_NOW,
        resume_id="v12-endurance-fresh-process-restart",
    )
    database = _open(root)
    wait_loop = _loop(
        database,
        resource_roots,
        "wait-child",
        wait_adapter,
        readiness=("OUTAGE", "OUTAGE"),
    )

    # STOP wins over the provider wake, route, child, and continuation.  The
    # positive observation stays durable and releases exactly once afterward.
    stop_path = root / "EMERGENCY_STOP"
    stop_path.write_text("V12 disposable STOP\n", encoding="utf-8")
    stopped = startup_resume_pass(
        database,
        now=PROOF_NOW,
        stop_path=stop_path,
        resume_id="v12-endurance-stop",
    )
    calls_before_stop_probe = len(wait_adapter.received)
    stopped_attempt = wait_loop.run_step(**_step(
        root_map["wait-child"], "wait-child"
    ))
    if stopped_attempt.outcome != BLOCKED_OUTCOME:
        raise RuntimeError("STOP did not block MissionLoop")
    record_provider_readiness(
        database,
        provider_id="wait-alternate",
        model="wait-alternate-model",
        readiness=ProviderReadiness.READY,
        reason="disposable alternate recovered during STOP",
    )
    if resume_mission_provider_waits(database):
        raise RuntimeError("provider wait resumed while STOP was active")
    dispatches_during_stop = len(wait_adapter.received) - calls_before_stop_probe
    if dispatches_during_stop:
        raise RuntimeError("provider routing occurred under STOP")
    stop_path.unlink()
    resume_campaign(database, CAMPAIGN_ID, stop_path=stop_path)
    wait_resumed = resume_mission_provider_waits(database)
    wait_second = wait_loop.run_step(**_step(
        root_map["wait-child"], "wait-child"
    ))
    if wait_second.outcome != PASSED:
        raise RuntimeError(f"recovered wait child did not pass: {wait_second}")
    final_resume = startup_resume_pass(
        database,
        now=PROOF_NOW,
        resume_id="v12-endurance-final",
    )
    final_join = evaluate_joins(database, CAMPAIGN_ID)
    state = rebuild_campaign_state(database)
    campaign = state.campaigns[CAMPAIGN_ID]
    if campaign.status != "COMPLETED":
        raise RuntimeError(f"campaign did not complete: {campaign.status}")
    if any(
        child.status != "SUCCEEDED"
        for child in state.children.values()
        if child.campaign_id == CAMPAIGN_ID
    ):
        raise RuntimeError("not every child ended in accepted SUCCEEDED truth")

    events = database.events()
    fallback_success = next(
        event for event in events
        if event.event_type == "provider.dispatch_succeeded"
        and event.payload.get("run_id") == "builder-run-v12-fallback"
    )
    ambiguous_alternate_index = next(
        index for index, event in enumerate(events)
        if event.event_type == "provider.dispatch_attempted"
        and event.payload.get("run_id") == "builder-run-v12-ambiguous"
        and event.payload.get("provider") == "ambiguous-alternate"
    )
    ambiguous_reconcile_index = next(
        index for index, event in enumerate(events)
        if event.event_type == "provider.effect_reconciled"
        and event.payload.get("run_id") == "builder-run-v12-ambiguous"
    )
    mission_duplicates = _event_duplicates(
        database, "mission.created", "mission_id"
    )
    effect_duplicates = _event_duplicates(
        database, "system.action_started", "action_key"
    )
    continuation_duplicates = _event_duplicates(
        database, "mission.provider_wait_resumed", "wait_id"
    )
    snapshot = campaign_snapshot(database, CAMPAIGN_ID)["campaigns"][CAMPAIGN_ID]
    return {
        "schema": "jarvis-v12-endurance-convergence-proof-v1",
        "verdict": "PASS",
        "source": _source_identity(),
        "root": str(root),
        "live_mutated": False,
        "provider_credentials_used": False,
        "campaign": {
            "campaign_id": CAMPAIGN_ID,
            "objective_id": OBJECTIVE_ID,
            "status": campaign.status,
            "child_statuses": {
                child.child_id: child.status
                for child in state.children.values()
                if child.campaign_id == CAMPAIGN_ID
            },
            "accepted_child_count": snapshot["accepted_child_count"],
        },
        "provider_routing": {
            "fallback_provider": fallback_success.payload.get("provider"),
            "fallback_used": fallback_success.payload.get("fallback_used"),
            "identity": {
                key: fallback_success.payload.get(key)
                for key in (
                    "objective_id", "campaign_id", "child_id",
                    "builder_run_id", "effect_action_key", "routing_identity",
                )
            },
        },
        "fallback": {
            "outcome": fallback.outcome,
            "builder_run_id": fallback.run_id,
            "providers": [
                manifest.provider for manifest in fallback_adapter.received
                if manifest.role == "builder"
            ],
        },
        "all_down_wait": {
            "first_outcome": wait_first.outcome,
            "restart_waiting_campaigns": list(restart.waiting_campaigns),
            "wait_ids": [
                event.payload.get("wait_id") for event in events
                if event.event_type == "mission.provider_wait_started"
                and event.payload.get("mission_id") == CHILDREN["wait-child"]["mission_id"]
            ],
        },
        "recovery": {
            "wait_resume_ids": wait_resumed,
            "final_outcome": wait_second.outcome,
            "builder_run_id": wait_second.run_id,
            "resume_receipts": sum(
                event.event_type == "mission.provider_wait_resumed"
                and event.payload.get("mission_id") == CHILDREN["wait-child"]["mission_id"]
                for event in events
            ),
        },
        "deadlines": {
            "replan_required": list(deadline_report.replan_required),
            "applied_deadline": campaign.deadline,
            "silent_expiry": False,
        },
        "parallel_children": {
            "limit": campaign.max_parallel_children,
            "initial_dispatchable": list(initial_dispatchable),
            "peak_running_observed": parallel_running_peak,
            "logical_overlap_proven": parallel_running_peak == 2,
            "isolated_roots": {key: str(value) for key, value in root_map.items()},
        },
        "joins": {
            "any": any_join["joins"].get("any-provider-child"),
            "all": final_join["joins"].get("all-provider-children"),
            "completed": final_join["completed"],
        },
        "startup_resume": {
            "hard_kill_returncode": killed.returncode,
            "fresh_process_status": restart.status,
            "final_status": final_resume.status,
            "post_join_campaign_status": campaign.status,
        },
        "stop": {
            "stopped_campaigns": list(stopped.stopped_campaigns),
            "dispatches_during_stop": dispatches_during_stop,
            "wait_resumed_during_stop": False,
        },
        "ambiguous_effect": {
            "first_outcome": ambiguous_first.outcome,
            "resume_ids": ambiguous_resumed,
            "final_outcome": ambiguous_second.outcome,
            "alternate_attempts_before_reconcile": alternate_attempts_before,
            "reconcile_before_fallback": (
                ambiguous_reconcile_index < ambiguous_alternate_index
            ),
        },
        "duplicates": {
            "missions": mission_duplicates,
            "effects": effect_duplicates,
            "continuations": continuation_duplicates,
        },
        "event_count": database.event_count(),
        "objective_status": rebuild_executive_state(database).objectives[
            OBJECTIVE_ID
        ].status,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("full", "hard-kill"), default="full")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="also write the complete JSON receipt to this path",
    )
    args = parser.parse_args(argv)
    root = (
        args.root
        or Path(tempfile.mkdtemp(prefix="jarvis-v12-endurance-proof-"))
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        if args.mode == "hard-kill":
            return _hard_kill(root)
        payload = json.dumps(_run_full(root), indent=2, sort_keys=True) + "\n"
        if args.output is not None:
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload, encoding="utf-8")
        print(payload, end="")
        return 0
    except Exception as error:  # pragma: no cover - CLI boundary
        print(json.dumps({
            "schema": "jarvis-v12-endurance-convergence-proof-v1",
            "verdict": "BLOCKED",
            "root": str(root),
            "error": f"{type(error).__name__}: {error}",
        }, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
