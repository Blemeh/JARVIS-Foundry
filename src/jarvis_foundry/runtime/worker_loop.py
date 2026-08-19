"""CANONICAL RUNTIME PATH — hybrid worker live driver.

One production path for worker-driven mission steps:

    Supervisor/STOP sentinel
      -> driver cycle (bounded, durable singleton lease)
      -> existing durable mission selector + executable step contract
      -> MissionLoop (canonical mission state machine)
      -> RoleDispatcher (durable leases, replay, exactly-once)
      -> HybridWorkerRoleAdapter (the single worker bridge)
      -> WorkerRouter (deterministic routing)
      -> worker adapters (Hermes / OpenHands, disposable state)
      -> EvidenceReconciler (JARVIS-owned truth)
      -> RoleResult -> durable ledger -> fresh verifier -> accept/repair
      -> next action selection

The driver is a long-lived companion process (like the Supervisor daemon)
so the Supervisor keeps its pure heartbeat/reap/STOP responsibility. It
refuses to start cycles while STOP is active or while the subsystem is
configured disabled, holds its own durable singleton lease, and relies on
the canonical replay/dedup semantics for exactly-once recovery.

No credential is ever read or stored here: adapters consume
``JARVIS_OPENCODE_API_KEY`` / ``JARVIS_OPENCODE_BASE_URL`` from the process
environment (approved runtime configuration, never committed).
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from ..database import FoundryDatabase
from ..events import Event
from ..paths import FoundryPaths
from ..roles.router import RoleRouter
from ..supervisor.estop import EmergencyStop
from ..supervisor.leases import LeaseManager
from ..mission.contracts import (
    MissionStepContractError,
    dispatchable_contract_missions,
    latest_contract,
)
from ..mission.directives import select_dispatchable_mission
from ..mission.loop import MissionLoop, ProposalError
from ._stack import build_worker_stack
from .config import WorkerRuntimeConfig

LOOP_LEASE_KEY = "worker-loop:singleton"
DEFAULT_MISSION_ID = "mission-wfm-optimizer"
DEFAULT_RESOURCE_ID = "resource-wfm-optimizer"


def _default_stop_path(paths: FoundryPaths, config: WorkerRuntimeConfig) -> Path:
    return config.resolve_stop_path(paths.home)


def _waiting_report(database: FoundryDatabase, *, loop_id: str,
                    instance_id: str, reason: str) -> dict[str, Any]:
    database.append(Event.create(
        "worker_loop.waiting",
        {"loop_id": loop_id, "instance_id": instance_id, "reason": reason},
        source_type="supervisor", source_id=instance_id,
        idempotency_key=f"worker_loop.waiting:{loop_id}",
    ))
    return {"loop_id": loop_id, "outcome": "WAITING", "reason": reason}


def _blocked_contract_report(database: FoundryDatabase, *, loop_id: str,
                             instance_id: str, mission_id: str | None,
                             reason: str) -> dict[str, Any]:
    database.append(Event.create(
        "worker_loop.contract_blocked",
        {"loop_id": loop_id, "instance_id": instance_id,
         "mission_id": mission_id, "reason": reason},
        source_type="supervisor", source_id=instance_id,
        idempotency_key=f"worker_loop.contract_blocked:{loop_id}",
    ))
    return {"loop_id": loop_id, "outcome": "BLOCKED",
            "mission_id": mission_id, "reason": reason}


def _select_contract(database: FoundryDatabase) -> str | None:
    """Select an existing mission that has a durable contract.

    Missions without executable authority are excluded from selection rather
    than converted into invented work.  A selected but malformed contract is
    returned for the caller to report as BLOCKED, preserving the distinction
    between honest WAITING and a corrupted authoritative record.
    """
    mission_ids = dispatchable_contract_missions(database)
    if not mission_ids:
        return None
    return select_dispatchable_mission(database, mission_ids)


def run_cycle(
    database: FoundryDatabase,
    paths: FoundryPaths,
    config: WorkerRuntimeConfig,
    *,
    loop_id: str,
    instance_id: str,
    vault_root: str | Path | None = None,
    handoff_dir: str | Path | None = None,
    research_dirs: Iterable[str | Path] = (),
    repo_paths: Iterable[str | Path] | None = None,
    target_root: str | Path | None = None,
    min_score: float = 0.35,
    score_budget: int = 8,
    max_items: int = 40,
    max_bytes: int = 80_000,
    instructions_builder: Callable[[str, Path], str] | None = None,
) -> dict[str, Any]:
    """Run one bounded autonomous loop pass through the canonical path.

    Returns a report dict; outcome is one of: DISABLED, FROZEN,
    LEASE_UNAVAILABLE, or the ``run_one_loop`` report (WAITING when no
    useful work exists — honest waiting, no invented work).
    """
    if not config.enabled:
        return {"loop_id": loop_id, "outcome": "DISABLED",
                "reason": "worker runtime subsystem is not enabled"}
    estop = EmergencyStop(_default_stop_path(paths, config))
    if estop.active():
        stop = estop.read()
        database.append(Event.create(
            "worker_loop.frozen",
            {"instance_id": instance_id, "loop_id": loop_id,
             "stop_id": stop.get("stop_id") if stop else None,
             "reason": stop.get("reason") if stop else "stop sentinel present"},
            source_type="supervisor", source_id=instance_id,
            idempotency_key=f"worker_loop.frozen:{loop_id}:{instance_id}",
        ))
        return {"loop_id": loop_id, "outcome": "FROZEN",
                "reason": "emergency stop is active"}

    leases = LeaseManager(database)
    try:
        leases.acquire(LOOP_LEASE_KEY, instance_id, 120)
    except Exception as error:  # noqa: BLE001 - lease conflict is honest
        return {"loop_id": loop_id, "outcome": "LEASE_UNAVAILABLE",
                "reason": f"{type(error).__name__}: {error}"}
    try:
        database.append(Event.create(
            "worker_loop.cycle_started",
            {"instance_id": instance_id, "loop_id": loop_id,
             "resource_id": config.resource_id,
             "capability": config.capability},
            source_type="supervisor", source_id=instance_id,
            idempotency_key=f"worker_loop.cycle_started:{loop_id}",
        ))
        mission_id = _select_contract(database)
        if mission_id is None:
            return _waiting_report(
                database, loop_id=loop_id, instance_id=instance_id,
                reason="no dispatchable mission has an authoritative executable contract",
            )
        try:
            contract = latest_contract(database, mission_id)
            if contract is None:
                return _waiting_report(
                    database, loop_id=loop_id, instance_id=instance_id,
                    reason=f"mission {mission_id} has no executable contract",
                )
            runtime_kwargs = contract.runtime_kwargs(database)
        except (MissionStepContractError, KeyError, TypeError, ValueError) as error:
            return _blocked_contract_report(
                database, loop_id=loop_id, instance_id=instance_id,
                mission_id=mission_id, reason=f"invalid executable contract: {error}",
            )

        role_router = RoleRouter()
        role_router.enforce_directive_routing()
        stack = build_worker_stack(
            database, paths, config, run_id=f"loop-{loop_id}")
        loop = MissionLoop(
            database, router=role_router, adapter=stack.bridge,
            leases=leases, max_repairs=config.max_repairs,
            source_id=f"worker-loop:{instance_id}",
        )
        try:
            outcome = loop.run_until_settled(**runtime_kwargs)
        except (MissionStepContractError, ProposalError, KeyError, TypeError, ValueError) as error:
            return _blocked_contract_report(
                database, loop_id=loop_id, instance_id=instance_id,
                mission_id=mission_id, reason=f"mission contract dispatch blocked: {error}",
            )
        return {
            "loop_id": loop_id,
            "outcome": outcome.outcome,
            "mission_id": mission_id,
            "step_id": outcome.step_id,
            "run_id": outcome.run_id,
            "verdict_id": outcome.verdict_id,
            "reason": outcome.reason,
        }
    finally:
        leases.release(LOOP_LEASE_KEY, instance_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis-worker-loop",
        description="Canonical hybrid worker live loop driver")
    parser.add_argument("--home", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, default=None,
                        help="worker runtime YAML (default: none -> disabled)")
    parser.add_argument("--once", action="store_true",
                        help="run a single bounded cycle and exit")
    parser.add_argument("--interval", type=float, default=300.0,
                        help="seconds between cycles (default 300)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = FoundryPaths.discover(getattr(args, "home", None))
    config = WorkerRuntimeConfig.from_yaml(args.config)
    if not config.enabled:
        print(json.dumps({
            "worker_loop": "DISABLED",
            "config": str(args.config),
            "reason": "worker runtime subsystem is not enabled",
        }, indent=2))
        return 0
    database = FoundryDatabase(paths.database)
    database.initialize()
    instance_id = f"worker-loop-{uuid.uuid4().hex[:12]}"
    iterations = 0
    while True:
        loop_id = f"{iterations}-{uuid.uuid4().hex[:8]}"
        report = run_cycle(
            database, paths, config,
            loop_id=loop_id, instance_id=instance_id,
        )
        print(json.dumps({"worker_loop": report}, indent=2), flush=True)
        iterations += 1
        if args.once or report.get("outcome") == "FROZEN":
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
