"""Distinct lifecycle states for real project outcomes (WP6).

Lane 2/3 engineering truths must remain distinguishable; collapsing all into
"fixed" is forbidden. These states reuse the existing Tier B knowledge claim
machinery (no new truth database) and the existing V3/portfolio outcome path.
The brain represents state accurately, and the context compiler preserves it.

States:
  LOCAL_VERIFIED   - verifier PASS on local checkout, not yet pushed
  PROPOSED_REMOTE  - commit pushed as draft PR / proposed remote branch
  MERGED           - change integrated into upstream main
  BLOCKED_EXTERNAL - push/merge blocked by external authority (upstream
                     unchanged, honest BLOCKED)
  DEPLOYED         - artifact deployed / released
  SUPERSEDED       - newer outcome supersedes this one
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..database import FoundryDatabase
from ..events import Event

LIFECYCLE_STATES = frozenset({
    "LOCAL_VERIFIED",
    "PROPOSED_REMOTE",
    "MERGED",
    "BLOCKED_EXTERNAL",
    "DEPLOYED",
    "SUPERSEDED",
})

# Explicit truth mapping for the three lane headline projects.
# These are the canonical examples WP6 says MUST remain distinguishable.
PROJECT_TRUTHS: dict[str, dict[str, Any]] = {
    "leadgen": {
        "project": "LeadGen",
        "implementation": "VERIFIED",
        "commit": "58b0935754c265761313079eeeca669a50fb4775",
        "lifecycle": "PROPOSED_REMOTE",
        "remote": "draft PR #1 exists",
        "upstream_main_contains_fix": False,
        "evidence": "M1_OUTCOME.md / FINAL_REPORT.md",
        "human": "LeadGen: implementation VERIFIED at 58b0935 PROPOSED_REMOTE draft PR #1 exists, main does NOT yet contain the fix",
    },
    "wan2gp": {
        "project": "Wan2GP",
        "implementation": "VERIFIED LOCALLY",
        "commit": "c767a48043c1bb13b917a8683bd71dc72234a6f6",
        "lifecycle": "BLOCKED_EXTERNAL",
        "upstream_main_contains_fix": False,
        "durable_patch_sha256": "6CDBA83D2D3B10B704D7E6C28E6C60985C421678546AC44C290611D978C6843",
        "durable_patch_path": "evidence/integration/lane2-repo-evolution/M2_WAN2GP_LONGCAT_BSA_FIX_6E35B37_C767A48.patch",
        "human": "Wan2GP: repair VERIFIED LOCALLY at c767a48 BLOCKED_EXTERNAL upstream push blocked, durable patch exists, upstream main unchanged",
    },
    "taskward": {
        "project": "taskward",
        "lifecycle": "DEPLOYED",
        "resource_id": "res-taskward",
        "remote": "Blemeh/taskward",
        "visibility": "PRIVATE",
        "accepted_version": "v0.2.0",
        "human": "Taskward: project CREATED DEPLOYED private remote Blemeh/taskward exists, v0.2.0 accepted, current remote reconciled",
    },
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_lifecycle_claim(
    db: FoundryDatabase,
    *,
    mission_id: str,
    project: str,
    lifecycle: str,
    statement: str,
    commit: str | None = None,
    evidence_refs: list[str] | None = None,
    confidence: float = 0.85,
) -> str:
    """Record a lifecycle-distinguishable Tier B claim.

    Uses the existing knowledge.claim_recorded event. No new database.
    The statement MUST contain the lifecycle token verbatim so context
    compilation preserves it (EPISTEMIC_LABELS-like verbatim handling).
    """
    if lifecycle not in LIFECYCLE_STATES:
        raise ValueError(f"unsupported lifecycle state: {lifecycle!r}")
    if lifecycle not in statement:
        raise ValueError("statement must contain the lifecycle token verbatim for honest retrieval")
    # defense: no secrets in statement
    from ..hybrid_memory.defense import DefenseScanner

    DefenseScanner().assert_clean(statement)
    claim_id = f"claim-{_sha(statement)[:16]}"
    payload: dict[str, Any] = {
        "claim_id": claim_id,
        "mission_id": mission_id,
        "statement": statement,
        "claim_type": "outcome",
        "confidence": float(confidence),
        "source": {"run_id": "software-intelligence", "event_id": f"lifecycle:{claim_id}"},
        "lifecycle": lifecycle,
        "project": project,
    }
    if commit:
        payload["commit"] = commit
    if evidence_refs:
        payload["evidence_refs"] = list(evidence_refs)
    db.append(Event.create(
        "knowledge.claim_recorded",
        payload,
        source_type="supervisor",
        source_id="software-intelligence",
        idempotency_key=f"knowledge.claim_recorded:{claim_id}",
    ))
    return claim_id


def record_project_truth(
    db: FoundryDatabase,
    *,
    mission_id: str,
    key: str,
) -> list[str]:
    """Convenience: record the canonical lane headline truth for one of the three projects."""
    if key not in PROJECT_TRUTHS:
        raise ValueError(f"unknown project truth key: {key!r}")
    truth = PROJECT_TRUTHS[key]
    stmt = truth["human"]
    lifecycle = str(truth.get("lifecycle") or "LOCAL_VERIFIED")
    commit = truth.get("commit")
    evidence = truth.get("evidence")
    refs = [evidence] if isinstance(evidence, str) else list(evidence or [])
    claim_id = record_lifecycle_claim(
        db,
        mission_id=mission_id,
        project=str(truth.get("project") or key),
        lifecycle=lifecycle,
        statement=stmt,
        commit=str(commit) if commit else None,
        evidence_refs=refs,
    )
    # Also ensure a Tier C advisory note exists for advisory recall
    from .writeback import record_software_observation

    try:
        record_software_observation(
            db,
            mission_id=mission_id,
            content=f"LIFECYCLE {lifecycle} {stmt}",
            source_ids=[claim_id],
        )
    except Exception:
        pass
    return [claim_id]


__all__ = [
    "LIFECYCLE_STATES",
    "PROJECT_TRUTHS",
    "record_lifecycle_claim",
    "record_project_truth",
]
