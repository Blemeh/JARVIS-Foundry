"""V5-4 deterministic connector resource discovery and registration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.connectors.resources import (
    ResourceDiscoveryError,
    discover_resource,
    registration_event,
)
from jarvis_foundry.database import FoundryDatabase


def test_filesystem_discovery_is_canonical_and_durable(tmp_path: Path) -> None:
    root = tmp_path / "owned-root"
    root.mkdir()

    first = discover_resource(
        "filesystem_root",
        root,
        provenance={"source": "v5-test", "owner": "lane"},
    )
    second = discover_resource("filesystem_root", root / ".")

    assert first["resource_id"] == second["resource_id"]
    assert first["resolved_root"] == str(root.resolve())
    assert first["readiness_state"] == "READY"
    assert first["health"] == "READY"
    assert first["allowed_semantics"] == ["read", "write"]
    assert first["provenance"]["source"] == "v5-test"
    assert first["rollback_strategy"] == "restore-from-before-state"
    assert first["reconciliation_strategy"] == "system-readback"

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(registration_event(first))
    row = database.row("resources", "resource_id", first["resource_id"])
    assert row is not None
    baseline = json.loads(row["baseline_json"])
    capabilities = json.loads(row["capabilities_json"])
    assert baseline["endpoint"] == first["endpoint"]
    assert baseline["provenance"]["source"] == "v5-test"
    assert capabilities["allowed_semantics"] == ["read", "write"]


def test_online_and_browser_discovery_is_record_only() -> None:
    http = discover_resource("http_service", "http://127.0.0.1:8765/api")
    browser = discover_resource("browser_endpoint", "http://127.0.0.1:8765/app")

    for record in (http, browser):
        assert record["readiness_state"] == "NOT_TESTED"
        assert record["health"] == "UNKNOWN"
        assert record["observed_capabilities"]["network_probe"] == "NOT_TESTED"
        assert record["provenance"]["observation_mode"] == "record-only"

    with pytest.raises(ResourceDiscoveryError, match="credentials"):
        discover_resource("http_service", "https://user:secret@example.invalid/api")


def test_discovery_idempotency_and_health_update_are_event_derived(tmp_path: Path) -> None:
    root = tmp_path / "service-root"
    root.mkdir()
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()

    ready = discover_resource("filesystem_root", root)
    database.append(registration_event(ready))
    _, inserted = database.append(registration_event(discover_resource("filesystem_root", root)))
    assert inserted is False

    degraded = discover_resource("filesystem_root", root, health_state="DEGRADED")
    database.append(registration_event(degraded))
    row = database.row("resources", "resource_id", ready["resource_id"])
    assert row is not None
    assert json.loads(row["baseline_json"])["health"] == "DEGRADED"
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'project.registered'"
        ).fetchone()[0]
    assert count == 2


def test_filesystem_discovery_rejects_escape_from_bounded_root(tmp_path: Path) -> None:
    bounded = tmp_path / "bounded"
    bounded.mkdir()
    sibling = tmp_path / "bounded-copy"
    sibling.mkdir()

    with pytest.raises(ResourceDiscoveryError, match="bounded root"):
        discover_resource("filesystem_root", sibling, bounded_root=bounded)
