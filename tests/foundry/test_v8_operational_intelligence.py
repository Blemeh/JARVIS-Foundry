"""V8 operational-intelligence vertical slice and adversarial proof."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.capabilities.registry import (
    CapabilityError,
    capability_status,
    discover_capabilities,
    record_capability_observation,
    register_capability,
)
from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.knowledge_ingestion import ingest_documents
from jarvis_foundry.operational_intelligence import project_operational_intelligence
from jarvis_foundry.roles.router import (
    ProviderRouteCandidate,
    RoutingRequest,
    route_provider_candidates,
)
from jarvis_foundry.supervisor.authority import SUPERVISOR_CONTROLLER
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.hybrid_memory.lifecycle import TierCMemoryStore


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _event(
    database: FoundryDatabase,
    event_type: str,
    payload: dict[str, object],
    key: str,
    *,
    occurred_at: str = "2026-08-29T08:00:00+00:00",
) -> None:
    database.append(Event.create(
        event_type,
        payload,
        source_type="test",
        source_id="v8-test",
        idempotency_key=key,
        occurred_at=occurred_at,
    ))


def test_capability_observation_is_authoritative_bounded_and_idempotent(
    db: FoundryDatabase,
) -> None:
    register_capability(
        db,
        capability_id="caps.code.run",
        name="Code runner",
        family="terminal",
        surfaces=["shell"],
        effect_class="local-command",
        risk="R1",
        verification_hint="exit code",
        controller=SUPERVISOR_CONTROLLER,
        source_type="supervisor",
        source_id="v8-test",
        provider_id="opencode_go",
        model_id="muse-spark-1.3-contributor",
        permissions=["workspace.read"],
        context={"scope": "candidate"},
        tool_support=["shell"],
        limits={"max_duration_s": 60},
    )
    first = record_capability_observation(
        db,
        "caps.code.run",
        status="HEALTHY",
        auth_state="READY",
        availability="AVAILABLE",
        latency_ms=42,
        reliability=0.98,
        evidence_refs=["probe-1"],
        controller=SUPERVISOR_CONTROLLER,
        source_type="supervisor",
        source_id="v8-test",
    )
    second = record_capability_observation(
        db,
        "caps.code.run",
        status="HEALTHY",
        auth_state="READY",
        availability="AVAILABLE",
        latency_ms=42,
        reliability=0.98,
        evidence_refs=["probe-1"],
        controller=SUPERVISOR_CONTROLLER,
        source_type="supervisor",
        source_id="v8-test",
    )
    assert first == second
    assert len([event for event in db.events() if event.event_type == "capability.observed"]) == 1
    status = capability_status(db, "caps.code.run")
    assert status["health"] == "HEALTHY"
    assert status["auth_state"] == "READY"
    assert status["availability"] == "AVAILABLE"
    assert status["latency_ms"] == 42.0
    assert status["reliability"] == 0.98
    assert status["limits"] == {"max_duration_s": 60}
    assert discover_capabilities(db)[0]["required_authority"] == SUPERVISOR_CONTROLLER


def test_capability_observation_cannot_register_unknown_or_bypass_supervisor(
    db: FoundryDatabase,
) -> None:
    with pytest.raises(CapabilityError, match="unknown capability"):
        record_capability_observation(
            db,
            "caps.unknown",
            controller=SUPERVISOR_CONTROLLER,
            source_type="test",
            source_id="v8-test",
        )
    with pytest.raises(CapabilityError, match="only the Supervisor"):
        record_capability_observation(
            db,
            "caps.unknown",
            controller="candidate",
            source_type="test",
            source_id="v8-test",
        )


def _candidate(
    provider: str,
    *,
    health: str = "HEALTHY",
    capabilities: tuple[str, ...] = ("code", "tools"),
    latency: float | None = 100,
) -> ProviderRouteCandidate:
    return ProviderRouteCandidate(
        provider=provider,
        model="model-1",
        capabilities=capabilities,
        context_window_tokens=32_000,
        latency_ms=latency,
        reliability=0.95,
        cost_usd=0.01,
        health=health,
        auth_state="READY",
        availability="AVAILABLE",
        evidence_refs=(f"probe:{provider}",),
    )


def test_route_requires_observed_capability_and_supports_explicit_fallback() -> None:
    request = RoutingRequest(
        task_type="code",
        required_capabilities=("code",),
        preferred_provider="primary",
    )
    selected = route_provider_candidates(request, [_candidate("primary"), _candidate("backup")])
    assert selected.status == "SELECTED"
    assert selected.selected is not None and selected.selected.provider == "primary"
    fallback = route_provider_candidates(
        request,
        [_candidate("primary", health="DEGRADED"), _candidate("backup")],
    )
    assert fallback.status == "FALLBACK"
    assert fallback.fallback_used is True
    assert fallback.selected is not None and fallback.selected.provider == "backup"
    unknown = route_provider_candidates(
        request,
        [_candidate("primary", health="UNKNOWN")],
    )
    assert unknown.status == "UNKNOWN"
    assert unknown.failure_class == "HEALTH_UNKNOWN"
    incapable = route_provider_candidates(
        request,
        [_candidate("primary", capabilities=("research",))],
    )
    assert incapable.status == "BLOCKED"
    assert incapable.failure_class == "CAPABILITY_MISMATCH"


def test_operational_projection_dedupes_recurrence_resolves_and_preserves_provenance(
    db: FoundryDatabase,
) -> None:
    for index in range(3):
        _event(
            db,
            "agent_run.failed",
            {
                "run_id": "run-provider",
                "mission_id": "mission-1",
                "failure_class": "provider_timeout",
                "urgency": 0.6,
                "evidence_refs": [f"failure-proof-{index}"],
            },
            f"failed-{index}",
            occurred_at=f"2026-08-29T{8 + index:02d}:00:00+00:00",
        )
    projection = project_operational_intelligence(
        db,
        now="2026-08-29T12:00:00+00:00",
    )
    signal = next(item for item in projection["signals"] if item["entity_id"] == "mission-1")
    assert signal["kind"] == "FAILED"
    assert signal["recurrence_count"] == 3
    assert signal["suppressed_count"] == 2
    assert signal["salience"] == "CREATE_WORK"
    assert len(signal["source_event_ids"]) == 3
    assert {"failure-proof-0", "failure-proof-1", "failure-proof-2"} <= set(
        signal["evidence_refs"]
    )
    followup = next(item for item in projection["followups"] if item["fingerprint"] == signal["fingerprint"])
    assert followup["state"] == "OPEN"
    assert followup["next_action"]
    assert projection["recurring_work"]["status"] == "OBSERVED"
    assert projection["context"]["facts"]
    assert projection["context"]["facts"][0]["source_event_ids"]
    assert projection["evolution"]["candidate_recommendations"]
    assert projection["evolution"]["candidate_recommendations"][0]["auto_mutation"] is False

    _event(
        db,
        "agent_run.completed",
        {"run_id": "run-provider", "mission_id": "mission-1", "outcome": "COMPLETED"},
        "completed-run-provider",
        occurred_at="2026-08-29T13:00:00+00:00",
    )
    resolved = project_operational_intelligence(db, now="2026-08-29T14:00:00+00:00")
    resolved_signal = next(item for item in resolved["signals"] if item["fingerprint"] == signal["fingerprint"])
    assert resolved_signal["state"] == "RESOLVED"
    assert resolved_signal["resolution_event_id"]
    assert next(item for item in resolved["followups"] if item["fingerprint"] == signal["fingerprint"])["state"] == "RESOLVED"


def test_provider_and_command_center_surfaces_are_truthful_and_stop_is_non_authorizing(
    db: FoundryDatabase,
    tmp_path: Path,
) -> None:
    _event(
        db,
        "provider.health_sample",
        {"provider_id": "opencode_go", "model": "muse-spark-1.3-contributor", "status": "UNKNOWN"},
        "provider-unknown",
    )
    projection = project_operational_intelligence(db)
    assert projection["providers"]["status"] == "OBSERVED"
    provider = next(item for item in projection["providers"]["items"] if item["provider"] == "opencode_go")
    assert provider["health"] == "UNKNOWN"
    assert all(role["status"] in {"UNKNOWN", "UNAVAILABLE", "BLOCKED"} for role in projection["routing"]["roles"])
    before = db.event_count()
    stop_path = tmp_path / "EMERGENCY_STOP"
    EmergencyStop(stop_path).activate("test stop")
    stopped = project_operational_intelligence(db, stop_path=stop_path)
    assert stopped["controls"]["stop_active"] is True
    assert stopped["controls"]["can_execute"] is False
    assert db.event_count() == before
    snapshot = build_snapshot(db, stop_path=stop_path, now="2026-08-29T12:00:00+00:00")
    operational = snapshot["discovery"]["operational_intelligence"]
    assert operational["schema"] == "v8.operational-intelligence.v1"
    assert operational["controls"]["can_execute"] is False
    assert db.event_count() == before


def test_memory_and_knowledge_quality_surfaces_preserve_authority_and_provenance(
    db: FoundryDatabase,
) -> None:
    memories = TierCMemoryStore(db)
    first = memories.record_observation(
        "provider fallback is required when primary health is degraded",
        mission_id="mission-memory",
        source_ids=["run-1"],
        evidence_ids=["evidence-1"],
        confidence=0.82,
    )
    second = memories.record_observation(
        "provider fallback is required when primary health is degraded",
        mission_id="mission-memory",
        source_ids=["run-2"],
        evidence_ids=["evidence-2"],
        confidence=0.74,
    )
    ingest_documents(
        db,
        "mission-knowledge",
        "test",
        "source-a",
        [
            {"statement": "the primary provider must be healthy", "confidence": 0.9},
            {"statement": "the primary provider must be healthy", "confidence": 0.8},
        ],
    )
    projection = project_operational_intelligence(db)
    memory = projection["memory"]
    assert memory["quality"]["total"] == 2
    assert memory["quality"]["provenance_coverage"] == 1.0
    assert {first, second} <= set(memory["consolidation_candidates"][0]["memory_ids"])
    assert memory["consolidation_candidates"][0]["authority"].endswith("CurationPipeline")
    knowledge = projection["knowledge"]
    assert knowledge["quality"]["total"] == 2
    assert knowledge["quality"]["evidence_coverage"] == 1.0
    assert knowledge["consolidation_candidates"]
    assert projection["truth_notes"]
