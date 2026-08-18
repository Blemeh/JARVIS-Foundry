"""Deterministic repetition miner over semantic observation sessions."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from statistics import median
from typing import Iterable

from ..clock import parse
from ..control.redaction import contains_secret_like
from ..events import canonical_json
from ..perception.contracts import PerceptionObservation
from .contracts import (
    EVIDENCE_VERSION,
    MINER_VERSION,
    PatternEvidence,
    PatternOccurrence,
    WorkflowDiscoveryConfig,
    WorkflowSession,
)
from .fingerprints import fingerprint_for_sequence
from .sessionizer import sessionize
from .similarity import normalized_edit_similarity, structurally_similar
from .tokenizer import tokenize


@dataclass(frozen=True, slots=True)
class MineResult:
    sessions: tuple[WorkflowSession, ...]
    patterns: tuple[PatternEvidence, ...]
    non_repeated_session_ids: tuple[str, ...] = ()

    @property
    def repeated_occurrences(self) -> int:
        return sum(pattern.occurrence_count for pattern in self.patterns)

    def as_dict(self) -> dict[str, object]:
        return {
            "sessions": [session.as_dict() for session in self.sessions],
            "patterns": [pattern.as_dict() for pattern in self.patterns],
            "non_repeated_session_ids": list(self.non_repeated_session_ids),
        }


def _time(value: str) -> datetime:
    return parse(value)


def _component_key(sessions: list[WorkflowSession],
                   config: WorkflowDiscoveryConfig) -> list[list[WorkflowSession]]:
    """Group exact sequences and conservative variants with bounded work."""
    ordered = sorted(sessions, key=lambda item: item.session_id)
    sequence_groups: dict[tuple[str, ...], list[WorkflowSession]] = {}
    for session in ordered:
        sequence = tuple(token.token_id for token in session.tokens)
        sequence_groups.setdefault(sequence, []).append(session)

    # Deterministic greedy representatives are intentionally conservative:
    # a variant must match the chosen representative, so transitive chains
    # cannot merge materially different sequences. Comparisons are capped per
    # endpoint bucket; omitted comparisons become honest false negatives.
    groups: list[list[WorkflowSession]] = []
    representatives: dict[tuple[str, str], list[tuple[tuple[str, ...], int]]] = {}
    comparisons: dict[tuple[str, str], int] = {}
    for sequence in sorted(sequence_groups):
        if not sequence:
            groups.append(sequence_groups[sequence])
            continue
        bucket_key = (sequence[0], sequence[-1])
        matched: int | None = None
        bucket = representatives.setdefault(bucket_key, [])
        comparisons.setdefault(bucket_key, 0)
        for representative, group_index in bucket:
            if comparisons[bucket_key] >= config.max_similarity_comparisons:
                break
            comparisons[bucket_key] += 1
            max_length = max(len(sequence), len(representative))
            max_delta = max(1, int((1.0 - config.similarity_threshold) * max_length))
            if abs(len(sequence) - len(representative)) <= max_delta and \
                    structurally_similar(sequence, representative,
                                         config.similarity_threshold):
                matched = group_index
                break
        if matched is None:
            matched = len(groups)
            groups.append([])
            bucket.append((sequence, matched))
        groups[matched].extend(sequence_groups[sequence])
    return sorted(groups, key=lambda group: min(item.session_id for item in group))


def _safe_pattern_payload(pattern: PatternEvidence) -> str:
    serialized = canonical_json(pattern.as_dict())
    if contains_secret_like(serialized):
        raise ValueError("secret-like material reached workflow evidence")
    return serialized


def _build_pattern(group: list[WorkflowSession],
                   config: WorkflowDiscoveryConfig) -> PatternEvidence:
    ordered_pairs = sorted(
        ((session, tuple(token.token_id for token in session.tokens))
         for session in group),
        key=lambda pair: pair[0].session_id,
    )
    sequences = [sequence for _, sequence in ordered_pairs]
    representative = min(sequences)
    fingerprint = fingerprint_for_sequence(representative, config)
    occurrences: list[PatternOccurrence] = []
    parameter_keys: set[str] = set()
    privacy_states: list[str] = []
    for session, sequence in ordered_pairs:
        similarity = normalized_edit_similarity(representative, sequence)
        occurrences.append(PatternOccurrence(
            session_id=session.session_id,
            observation_ids=session.observation_ids,
            started_at=session.started_at,
            ended_at=session.ended_at,
            duration_seconds=session.duration_seconds,
            similarity=similarity,
        ))
        for token in session.tokens:
            parameter_keys.update(token.safe_parameter_keys)
            if token.privacy_class != "public" or token.redaction_state != "none":
                privacy_states.append("REDACTED_SENSITIVE")
    occurrences.sort(key=lambda item: (item.started_at, item.session_id))
    first_seen = min(item.started_at for item in occurrences)
    last_seen = max(item.ended_at for item in occurrences)
    durations = [item.duration_seconds for item in occurrences]
    median_duration = float(median(durations))
    variability = ((max(durations) - min(durations)) /
                   max(1.0, median_duration))
    sequence_variability = 1.0 - sum(item.similarity for item in occurrences) / len(occurrences)
    span_hours = max(1.0 / 3600.0,
                     (_time(last_seen) - _time(first_seen)).total_seconds() / 3600.0)
    repeated_tokens = set(representative)
    low_value_names = {"idle", "window_switch", "app_open", "app_close",
                       "stationary", "noise"}
    low_value = len(repeated_tokens) <= 1 or any(
        part in low_value_names for token in representative for part in token.split(":"))
    feasibility = "LOW_VALUE_REPETITION" if low_value else "OBSERVATION_ONLY_PHASE_3"
    pattern_id = f"pattern-{fingerprint[:16]}"
    evidence_ref = f"workflow-pattern:{pattern_id}"
    pattern = PatternEvidence(
        pattern_id=pattern_id,
        fingerprint=fingerprint,
        normalized_sequence=representative,
        occurrences=tuple(occurrences),
        configuration_hash=config.configuration_hash,
        algorithm_version=(f"{EVIDENCE_VERSION}+{MINER_VERSION}"),
        first_seen=first_seen,
        last_seen=last_seen,
        occurrence_count=len(occurrences),
        frequency_per_hour=round(len(occurrences) / span_hours, 6),
        median_duration_seconds=round(median_duration, 6),
        duration_variability=round(max(0.0, variability), 6),
        sequence_variability=round(max(0.0, sequence_variability), 6),
        privacy_state=("REDACTED_SENSITIVE" if privacy_states else "PUBLIC"),
        parameter_keys=tuple(sorted(parameter_keys)),
        estimated_owner_time_seconds=round(median_duration * len(occurrences), 6),
        automation_feasibility=feasibility,
        recent_occurrence_count=len(occurrences),
        evidence_ref=evidence_ref,
    )
    _safe_pattern_payload(pattern)
    return pattern


def mine(observations: Iterable[PerceptionObservation],
         config: WorkflowDiscoveryConfig | None = None,
         *, reference_time: str | None = None) -> MineResult:
    """Mine a bounded observation history without writing or executing."""
    config = config or WorkflowDiscoveryConfig(enabled=True)
    config.validate()
    raw_sessions = sessionize(observations, config)
    sessions: list[WorkflowSession] = []
    for session in raw_sessions:
        tokens = tokenize(session.observations)
        if len(tokens) < config.min_tokens:
            continue
        sessions.append(replace(session, tokens=tokens))
    groups = _component_key(sessions, config)
    patterns: list[PatternEvidence] = []
    non_repeated: list[str] = []
    for group in groups:
        if len(group) < config.min_occurrences:
            non_repeated.extend(item.session_id for item in group)
            continue
        pattern = _build_pattern(group, config)
        if reference_time is not None:
            age = (_time(reference_time) - _time(pattern.last_seen)).total_seconds()
            recent = sum(
                (_time(reference_time) - _time(item.ended_at)).total_seconds()
                <= config.freshness_horizon_seconds
                for item in pattern.occurrences
            )
            pattern = replace(pattern, stale=age > config.freshness_horizon_seconds,
                              recent_occurrence_count=int(recent))
        patterns.append(pattern)
    return MineResult(
        sessions=tuple(sorted(sessions, key=lambda item: item.session_id)),
        patterns=tuple(sorted(patterns, key=lambda item: item.pattern_id)),
        non_repeated_session_ids=tuple(sorted(non_repeated)),
    )
