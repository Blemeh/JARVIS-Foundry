"""Deterministic event-kind registration for legacy compatibility."""

from __future__ import annotations

from collections.abc import Iterable

from jarvis_foundry.kernel.events import EventKindRegistry

# Union of every event type observed in the frozen R0 restore samples and the
# live-copy snapshot.  A row with a type outside this catalog fails closed.
HISTORICAL_EVENT_TYPE_CATALOG: frozenset[str] = frozenset(
    {
        "agent_run.completed",
        "agent_run.failed",
        "agent_run.heartbeat",
        "agent_run.started",
        "briefing.recorded",
        "canary.completed",
        "canary.started",
        "canary.tick",
        "candidate.build_completed",
        "candidate.build_failed",
        "candidate.build_started",
        "candidate_root.registered",
        "capability.matrix.recorded",
        "connector.activated",
        "connector.activation_blocked",
        "connector.discovered",
        "connector.health_sample",
        "connector.registered",
        "consolidation.state_changed",
        "control.changed",
        "cycle.completed",
        "cycle.started",
        "discovery.candidate_approved",
        "discovery.candidate_generated",
        "discovery.candidate_scored",
        "discovery.candidate_superseded",
        "discovery.monitor_polled",
        "discovery.monitor_registered",
        "discovery.observed",
        "emergency_stop.frozen",
        "evaluation.comparison_recorded",
        "evaluation.run_completed",
        "evaluation.run_started",
        "evaluation.workload_registered",
        "evidence.created",
        "fault_injection.blocked",
        "fault_injection.completed",
        "finding.recorded",
        "git.local_commit_created",
        "improvement.recorded",
        "ingestion.cursor_updated",
        "installation.enabled",
        "installation.installed",
        "knowledge.claim_recorded",
        "knowledge.claims_rolled_back",
        "knowledge.ingestion_completed",
        "knowledge.ingestion_queued",
        "knowledge.snapshot_created",
        "mission.completed",
        "mission.created",
        "mission.state_changed",
        "obsidian.rolled_back",
        "obsidian.write_completed",
        "obsidian.write_started",
        "observation.started",
        "owner_directive.recorded",
        "project.registered",
        "release.frozen",
        "release.manifest_recorded",
        "release.promoted",
        "release.promotion_intent",
        "release.rolled_back",
        "release.slot_changed",
        "release.unfrozen",
        "research.queue_item_created",
        "research.queue_item_resolved",
        "rollback.completed",
        "rollback.triggered",
        "step.completed",
        "step.created",
        "step.ready",
        "supervisor.degraded",
        "supervisor.heartbeat",
        "supervisor.started",
        "supervisor.stopped",
        "system.registered",
        "system.start_build_recorded",
        "vault.file_indexed",
        "vault.sync_completed",
        "verification.blocked",
        "verification.failed",
        "verification.passed",
        "verification.requested",
        "verification.started",
        "verification.verdict_recorded",
        "worker.manifest_delivered",
    }
)


def compat_kind_registry(
    event_types: Iterable[str],
    *,
    extra_types: Iterable[str] = (),
) -> EventKindRegistry:
    """Register legacy and extra kinds as version-1 non-financial kinds."""
    registry = EventKindRegistry()
    for event_type in sorted(set(event_types) | set(extra_types)):
        registry.register(event_type, version=1, required_keys=(), financial=False)
    return registry
