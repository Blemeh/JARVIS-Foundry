"""V5-S4: connector contract, registry, health, quota, and routing.

One connector's failure must never stop unrelated work; retries reroute
only among verified, policy-allowed connectors; quota/budget exhaustion
blocks selection; disable/re-enable works; capability mismatch blocks;
STOP blocks everything; contract versions validate and replay safely.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.connectors.contract import (
    ACTION_SEMANTICS,
    ConnectorSpec,
    check_action_semantics,
    validate_connector_spec,
)
from jarvis_foundry.connectors.registry import (
    ConnectorUnavailable,
    RoutingOutcome,
    connector_status,
    disable_connector,
    enable_connector,
    record_health_sample,
    record_quota_use,
    register_connector,
    select_connector,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _spec(connector_id: str = "conn-fs", **overrides: object) -> ConnectorSpec:
    values: dict[str, object] = {
        "connector_id": connector_id,
        "kind": "filesystem",
        "version": "1.0.0",
        "actions": {"file.read": "read", "file.write": "write",
                    "file.execute": "execute"},
        "capabilities": {"read": "READY", "write": "READY",
                         "execute": "READY"},
        "retry_policy": {"max_attempts": 3, "backoff_s": [1, 3, 9]},
    }
    values.update(overrides)
    return ConnectorSpec(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# contract validation
# ---------------------------------------------------------------------------

def test_valid_spec_passes() -> None:
    assert validate_connector_spec(_spec()) is None


@pytest.mark.parametrize("override,why", [
    ({"connector_id": ""}, "connector id"),
    ({"version": "1.0"}, "version"),
    ({"version": "abc"}, "version"),
    ({"actions": {"file.read": "teleport"}}, "semantic"),
    ({"actions": {}}, "actions"),
    ({"capabilities": {"read": "MAYBE"}}, "capability"),
    ({"retry_policy": {"max_attempts": 0, "backoff_s": [1]}}, "attempts"),
    ({"retry_policy": {"max_attempts": 3, "backoff_s": [-1]}}, "backoff"),
])
def test_invalid_specs_refused(override: dict[str, object],
                               why: str) -> None:
    error = validate_connector_spec(_spec(**override))
    assert error is not None and why in error


def test_action_semantics_enum() -> None:
    assert ACTION_SEMANTICS == frozenset(
        {"read", "write", "execute", "communicate", "external_effect"})


# ---------------------------------------------------------------------------
# action-semantics declaration (S2 verifier F3 structural fix)
# ---------------------------------------------------------------------------

def test_check_action_semantics_mutating_requires_effects(
        db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    # mutating action (write) with NO declared effects -> gap
    gap = check_action_semantics(db, "conn-fs", "file.write",
                                 expected_effects=(), before_state_capture={})
    assert gap is not None and "effects" in gap
    # with declared effects but no before-state -> gap
    gap = check_action_semantics(db, "conn-fs", "file.write",
                                 expected_effects=("create x",),
                                 before_state_capture={})
    assert gap is not None and "before" in gap
    # fully declared -> no gap
    assert check_action_semantics(
        db, "conn-fs", "file.write", expected_effects=("create x",),
        before_state_capture={"hash": "h"}) is None
    # read semantics never require effects
    assert check_action_semantics(
        db, "conn-fs", "file.read", expected_effects=(), before_state_capture={}) \
        is None
    # unknown connector / undeclared action -> None (no declaration, no claim)
    assert check_action_semantics(
        db, "conn-ghost", "file.write", expected_effects=(), before_state_capture={}) \
        is None


# ---------------------------------------------------------------------------
# registry: registration, health, quota, disable/enable
# ---------------------------------------------------------------------------

def test_register_connector_idempotent_and_versioned(db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    register_connector(db, _spec())  # identical re-delivery dedupes
    with db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'connector.registered'").fetchone()[0]
    assert count == 1
    # a NEW version is a new registration record (append-only history)
    register_connector(db, _spec(version="1.1.0"))
    with db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'connector.registered'").fetchone()[0]
    assert count == 2
    assert connector_status(db, "conn-fs")["version"] == "1.1.0"


def test_health_unknown_until_sampled(db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    assert connector_status(db, "conn-fs")["health"] == "UNKNOWN"


def test_health_samples_and_derivation(db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    record_health_sample(db, "conn-fs", "HEALTHY")
    assert connector_status(db, "conn-fs")["health"] == "HEALTHY"
    record_health_sample(db, "conn-fs", "DEGRADED")
    assert connector_status(db, "conn-fs")["health"] == "DEGRADED"
    # duplicate sample re-delivery dedupes
    record_health_sample(db, "conn-fs", "DEGRADED")
    with db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'connector.health_sample'").fetchone()[0]
    assert count == 2


def test_invalid_health_status_refused(db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    with pytest.raises(ValueError):
        record_health_sample(db, "conn-fs", "ON_FIRE")


def test_quota_accounting(db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    record_quota_use(db, "conn-fs", used=2, limit=10)
    quota = connector_status(db, "conn-fs")["quota"]
    assert quota == {"used": 2, "limit": 10}
    record_quota_use(db, "conn-fs", used=10, limit=10)
    assert connector_status(db, "conn-fs")["quota"]["used"] == 10


def test_disable_and_enable(db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    assert connector_status(db, "conn-fs")["disabled"] is False
    disable_connector(db, "conn-fs", reason="maintenance")
    assert connector_status(db, "conn-fs")["disabled"] is True
    enable_connector(db, "conn-fs")
    assert connector_status(db, "conn-fs")["disabled"] is False


# ---------------------------------------------------------------------------
# routing: isolation, reroute, quota, capability, STOP, determinism
# ---------------------------------------------------------------------------

def test_select_healthy_connector(db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    record_health_sample(db, "conn-fs", "HEALTHY")
    outcome = select_connector(db, "file.read",
                               capabilities_required=("read",))
    assert outcome.connector is not None
    assert outcome.connector.connector_id == "conn-fs"


def test_select_unknown_health_not_selected(db: FoundryDatabase) -> None:
    register_connector(db, _spec())  # no health samples -> UNKNOWN
    outcome = select_connector(db, "file.read",
                               capabilities_required=("read",))
    assert outcome.connector is None
    assert "UNKNOWN" in outcome.reason


def test_one_connector_failure_isolated(db: FoundryDatabase) -> None:
    """Gate probe: one connector's outage never stops unrelated work."""
    register_connector(db, _spec("conn-a"))
    register_connector(db, _spec("conn-b"))
    record_health_sample(db, "conn-a", "DEGRADED")  # outage
    record_health_sample(db, "conn-b", "HEALTHY")
    outcome = select_connector(db, "file.read",
                               capabilities_required=("read",))
    assert outcome.connector is not None
    assert outcome.connector.connector_id == "conn-b"


def test_reroute_when_preferred_fails(db: FoundryDatabase) -> None:
    """Automatic rerouting only among verified, policy-allowed
    connectors: the preferred connector is degraded -> the healthy
    policy-allowed one is selected."""
    register_connector(db, _spec("conn-a"))
    register_connector(db, _spec("conn-b"))
    record_health_sample(db, "conn-a", "DEGRADED")
    record_health_sample(db, "conn-b", "HEALTHY")
    outcome = select_connector(db, "file.read", preferred="conn-a",
                               capabilities_required=("read",))
    assert outcome.connector.connector_id == "conn-b"
    assert "conn-a" in outcome.reason


def test_reroute_reason_names_the_real_gap(db: FoundryDatabase) -> None:
    """S4 verifier deleg_761e4d85 LOW: when the preferred connector IS
    registered but fails a gate, the reroute reason must name the real
    gap — not claim the connector is unregistered."""
    register_connector(db, _spec("conn-a"))
    register_connector(db, _spec("conn-b"))
    record_health_sample(db, "conn-a", "DEGRADED")
    record_health_sample(db, "conn-b", "HEALTHY")
    outcome = select_connector(db, "file.read", preferred="conn-a",
                               capabilities_required=("read",))
    assert outcome.connector.connector_id == "conn-b"
    assert "DEGRADED" in outcome.reason
    assert "not registered" not in outcome.reason
    # quota-exhausted preferred: same truthful attribution
    record_quota_use(db, "conn-b", used=10, limit=10)
    outcome = select_connector(db, "file.read", preferred="conn-b",
                               capabilities_required=("read",))
    assert outcome.connector is None
    assert "quota" in outcome.reason
    assert "not registered" not in outcome.reason


def test_quota_exhaustion_blocks_selection(db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    record_health_sample(db, "conn-fs", "HEALTHY")
    record_quota_use(db, "conn-fs", used=10, limit=10)
    outcome = select_connector(db, "file.read",
                               capabilities_required=("read",))
    assert outcome.connector is None
    assert "quota" in outcome.reason


def test_capability_mismatch_blocks_selection(db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    record_health_sample(db, "conn-fs", "HEALTHY")
    outcome = select_connector(db, "file.write",
                               capabilities_required=("unlisted",))
    assert outcome.connector is None
    assert "capability" in outcome.reason


def test_undeclared_action_not_selected(db: FoundryDatabase) -> None:
    register_connector(db, _spec())  # no browser.navigate action
    outcome = select_connector(db, "browser.navigate",
                               capabilities_required=("navigate",))
    assert outcome.connector is None


def test_disabled_connector_not_selected(db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    record_health_sample(db, "conn-fs", "HEALTHY")
    disable_connector(db, "conn-fs", reason="maintenance")
    outcome = select_connector(db, "file.read",
                               capabilities_required=("read",))
    assert outcome.connector is None
    assert "disabled" in outcome.reason


def test_stop_blocks_all_selection(db: FoundryDatabase,
                                   tmp_path: Path) -> None:
    register_connector(db, _spec())
    record_health_sample(db, "conn-fs", "HEALTHY")
    marker = tmp_path / "EMERGENCY_STOP"
    marker.write_text("", encoding="utf-8")
    outcome = select_connector(db, "file.read",
                               capabilities_required=("read",),
                               stop_path=marker)
    assert outcome.connector is None
    assert "STOP" in outcome.reason


def test_veto_blocks_selection(db: FoundryDatabase) -> None:
    register_connector(db, _spec())
    record_health_sample(db, "conn-fs", "HEALTHY")
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-veto", "command_type": "VETO_ACTION",
         "target": "conn-fs", "exact_text": "veto the fs connector",
         "reason": "t"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-veto"))
    outcome = select_connector(db, "file.read",
                               capabilities_required=("read",))
    assert outcome.connector is None
    assert "veto" in outcome.reason


def test_selection_is_deterministic(db: FoundryDatabase) -> None:
    register_connector(db, _spec("conn-b"))
    register_connector(db, _spec("conn-a"))
    record_health_sample(db, "conn-a", "HEALTHY")
    record_health_sample(db, "conn-b", "HEALTHY")
    first = select_connector(db, "file.read",
                             capabilities_required=("read",))
    second = select_connector(db, "file.read",
                              capabilities_required=("read",))
    assert first.connector.connector_id == second.connector.connector_id
    # stable order (by connector id) — no randomness
    assert first.connector.connector_id == "conn-a"


def test_routing_replay_and_rebuild(db: FoundryDatabase) -> None:
    """Contract version/replay: events survive rebuild; routing derives
    from the replayed stream identically."""
    register_connector(db, _spec())
    record_health_sample(db, "conn-fs", "HEALTHY")
    record_quota_use(db, "conn-fs", used=1, limit=10)
    before = select_connector(db, "file.read",
                              capabilities_required=("read",))
    db.rebuild_projections()
    after = select_connector(db, "file.read",
                             capabilities_required=("read",))
    assert after.connector.connector_id == before.connector.connector_id
    with db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type LIKE "
            "'connector.%'").fetchone()[0]
    assert count == 3  # registered + health_sample + quota_recorded
