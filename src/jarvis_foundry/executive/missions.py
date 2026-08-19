"""Canonical mission + contract materialization from executive SELECT."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..discovery_candidates import approve_candidate
from ..events import Event
from ..mission.contracts import MissionStepContract, record_step_contract


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
    """Create mission + step contract through canonical candidate→mission path."""
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
            "executive_epoch": activation_epoch,
        },
    )
    record_step_contract(database, contract)
    return {
        "mission_id": mission_id,
        "contract_id": contract.contract_id,
        "step_id": contract.step_id,
        "target_relative_path": artifact_name,
    }
