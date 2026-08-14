"""V7-S9 — capability discovery, brokerage, effect reconciliation matrix.

Gate matrix (TDD):
  1. capabilities are recorded in the ledger with family, surfaces,
     effect class, risk, and a verification hint;
  2. discovery lists capabilities and filters by family/surface;
  3. brokerage matches a mission need to capabilities (deterministic
     scoring) and records the decision with reasons;
  4. every capability has exactly one effect-class row in the
     reconciliation matrix (effect, reconciliation method, verify probe);
  5. brokerage is Supervisor-only; discovery/reads are open;
  6. a mission need with NO matching capability yields a recorded
     honest WAITING (no fabrication);
  7. the matrix is complete and consistent (every registered capability
     appears; no orphan rows);
  8. re-registration of the same capability dedupes (content hash).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.capabilities.registry import (
    CapabilityError, register_capability, discover_capabilities,
    broker_mission_need, reconciliation_matrix, matrix_complete,
    matrix_for_capability,
)
from jarvis_foundry.supervisor.evolution_control import (
    SUPERVISOR_CONTROLLER,
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "caps.db")
    database.initialize()
    return database


def _cap(capability_id: str, *, family: str = "terminal",
         surfaces: list[str] | None = None, effect_class: str = "local-command",
         risk: str = "R1", verify: str = "exit-code") -> dict[str, object]:
    return {
        "capability_id": capability_id,
        "name": f"Capability {capability_id}",
        "family": family,
        "surfaces": surfaces or ["shell"],
        "effect_class": effect_class,
        "risk": risk,
        "verification_hint": verify,
    }


def test_capabilities_recorded_with_full_fields(db: FoundryDatabase) -> None:
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.terminal.run"),
                        source_type="supervisor", source_id="v7")
    caps = discover_capabilities(db)
    assert len(caps) == 1
    assert caps[0]["capability_id"] == "caps.terminal.run"
    assert caps[0]["family"] == "terminal"
    assert caps[0]["effect_class"] == "local-command"
    assert caps[0]["risk"] == "R1"


def test_register_requires_supervisor(db: FoundryDatabase) -> None:
    with pytest.raises(CapabilityError):
        register_capability(db, controller="candidate-1", **_cap("x"),
                            source_type="supervisor", source_id="v7")


def test_discovery_filters_by_family_and_surface(db: FoundryDatabase) -> None:
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.web.search", family="web",
                               surfaces=["internet"],
                               effect_class="web-read", verify="fetch"),
                        source_type="supervisor", source_id="v7")
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.term.run"),
                        source_type="supervisor", source_id="v7")
    assert len(discover_capabilities(db, family="web")) == 1
    assert len(discover_capabilities(db, surface="shell")) == 1
    assert len(discover_capabilities(db, family="web", surface="shell")) == 0


def test_brokerage_matches_and_records_decision(
        db: FoundryDatabase) -> None:
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.web.search", family="web",
                               surfaces=["internet"],
                               effect_class="web-read", verify="fetch"),
                        source_type="supervisor", source_id="v7")
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.term.run"),
                        source_type="supervisor", source_id="v7")
    chosen = broker_mission_need(
        db, need_id="need-1", surfaces=["internet"],
        controller=SUPERVISOR_CONTROLLER,
        source_type="supervisor", source_id="v7")
    assert chosen == ["caps.web.search"]
    decisions = [e for e in db.events()
                 if e.event_type == "brokerage.decision"]
    assert decisions and decisions[-1].payload["need_id"] == "need-1"
    assert decisions[-1].payload["capability_id"] == "caps.web.search"


def test_brokerage_requires_supervisor(db: FoundryDatabase) -> None:
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.term.run"),
                        source_type="supervisor", source_id="v7")
    with pytest.raises(CapabilityError):
        broker_mission_need(db, need_id="n", surfaces=["shell"],
                            controller="candidate-1",
                            source_type="supervisor", source_id="v7")


def test_unmatched_need_yields_honest_waiting(db: FoundryDatabase) -> None:
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.term.run"),
                        source_type="supervisor", source_id="v7")
    chosen = broker_mission_need(
        db, need_id="need-x", surfaces=["quantum-compute"],
        controller=SUPERVISOR_CONTROLLER,
        source_type="supervisor", source_id="v7")
    assert chosen == []
    waiting = [e for e in db.events()
               if e.event_type == "brokerage.waiting"]
    assert waiting and waiting[-1].payload["need_id"] == "need-x"
    assert waiting[-1].payload["reason"] == "no-matching-capability"


def test_reconciliation_matrix_complete_and_consistent(
        db: FoundryDatabase) -> None:
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.web.search", family="web",
                               surfaces=["internet"], effect_class="web-read",
                               verify="fetch"),
                        source_type="supervisor", source_id="v7")
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.term.run"),
                        source_type="supervisor", source_id="v7")
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.file.write", family="file",
                               surfaces=["disk"], effect_class="file-write",
                               verify="artifact-hash"),
                        source_type="supervisor", source_id="v7")
    matrix = reconciliation_matrix(db)
    assert len(matrix) == 3
    for cap in discover_capabilities(db):
        row = matrix_for_capability(db, cap["capability_id"])
        assert row["capability_id"] == cap["capability_id"]
        assert row["effect_class"] == cap["effect_class"]
        assert row["reconciliation"]  # non-empty
    assert matrix_complete(db) is True


def test_matrix_orphan_detected(db: FoundryDatabase) -> None:
    # a capability with an effect_class unknown to the matrix breaks
    # completeness (the registry refuses it at registration time)
    with pytest.raises(CapabilityError):
        register_capability(
            db, controller=SUPERVISOR_CONTROLLER,
            **_cap("caps.ghost", effect_class="no-such-effect"),
            source_type="supervisor", source_id="v7")


def test_reregistration_dedupes(db: FoundryDatabase) -> None:
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.term.run"),
                        source_type="supervisor", source_id="v7")
    register_capability(db, controller=SUPERVISOR_CONTROLLER,
                        **_cap("caps.term.run"),
                        source_type="supervisor", source_id="v7")
    assert len(discover_capabilities(db)) == 1
    assert len([e for e in db.events()
                if e.event_type == "capability.registered"]) == 1
