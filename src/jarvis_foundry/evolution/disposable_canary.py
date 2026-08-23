"""V6 lane JOB: canary plan / disposable canary execution.

Production canary activation is FORBIDDEN in this lane. This module
proves the reusable CANARY LOGIC against DISPOSABLE release slots:
a deterministic bounded traffic allocator routes work items to the
candidate slot inside an isolated runtime home, raw metrics are fed to
the S7 canary machinery every tick, health is RECOMPUTED by that
machinery from raw numbers (never trusting reported flags), automatic
stop conditions fire (health breach, cost budget, STOP sentinel,
duration), and after termination no further ticks are accepted — a
candidate cannot keep itself alive.

No live surface exists here by construction: everything lives under
the isolated runtime home; "live" means the disposable baseline slot.
"""
from __future__ import annotations

from typing import Any

from ..database import FoundryDatabase
from ..supervisor.authority import SUPERVISOR_CONTROLLER
from .canary import (
    CanaryError,
    canary_completed_successfully,
    canary_tick,
    start_canary,
)


class DisposableCanaryError(ValueError):
    """Disposable canary execution refused."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DisposableCanaryError(message)


def allocate_traffic(work_item_index: int, *, traffic_share: float,
                     seed: int = 0) -> bool:
    """Deterministic bounded routing: item i goes to the candidate iff
    (i*2654435761 + seed) % 1000 < round(traffic_share*1000).
    Stateless and strictly bounded by construction."""
    _require(0.0 < traffic_share <= 1.0, "traffic_share must be in (0, 1]")
    bucket = (work_item_index * 2654435761 + seed) % 1000
    return bucket < round(traffic_share * 1000)


def run_disposable_canary(
    database: FoundryDatabase, *, improvement_id: str,
    candidate_release_id: str,
    workload: list[dict[str, Any]],
    candidate_behavior_fn: Any,
    baseline_behavior_fn: Any,
    traffic_share: float = 0.25,
    duration_cycles: int = 2,
    max_cost_usd: float = 1.0,
    health_thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Execute one disposable canary over a synthetic workload.

    workload items: dicts with at least {"cost_usd": float}. Behavior
    fns map an item to {"error": bool, "latency_s": float}. Each cycle
    feeds RAW aggregate metrics for both slots into the real S7 tick;
    health decisions belong entirely to the S7 machinery.
    """
    thresholds = health_thresholds or {"error_rate_max": 0.05,
                                       "latency_max_s": 2.0}
    canary_id = start_canary(
        database, improvement_id=improvement_id,
        candidate_release_id=candidate_release_id,
        traffic_share=traffic_share, duration_cycles=duration_cycles,
        max_cost_usd=max_cost_usd, health_thresholds=dict(thresholds),
        controller=SUPERVISOR_CONTROLLER)

    per_cycle = max(len(workload) // duration_cycles, 1)
    cand = {"served": 0, "errors": 0, "latency_total": 0.0}
    base = {"served": 0, "errors": 0, "latency_total": 0.0}
    total_cost = 0.0
    stop_reason: str | None = None

    def route(item: dict[str, Any], global_index: int) -> dict[str, Any]:
        nonlocal total_cost
        to_candidate = allocate_traffic(global_index,
                                        traffic_share=traffic_share)
        behavior = (candidate_behavior_fn(item) if to_candidate
                    else baseline_behavior_fn(item))
        slot = cand if to_candidate else base
        slot["served"] += 1
        slot["errors"] += 1 if behavior.get("error") else 0
        slot["latency_total"] += float(behavior.get("latency_s", 0.0))
        total_cost += float(item.get("cost_usd", 0.0))
        return slot

    for cycle in range(1, duration_cycles + 1):
        start_index = (cycle - 1) * per_cycle
        chunk = workload[start_index:start_index + per_cycle]
        if cycle == duration_cycles:
            chunk = workload[start_index:]
        for offset, item in enumerate(chunk):
            route(item, start_index + offset)

        # RAW metrics per slot; served normalized to a share of the
        # window so the S7 error-rate computation sees honest numbers
        window = len(chunk) or 1
        status = canary_tick(
            database, canary_id=canary_id, cycle=cycle,
            traffic_served=min(round(cand["served"] / max(window, 1),
                                     4), 1.0),
            errors=cand["errors"], latency_s=round(
                cand["latency_total"] / max(cand["served"], 1), 4),
            cost_usd=round(total_cost, 6),
            controller=SUPERVISOR_CONTROLLER)
        if status == "TERMINATED":
            row = database.row("canaries", "canary_id", canary_id)
            stop_reason = str(row["reason"])
            break

    completed = canary_completed_successfully(database, canary_id)
    return {
        "canary_id": canary_id,
        "status": "COMPLETED" if completed else "TERMINATED",
        "stop_reason": stop_reason,
        "candidate": {**cand,
                      "error_rate": round(cand["errors"] /
                                          max(cand["served"], 1), 4)},
        "baseline": {**base,
                     "error_rate": round(base["errors"] /
                                         max(base["served"], 1), 4)},
    }


def assert_candidate_cannot_self_extend(database: FoundryDatabase,
                                        canary_id: str) -> None:
    """Attack proof: after termination ANY further tick must be
    refused — a candidate cannot keep itself alive post-stop."""
    try:
        canary_tick(database, canary_id=canary_id, cycle=99,
                    traffic_served=0.1, errors=0, latency_s=0.1,
                    cost_usd=0.0, controller="candidate-1")
    except CanaryError:
        return
    raise DisposableCanaryError(
        "terminated canary accepted a post-mortem tick — candidate "
        "kept itself alive")
