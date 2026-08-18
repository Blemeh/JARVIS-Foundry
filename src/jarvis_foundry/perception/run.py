"""Bounded perception driver (JARVIS Eyes input capability).

``python -m jarvis_foundry.perception.run --home <home> --config
config/perception.yaml --once`` runs ONE bounded cycle; ``--interval N``
runs a bounded loop for probes. The driver:

- registers ``screenpipe-local`` through the EXISTING connector registry;
- participates in existing health / disable / enable / STOP / veto / quota
  architecture (no bespoke bypass);
- appends only low-volume ``perception.observed`` semantic events to the
  EXISTING append-only ledger (no new database, no schema change);
- never creates knowledge claims, candidates, missions, owner directives or
  computer control (proven by tests).

Configuration is fail-closed: ``enabled: false`` (the committed default)
refuses to run unless the operator passes ``--force`` explicitly.
"""
from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..clock import isoformat
from ..connectors.contract import ConnectorSpec
from ..connectors.registry import (
    ConnectorUnavailable,
    connector_status,
    record_health_sample,
    record_quota_use,
    register_connector,
)
from ..database import FoundryDatabase
from ..events import Event
from ..paths import FoundryPaths
from ..supervisor.estop import EmergencyStop
from .aggregation import ActivityInterval, aggregate
from .contracts import (
    EVENT_OBSERVED,
    EVENT_REDACTED,
    EVENT_SOURCE_DEGRADED,
    EVENT_SOURCE_RECOVERED,
    SOURCE_CONNECTOR_ID,
    SOURCE_KIND,
    SOURCE_VERSION,
    PerceptionObservation,
    observation_id_for,
    payload_for,
)
from .cursor import advance_cursor, read_cursor
from .dedup import RecordDedup
from .normalizer import normalize_records
from .privacy import PrivacyPolicy
from .screenpipe import ScreenpipeClient, SourceFailure
from .significance import is_significant

_HEALTH_REGISTRY = {
    "HEALTHY": "HEALTHY",
    "OFFLINE_OK": "HEALTHY",
    "NOT_RUNNING": "DEGRADED",
    "STARTING": "DEGRADED",
    "STALLED": "DEGRADED",
    "QUERY_FAILURE": "DEGRADED",
    "DATA_CORRUPT": "DEGRADED",
}


@dataclass(frozen=True, slots=True)
class PerceptionConfig:
    enabled: bool = False
    connector_id: str = SOURCE_CONNECTOR_ID
    source_version: str = SOURCE_VERSION
    source: dict[str, Any] = field(default_factory=dict)
    aggregation: dict[str, Any] = field(default_factory=dict)
    privacy: dict[str, Any] = field(default_factory=dict)
    audio_enabled: bool = False
    max_observations_per_cycle: int = 100
    emit_redacted_events: bool = True
    retention: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path | None) -> "PerceptionConfig":
        if path is None:
            return cls()  # fail closed: disabled
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"perception config {path} must be a mapping")
        cfg = cls(
            enabled=bool(raw.get("enabled", False)),
            connector_id=str(raw.get("connector_id", SOURCE_CONNECTOR_ID)),
            source_version=str(raw.get("source_version", SOURCE_VERSION)),
            source=dict(raw.get("source") or {}),
            aggregation=dict(raw.get("aggregation") or {}),
            privacy=dict(raw.get("privacy") or {}),
            audio_enabled=bool(raw.get("audio_enabled", False)),
            max_observations_per_cycle=int(
                raw.get("max_observations_per_cycle", 100)),
            emit_redacted_events=bool(
                raw.get("emit_redacted_events", True)),
            retention=dict(raw.get("retention") or {}),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.audio_enabled:
            raise ValueError(
                "audio_enabled must be false: microphone activation is "
                "forbidden in Phase 2")
        if self.max_observations_per_cycle < 1:
            raise ValueError("max_observations_per_cycle must be positive")
        raw_escalation = bool(self.privacy.get("raw_frame_escalation", False))
        if raw_escalation:
            raise ValueError(
                "raw_frame_escalation must be false in Phase 2 "
                "(raw frames remain PROBING)")

    def policy(self) -> PrivacyPolicy:
        p = self.privacy
        zones = tuple(
            (str(a or ""), str(w or ""))
            for a, w in p.get("private_zones") or [])
        return PrivacyPolicy(
            excluded_apps=frozenset(p.get("excluded_apps") or []),
            excluded_windows=frozenset(p.get("excluded_windows") or []),
            sensitive_titles=frozenset(p.get("sensitive_titles") or []),
            private_zones=zones,
            secret_like_enabled=bool(p.get("secret_like_enabled", True)),
            raw_frame_escalation=bool(p.get("raw_frame_escalation", False)),
            max_text_fragments=int(p.get("max_text_fragments", 8)),
        )


def register_perception_connector(database: FoundryDatabase,
                                  version: str = SOURCE_VERSION) -> None:
    """Register the governed read-only perception connector (idempotent)."""
    register_connector(database, ConnectorSpec(
        connector_id=SOURCE_CONNECTOR_ID,
        kind="perception",
        version=version,
        actions={
            "perception.health": "read",
            "perception.activity": "read",
            "perception.elements": "read",
            "perception.frame": "read",
        },
        capabilities={
            "screenpipe.health": "READY",
            "screenpipe.activity": "READY",
            "screenpipe.history_query": "READY",
            "screenpipe.accessibility": "PROBING",
            "screenpipe.raw_frame": "PROBING",
            "screenpipe.audio": "DISCOVERED",
            "screenpipe.private_window_filtering": "PROBING",
        },
        retry_policy={"max_attempts": 3, "backoff_s": [1, 3, 9]},
    ))


def _observation(interval: ActivityInterval, *, recorded_at: str,
                 privacy_class: str, redaction_state: str) -> PerceptionObservation:
    return PerceptionObservation(
        observation_id=observation_id_for(
            application=interval.application,
            resource=interval.resource,
            activity_class=interval.activity_class,
            captured_at=interval.captured_at,
            interval_end=interval.interval_end),
        source_connector_id=SOURCE_CONNECTOR_ID,
        source_pointer={
            "source_ids": list(interval.source_ids)[:64],
            "record_count": len(interval.source_ids),
            "table": "frames/ui_events",
            "external": True,
        },
        captured_at=interval.captured_at,
        interval_end=interval.interval_end,
        recorded_at=recorded_at,
        application=interval.application,
        resource=interval.resource,
        activity_class=interval.activity_class,
        semantic_summary=interval.semantic_summary,
        transitions=interval.transitions,
        content_hash=interval.content_hash,
        privacy_class=privacy_class,
        redaction_state=redaction_state,
        confidence=0.8 if interval.activity_class != "idle" else 0.5,
        source_version=SOURCE_VERSION,
        source_metadata={"record_count": len(interval.source_ids)},
    )


def run_cycle(database: FoundryDatabase, config: PerceptionConfig,
              *, source_id: str | None = None,
              client: ScreenpipeClient | None = None) -> dict[str, Any]:
    """One bounded perception cycle. Never raises for source faults."""
    instance = source_id or f"perception-{uuid.uuid4().hex[:8]}"
    stop_path = database.path.parent / "EMERGENCY_STOP"
    if EmergencyStop(stop_path).active():
        return {"stopped": True, "appended": 0, "reason": "EMERGENCY_STOP"}

    register_perception_connector(database, version=config.source_version)
    try:
        status = connector_status(database, config.connector_id)
    except ConnectorUnavailable:
        status = None
    if status is not None and status["disabled"]:
        record_health_sample(database, config.connector_id, "DISABLED",
                             detail="connector disabled by owner control")
        return {"disabled": True, "appended": 0, "reason": "connector.disabled"}

    client = client or _client_from_config(config)
    health = client.health()
    registry_status = _HEALTH_REGISTRY.get(health.status, "DEGRADED")
    record_health_sample(
        database, config.connector_id, registry_status,
        detail=f"{health.status}: {health.detail}")
    if not health.readable:
        _append_source_event(
            database, EVENT_SOURCE_DEGRADED, instance,
            {"connector_id": config.connector_id, "status": health.status,
             "category": health.status, "detail": health.detail[:300],
             "instance_id": instance})
        return {"degraded": True, "source_status": health.status,
                "appended": 0}
    _append_source_event(
        database, EVENT_SOURCE_RECOVERED, instance,
        {"connector_id": config.connector_id, "status": health.status,
         "instance_id": instance})

    policy = config.policy()
    cursor_from = read_cursor(database, SOURCE_KIND)
    try:
        rows = client.read_records(
            after_id=cursor_from,
            limit=int(config.source.get("max_records_per_cycle", 500)))
    except SourceFailure as failure:
        record_health_sample(database, config.connector_id, "DEGRADED",
                             detail=failure.category)
        _append_source_event(
            database, EVENT_SOURCE_DEGRADED, instance,
            {"connector_id": config.connector_id, "status": "QUERY_FAILURE",
             "category": failure.category, "detail": failure.detail[:300],
             "instance_id": instance})
        return {"degraded": True, "source_status": "QUERY_FAILURE",
                "category": failure.category, "appended": 0}

    records, counts = normalize_records(rows, policy)
    dedup = RecordDedup()
    records = [r for r in records if dedup.is_new(r)]
    intervals = aggregate(
        records,
        idle_floor_s=float(config.aggregation.get("idle_floor_seconds", 30.0)),
        max_transitions=int(config.aggregation.get("max_transitions", 8)))
    significant = []
    for interval in intervals:
        ok, reason = is_significant(
            interval,
            min_duration_s=float(
                config.aggregation.get("min_duration_seconds", 2.0)),
            unchanged_floor_s=float(
                config.aggregation.get("unchanged_floor_seconds", 60.0)))
        if ok:
            significant.append(interval)
    significant = significant[:config.max_observations_per_cycle]

    appended = 0
    for interval in significant:
        observation = _observation(
            interval,
            recorded_at=isoformat(),
            privacy_class="sensitive" if interval.resource.startswith(
                "[REDACTED") else "public",
            redaction_state="redacted" if interval.resource.startswith(
                "[REDACTED") else "none")
        observation.validate()
        event = Event.create(
            EVENT_OBSERVED, payload_for(observation),
            source_type="supervisor", source_id=instance,
            correlation_id=config.connector_id,
            idempotency_key=f"{EVENT_OBSERVED}:{observation.observation_id}")
        applied = database.append(event)
        if applied[1]:
            appended += 1

    if (counts["suppressed"] or counts["redacted"]
            or counts["fragments_dropped"]) and config.emit_redacted_events:
        database.append(Event.create(
            EVENT_REDACTED,
            {"connector_id": config.connector_id,
             "instance_id": instance,
             "suppressed": counts["suppressed"],
             "redacted": counts["redacted"],
             "fragments_dropped": counts["fragments_dropped"]},
            source_type="supervisor", source_id=instance,
            correlation_id=config.connector_id,
            idempotency_key=(
                f"{EVENT_REDACTED}:{instance}:{cursor_from}:"
                f"{rows[-1]['source_ids'][0] if rows else cursor_from}")))

    last_id = max((max(r["source_ids"]) for r in rows), default=cursor_from)
    if rows:
        advance_cursor(database, SOURCE_KIND, last_id, source_id=instance)
    record_quota_use(database, config.connector_id,
                     used=appended,
                     limit=config.max_observations_per_cycle)
    return {
        "cursor_from": cursor_from,
        "cursor_to": last_id,
        "source_records": len(rows),
        "normalized_records": len(records),
        "suppressed": counts["suppressed"],
        "redacted": counts["redacted"],
        "intervals": len(intervals),
        "observations_emitted": appended,
        "appended": appended,
        "source_status": health.status,
    }


def _append_source_event(database: FoundryDatabase, event_type: str,
                         instance: str, payload: dict[str, Any]) -> None:
    key = f"{event_type}:{instance}:{payload.get('category') or 'state'}"
    try:
        database.append(Event.create(
            event_type, payload,
            source_type="supervisor", source_id=instance,
            correlation_id=payload.get("connector_id"),
            idempotency_key=key))
    except Exception:
        return  # degraded-event recording must never take the driver down


def _client_from_config(config: PerceptionConfig) -> ScreenpipeClient:
    source = config.source
    return ScreenpipeClient(
        api_url=str(source.get("api_url", "http://127.0.0.1:3030")),
        data_dir=source.get("data_dir") or None,
        db_path=source.get("db_path") or None,
        api_token=source.get("api_token") or None,
        timeout_s=float(source.get("read_timeout_seconds", 15.0)),
        max_records=int(source.get("max_records_per_cycle", 500)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis-perception")
    parser.add_argument("--home", type=Path, default=None,
                        help="Foundry state directory (default: discover)")
    parser.add_argument("--config", type=Path, default=None,
                        help="perception config yaml (default: none -> disabled)")
    parser.add_argument("--once", action="store_true",
                        help="run exactly one cycle and exit")
    parser.add_argument("--interval", type=float, default=30.0,
                        help="poll interval seconds (loop mode)")
    parser.add_argument("--max-cycles", type=int, default=0,
                        help="bounded loop count (0 = unlimited)")
    parser.add_argument("--force", action="store_true",
                        help="run even when config.enabled is false")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = FoundryPaths.discover(args.home)
    config = PerceptionConfig.from_yaml(args.config)
    if not config.enabled and not args.force:
        print(json.dumps({"perception": "INACTIVE",
                          "reason": "config.enabled=false (fail closed)"}))
        return 0
    database = FoundryDatabase(paths.database)
    database.initialize()
    cycles = 0
    while True:
        report = run_cycle(database, config)
        print(json.dumps(report, sort_keys=True))
        cycles += 1
        if args.once or (args.max_cycles and cycles >= args.max_cycles):
            break
        import time
        time.sleep(max(1.0, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
