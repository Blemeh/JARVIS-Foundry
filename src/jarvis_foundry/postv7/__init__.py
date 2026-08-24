"""Canonical cross-lane seams for the post-V7 integration candidate.

The package is intentionally small: it composes the existing account,
portfolio, procedure, effect, verifier, resilience, and evidence authorities.
It does not introduce a second ledger or an alternate execution controller.
"""

from .integration import (
    ProcedureEligibility,
    execute_library_step,
    plan_library_procedure,
    procedure_eligibility,
    project_account_capabilities,
    record_library_outcome,
    register_eligible_procedure_work,
    select_library_work,
)

__all__ = [
    "ProcedureEligibility",
    "execute_library_step",
    "plan_library_procedure",
    "procedure_eligibility",
    "project_account_capabilities",
    "record_library_outcome",
    "register_eligible_procedure_work",
    "select_library_work",
]
