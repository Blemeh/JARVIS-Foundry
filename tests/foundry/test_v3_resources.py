"""Resource envelope counters are bounded and read-only."""
from __future__ import annotations

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.v3_resources import (
    ResourceLimits,
    capture_resource_snapshot,
    envelope_violations,
)


def test_resource_snapshot_and_limits(tmp_path):
    database = FoundryDatabase(tmp_path / "resource.db")
    database.initialize()
    database.append(Event.create(
        "test.resource_probe", {"probe": True},
        source_type="test", source_id="resource",
        idempotency_key="resource:probe"))
    before = database.event_count()
    snapshot = capture_resource_snapshot(database, provider_calls=0,
                                         worker_count=1, retry_count=0)
    assert snapshot["event_count"] == before
    assert snapshot["provider_calls"] == 0
    assert envelope_violations(snapshot, ResourceLimits()) == []
    assert envelope_violations(
        snapshot, ResourceLimits(max_event_count=0))
    assert database.event_count() == before

