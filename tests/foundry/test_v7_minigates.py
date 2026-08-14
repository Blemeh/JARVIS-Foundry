"""V7-S10 — capability-family mini-gates (research, monitoring,
local-computer, git, deployment, cloud).

Gate matrix (TDD):
  1. every family has a gate: operations are decision-recorded and
     allow/deny is deterministic;
  2. all gates are Supervisor-only (controller gate);
  3. read-only operations pass by default; effectful operations require
     the gate's explicit policy (deny-by-default for effectful classes);
  4. every gate decision is ledger-recorded with the operation, family,
     reason, and controller;
  5. gate policies are declarative per family (allowed operation classes);
  6. a denied operation returns a refusal record (never a silent no-op).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.gates.minigates import (
    GateError, FAMILIES, family_policy, gate_operation,
    decision_history,
)
from jarvis_foundry.supervisor.evolution_control import (
    SUPERVISOR_CONTROLLER,
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "gates.db")
    database.initialize()
    return database


def test_all_six_families_are_declared(db: FoundryDatabase) -> None:
    expected = {"research", "monitoring", "local-computer",
                "git", "deployment", "cloud"}
    assert set(FAMILIES) == expected
    for family in expected:
        policy = family_policy(db, family)
        assert policy["family"] == family
        assert policy["read_only_classes"]
        assert policy["effectful_classes"]


def test_read_only_operations_pass_and_are_recorded(
        db: FoundryDatabase) -> None:
    result = gate_operation(db, family="research", operation="web_search",
                            controller=SUPERVISOR_CONTROLLER,
                            params={"query": "test"},
                            source_type="supervisor", source_id="v7")
    assert result["allowed"] is True
    assert result["family"] == "research"
    assert result["operation"] == "web_search"
    history = decision_history(db)
    assert history[-1]["operation"] == "web_search"
    assert history[-1]["allowed"] is True


def test_effectful_operations_deny_by_default(db: FoundryDatabase) -> None:
    result = gate_operation(db, family="git", operation="push",
                            controller=SUPERVISOR_CONTROLLER,
                            params={"remote": "origin"},
                            source_type="supervisor", source_id="v7")
    assert result["allowed"] is False
    assert "deny" in result["reason"].lower()
    history = decision_history(db)
    assert history[-1]["allowed"] is False


def test_gates_require_supervisor(db: FoundryDatabase) -> None:
    with pytest.raises(GateError):
        gate_operation(db, family="cloud", operation="list",
                       controller="candidate-1",
                       params={}, source_type="supervisor", source_id="v7")


def test_unknown_family_and_operation_refused(
        db: FoundryDatabase) -> None:
    with pytest.raises(GateError):
        gate_operation(db, family="no-such-family", operation="x",
                       controller=SUPERVISOR_CONTROLLER,
                       params={}, source_type="supervisor", source_id="v7")
    with pytest.raises(GateError):
        gate_operation(db, family="git", operation="no-such-op",
                       controller=SUPERVISOR_CONTROLLER,
                       params={}, source_type="supervisor", source_id="v7")


def test_each_family_read_operation_passes(db: FoundryDatabase) -> None:
    reads = {
        "research": "web_search",
        "monitoring": "list_monitors",
        "local-computer": "read_file",
        "git": "status",
        "deployment": "dry_run",
        "cloud": "list_resources",
    }
    for family, operation in reads.items():
        result = gate_operation(
            db, family=family, operation=operation,
            controller=SUPERVISOR_CONTROLLER,
            params={}, source_type="supervisor", source_id="v7")
        assert result["allowed"] is True, (family, operation)


def test_effectful_classes_denied_everywhere(db: FoundryDatabase) -> None:
    effectful = {
        "research": "web_write",
        "monitoring": "send_alert",
        "local-computer": "write_file",
        "git": "push",
        "deployment": "deploy",
        "cloud": "create_resource",
    }
    for family, operation in effectful.items():
        result = gate_operation(
            db, family=family, operation=operation,
            controller=SUPERVISOR_CONTROLLER,
            params={}, source_type="supervisor", source_id="v7")
        assert result["allowed"] is False, (family, operation)
        assert result["denied"] is True


def test_refusal_is_never_a_silent_noop(db: FoundryDatabase) -> None:
    result = gate_operation(db, family="deployment", operation="deploy",
                            controller=SUPERVISOR_CONTROLLER,
                            params={"target": "prod"},
                            source_type="supervisor", source_id="v7")
    assert result["denied"] is True
    assert result["recorded"] is True
    history = decision_history(db)
    assert len([h for h in history if h["denied"]]) >= 1
