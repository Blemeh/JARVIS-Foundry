"""Bounded Phase-3 synthetic/controlled probe; no desktop or live DB access."""
from __future__ import annotations

import json
import time
import tracemalloc
from datetime import datetime, timedelta, timezone

from jarvis_foundry.perception.contracts import PerceptionObservation, observation_id_for
from jarvis_foundry.workflow_discovery.contracts import WorkflowDiscoveryConfig
from jarvis_foundry.workflow_discovery.miner import mine


BASE = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
CONFIG = WorkflowDiscoveryConfig(
    enabled=True,
    idle_gap_seconds=120,
    max_session_duration_seconds=3600,
    min_occurrences=2,
    max_similarity_comparisons=256,
)


def _observation(index: int, offset: int, application: str) -> PerceptionObservation:
    start = BASE + timedelta(seconds=offset)
    end = start + timedelta(seconds=10)
    captured = start.isoformat().replace("+00:00", "Z")
    interval_end = end.isoformat().replace("+00:00", "Z")
    return PerceptionObservation(
        observation_id=observation_id_for(
            application=application, resource="phase3-probe",
            activity_class="action", captured_at=captured,
            interval_end=interval_end),
        source_connector_id="screenpipe-local",
        source_pointer={"source_ids": [index], "external": True,
                        "table": "frames/ui_events"},
        captured_at=captured,
        interval_end=interval_end,
        recorded_at=(end + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        application=application,
        resource="phase3-probe",
        activity_class="action",
        semantic_summary=f"{application} - phase3-probe: action",
        content_hash=f"probe-{index}",
        raw_external=True,
        source_version="0.4.40",
    )


def _history(observation_count: int) -> list[PerceptionObservation]:
    names = ("editor", "terminal", "browser", "database")
    observations: list[PerceptionObservation] = []
    session_count = observation_count // len(names)
    for session in range(session_count):
        start = session * 200
        observations.extend(
            _observation(session * len(names) + position,
                         start + position * 20, name)
            for position, name in enumerate(names)
        )
    return observations


def benchmark(observation_count: int) -> dict[str, object]:
    observations = _history(observation_count)
    tracemalloc.start()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    result = mine(observations, CONFIG)
    cpu_seconds = time.process_time() - cpu_start
    wall_seconds = time.perf_counter() - wall_start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "observations_processed": len(observations),
        "sessions": len(result.sessions),
        "patterns": len(result.patterns),
        "repeated_occurrences": result.repeated_occurrences,
        "wall_seconds": round(wall_seconds, 6),
        "cpu_seconds": round(cpu_seconds, 6),
        "peak_traced_memory_bytes": peak,
        "similarity_comparison_budget": CONFIG.max_similarity_comparisons,
    }


def controlled_probe() -> dict[str, object]:
    result = mine(_history(12), CONFIG)
    pattern = result.patterns[0] if result.patterns else None
    return {
        "source": "bounded-controlled-semantic-fixture",
        "computer_execution": False,
        "raw_screenpipe_data": False,
        "sessions": len(result.sessions),
        "patterns": len(result.patterns),
        "occurrences": pattern.occurrence_count if pattern else 0,
        "fingerprint": pattern.fingerprint if pattern else None,
        "evidence_ref": pattern.evidence_ref if pattern else None,
        "verdict": "REPEATED_WORKFLOW_FOUND" if pattern else "NO_REPEATED_WORKFLOW_FOUND",
    }


def main() -> None:
    print(json.dumps({
        "algorithm_version": CONFIG.algorithm_version,
        "configuration_hash": CONFIG.configuration_hash,
        "controlled_probe": controlled_probe(),
        "benchmarks": [benchmark(size) for size in (100, 1000, 10000)],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
