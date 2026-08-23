"""V5-11 health/quota/cost/rerouting truth."""

from __future__ import annotations

from pathlib import Path

from jarvis_foundry.connectors.contract import ConnectorSpec
from jarvis_foundry.connectors.operations import (
    operational_status,
    record_cost_sample,
    record_operational_health,
    select_operational_connector,
)
from jarvis_foundry.connectors.registry import register_connector, record_quota_use
from jarvis_foundry.database import FoundryDatabase


def _spec(connector_id: str, *, cost_known: bool = False) -> ConnectorSpec:
    return ConnectorSpec(
        connector_id=connector_id, kind="test-service", version="1.0.0",
        actions={"service.read": "read"}, capabilities={"read": "READY"},
        retry_policy={"max_attempts": 1, "backoff_s": []},
        target_kinds=("opaque",), cost_metadata={"cost_known": cost_known},
    )


def test_status_is_truthful_and_preferred_connector_reroutes(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    register_connector(db, _spec("conn-a"))
    register_connector(db, _spec("conn-b", cost_known=True))
    record_operational_health(db, "conn-a", "BLOCKED", detail="service outage")
    record_operational_health(db, "conn-b", "READY")
    outcome = select_operational_connector(db, "service.read", preferred="conn-a")
    assert outcome.connector is not None
    assert outcome.connector.connector_id == "conn-b"
    assert "rerouted" in outcome.reason and "BLOCKED" in outcome.reason
    status = operational_status(db, "conn-a")
    assert status["health"] == "BLOCKED"
    assert status["readiness"] == "BLOCKED"
    assert status["cost_state"] == "UNKNOWN"


def test_quota_exhaustion_cost_sample_and_veto_are_visible(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    register_connector(db, _spec("conn-c"))
    record_operational_health(db, "conn-c", "READY")
    record_quota_use(db, "conn-c", used=10, limit=10)
    status = operational_status(db, "conn-c")
    assert status["health"] == "READY"
    assert status["quota_state"] == "EXHAUSTED"
    assert status["cost_state"] == "UNKNOWN"
    record_cost_sample(db, "conn-c", cost_usd=0.12, known=True)
    assert operational_status(db, "conn-c")["cost_state"] == "KNOWN"
    record_operational_health(db, "conn-c", "VETOED", detail="owner veto")
    vetoed = select_operational_connector(db, "service.read")
    assert vetoed.connector is None
    assert "VETOED" in vetoed.reason

