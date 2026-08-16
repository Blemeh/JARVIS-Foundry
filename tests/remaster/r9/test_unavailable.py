from __future__ import annotations

from r9_helpers import make_ledger, make_registry

from jarvis_foundry.capabilities_live.adapters.unavailable import UNAVAILABLE_CONNECTORS
from jarvis_foundry.capabilities_live.states import CapabilityStatus


def test_unavailable_connectors_recorded_without_blocking(tmp_path) -> None:
    registry = make_registry(make_ledger(tmp_path))
    for connector in UNAVAILABLE_CONNECTORS:
        registry.discover(
            capability_id=connector.capability_id,
            domain=connector.domain,
            name=connector.name,
            system_id="none",
        )
        if connector.status == "BLOCKED":
            assert (
                registry.block(connector.capability_id, connector.reason)
                == CapabilityStatus.BLOCKED
            )
        else:
            assert (
                registry.not_tested(connector.capability_id, connector.reason)
                == CapabilityStatus.NOT_TESTED
            )

    states = registry.snapshot()["states"]["capabilities"]
    assert states["capability.browser"]["status"] == "NOT_TESTED"
    assert states["capability.communication"]["status"] == "BLOCKED"
    assert states["capability.calendar"]["status"] == "BLOCKED"
    assert "capability.active" not in [e.event_type for e in registry.events()]
