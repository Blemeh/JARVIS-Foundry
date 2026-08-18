"""Deterministic sessionization of Phase-2 observations."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Iterable

from ..clock import parse
from ..events import canonical_json
from ..perception.contracts import PRIVACY_EXCLUDED, PerceptionObservation, payload_for
from .contracts import WorkflowDiscoveryConfig, WorkflowSession, SESSIONIZER_VERSION


def _source_key(observation: PerceptionObservation) -> str:
    """Use only explicit, non-volatile source continuity markers."""
    values: list[tuple[str, str]] = [("connector", observation.source_connector_id),
                                     ("version", observation.source_version)]
    for mapping in (observation.source_pointer, observation.source_metadata):
        for key in ("source_session_id", "source_run_id", "source_instance_id",
                    "restart_id", "source_restart"):
            if key in mapping and mapping[key] not in (None, "", False):
                value = mapping[key]
                if isinstance(value, bool):
                    values.append((key, "true"))
                elif isinstance(value, (str, int, float)):
                    values.append((key, str(value)))
    return canonical_json(values)


def _time(value: str) -> datetime:
    return parse(value)


def _observation_tiebreaker(observation: PerceptionObservation) -> str:
    """Choose duplicate payloads deterministically without retaining them."""
    return hashlib.sha256(
        canonical_json(payload_for(observation)).encode("utf-8")
    ).hexdigest()


def _session_id(observation_ids: tuple[str, ...], start: str, end: str,
                config: WorkflowDiscoveryConfig) -> str:
    payload = {"version": SESSIONIZER_VERSION,
               "config_hash": config.configuration_hash,
               "observation_ids": list(observation_ids),
               "started_at": start, "ended_at": end}
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"session-{digest[:16]}"


def _make_session(items: list[PerceptionObservation], reasons: list[str],
                  late_ids: list[str], source_keys: list[str],
                  config: WorkflowDiscoveryConfig) -> WorkflowSession:
    ordered = tuple(items)
    start = ordered[0].captured_at
    end = max(
        (value for item in ordered for value in
         (item.interval_end, item.captured_at)),
        key=lambda value: _time(value),
    )
    duration = max(0.0, (_time(end) - _time(start)).total_seconds())
    ids = tuple(item.observation_id for item in ordered)
    return WorkflowSession(
        session_id=_session_id(ids, start, end, config),
        observation_ids=ids,
        started_at=start,
        ended_at=end,
        duration_seconds=duration,
        boundary_reasons=tuple(reasons),
        late_observation_ids=tuple(sorted(set(late_ids))),
        source_keys=tuple(dict.fromkeys(source_keys)),
        observations=ordered,
    )


def sessionize(observations: Iterable[PerceptionObservation],
               config: WorkflowDiscoveryConfig | None = None
               ) -> tuple[WorkflowSession, ...]:
    """Split observations on idle, max-duration, and source-restart boundaries.

    Input order is not semantic: ordering and duplicate conflict resolution
    are deterministic, while late provenance is accepted only from an
    explicit source marker. Replay cannot inflate a session.
    """
    config = config or WorkflowDiscoveryConfig(enabled=True)
    config.validate()
    unique: dict[str, PerceptionObservation] = {}
    for observation in observations:
        observation.validate()
        if observation.privacy_class == PRIVACY_EXCLUDED:
            continue
        existing = unique.get(observation.observation_id)
        observation_key = _observation_tiebreaker(observation)
        existing_key = (_observation_tiebreaker(existing)
                        if existing else None)
        if existing is None or observation_key < existing_key:
            unique[observation.observation_id] = observation
    # Arrival order is not part of the semantic observation contract. A
    # caller may replay a batch in any order, so late provenance is accepted
    # only when the source explicitly marks it; otherwise sorting remains
    # identical for reordered input.
    late_ids: set[str] = {
        observation_id for observation_id, observation in unique.items()
        if bool(observation.source_pointer.get("late") or
                observation.source_pointer.get("late_event") or
                observation.source_metadata.get("late") or
                observation.source_metadata.get("late_event"))
    }

    ordered = sorted(unique.values(), key=lambda item: (
        _time(item.captured_at), _time(item.interval_end), item.observation_id))
    if not ordered:
        return ()

    sessions: list[WorkflowSession] = []
    current: list[PerceptionObservation] = [ordered[0]]
    current_reasons: list[str] = []
    current_sources: list[str] = [_source_key(ordered[0])]
    current_late: list[str] = ([ordered[0].observation_id]
                               if ordered[0].observation_id in late_ids else [])
    session_start = _time(ordered[0].captured_at)
    previous_end = max(_time(ordered[0].interval_end),
                       _time(ordered[0].captured_at))
    previous_source = current_sources[0]

    for observation in ordered[1:]:
        captured = _time(observation.captured_at)
        source = _source_key(observation)
        gap = (captured - previous_end).total_seconds()
        elapsed = (captured - session_start).total_seconds()
        reasons: list[str] = []
        if gap > config.idle_gap_seconds:
            reasons.append("idle_gap")
        if elapsed > config.max_session_duration_seconds:
            reasons.append("max_session_duration")
        if source != previous_source:
            reasons.append("source_restart_or_context")
        if reasons:
            sessions.append(_make_session(current, current_reasons, current_late,
                                          current_sources, config))
            current = [observation]
            current_reasons = reasons
            current_sources = [source]
            current_late = ([observation.observation_id]
                            if observation.observation_id in late_ids else [])
            session_start = captured
            previous_end = captured
        else:
            current.append(observation)
            current_reasons.extend(reasons)
            current_sources.append(source)
            if observation.observation_id in late_ids:
                current_late.append(observation.observation_id)
        previous_end = max(previous_end, _time(observation.interval_end), captured)
        previous_source = source
    sessions.append(_make_session(current, current_reasons, current_late,
                                  current_sources, config))
    return tuple(sessions)
