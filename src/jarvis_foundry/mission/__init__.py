"""WP5 — mission and verification loop: proposal, dispatch, verification,
repair, commit-after-PASS, and honest waiting."""
from __future__ import annotations

from .commit import CommitError, commit_after_pass
from .loop import MissionLoop, StepOutcome
from .proposal import Proposal, ProposalError, Waiting, propose_next_action
from .verification import (
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
    "propose_next_action",
    "record_verification_outcome",
    "start_verification",
    "verification_outcome",
]
