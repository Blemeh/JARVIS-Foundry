"""V6-S7: Supervisor-owned canary controller.

Live canaries are bounded in traffic share, duration, budget, health
thresholds and promotion eligibility — and the SUPERVISOR (controller)
owns start, tick allocation and termination. A candidate can never
start, extend, expand or terminate its own canary (controller strings
containing "candidate" are refused). STOP terminates immediately.
Termination works WITHOUT candidate cooperation: the controller can
terminate a hung canary and stale RUNNING canaries are reaped; after
termination no further ticks are accepted — traffic stops in the
ledger without the candidate's consent. Health is recomputed from RAW
metrics every tick (reported health flags are never trusted). Cost
spikes, regressions and invariant breaches terminate automatically.
Duration reached completes the canary and makes it promotion-eligible
through canary_completed_successfully().
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event

STATUSES = ("RUNNING", "COMPLETED", "TERMINATED")


class CanaryError(ValueError):
    """A canary operation is refused."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CanaryError(message)


def _controller_ok(controller: str) -> bool:
    """Canary control is Supervisor-owned: candidate identities can
    never start, tick, extend or terminate their own canary."""
    return (isinstance(controller, str) and controller.strip()
            and "candidate" not in controller.lower())


def _stop_active(database: FoundryDatabase) -> bool:
    return (Path(database.path).parent / "EMERGENCY_STOP").exists()


def start_canary(
    database: FoundryDatabase, *, improvement_id: str,
    candidate_release_id: str, traffic_share: float,
    duration_cycles: int, max_cost_usd: float,
    health_thresholds: dict[str, float], controller: str,
    source_type: str = "supervisor", source_id: str = "v6",
) -> str:
    """Start a live canary. Only the controller may do this."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can start a canary — "
             "candidate identities are refused")
    _require(database.row("improvement_records", "improvement_id",
                          improvement_id) is not None,
             f"improvement {improvement_id} does not exist — refused")
    _require(database.row("release_manifests", "release_id",
                          candidate_release_id) is not None,
             f"release {candidate_release_id} has no manifest — refused")
    _require(isinstance(traffic_share, (int, float))
             and 0.0 < traffic_share <= 1.0,
             "traffic_share must be in (0, 1]")
    _require(isinstance(duration_cycles, int) and duration_cycles > 0,
             "duration_cycles must be a positive integer")
    _require(isinstance(max_cost_usd, (int, float)) and max_cost_usd >= 0,
             "max_cost_usd must be non-negative")
    _require(isinstance(health_thresholds, dict)
             and "error_rate_max" in health_thresholds
             and "latency_max_s" in health_thresholds,
             "health_thresholds requires error_rate_max and "
             "latency_max_s")
    _require(not _stop_active(database),
             "stop active — canaries cannot start while STOP is set")
    canary_id = "canary-" + uuid.uuid4().hex[:12]
    database.append(Event.create(
        "canary.started",
        {"canary_id": canary_id, "improvement_id": improvement_id,
         "candidate_release_id": candidate_release_id,
         "traffic_share": traffic_share,
         "duration_cycles": duration_cycles,
         "max_cost_usd": max_cost_usd,
         "health_thresholds": dict(health_thresholds),
         "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"canary.started:{canary_id}"))
    return canary_id


def _canary_row(database: FoundryDatabase, canary_id: str) -> dict[str, Any]:
    row = database.row("canaries", "canary_id", canary_id)
    if row is None:
        raise CanaryError(f"canary {canary_id} does not exist")
    return row


def _terminate(database: FoundryDatabase, canary_id: str, reason: str,
               controller: str, event_type: str = "canary.terminated",
               source_type: str = "supervisor", source_id: str = "v6",
               ) -> None:
    database.append(Event.create(
        event_type,
        {"canary_id": canary_id, "reason": reason,
         "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"{event_type}:{canary_id}"))


def canary_tick(
    database: FoundryDatabase, *, canary_id: str, cycle: int,
    traffic_served: float, errors: int, latency_s: float,
    cost_usd: float, controller: str,
    regression_observed: bool = False,
    invariant_violated: bool = False,
    reported_healthy: bool = True,
    source_type: str = "supervisor", source_id: str = "v6",
) -> str:
    """One traffic window. Health is RECOMPUTED from raw metrics —
    reported_healthy is never trusted. Returns the canary status after
    the tick (RUNNING | COMPLETED | TERMINATED)."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can tick a canary")
    row = _canary_row(database, canary_id)
    _require(row["status"] == "RUNNING",
             f"canary {canary_id} is {row['status']} — traffic already "
             "stopped")
    _require(isinstance(cycle, int) and cycle > 0,
             "cycle must be a positive integer")
    _require(isinstance(traffic_served, (int, float))
             and 0.0 <= traffic_served <= 1.0,
             "traffic_served must be in [0, 1]")
    _require(isinstance(errors, int) and errors >= 0,
             "errors must be a non-negative integer")
    _require(isinstance(latency_s, (int, float)) and latency_s >= 0,
             "latency_s must be non-negative")
    _require(isinstance(cost_usd, (int, float)) and cost_usd >= 0,
             "cost_usd must be non-negative")

    import json as _json
    thresholds = _json.loads(row["health_thresholds_json"])
    error_rate = errors / traffic_served if traffic_served > 0 else \
        (1.0 if errors > 0 else 0.0)
    healthy = (error_rate <= float(thresholds["error_rate_max"])
               and latency_s <= float(thresholds["latency_max_s"]))
    # reported_healthy is deliberately unused for the decision:
    # health comes from the raw numbers only

    database.append(Event.create(
        "canary.tick",
        {"canary_id": canary_id, "cycle": cycle,
         "traffic_served": traffic_served, "errors": errors,
         "latency_s": latency_s, "cost_usd": cost_usd,
         "error_rate": round(error_rate, 6),
         "healthy": bool(healthy),
         "regression_observed": bool(regression_observed),
         "invariant_violated": bool(invariant_violated),
         "reported_healthy": bool(reported_healthy),
         "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"canary.tick:{canary_id}:{cycle}"))

    total_cost = float(row["total_cost_usd"]) + cost_usd
    # the tick handler has already applied this tick (append is
    # synchronous): re-read the row for the updated counters
    updated = _canary_row(database, canary_id)
    reason: str | None = None
    if not healthy:
        reason = f"health threshold breached (error_rate {error_rate}, " \
                 f"latency {latency_s}s)"
    elif total_cost > float(row["max_cost_usd"]):
        reason = f"cost budget exceeded ({total_cost} > " \
                 f"{row['max_cost_usd']})"
    elif regression_observed:
        reason = "outcome regression observed"
    elif invariant_violated:
        reason = "invariant breach observed"
    elif _stop_active(database):
        reason = "stop active — canary terminated immediately"
    elif int(updated["cycles_run"]) >= int(row["duration_cycles"]):
        _terminate(database, canary_id, "duration reached — canary "
                   "complete", controller,
                   event_type="canary.completed")
        return "COMPLETED"

    if reason is not None:
        _terminate(database, canary_id, reason, controller)
        return "TERMINATED"
    return "RUNNING"


def terminate_canary(
    database: FoundryDatabase, *, canary_id: str, reason: str,
    controller: str,
    source_type: str = "supervisor", source_id: str = "v6",
) -> None:
    """Controller-owned termination. Works WITHOUT candidate
    cooperation (a hung candidate cannot prevent it)."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can terminate a canary")
    row = _canary_row(database, canary_id)
    _require(row["status"] == "RUNNING",
             f"canary {canary_id} is already {row['status']}")
    _require(isinstance(reason, str) and reason.strip(),
             "reason must be a non-empty string")
    _terminate(database, canary_id, reason, controller)


def reap_stale_canaries(
    database: FoundryDatabase, *, current_cycle: int,
    max_cycles_without_tick: int, controller: str,
    source_type: str = "supervisor", source_id: str = "v6",
) -> list[str]:
    """Terminate RUNNING canaries that have not ticked for more than
    max_cycles_without_tick cycles (hung candidates — termination does
    not require their cooperation)."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can reap canaries")
    _require(isinstance(max_cycles_without_tick, int)
             and max_cycles_without_tick >= 0,
             "max_cycles_without_tick must be a non-negative integer")
    reaped: list[str] = []
    for row in database.all("canaries"):
        if row["status"] != "RUNNING":
            continue
        if int(row["last_tick_cycle"] or 0) < \
                current_cycle - max_cycles_without_tick:
            _terminate(database, row["canary_id"],
                       "stale — no tick for "
                       f"{current_cycle - int(row['last_tick_cycle'] or 0)} "
                       "cycles (hung candidate, controller reaped)",
                       controller)
            reaped.append(row["canary_id"])
    return reaped


def canary_status(database: FoundryDatabase, canary_id: str) -> str:
    return str(_canary_row(database, canary_id)["status"])


def canary_completed_successfully(database: FoundryDatabase,
                                  canary_id: str) -> bool:
    """The promotion gate: TRUE only for a canary that COMPLETED (all
    ticks healthy, duration reached, within budget, no regression, no
    invariant breach, no stop/termination reason)."""
    row = _canary_row(database, canary_id)
    if row["status"] != "COMPLETED":
        return False
    # the COMPLETED reason is informational ("duration reached"); any
    # OTHER reason attached to a completed row means a raw-writer
    # forged the completion — fail closed
    if row["reason"] and not row["reason"].startswith("duration reached"):
        return False
    if float(row["total_cost_usd"]) > float(row["max_cost_usd"]):
        return False
    if int(row["cycles_run"]) != int(row["duration_cycles"]):
        return False
    return int(row["last_health"] or 0) == 1
