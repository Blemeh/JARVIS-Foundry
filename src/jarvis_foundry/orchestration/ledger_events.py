"""Durable R8 mission lifecycle ledger kinds, events, and projections."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.clock import Clock, FixedClock, SystemClock
from jarvis_foundry.kernel.events import EventKindRegistry, LedgerEvent
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.portfolio.records import MissionLink
from jarvis_foundry.kernel.portfolio.store import PortfolioStore

LIFECYCLE_KINDS: tuple[str, ...] = (
    "mission.dispatched",
    "mission.result_ingested",
    "mission.malformed_rejected",
    "mission.inspected",
    "mission.findings_recorded",
    "mission.repair_dispatched",
    "mission.reverified",
    "mission.reconciled",
    "mission.rollback_demonstrated",
    "mission.accepted",
    "mission.rejected",
    "mission.waiting",
)


def r8_event_registry() -> EventKindRegistry:
    registry = EventKindRegistry()
    required: dict[str, tuple[str, ...]] = {
        "mission.dispatched": (
            "mission_id",
            "worker_id",
            "clone_path",
            "branch",
            "mission_context_hash",
        ),
        "mission.result_ingested": (
            "mission_id",
            "worker_id",
            "result_status",
            "result_hash",
            "validated",
        ),
        "mission.malformed_rejected": ("mission_id", "worker_id", "reasons", "result_hash"),
        "mission.inspected": ("mission_id", "diff_summary_hash", "changed_files"),
        "mission.findings_recorded": ("mission_id", "finding_ids"),
        "mission.repair_dispatched": (
            "mission_id",
            "repair_mission_id",
            "repair_worker_id",
            "defect_finding_id",
            "reproduced_test_command",
        ),
        "mission.reverified": ("mission_id", "verifier_id", "verdict", "reason"),
        "mission.reconciled": (
            "mission_id",
            "reconciliation_status",
            "git_tree_hash",
            "ledger_head_hash",
        ),
        "mission.rollback_demonstrated": (
            "mission_id",
            "baseline_tree_hash",
            "restored_tree_hash",
            "restored",
        ),
        "mission.accepted": ("mission_id", "accepted_by", "artifact_hashes", "reconciled"),
        "mission.rejected": ("mission_id", "rejected_by", "reasons"),
        "mission.waiting": ("source", "reason", "next_action"),
    }
    for kind, keys in required.items():
        registry.register(kind, required_keys=keys)
    return registry


def r8_ledger(path: Path, *, fixed_utc: str | None = None) -> Ledger:
    clock: Clock = FixedClock(fixed_utc) if fixed_utc else SystemClock()
    return Ledger(path, registry=r8_event_registry(), clock=clock)


def record_event(
    ledger: Ledger,
    *,
    event_type: str,
    payload: Mapping[str, Any],
    source_type: str,
    source_id: str,
    idempotency_key: str,
    event_id: str,
    occurred_at: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> LedgerEvent:
    event = LedgerEvent.create(
        event_type=event_type,
        payload=payload,
        source_type=source_type,
        source_id=source_id,
        idempotency_key=idempotency_key,
        event_id=event_id,
        occurred_at=occurred_at,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )
    return ledger.append(event).event


def link_mission(
    store: PortfolioStore,
    *,
    mission_id: str,
    goal_id: str,
    candidate_id: str,
    decision_id: str,
    ledger_event_id: str,
    ledger_header_hash: str | None,
    created_at: str,
) -> MissionLink:
    link = MissionLink(
        mission_id=mission_id,
        goal_id=goal_id,
        candidate_id=candidate_id,
        decision_id=decision_id,
        ledger_event_id=ledger_event_id,
        ledger_header_hash=ledger_header_hash,
        created_at=created_at,
    )
    store.append_mission_link(link, idempotency_key=f"mission_link:{mission_id}")
    return link
