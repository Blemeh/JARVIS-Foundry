"""V9 action and capability fabric."""

from .adapter import FabricRoleAdapter, resolve_fabric_action
from .catalog import (
    catalog_snapshot,
    listed_capabilities,
    observed_readiness,
    record_gap,
    record_outcome,
    register_capability,
    reliability,
)
from .compose import run_workflow
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
    WorkflowStep,
)
from .providers import route_provider, unattended_mode_for
from .spine import execute_for_spine

__all__ = [
    "ActionResult",
    "CapabilitySpec",
    "ClaimOutcome",
    "ExecutorContext",
    "FabricRoleAdapter",
    "GapKind",
    "GapRecord",
    "HASH_CANDIDATE_SOURCE",
    "Kind",
    "POOR_HASH_CANDIDATE_SOURCE",
    "Readiness",
    "TaskIntent",
    "UnattendedMode",
    "VerifyVerdict",
    "WorkflowStep",
    "acquire_hash_capability",
    "catalog_snapshot",
    "dispatch",
    "execute_action",
    "execute_for_spine",
    "listed_capabilities",
    "observed_readiness",
    "record_gap",
    "record_outcome",
    "register_capability",
    "reliability",
    "resolve_fabric_action",
    "route_provider",
    "run_intent",
    "run_workflow",
    "seed_builtins",
    "select_capability",
    "try_acquire",
    "unattended_mode_for",
]
