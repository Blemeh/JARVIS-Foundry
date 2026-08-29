"""V9 action and capability fabric."""

from .catalog import (
    catalog_snapshot,
    listed_capabilities,
    observed_readiness,
    record_gap,
    record_outcome,
    register_capability,
)
from .executors import ExecutorContext, dispatch
from .expansion import (
    HASH_CANDIDATE_SOURCE,
    POOR_HASH_CANDIDATE_SOURCE,
    acquire_hash_capability,
)
from .loop import execute_action, run_intent, seed_builtins, select_capability, try_acquire
from .model import (
    ActionResult,
    CapabilitySpec,
    ClaimOutcome,
    GapKind,
    GapRecord,
    Kind,
    Readiness,
    TaskIntent,
    UnattendedMode,
    VerifyVerdict,
)
from .providers import route_provider, unattended_mode_for

__all__ = [
    "ActionResult",
    "CapabilitySpec",
    "ClaimOutcome",
    "ExecutorContext",
    "GapKind",
    "GapRecord",
    "HASH_CANDIDATE_SOURCE",
    "Kind",
    "POOR_HASH_CANDIDATE_SOURCE",
    "Readiness",
    "TaskIntent",
    "UnattendedMode",
    "VerifyVerdict",
    "acquire_hash_capability",
    "catalog_snapshot",
    "dispatch",
    "execute_action",
    "listed_capabilities",
    "observed_readiness",
    "record_gap",
    "record_outcome",
    "register_capability",
    "route_provider",
    "run_intent",
    "seed_builtins",
    "select_capability",
    "try_acquire",
    "unattended_mode_for",
]
