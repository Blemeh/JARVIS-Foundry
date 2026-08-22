"""V3 Discovery and Building: bounded temporary teams + verifier binding (WP4).

MVP scope over the EXISTING RoleDispatcher / verification machinery —
no new agent framework:
- ``ROLE_MANIFEST``: the bounded role table (read/propose/mutate/verify/
  accept) with max team size and delegation depth from spec §3.7;
- ``fresh_verifier_identity``: verifier binding that REQUIRES
  ``fresh_session=true``; same provider is recorded as
  ``independence=REDUCED``, never silently accepted as independent;
- ``repair_budget_exhausted``: durable repair budget (default 3) read
  from recorded FAIL verdicts, not memory.
"""
from __future__ import annotations

from typing import Any

from .database import FoundryDatabase
from .mission.verification import failed_verdict_count

MAX_TEAM_SIZE = 6
MAX_DELEGATION_DEPTH = 2
DEFAULT_REPAIR_BUDGET = 3

ROLE_MANIFEST: dict[str, dict[str, Any]] = {
    "scout": {"read": True, "propose": "candidates", "mutate": None,
              "verify": False, "accept": False},
    "researcher": {"read": True, "propose": "research_artifacts",
                   "mutate": "worktree_read", "verify": False,
                   "accept": False},
    "architect": {"read": True, "propose": "design_docs", "mutate": None,
                  "verify": False, "accept": False},
    "builder": {"read": "mission_scope", "propose": "code_changes",
                "mutate": "worktree_write", "verify": False,
                "accept": False},
    "tester": {"read": "build_output", "propose": "test_results",
               "mutate": "test_fixtures", "verify": False, "accept": False},
    "critic": {"read": True, "propose": "review_notes", "mutate": None,
               "verify": False, "accept": False},
    "verifier": {"read": True, "propose": "verdict", "mutate": None,
                 "verify": True, "accept": False},
    "repairer": {"read": "failure_evidence", "propose": "fix",
                 "mutate": "worktree_write", "verify": False,
                 "accept": False},
    "documenter": {"read": "mission_state", "propose": "docs",
                   "mutate": "docs_only", "verify": False, "accept": False},
    "knowledge_curator": {"read": True, "propose": "claim_proposals",
                          "mutate": "vault_governed", "verify": False,
                          "accept": False},
}


def validate_role(role: str) -> dict[str, Any]:
    """Fail-closed role lookup: an unknown role cannot dispatch."""
    entry = ROLE_MANIFEST.get(role)
    if entry is None:
        raise ValueError(f"unknown team role: {role!r} "
                         f"(bounded manifest has {len(ROLE_MANIFEST)} roles)")
    return entry


def validate_team(roles: list[str]) -> dict[str, Any]:
    """Team-size cap + builder/verifier separation at manifest level."""
    if len(roles) > MAX_TEAM_SIZE:
        raise ValueError(
            f"team size {len(roles)} exceeds bounded maximum "
            f"{MAX_TEAM_SIZE}")
    unique = sorted(set(roles))
    for role in unique:
        validate_role(role)
    # Verifier uniqueness counts RAW entries: two "verifier" slots is a
    # two-verifier team even though the role set dedupes.
    verifier_slots = [r for r in roles
                      if ROLE_MANIFEST[r]["verify"]]
    if len(verifier_slots) > 1:
        raise ValueError(
            f"at most one verifier per team, got {len(verifier_slots)}")
    return {"size": len(unique), "roles": unique, "verifiers": verifier_slots}


def fresh_verifier_identity(
    *, verifier_provider: str, verifier_session_id: str,
    builder_provider: str | None, builder_run_id: str | None = None,
) -> dict[str, Any]:
    """Verifier identity binding for one verification pass.

    Fail-closed rules (spec §3.7):
    - ``verifier_session_id`` MUST be a fresh session id distinct from
      the builder run — reuse raises;
    - same provider as the builder does NOT block verification but is
      recorded as ``independence=REDUCED`` so downstream consumers can
      weigh the verdict honestly.
    """
    if not verifier_session_id or not str(verifier_session_id).strip():
        raise ValueError("verifier requires a fresh session identity")
    if builder_run_id and verifier_session_id == builder_run_id:
        raise ValueError(
            "verifier session must be FRESH — reusing the builder run id "
            "is self-certification and is rejected")
    independence = (
        "INDEPENDENT"
        if builder_provider is None or verifier_provider != builder_provider
        else "REDUCED")
    return {
        "role": "verifier",
        "manifest": ROLE_MANIFEST["verifier"],
        "provider": verifier_provider,
        "session_id": verifier_session_id,
        "fresh_session": True,
        "builder_run_id": builder_run_id,
        "builder_provider": builder_provider,
        "independence": independence,
    }


def repair_budget_exhausted(
    database: FoundryDatabase, *, mission_id: str, step_id: str,
    repair_budget: int = DEFAULT_REPAIR_BUDGET,
) -> dict[str, Any]:
    """Durable repair-budget check: FAIL verdicts recorded for the step.

    After ``repair_budget`` FAILs the step is exhausted — callers must
    surface BLOCKED instead of dispatching an infinite repair loop.
    """
    fails = failed_verdict_count(database, mission_id, step_id)
    remaining = max(0, int(repair_budget) - int(fails))
    return {
        "mission_id": mission_id,
        "step_id": step_id,
        "failed_verdicts": fails,
        "repair_budget": int(repair_budget),
        "remaining": remaining,
        "exhausted": remaining <= 0,
    }
