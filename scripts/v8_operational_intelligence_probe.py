"""Run a disposable V8 operational-intelligence workflow proof."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from jarvis_foundry.capabilities.registry import record_capability_observation, register_capability
from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.hybrid_memory.lifecycle import TierCMemoryStore
from jarvis_foundry.knowledge_ingestion import ingest_documents
from jarvis_foundry.operational_intelligence import project_operational_intelligence
from jarvis_foundry.roles.router import ProviderRouteCandidate, RoutingRequest, route_provider_candidates
from jarvis_foundry.supervisor.authority import SUPERVISOR_CONTROLLER
from jarvis_foundry.supervisor.estop import EmergencyStop


def _event(database: FoundryDatabase, event_type: str, payload: dict, key: str, at: str) -> None:
    database.append(Event.create(
        event_type,
        payload,
        source_type="v8-proof",
        source_id="operational-intelligence-probe",
        idempotency_key=key,
        occurred_at=at,
    ))


def run(output: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="jarvis-v8-oi-") as home:
        root = Path(home)
        database = FoundryDatabase(root / "foundry.db")
        database.initialize()
        register_capability(
            database,
            capability_id="caps.code.run",
            name="Disposable code runner",
            family="terminal",
            surfaces=["shell"],
            effect_class="local-command",
            risk="R1",
            verification_hint="exit code",
            controller=SUPERVISOR_CONTROLLER,
            source_type="supervisor",
            source_id="v8-proof",
            provider_id="opencode_go",
            model_id="muse-spark-1.3-contributor",
            permissions=["candidate.read"],
            context={"scope": "disposable"},
            tool_support=["shell"],
            limits={"max_duration_s": 30},
        )
        record_capability_observation(
            database,
            "caps.code.run",
            status="HEALTHY",
            auth_state="READY",
            availability="AVAILABLE",
            latency_ms=25,
            reliability=0.99,
            evidence_refs=["capability-probe"],
            controller=SUPERVISOR_CONTROLLER,
            source_type="supervisor",
            source_id="v8-proof",
        )
        for index in range(3):
            _event(
                database,
                "provider.health_sample",
                {
                    "provider_id": "opencode_go",
                    "model": "muse-spark-1.3-contributor",
                    "status": "DEGRADED",
                    "urgency": 0.62,
                    "evidence_refs": [f"provider-probe-{index}"],
                },
                f"provider-degraded-{index}",
                f"2026-08-29T{8 + index:02d}:00:00+00:00",
            )
        for index in range(2):
            _event(
                database,
                "agent_run.failed",
                {
                    "run_id": "proof-run",
                    "mission_id": "proof-mission",
                    "provider_id": "opencode_go",
                    "failure_class": "provider_timeout",
                    "urgency": 0.58,
                    "evidence_refs": [f"failure-proof-{index}"],
                },
                f"proof-failed-{index}",
                f"2026-08-29T{10 + index:02d}:00:00+00:00",
            )
        memory_store = TierCMemoryStore(database)
        memory_store.record_observation(
            "provider fallback is required when primary health is degraded",
            mission_id="proof-mission",
            source_ids=["proof-run-0"],
            evidence_ids=["memory-proof-0"],
            confidence=0.8,
        )
        memory_store.record_observation(
            "provider fallback is required when primary health is degraded",
            mission_id="proof-mission",
            source_ids=["proof-run-1"],
            evidence_ids=["memory-proof-1"],
            confidence=0.7,
        )
        ingest_documents(
            database,
            "proof-mission",
            "v8-proof",
            "knowledge-proof",
            [
                {"statement": "the provider must be healthy", "confidence": 0.9},
                {"statement": "the provider must be healthy", "confidence": 0.8},
            ],
        )
        before = project_operational_intelligence(database, now="2026-08-29T12:00:00+00:00")
        failure = next(item for item in before["signals"] if item["kind"] == "FAILED" and item["mission_id"] == "proof-mission")
        fallback = route_provider_candidates(
            RoutingRequest(task_type="code", required_capabilities=("code",), preferred_provider="primary"),
            [
                ProviderRouteCandidate(
                    provider="primary", model="model-1", capabilities=("research",),
                    health="HEALTHY", auth_state="READY", availability="AVAILABLE",
                    context_window_tokens=16_000, latency_ms=100, reliability=0.9,
                ),
                ProviderRouteCandidate(
                    provider="backup", model="model-1", capabilities=("code",),
                    health="HEALTHY", auth_state="READY", availability="AVAILABLE",
                    context_window_tokens=16_000, latency_ms=120, reliability=0.9,
                ),
            ],
        )
        _event(
            database,
            "provider.health_sample",
            {
                "provider_id": "opencode_go",
                "model": "muse-spark-1.3-contributor",
                "status": "HEALTHY",
                "evidence_refs": ["provider-recovery"],
            },
            "provider-recovered",
            "2026-08-29T13:00:00+00:00",
        )
        after = project_operational_intelligence(database, now="2026-08-29T14:00:00+00:00")
        recovered = next(item for item in after["signals"] if item["fingerprint"] == failure["fingerprint"])
        event_count = database.event_count()
        stop_path = root / "EMERGENCY_STOP"
        EmergencyStop(stop_path).activate("disposable proof")
        stopped = project_operational_intelligence(database, stop_path=stop_path)
        snapshot = build_snapshot(database, stop_path=stop_path, now="2026-08-29T14:00:00+00:00")
        result = {
            "schema": "v8.operational-intelligence.proof.v1",
            "workflow": [
                "capability registration and health observation",
                "repeated provider degradation",
                "repeated agent failure classified as CREATE_WORK",
                "memory and knowledge quality/consolidation candidates surfaced",
                "existing EvolutionControl path receives a read-only improvement recommendation",
                "explicit fallback selected only for a capable observed candidate",
                "provider recovery resolves the signal",
                "STOP leaves read projections intact and non-authorizing",
            ],
            "checks": {
                "repeated_failure_deduped": failure["suppressed_count"] >= 1,
                "repeated_failure_create_work": failure["salience"] == "CREATE_WORK",
                "followup_open": any(item["fingerprint"] == failure["fingerprint"] and item["state"] == "OPEN" for item in before["followups"]),
                "recurring_work_observed": before["recurring_work"]["status"] == "OBSERVED",
                "memory_quality_visible": before["memory"]["quality"]["provenance_coverage"] == 1.0 and bool(before["memory"]["consolidation_candidates"]),
                "knowledge_quality_visible": before["knowledge"]["quality"]["evidence_coverage"] == 1.0 and bool(before["knowledge"]["consolidation_candidates"]),
                "evolution_recommendation_visible": bool(before["evolution"]["candidate_recommendations"]) and before["evolution"]["candidate_recommendations"][0]["auto_mutation"] is False,
                "fallback_capability_checked": fallback.status == "FALLBACK" and fallback.selected is not None and fallback.selected.provider == "backup",
                "recovery_resolved": recovered["state"] == "RESOLVED",
                "stop_non_authorizing": stopped["controls"]["can_execute"] is False,
                "snapshot_surface_present": snapshot["discovery"]["operational_intelligence"]["schema"] == "v8.operational-intelligence.v1",
                "projection_did_not_append": database.event_count() == event_count,
            },
            "evidence": {
                "failure_signal": failure,
                "recovered_signal": recovered,
                "fallback_status": fallback.to_dict(),
                "event_count": event_count,
                "snapshot_status": snapshot["discovery"]["operational_intelligence"]["status"],
            },
        }
    if not all(result["checks"].values()):
        raise RuntimeError(json.dumps(result, sort_keys=True))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evidence/v8-operational-intelligence/proof.json"))
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({"output": str(args.output.resolve()), "checks": result["checks"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
