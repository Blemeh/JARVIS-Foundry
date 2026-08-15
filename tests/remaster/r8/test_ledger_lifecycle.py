from __future__ import annotations

from pathlib import Path

from r8_helpers import FIXED_UTC

from jarvis_foundry.kernel.portfolio.store import PortfolioStore
from jarvis_foundry.orchestration.ledger_events import (
    LIFECYCLE_KINDS,
    link_mission,
    r8_event_registry,
    r8_ledger,
    record_event,
)
from jarvis_foundry.orchestration.projections import lifecycle_snapshot


def test_all_lifecycle_kinds_append_and_chain(tmp_path: Path) -> None:
    ledger = r8_ledger(tmp_path / "ledger.db", fixed_utc=FIXED_UTC)
    payloads: dict[str, dict[str, object]] = {
        "mission.dispatched": {
            "mission_id": "m1",
            "worker_id": "w1",
            "clone_path": "clone",
            "branch": "r8/a",
            "mission_context_hash": "a" * 64,
        },
        "mission.result_ingested": {
            "mission_id": "m1",
            "worker_id": "w1",
            "result_status": "PASS",
            "result_hash": "b" * 64,
            "validated": True,
        },
        "mission.malformed_rejected": {
            "mission_id": "m1",
            "worker_id": "w1",
            "reasons": ["missing keys"],
            "result_hash": "c" * 64,
        },
        "mission.inspected": {
            "mission_id": "m1",
            "diff_summary_hash": "d" * 64,
            "changed_files": ["tests/a.py"],
        },
        "mission.findings_recorded": {"mission_id": "m1", "finding_ids": ["F-1"]},
        "mission.repair_dispatched": {
            "mission_id": "m1",
            "repair_mission_id": "m2",
            "repair_worker_id": "w2",
            "defect_finding_id": "F-1",
            "reproduced_test_command": "pytest -q",
        },
        "mission.reverified": {
            "mission_id": "m1",
            "verifier_id": "v1",
            "verdict": "PASS",
            "reason": "[]",
        },
        "mission.reconciled": {
            "mission_id": "m1",
            "reconciliation_status": "RECONCILED",
            "git_tree_hash": "e" * 64,
            "ledger_head_hash": "f" * 64,
        },
        "mission.rollback_demonstrated": {
            "mission_id": "m1",
            "baseline_tree_hash": "g" * 64,
            "restored_tree_hash": "g" * 64,
            "restored": True,
        },
        "mission.accepted": {
            "mission_id": "m1",
            "accepted_by": "orchestrator",
            "artifact_hashes": {"tests/a.py": "h" * 64},
            "reconciled": True,
        },
        "mission.rejected": {
            "mission_id": "m1",
            "rejected_by": "orchestrator",
            "reasons": ["verdict FAIL"],
        },
        "mission.waiting": {
            "source": "proving",
            "reason": "no work",
            "next_action": "wait",
        },
    }
    for index, kind in enumerate(LIFECYCLE_KINDS, start=1):
        record_event(
            ledger,
            event_type=kind,
            payload=payloads[kind],
            source_type="r8-test",
            source_id="m1",
            idempotency_key=f"{kind}:m1:{index}",
            event_id=f"evt-{kind}-{index}",
            occurred_at=FIXED_UTC,
        )
    assert ledger.verify_chain().valid is True
    assert len(ledger.replay()) == len(LIFECYCLE_KINDS)


def test_projection_replays_mission_state(tmp_path: Path) -> None:
    ledger = r8_ledger(tmp_path / "ledger.db", fixed_utc=FIXED_UTC)
    record_event(
        ledger,
        event_type="mission.dispatched",
        payload={
            "mission_id": "m1",
            "worker_id": "w1",
            "clone_path": "clone",
            "branch": "r8/a",
            "mission_context_hash": "a" * 64,
            "candidate_id": "cand-1",
            "decision_id": "dec-1",
        },
        source_type="r8-test",
        source_id="m1",
        idempotency_key="mission.dispatched:m1",
        event_id="evt-1",
        occurred_at=FIXED_UTC,
    )
    record_event(
        ledger,
        event_type="mission.accepted",
        payload={
            "mission_id": "m1",
            "accepted_by": "orchestrator",
            "artifact_hashes": {},
            "reconciled": True,
        },
        source_type="r8-test",
        source_id="m1",
        idempotency_key="mission.accepted:m1",
        event_id="evt-2",
        occurred_at=FIXED_UTC,
    )
    snapshot = lifecycle_snapshot(ledger.replay())
    assert snapshot["states"]["missions"]["m1"]["status"] == "accepted"
    assert snapshot["states"]["lineage"]["candidates"]["cand-1"] == ["m1"]


def test_mission_link_store_records_lineage(tmp_path: Path) -> None:
    store = PortfolioStore(tmp_path / "store")
    ledger = r8_ledger(tmp_path / "ledger.db", fixed_utc=FIXED_UTC)
    event = record_event(
        ledger,
        event_type="mission.dispatched",
        payload={
            "mission_id": "m1",
            "worker_id": "w1",
            "clone_path": "clone",
            "branch": "r8/a",
            "mission_context_hash": "a" * 64,
            "candidate_id": "cand-1",
            "decision_id": "dec-1",
        },
        source_type="r8-test",
        source_id="m1",
        idempotency_key="mission.dispatched:m1",
        event_id="evt-1",
        occurred_at=FIXED_UTC,
    )
    link_mission(
        store,
        mission_id="m1",
        goal_id="g1",
        candidate_id="cand-1",
        decision_id="dec-1",
        ledger_event_id=event.event_id,
        ledger_header_hash=event.header_hash,
        created_at=FIXED_UTC,
    )
    assert store.mission_links()[0].candidate_id == "cand-1"
    assert r8_event_registry().kinds() >= set(LIFECYCLE_KINDS)
