"""Append-only evidence persistence and reuse of the existing candidate API."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..control.broker import emergency_stop_reason
from ..control.redaction import contains_secret_like
from ..database import FoundryDatabase
from ..discovery_candidates import (
    covered_objective,
    generate_candidate,
    score_candidate,
)
from ..events import Event, canonical_json
from ..perception.contracts import PerceptionObservation
from .contracts import BRIDGE_SOURCE, PatternEvidence, WorkflowDiscoveryConfig
from .miner import mine


@dataclass(frozen=True, slots=True)
class BridgeResult:
    pattern_id: str
    evidence_ref: str
    evidence_event_id: str | None
    candidate_id: str | None
    created: bool
    duplicate_of: str | None
    score: float | None
    stopped: bool = False
    stop_reason: str | None = None


def run_cycle(database: FoundryDatabase,
              observations: list[PerceptionObservation],
              config: WorkflowDiscoveryConfig,
              *, stop_path: str | Path | None = None,
              reference_time: str | None = None) -> dict[str, Any]:
    """Run one bounded, explicitly enabled mining cycle.

    There is no scheduler here. The wrapper exists to make the independent
    Workflow Discovery disable switch testable and fail closed; continuous
    activation remains outside Phase 3.
    """
    config.validate()
    if not config.enabled:
        return {"disabled": True, "sessions": 0, "patterns": 0,
                "bridges": []}
    result = mine(observations, config, reference_time=reference_time)
    bridges = [bridge_pattern(database, pattern, stop_path=stop_path)
               for pattern in result.patterns]
    return {
        "disabled": False,
        "sessions": len(result.sessions),
        "patterns": len(result.patterns),
        "bridges": bridges,
    }


def _evidence_digest(evidence: PatternEvidence) -> str:
    return hashlib.sha256(
        canonical_json(evidence.as_dict()).encode("utf-8")
    ).hexdigest()[:24]


def _event_for_key(database: FoundryDatabase, key: str) -> dict[str, Any] | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT event_id, payload_json FROM events WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
    return dict(row) if row else None


def persist_pattern_evidence(database: FoundryDatabase,
                             evidence: PatternEvidence,
                             *, stop_path: str | Path | None = None
                             ) -> tuple[str, bool]:
    """Persist one immutable evidence snapshot in the existing event ledger.

    A new occurrence creates a new content-derived event key and the payload
    contains the complete lineage. Re-mining identical history is a no-op.
    """
    stop_path = stop_path or database.path.parent / "EMERGENCY_STOP"
    reason = emergency_stop_reason(database, stop_path)
    if reason:
        return "", False
    payload = evidence.as_dict()
    serialized = canonical_json(payload)
    if ("raw_external" in serialized or "semantic_summary" in serialized or
            contains_secret_like(serialized)):
        raise ValueError("workflow evidence must not contain raw observation fields")
    digest = _evidence_digest(evidence)
    key = f"workflow.pattern_evidence:{evidence.pattern_id}:{digest}"
    existing = _event_for_key(database, key)
    if existing:
        return str(existing["event_id"]), False
    previous = None
    with database.connect() as connection:
        row = connection.execute(
            "SELECT event_id FROM events WHERE event_type = 'workflow.pattern_evidence' "
            "AND payload_json LIKE ? ORDER BY sequence DESC LIMIT 1",
            (f'%"pattern_id":"{evidence.pattern_id}"%',),
        ).fetchone()
        if row:
            previous = row[0]
    if previous:
        payload = dict(payload)
        payload["supersedes_event_id"] = previous
    event = Event.create(
        "workflow.pattern_evidence", payload,
        source_type="supervisor", source_id=BRIDGE_SOURCE,
        idempotency_key=key,
    )
    database.append(event)
    stored = _event_for_key(database, key)
    return str(stored["event_id"] if stored else event.event_id), True


def _candidate_row(database: FoundryDatabase, candidate_id: str) -> dict[str, Any] | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT candidate_id, source, status FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    return dict(row) if row else None


def bridge_pattern(database: FoundryDatabase, evidence: PatternEvidence,
                   *, objective: str | None = None,
                   stop_path: str | Path | None = None) -> BridgeResult:
    """Persist evidence, then call only the existing candidate generator/scorer.

    This function never approves a candidate and never creates a mission.
    The deterministic placeholder mission id is provenance text only; no
    mission creation is emitted by workflow discovery.
    """
    stop_path = stop_path or database.path.parent / "EMERGENCY_STOP"
    reason = emergency_stop_reason(database, stop_path)
    if reason:
        return BridgeResult(evidence.pattern_id, evidence.evidence_ref, None,
                            None, False, None, None, True, reason)
    objective = objective or (
        f"Investigate repeated workflow opportunity {evidence.pattern_id}")
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("candidate objective must be a non-empty string")
    if "workflow" not in objective.lower() or evidence.pattern_id not in objective:
        raise ValueError("candidate objective must reference the safe pattern id")
    if contains_secret_like(objective):
        raise ValueError("candidate objective contains secret-like material")
    event_id, _ = persist_pattern_evidence(database, evidence,
                                           stop_path=stop_path)
    if not event_id:
        reason = emergency_stop_reason(database, stop_path) or "STOP active"
        return BridgeResult(evidence.pattern_id, evidence.evidence_ref, None,
                            None, False, None, None, True, reason)
    reason = emergency_stop_reason(database, stop_path)
    if reason:
        return BridgeResult(evidence.pattern_id, evidence.evidence_ref, event_id,
                            None, False, None, None, True, reason)
    evidence_ref = {
        "kind": "workflow_pattern",
        "ref": evidence.evidence_ref,
        "event_id": event_id,
        "fingerprint": evidence.fingerprint,
        "occurrence_count": evidence.occurrence_count,
        "privacy_state": evidence.privacy_state,
    }
    canonical = objective.strip()
    duplicate = covered_objective(database, canonical)
    if duplicate:
        row = _candidate_row(database, duplicate)
        score = None
        if row and row["source"] == BRIDGE_SOURCE and row["status"] == "NEW":
            score = score_candidate(database, duplicate)
        return BridgeResult(evidence.pattern_id, evidence.evidence_ref, event_id,
                            duplicate if row else None, False, duplicate, score)
    result = generate_candidate(
        database,
        f"workflow-discovery:{evidence.pattern_id}",
        BRIDGE_SOURCE,
        canonical,
        [evidence_ref],
    )
    candidate_id = str(result["candidate_id"])
    score = score_candidate(database, candidate_id) if result["created"] else None
    return BridgeResult(evidence.pattern_id, evidence.evidence_ref, event_id,
                        candidate_id, bool(result["created"]),
                        result.get("duplicate_of"), score)
