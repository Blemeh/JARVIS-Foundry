"""Deterministic, observation-only workflow discovery.

This package mines the Phase-2 :class:`PerceptionObservation` contract into
bounded repetition evidence. It does not execute workflows, control a
desktop, or replace any existing candidate, scoring, mission, or STOP path.
"""

from .bridge import BridgeResult, bridge_pattern, persist_pattern_evidence, run_cycle
from .contracts import (
    ActivityToken,
    PatternEvidence,
    PatternOccurrence,
    WorkflowDiscoveryConfig,
    WorkflowSession,
    observation_from_payload,
)
from .miner import MineResult, mine

__all__ = [
    "ActivityToken",
    "BridgeResult",
    "MineResult",
    "PatternEvidence",
    "PatternOccurrence",
    "WorkflowDiscoveryConfig",
    "WorkflowSession",
    "bridge_pattern",
    "mine",
    "observation_from_payload",
    "persist_pattern_evidence",
    "run_cycle",
]
