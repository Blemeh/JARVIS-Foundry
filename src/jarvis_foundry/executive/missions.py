"""Canonical mission + contract materialization from executive SELECT."""
from __future__ import annotations

import uuid
from typing import Any

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..discovery_candidates import approve_candidate
from ..events import Event
from ..mission.contracts import MissionStepContract, record_step_contract
from .eligibility import assess_eligibility, current_activation_epoch


class MaterializationBlocked(Exception):
    """Raised when preflight checks fail at the materialization boundary."""

    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


def _approved_mission_id(database: FoundryDatabase,
                         candidate_id: str) -> str | None:
    """Return a prior approval's mission identity for crash recovery."""
    import json
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'discovery.candidate_approved' AND json_extract(payload_json, "
            "'$.candidate_id') = ? ORDER BY sequence LIMIT 1",
            (candidate_id,),
        ).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row[0]).get("mission_id")
    except (TypeError, ValueError):
        return None
    return str(value) if value else None


def existing_materialization(
    database: FoundryDatabase,
    decision_id: str,
) -> dict[str, Any] | None:
    """Return prior materialization or blocked record for one decision (idempotent)."""
    for event in database.events():
        if event.event_type == "executive.decision_materialized":
            if str(event.payload.get("decision_id") or "") == decision_id:
                return {
                    "mission_id": str(event.payload.get("mission_id") or ""),
                    "materialized": True,
                    "blocked_reason": None,
                }
        if event.event_type == "executive.materialization_blocked":
            if str(event.payload.get("decision_id") or "") == decision_id:
                return {
                    "mission_id": None,
                    "materialized": False,
                    "blocked_reason": str(event.payload.get("reason") or ""),
                }
    return None


def _record_materialization_blocked(
    database: FoundryDatabase,
    *,
    decision_id: str,
    candidate_id: str,
    reason: str,
    details: dict[str, Any],
    source_id: str,
) -> dict[str, Any]:
    database.append(Event.create(
        "executive.materialization_blocked",
        {
            "decision_id": decision_id,
            "candidate_id": candidate_id,
            "reason": reason,
            "details": details,
        },
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"executive.materialization_blocked:{decision_id}",
    ))
    return {"mission_id": None, "materialized": False, "blocked_reason": reason}


def _materialization_preflight(
    database: FoundryDatabase,
    *,
    candidate_id: str,
    resource_id: str,
    capability: str,
    decision_id: str,
    activation_epoch: str,
) -> str | None:
    """Re-read safety and eligibility at materialization boundary (fail-closed)."""
    stop = emergency_stop_reason(database, None)
    if stop:
        return f"STOP active at materialization: {stop}"
    veto = matching_veto_reason(database, {"GLOBAL", candidate_id})
    if veto:
        return f"veto active at materialization: {veto}"
    row = database.row("candidates", "candidate_id", candidate_id)
    if row is None:
        return f"candidate {candidate_id} missing at materialization"
    eligibility = assess_eligibility(
        database, row,
        activation_epoch=activation_epoch or current_activation_epoch(database),
        resource_id=resource_id,
        capability=capability,
    )
    # APPROVED with a durable candidate_approved event is the recoverable
    # half-transition left by a crash before mission.created. It is safe to
    # resume because the SELECT decision is still required below and
    # approve_candidate is idempotent on the bound mission identity.
    if not eligibility.eligible and not (
            str(row.get("status") or "") == "APPROVED"
            and _approved_mission_id(database, candidate_id)):
        return f"candidate no longer eligible: {eligibility.reason}"
    decision_found = False
    for event in database.events():
        if event.event_type != "executive.decision_recorded":
            continue
        if (
            str(event.payload.get("decision_id") or "") == decision_id
            and event.payload.get("decision") == "SELECT"
            and str(event.payload.get("candidate_id") or "") == candidate_id
        ):
            decision_found = True
            break
    if not decision_found:
        return f"no valid SELECT decision {decision_id} for {candidate_id}"
    return None


def materialize_selection(
    database: FoundryDatabase,
    *,
    candidate_id: str,
    objective: str,
    resource_id: str,
    capability: str = "write_registered_worktree_files",
    decision_id: str,
    target_relative_path: str = "executive-work",
    activation_epoch: str = "",
    source_id: str = "executive",
) -> dict[str, Any]:
    """Create mission + step contract through canonical candidate→mission path.

    SELECT is permission to ATTEMPT materialization, not irrevocable authority.
    """
    prior = existing_materialization(database, decision_id)
    if prior is not None:
        return prior

    epoch = activation_epoch or current_activation_epoch(database)
    block_reason = _materialization_preflight(
        database,
        candidate_id=candidate_id,
        resource_id=resource_id,
        capability=capability,
        decision_id=decision_id,
        activation_epoch=epoch,
    )
    if block_reason:
        return _record_materialization_blocked(
            database,
            decision_id=decision_id,
            candidate_id=candidate_id,
            reason=block_reason,
            details={"epoch": epoch},
            source_id=source_id,
        )

    mission_id = approve_candidate(
        database, candidate_id,
        resource_id=resource_id,
        next_action=f"execute: {objective}",
        role="builder",
    )
    for state, next_action in (
        ("ORIENTING", f"orient: {objective}"),
        ("ACTION_SELECTION", f"execute: {objective}"),
        ("READY", f"execute: {objective}"),
    ):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": next_action},
            source_type="supervisor",
            source_id=source_id,
            idempotency_key=f"mission.state_changed:{mission_id}:{state}:{decision_id}",
        ))

    artifact_name = f"report-{decision_id[:8]}.md"
    contract = MissionStepContract(
        mission_id=mission_id,
        step_id="executive-step",
        version=1,
        resource_id=resource_id,
        capability=capability,
        target_relative_path=artifact_name,
        instructions=(
            f"Mission objective: {objective}\n"
            f"Create the file {artifact_name} with a concise report stating: "
            f"(1) the objective, (2) what was verified, (3) a line "
            f"VERIFIED_BY executive-worker. Keep under 1200 bytes."
        ),
        expected_effects=(f"{artifact_name} exists",),
        rollback={"method": "delete_file", "reversible": True},
        risk_class="C1",
        verification_criteria=(
            f"{artifact_name} exists",
            "report states the objective",
        ),
        expected_artifacts=(artifact_name,),
        forbidden_paths=(),
        recorded_by="supervisor",
        source="executive",
        provenance={
            "candidate_id": candidate_id,
            "decision_id": decision_id,
            "executive_epoch": epoch,
        },
    )
    record_step_contract(database, contract)
    return {
        "mission_id": mission_id,
        "contract_id": contract.contract_id,
        "step_id": contract.step_id,
        "target_relative_path": artifact_name,
        "materialized": True,
        "blocked_reason": None,
    }
