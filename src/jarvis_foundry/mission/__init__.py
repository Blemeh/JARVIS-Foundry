"""WP5 — mission and verification loop: proposal, dispatch, verification,
repair, commit-after-PASS, and honest waiting."""
from __future__ import annotations

from .commit import CommitError, commit_after_pass
from .directives import DirectiveEffect, consume_directives, matching_effects, select_dispatchable_mission
from .loop import MissionLoop, StepOutcome
from .proposal import Proposal, ProposalError, Waiting, propose_next_action
from .verification import (
    accepted_verifications,
    final_outcome_history,
    final_outcome_projection,
    record_final_outcome,
    record_verification_acceptance,
    record_verification_outcome,
    start_verification,
)

__all__ = [
    "CommitError",
    "MissionLoop",
    "Proposal",
    "ProposalError",
    "StepOutcome",
    "Waiting",
    "commit_after_pass",
    "DirectiveEffect",
    "consume_directives",
    "matching_effects",
    "select_dispatchable_mission",
    "propose_next_action",
    "accepted_verifications",
    "final_outcome_history",
    "final_outcome_projection",
    "record_final_outcome",
    "record_verification_acceptance",
    "record_verification_outcome",
    "start_verification",
    "verification_outcome",
]
