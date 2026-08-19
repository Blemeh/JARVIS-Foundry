"""Deterministic next-action proposal (WP5).

The loop never invents work: ``propose_next_action`` reads the mission's
durable ``next_action`` requirement and the mission state machine. A mission
with no scheduled requirement and no pending repair returns ``Waiting`` —
honest waiting, zero events, no invented action.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..database import FoundryDatabase

DISPATCHABLE_STATES = frozenset({"ACTION_SELECTION", "REPAIRING", "READY"})


class ProposalError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Proposal:
    """A validated, dispatchable next action."""
    action_requirement: str


@dataclass(frozen=True, slots=True)
class Waiting:
    """No useful action exists right now (honest waiting, recorded)."""
    reason: str


def propose_next_action(database: FoundryDatabase, mission_id: str) -> Proposal | Waiting:
    """Return the mission's next dispatchable action, or honest waiting.

    Raises ``ProposalError`` for unknown missions or states that cannot
    dispatch a step (the caller must not paper over that with invented work).
    """
    row = database.row("missions", "mission_id", mission_id)
    if row is None:
        raise ProposalError(f"unknown mission: {mission_id}")
    state = str(row["state"])
    if state not in DISPATCHABLE_STATES:
        raise ProposalError(f"mission {mission_id} is not dispatchable in state {state}")
    # Consume owner directives at the canonical proposal boundary so a fresh
    # MissionLoop instance observes the same durable interpretation after a
    # restart.
    from .directives import consume_directives
    consume_directives(database, mission_id)
    # Redirect projection may have changed the durable next action; reload
    # the row so this proposal and the worker context use the same truth.
    row = database.row("missions", "mission_id", mission_id)
    assert row is not None  # the existence check above remains authoritative
    state = str(row["state"])
    if state not in DISPATCHABLE_STATES:
        raise ProposalError(f"mission {mission_id} is not dispatchable in state {state}")
    requirement = str(row["next_action"] or "").strip()
    if state == "ACTION_SELECTION" and not requirement:
        return Waiting("no next action scheduled; the mission's action list is exhausted")
    if not requirement:
        requirement = "continue the current step"
    return Proposal(requirement)
