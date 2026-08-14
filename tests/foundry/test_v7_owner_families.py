"""V7-S11 — S10 gate extension: communications, calendar, accounts,
services, purchases, finance families (declarative, deny-by-default).

Gate matrix (TDD):
  1. the six S11 families are declared with read-only + effectful classes;
  2. read-only operations pass; effectful operations deny by default
     (recorded, never silent);
  3. Supervisor-only gating holds for the new families;
  4. the full 12-family union (S10 + S11) is coherent — no family
     collisions, no class overlaps between families;
  5. every S11 family's read op passes and every effectful op denies;
  6. gate decisions are ledger-recorded with the family and operation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.gates.minigates import (
    FAMILIES, gate_operation, decision_history,
)
from jarvis_foundry.gates.owner_families import OWNER_FAMILIES
from jarvis_foundry.supervisor.evolution_control import (
    SUPERVISOR_CONTROLLER,
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "s11.db")
    database.initialize()
    return database


def test_s11_families_are_declared(db: FoundryDatabase) -> None:
    expected = {"communications", "calendar", "accounts",
                "services", "purchases", "finance"}
    assert set(OWNER_FAMILIES) == expected
    for family in expected:
        policy = OWNER_FAMILIES[family]
        assert policy["read_only_classes"]
        assert policy["effectful_classes"]


def test_full_union_is_coherent(db: FoundryDatabase) -> None:
    union = dict(FAMILIES)
    union.update(OWNER_FAMILIES)
    assert set(union) == {
        "research", "monitoring", "local-computer", "git", "deployment",
        "cloud", "communications", "calendar", "accounts", "services",
        "purchases", "finance"}
    s10_names = {"research", "monitoring", "local-computer",
                 "git", "deployment", "cloud"}
    s10_classes = {
        op for family, policy in FAMILIES.items() if family in s10_names
        for op in policy["read_only_classes"] + policy["effectful_classes"]}
    for family, policy in OWNER_FAMILIES.items():
        for operation in (policy["read_only_classes"] +
                          policy["effectful_classes"]):
            assert operation not in s10_classes, (
                f"S11 class collides with S10: {operation} in {family}")
    # S11 families must not collide with each other
    seen: dict[str, str] = {}
    for family, policy in OWNER_FAMILIES.items():
        for operation in (policy["read_only_classes"] +
                          policy["effectful_classes"]):
            assert operation not in seen, (
                f"S11 collision: {operation} in {family} and "
                f"{seen[operation]}")
            seen[operation] = family


def test_s11_read_ops_pass_and_effectful_deny(
        db: FoundryDatabase) -> None:
    reads = {
        "communications": "list_channels",
        "calendar": "list_events",
        "accounts": "list_accounts",
        "services": "list_services",
        "purchases": "list_orders",
        "finance": "balance",
    }
    effectful = {
        "communications": "send_message",
        "calendar": "create_event",
        "accounts": "create_account",
        "services": "subscribe_service",
        "purchases": "place_order",
        "finance": "transfer",
    }
    for family, operation in reads.items():
        result = gate_operation(
            db, family=family, operation=operation,
            controller=SUPERVISOR_CONTROLLER,
            params={}, source_type="supervisor", source_id="v7")
        assert result["allowed"] is True, (family, operation)
    for family, operation in effectful.items():
        result = gate_operation(
            db, family=family, operation=operation,
            controller=SUPERVISOR_CONTROLLER,
            params={}, source_type="supervisor", source_id="v7")
        assert result["allowed"] is False, (family, operation)
        assert result["denied"] is True
        assert result["recorded"] is True


def test_s11_gates_require_supervisor(db: FoundryDatabase) -> None:
    with pytest.raises(Exception):
        gate_operation(db, family="finance", operation="balance",
                       controller="candidate-1",
                       params={}, source_type="supervisor", source_id="v7")


def test_unknown_s11_operation_refused(db: FoundryDatabase) -> None:
    with pytest.raises(Exception):
        gate_operation(db, family="calendar", operation="no-such-op",
                       controller=SUPERVISOR_CONTROLLER,
                       params={}, source_type="supervisor", source_id="v7")


def test_s11_decisions_recorded_with_family(db: FoundryDatabase) -> None:
    gate_operation(db, family="finance", operation="balance",
                   controller=SUPERVISOR_CONTROLLER,
                   params={}, source_type="supervisor", source_id="v7")
    gate_operation(db, family="finance", operation="transfer",
                   controller=SUPERVISOR_CONTROLLER,
                   params={"amount": 100},
                   source_type="supervisor", source_id="v7")
    history = decision_history(db)
    finance = [h for h in history if h["family"] == "finance"]
    assert len(finance) == 2
    assert finance[0]["allowed"] is True
    assert finance[1]["allowed"] is False
