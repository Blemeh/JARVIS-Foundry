"""Controlled self-evolution with durable, independently verified cycles (R11).

This package is additive and imports existing kernel machinery read-only.  It
implements the full lifecycle
OBSERVED -> HYPOTHESIZED -> CANDIDATE_ISOLATED -> EVALUATED ->
INDEPENDENTLY_VERIFIED -> SHADOWED -> CANARY_READY -> PROMOTED or REJECTED ->
OUTCOME_MEASURED -> LEARNED -> ROLLED_BACK/RESTORED, with honest WAITING.

Only the Supervisor controller may promote, freeze, rollback, or restore.
Candidates can never certify, promote, relax, widen, budget-increase, stop-
disable, erase, or terminate themselves; every refusal is durable.
"""

from __future__ import annotations

from jarvis_foundry.evolution_live.engine import (
    EvolutionEngine,
    TransitionResult,
)
from jarvis_foundry.evolution_live.events import r11_event_registry, r11_ledger
from jarvis_foundry.evolution_live.lifecycle import (
    CANDIDATE_ROLE,
    LIFECYCLE_EVENTS,
    RISK_ORDER,
    SUPERVISOR_CONTROLLER,
    LifecycleError,
)
from jarvis_foundry.evolution_live.projections import (
    r11_projection_registry,
    replay_snapshot,
)
from jarvis_foundry.evolution_live.store import (
    EffectJournal,
    EvolutionStore,
    ReleaseSnapshot,
    file_manifest,
    tree_hash,
)

__all__ = [
    "CANDIDATE_ROLE",
    "LIFECYCLE_EVENTS",
    "RISK_ORDER",
    "SUPERVISOR_CONTROLLER",
    "EffectJournal",
    "EvolutionEngine",
    "EvolutionStore",
    "LifecycleError",
    "ReleaseSnapshot",
    "TransitionResult",
    "file_manifest",
    "r11_event_registry",
    "r11_ledger",
    "r11_projection_registry",
    "replay_snapshot",
    "tree_hash",
]
