"""Minimal-instruction delegation contracts and lifecycle."""

from .calibration import (
    DELEGATION_CALIBRATION_CONTRACT,
    build_delegation_calibration,
)
from .contracts import (
    ConsequenceClass,
    DelegationDecision,
    DelegationIntent,
    DelegationIntentStatus,
    DelegationMode,
    MaterialAmbiguity,
    MethodAssessment,
    ReferentCandidate,
    Reversibility,
    stable_intent_id,
)
from .context import DelegationContext, compile_delegation_context
from .inference import ModeInference, infer_delegation_intent
from .projection import DelegationProjection, project_delegation_intents
from .service import (
    DELEGATION_ADMISSION_CONTRACT,
    DelegationOutcome,
    DelegationService,
    reconcile_owner_delegations,
)

__all__ = [
    "ConsequenceClass",
    "DELEGATION_ADMISSION_CONTRACT",
    "DELEGATION_CALIBRATION_CONTRACT",
    "DelegationDecision",
    "DelegationIntent",
    "DelegationIntentStatus",
    "DelegationMode",
    "DelegationOutcome",
    "DelegationProjection",
    "DelegationService",
    "DelegationContext",
    "MaterialAmbiguity",
    "MethodAssessment",
    "ModeInference",
    "ReferentCandidate",
    "Reversibility",
    "compile_delegation_context",
    "build_delegation_calibration",
    "infer_delegation_intent",
    "project_delegation_intents",
    "reconcile_owner_delegations",
    "stable_intent_id",
]
