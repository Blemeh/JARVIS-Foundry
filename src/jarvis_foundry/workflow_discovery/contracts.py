"""Contracts for deterministic workflow discovery.

The only accepted input is the existing Phase-2 ``PerceptionObservation``
contract. Derived records contain structural tokens and provenance references,
never raw screen text, frames, URLs, credentials, or arbitrary observation
metadata.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..clock import parse
from ..events import canonical_json
from ..perception.contracts import PerceptionObservation

SESSIONIZER_VERSION = "sessionizer-v1"
TOKENIZER_VERSION = "safe-tokenizer-v1"
FINGERPRINT_VERSION = "sequence-fingerprint-v1"
SIMILARITY_VERSION = "levenshtein-similarity-v1"
MINER_VERSION = "workflow-miner-v1"
EVIDENCE_VERSION = "workflow-evidence-v1"
BRIDGE_SOURCE = "workflow_discovery"


@dataclass(frozen=True, slots=True)
class WorkflowDiscoveryConfig:
    """Versioned controls for a bounded mining run.

    ``enabled`` is deliberately false by default. The pure miner can be used
    in an explicitly enabled test or bounded probe, while no continuous loop
    is activated by importing this package.
    """

    enabled: bool = False
    idle_gap_seconds: float = 300.0
    max_session_duration_seconds: float = 3600.0
    min_occurrences: int = 2
    min_tokens: int = 1
    similarity_threshold: float = 0.80
    freshness_horizon_seconds: float = 30 * 24 * 3600.0
    max_similarity_comparisons: int = 256

    @classmethod
    def from_yaml(cls, path: str | Path) -> "WorkflowDiscoveryConfig":
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise ValueError("workflow discovery config must be a mapping")
        config = cls(
            enabled=bool(raw.get("enabled", False)),
            idle_gap_seconds=float(raw.get("idle_gap_seconds", 300.0)),
            max_session_duration_seconds=float(
                raw.get("max_session_duration_seconds", 3600.0)),
            min_occurrences=int(raw.get("min_occurrences", 2)),
            min_tokens=int(raw.get("min_tokens", 1)),
            similarity_threshold=float(raw.get("similarity_threshold", 0.8)),
            freshness_horizon_seconds=float(
                raw.get("freshness_horizon_seconds", 30 * 24 * 3600.0)),
            max_similarity_comparisons=int(
                raw.get("max_similarity_comparisons", 256)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.idle_gap_seconds <= 0:
            raise ValueError("idle_gap_seconds must be positive")
        if self.max_session_duration_seconds <= 0:
            raise ValueError("max_session_duration_seconds must be positive")
        if self.min_occurrences < 2:
            raise ValueError("min_occurrences must be at least 2")
        if self.min_tokens < 1:
            raise ValueError("min_tokens must be positive")
        if not 0.0 < self.similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be in (0, 1]")
        if self.freshness_horizon_seconds <= 0:
            raise ValueError("freshness_horizon_seconds must be positive")
        if self.max_similarity_comparisons < 1:
            raise ValueError("max_similarity_comparisons must be positive")

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "idle_gap_seconds": self.idle_gap_seconds,
            "max_session_duration_seconds": self.max_session_duration_seconds,
            "min_occurrences": self.min_occurrences,
            "min_tokens": self.min_tokens,
            "similarity_threshold": self.similarity_threshold,
            "freshness_horizon_seconds": self.freshness_horizon_seconds,
            "max_similarity_comparisons": self.max_similarity_comparisons,
        }

    @property
    def configuration_hash(self) -> str:
        self.validate()
        return hashlib.sha256(
            canonical_json(self.as_dict()).encode("utf-8")
        ).hexdigest()

    @property
    def algorithm_version(self) -> str:
        return "+".join((SESSIONIZER_VERSION, TOKENIZER_VERSION,
                          FINGERPRINT_VERSION, SIMILARITY_VERSION,
                          MINER_VERSION))


@dataclass(frozen=True, slots=True)
class ActivityToken:
    """A safe structural activity token derived from one observation."""

    observation_id: str
    token_id: str
    structure: tuple[str, str, str]
    safe_parameter_keys: tuple[str, ...]
    captured_at: str
    interval_end: str
    privacy_class: str
    redaction_state: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "token": self.token_id,
            "structure": list(self.structure),
            "safe_parameter_keys": list(self.safe_parameter_keys),
            "captured_at": self.captured_at,
            "interval_end": self.interval_end,
            "privacy_class": self.privacy_class,
            "redaction_state": self.redaction_state,
        }


@dataclass(frozen=True, slots=True)
class WorkflowSession:
    """A deterministic contiguous group of semantic observations."""

    session_id: str
    observation_ids: tuple[str, ...]
    started_at: str
    ended_at: str
    duration_seconds: float
    boundary_reasons: tuple[str, ...] = ()
    late_observation_ids: tuple[str, ...] = ()
    source_keys: tuple[str, ...] = ()
    observations: tuple[PerceptionObservation, ...] = field(
        default=(), repr=False, compare=False)
    tokens: tuple[ActivityToken, ...] = field(
        default=(), repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        """Return only safe, reconstructible session evidence."""
        return {
            "session_id": self.session_id,
            "observation_ids": list(self.observation_ids),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "boundary_reasons": list(self.boundary_reasons),
            "late_observation_ids": list(self.late_observation_ids),
            "source_segment_count": len(self.source_keys),
            "tokens": [token.as_dict() for token in self.tokens],
        }


@dataclass(frozen=True, slots=True)
class PatternOccurrence:
    session_id: str
    observation_ids: tuple[str, ...]
    started_at: str
    ended_at: str
    duration_seconds: float
    similarity: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "observation_ids": list(self.observation_ids),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "similarity": self.similarity,
        }


@dataclass(frozen=True, slots=True)
class PatternEvidence:
    """Durable evidence that a structural sequence repeated."""

    pattern_id: str
    fingerprint: str
    normalized_sequence: tuple[str, ...]
    occurrences: tuple[PatternOccurrence, ...]
    configuration_hash: str
    algorithm_version: str
    first_seen: str
    last_seen: str
    occurrence_count: int
    frequency_per_hour: float
    median_duration_seconds: float
    duration_variability: float
    sequence_variability: float
    privacy_state: str
    parameter_keys: tuple[str, ...]
    estimated_owner_time_seconds: float
    automation_feasibility: str
    stale: bool = False
    recent_occurrence_count: int = 0
    evidence_ref: str = ""
    event_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Safe payload for the existing append-only event ledger."""
        return {
            "evidence_version": EVIDENCE_VERSION,
            "pattern_id": self.pattern_id,
            "fingerprint": self.fingerprint,
            "normalized_sequence": list(self.normalized_sequence),
            "occurrences": [item.as_dict() for item in self.occurrences],
            "configuration_hash": self.configuration_hash,
            "algorithm_version": self.algorithm_version,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "occurrence_count": self.occurrence_count,
            "frequency_per_hour": self.frequency_per_hour,
            "median_duration_seconds": self.median_duration_seconds,
            "duration_variability": self.duration_variability,
            "sequence_variability": self.sequence_variability,
            "privacy_state": self.privacy_state,
            "parameter_keys": list(self.parameter_keys),
            "estimated_owner_time": {
                "value_seconds": self.estimated_owner_time_seconds,
                "label": "ESTIMATE",
            },
            "frequency_kind": "OBSERVED_WINDOW_RATE",
            "automation_feasibility": self.automation_feasibility,
            "stale": self.stale,
            "recent_occurrence_count": self.recent_occurrence_count,
            "evidence_ref": self.evidence_ref,
        }


def observation_from_payload(payload: Mapping[str, Any]) -> PerceptionObservation:
    """Rehydrate the actual Phase-2 observation contract from a ledger row."""
    if not isinstance(payload, Mapping):
        raise TypeError("perception observation payload must be a mapping")
    required = (
        "observation_id", "source_connector_id", "source_pointer",
        "captured_at", "interval_end", "recorded_at", "application",
        "resource", "activity_class", "semantic_summary", "content_hash",
    )
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"observation payload missing fields: {missing}")
    observation = PerceptionObservation(
        observation_id=str(payload["observation_id"]),
        source_connector_id=str(payload["source_connector_id"]),
        source_pointer=dict(payload["source_pointer"]),
        captured_at=str(payload["captured_at"]),
        interval_end=str(payload["interval_end"]),
        recorded_at=str(payload["recorded_at"]),
        application=str(payload["application"]),
        resource=str(payload.get("resource", "")),
        activity_class=str(payload["activity_class"]),
        semantic_summary=str(payload.get("semantic_summary", "")),
        transitions=tuple(str(item) for item in payload.get("transitions", [])),
        content_hash=str(payload["content_hash"]),
        privacy_class=str(payload.get("privacy_class", "public")),
        redaction_state=str(payload.get("redaction_state", "none")),
        confidence=float(payload.get("confidence", 1.0)),
        raw_external=bool(payload.get("raw_external", True)),
        source_version=str(payload.get("source_version", "")),
        retention_class=str(payload.get("retention_class", "bounded")),
        source_metadata=dict(payload.get("source_metadata") or {}),
    )
    observation.validate()
    parse(observation.captured_at)
    parse(observation.interval_end)
    return observation
