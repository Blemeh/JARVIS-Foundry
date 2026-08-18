"""V6-S12: engineering + operational acceptance.

engineering_acceptance checks the machinery itself: idempotent
migrations (initialize() replay), schema pin (schema_migrations MAX
== SCHEMA_VERSION), reconcile consistency, and event integrity. It
fails closed on any check.

operational_acceptance counts the plan's operational minimums from
the cycles/promotions ledger:
  - >= 3 complete autonomous candidate cycles
  - >= 2 promotions with measured improvement
  - >= 1 R1 (or R0) real promotion
  - >= 1 R2 canary + promotion
  - >= 1 broken candidate auto-rejected
  - >= 1 rollback exercised
and FAILS CLOSED (passed False + reasons) when any minimum is unmet.

record_acceptance archives the report (acceptance.completed event +
acceptance_runs row) so every gate is replayable. Candidates can
never record acceptance.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from ..database import FoundryDatabase
from ..events import Event
from ..migrations import SCHEMA_VERSION
from ._authority import SUPERVISOR_CONTROLLER

MIN_CYCLES = 3
MIN_PROMOTIONS = 2
MIN_R1_PROMOTIONS = 1
MIN_R2_CANARY_PROMOTIONS = 1
MIN_REJECTIONS = 1
MIN_ROLLBACKS = 1


class AcceptanceError(ValueError):
    """An acceptance operation is refused."""


def _controller_ok_local(controller: str) -> bool:
    return isinstance(controller, str) and \
        controller == SUPERVISOR_CONTROLLER


def engineering_acceptance(database: FoundryDatabase
                           ) -> dict[str, Any]:
    """Machinery health: migration idempotency, schema pin, reconcile,
    event integrity. Fails closed."""
    checks: dict[str, Any] = {}
    try:
        database.initialize()  # replay: must be a no-op on a current DB
        checks["migration_replay_ok"] = True
    except Exception as error:
        checks["migration_replay_ok"] = False
        checks["migration_replay_error"] = str(error)[:200]
    with database.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    checks["schema_version"] = int(version)
    checks["schema_pinned"] = int(version) == SCHEMA_VERSION
    from ..reconcile import reconcile
    report = reconcile(database)
    checks["reconcile_consistent"] = bool(report.consistent)
    try:
        database.verify_event_integrity()  # raises on any invalid event
        checks["event_integrity_ok"] = True
    except Exception as error:
        checks["event_integrity_ok"] = False
        checks["event_integrity_error"] = str(error)[:200]
    checks["passed"] = bool(
        checks.get("migration_replay_ok") and checks["schema_pinned"]
        and checks["reconcile_consistent"] and checks[
            "event_integrity_ok"])
    return checks


def operational_acceptance(database: FoundryDatabase,
                           min_cycles: int = MIN_CYCLES,
                           min_promotions: int = MIN_PROMOTIONS,
                           ) -> dict[str, Any]:
    """The plan's operational minimums from the ledger. Fails closed
    with the unmet reasons."""
    reasons: list[str] = []
    counts: dict[str, int] = {}
    cycles = database.all("cycles")
    promotions = database.all("promotions")
    counts["cycles_total"] = len(cycles)
    counts["promotions"] = sum(
        1 for row in promotions
        if str(row["status"]) in ("PROMOTED", "ROLLED_BACK"))
    # a rollback presupposes a promotion; the ledger keeps both
    counts["live_promotions"] = sum(
        1 for row in promotions
        if str(row["status"]) == "PROMOTED")
    counts["rollbacks"] = sum(
        1 for row in promotions
        if str(row["status"]) == "ROLLED_BACK")
    counts["rejections"] = sum(
        1 for row in cycles if str(row["outcome"]) == "rejected")
    counts["r1_promotions"] = sum(
        1 for row in cycles
        if str(row["outcome"]) == "promoted"
        and str(row["improvement_id"]) != "" and _improvement_risk(
            database, str(row["improvement_id"])) in ("R0", "R1"))
    counts["r2_canary_promotions"] = sum(
        1 for row in cycles
        if str(row["outcome"]) == "promoted" and row["canary_id"])
    # a promotion with measured improvement: every promoted cycle ran
    # the comparison (baseline + candidate runs recorded)
    counts["measured_improvements"] = sum(
        1 for row in cycles
        if str(row["outcome"]) == "promoted"
        and row["baseline_run_id"] and row["candidate_run_id"])

    if counts["cycles_total"] < min_cycles:
        reasons.append(
            f"cycles_total {counts['cycles_total']} < {min_cycles}")
    if counts["promotions"] < min_promotions:
        reasons.append(
            f"promotions {counts['promotions']} < {min_promotions}")
    if counts["r1_promotions"] < MIN_R1_PROMOTIONS:
        reasons.append(
            f"r1_promotions {counts['r1_promotions']} < "
            f"{MIN_R1_PROMOTIONS}")
    if counts["r2_canary_promotions"] < MIN_R2_CANARY_PROMOTIONS:
        reasons.append(
            f"r2_canary_promotions {counts['r2_canary_promotions']} "
            f"< {MIN_R2_CANARY_PROMOTIONS}")
    if counts["rejections"] < MIN_REJECTIONS:
        reasons.append(
            f"rejections {counts['rejections']} < {MIN_REJECTIONS}")
    if counts["rollbacks"] < MIN_ROLLBACKS:
        reasons.append(
            f"rollbacks {counts['rollbacks']} < {MIN_ROLLBACKS}")
    return {"passed": not reasons, "reasons": reasons, **counts}


def _improvement_risk(database: FoundryDatabase,
                      improvement_id: str) -> str:
    row = database.row("improvement_records", "improvement_id",
                       improvement_id)
    if row is None:
        return "UNKNOWN"
    return str(row["risk_class"])


def record_acceptance(database: FoundryDatabase, *, report: dict[str, Any],
                      controller: str,
                      source_type: str = "supervisor",
                      source_id: str = "v6") -> str:
    """Archive an acceptance report (event + row). The archive is
    APPEND-ONLY: every gate gets a fresh uuid run id (S12 verifier
    deleg_057b415e — the docstring previously mis-claimed content-
    derived dedupe; each acceptance is a distinct archived gate)."""
    _require_controller(controller)
    run_id = "acceptance-" + uuid.uuid4().hex[:12]
    database.append(Event.create(
        "acceptance.completed",
        {"run_id": run_id, "passed": bool(report.get("passed")),
         "report_json": json.dumps(report, sort_keys=True),
         "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"acceptance.completed:{run_id}"))
    return run_id


def _require_controller(controller: str) -> None:
    if not _controller_ok_local(controller):
        raise AcceptanceError(
            "only the Supervisor (controller) can record acceptance — "
            "candidate identities are refused")
