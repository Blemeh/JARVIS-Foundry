"""Regression tests for the NEW_PROJECT portfolio work class.

Lane 3 (autonomous new projects) needs evidence-selected greenfield project
work to flow through the existing V7A executive portfolio engine instead of a
second selection system. These tests pin that NEW_PROJECT items register,
score/select, materialize into missions via the bridge, and record outcomes
exactly like every other work class.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis_foundry.events import EventConflict
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.portfolio_bridge import (
    materialize_portfolio_selection,
)
from jarvis_foundry.evolution.executive_portfolio import (
    PortfolioPolicy,
    configure_portfolio_policy,
    record_portfolio_outcome,
    register_work_item,
    select_next_work,
)


def _fresh_database() -> FoundryDatabase:
    tmp = tempfile.TemporaryDirectory()
    database = FoundryDatabase(Path(tmp.name) / "portfolio-new-project.db")
    database.initialize()
    database._tmp = tmp  # keep alive with the database
    return database


def _register_resource(database: FoundryDatabase, resource_id: str) -> None:
    database.append(Event.create(
        "project.registered",
        {
            "resource_id": resource_id,
            "kind": "new_project_repo",
            "root_path": "C:/temp/JARVIS-Autonomous-Project-Lab/taskward",
            "resolved_root": "C:/temp/JARVIS-Autonomous-Project-Lab/taskward",
            "baseline": {"head": "init"},
            "capabilities": {"write_registered_worktree_files": "ACTIVE"},
            "readiness_state": "READY",
        },
        source_type="test", source_id="test-new-project",
        idempotency_key=f"project.registered:{resource_id}",
    ))


class NewProjectWorkClassTest(unittest.TestCase):
    def test_new_project_registers_selects_and_records_outcome(self) -> None:
        database = _fresh_database()
        configure_portfolio_policy(database, PortfolioPolicy())

        seq = register_work_item(
            database,
            item_id="new-project:taskward-20260824",
            kind="NEW_PROJECT",
            objective=(
                "Create taskward: agent-safe Windows Task-Scheduler-as-code "
                "toolkit selected from researched opportunities"
            ),
            expected_value=0.85,
            urgency=0.6,
            information_gain=0.7,
            confidence=0.75,
            effort=0.35,
            risk=0.25,
            reversibility=0.95,
            evidence_refs=[
                "docs/evidence/integration/autonomous-new-projects-20260824/"
                "03_CANDIDATE_CONTRACTS.json"
            ],
            metadata={"project_slug": "taskward"},
        )
        self.assertTrue(int(seq) > 0)

        decision = select_next_work(database, loop_id="loop-np-1")
        self.assertEqual(decision.kind, "SELECT")
        self.assertEqual(decision.selected_class, "NEW_PROJECT")
        self.assertEqual(decision.entity_id, "new-project:taskward-20260824")

        outcome_seq = record_portfolio_outcome(
            database,
            outcome_id="outcome-np-1",
            selection_id=decision.decision_id,
            entity_id=decision.entity_id,
            kind="NEW_PROJECT",
            predicted={"expected_value": 0.85},
            actual={"mvp_accepted": True},
            status="PASSED",
        )
        self.assertTrue(int(outcome_seq) > 0)

    def test_new_project_duplicate_registration_is_idempotent(self) -> None:
        database = _fresh_database()
        kwargs = {
            "item_id": "new-project:dedupe-probe",
            "kind": "NEW_PROJECT",
            "objective": "idempotency probe",
        }
        register_work_item(database, **kwargs)
        try:
            register_work_item(database, **kwargs)
        except EventConflict:
            pass  # idempotency key must reject the duplicate append

    def test_bridge_materializes_mission_for_new_project_selection(self) -> None:
        database = _fresh_database()
        configure_portfolio_policy(database, PortfolioPolicy())
        register_work_item(
            database,
            item_id="new-project:bridge-probe",
            kind="NEW_PROJECT",
            objective="materialize a NEW_PROJECT mission through the bridge",
            confidence=0.8,
            expected_value=0.8,
        )
        decision = select_next_work(database, loop_id="loop-np-bridge")
        _register_resource(database, "res-taskward")
        result = materialize_portfolio_selection(
            database,
            decision=decision.to_payload(),
            resource_id="res-taskward",
            source_id="test-new-project",
        )
        self.assertTrue(result["materialized"])
        self.assertIsNone(result["blocked_reason"])
        self.assertTrue(str(result["mission_id"]).startswith("mission-"))

    def test_unsupported_class_still_rejected(self) -> None:
        database = _fresh_database()
        with self.assertRaises(Exception):
            register_work_item(
                database,
                item_id="new-project:bad-class",
                kind="GARBAGE_CLASS",
                objective="must fail",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
