"""Phase 4 — governed procedural learning and OpenAdapt substrate."""

from .adapter import OpenAdaptAdapter, OpenAdaptConfig, check_openadapt
from .bundle import (adopt_openadapt_bundle, build_deterministic_bundle,
                     verify_bundle)
from .contracts import (ActionAuthorityError, AmbiguousEffect,
                        BundleIntegrityError, BundleVersionError,
                        EffectVerificationError, OpenAdaptUnavailable,
                        ProcedureBundle, ProcedureError, ReplayOutcome,
                        validate_parameters)
from .executor import LocalStateOracle, governed_replay
from .registry import capability_matrix, register_openadapt_capabilities
from .shadow import shadow_procedure
from .tier_c import (register_tier_c_reference, tier_c_reference,
                     verify_tier_c_reference)

__all__ = [
    "ActionAuthorityError", "AmbiguousEffect", "BundleIntegrityError",
    "BundleVersionError", "EffectVerificationError", "LocalStateOracle",
    "OpenAdaptAdapter", "OpenAdaptConfig", "OpenAdaptUnavailable",
    "ProcedureBundle", "ProcedureError", "ReplayOutcome",
    "adopt_openadapt_bundle", "build_deterministic_bundle", "capability_matrix",
    "check_openadapt", "governed_replay", "register_openadapt_capabilities",
    "register_tier_c_reference", "tier_c_reference", "verify_bundle",
    "verify_tier_c_reference", "shadow_procedure", "validate_parameters",
]
