"""Governed software-memory writeback over EXISTING canonical contracts.

This module composes the already-promoted machinery into software-engineering
writebacks. It owns no new authority and no new event vocabulary beyond the
lane's alias binding: Tier A ledger events stay append-only, Tier B knowledge
claims start UNSUPPORTED until evidence links exist, Tier C stays advisory,
and Obsidian writes ride the governed V1 writer.

Write paths (each maps to a lane phase):

- record_software_observation  -> Tier C OBSERVED (defense-scanned)
- record_research              -> Phase F research ingestion (claims+evidence,
                                  supersession, freshness, contradictions)
- record_engineering_outcome   -> Phase G accepted work (mission ->
                                  verification PASS -> outcome -> claims/
                                  experience/Obsidian)
- record_failed_attempt        -> Phase I failed builder attempt (lesson only;
                                  rejected output NEVER becomes accepted truth)
- update_procedure_experience  -> Phase J procedure trust maturity evidence
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..database import FoundryDatabase
from ..events import Event
from ..hybrid_memory.defense import DefenseScanner, PoisonedMemoryError
from ..knowledge_records import RECORD_KINDS
from ..mission.verification import record_verification_outcome
from ..obsidian import ObsidianService
from ..obsidian.policy import FRONTMATTER as NOTE_FRONTMATTER
from ..v3_outcomes import record_outcome as record_candidate_outcome

#: Tier B claim types used by software intelligence. These are free-form in
#: the schema but kept to a fixed set here so retrieval stays deterministic.
SOFTWARE_CLAIM_TYPES = ("research", "decision", "outcome", "failure",
                        "constraint", "dependency")

#: epistemic labels that context compilation must PRESERVE verbatim
EPISTEMIC_LABELS = ("UNKNOWN", "CONFLICT", "STALE", "UNVERIFIED", "NOT_TESTED")


def _now_key(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class WritebackRejected(ValueError):
    """A governed writeback refused the content (defense/policy/contract)."""


@dataclass
class WritebackResult:
    """Every surface touched by one writeback, for evidence + assertions."""

    memory_id: str | None = None
    ingestion_id: str | None = None
    claim_ids: list[str] = field(default_factory=list)
    superseded_ids: list[str] = field(default_factory=list)
    verdict_id: str | None = None
    candidate_id: str | None = None
    outcome_id: str | None = None
    procedure_id: str | None = None
    obsidian_write_id: str | None = None
    quarantined: bool = False

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _scan(text: str) -> None:
    """Defense gate: poison/secrets never enter any durable store."""
    scanner = DefenseScanner()
    try:
        scanner.assert_clean(text)
    except PoisonedMemoryError as exc:
        raise WritebackRejected(str(exc)) from exc


def _tier_c_observe(db: FoundryDatabase, content: str, *,
                    mission_id: str, source_ids: list[str],
                    evidence_ids: list[str] | None = None) -> str:
    from ..hybrid_memory import TierCMemoryStore

    memories = TierCMemoryStore(db)
    return memories.record_observation(
        content, mission_id=mission_id, source_ids=source_ids,
        evidence_ids=evidence_ids or [])


def record_software_observation(db: FoundryDatabase, *, mission_id: str,
                                content: str, source_ids: list[str],
                                evidence_ids: list[str] | None = None,
                                ) -> str:
    """Tier C OBSERVED entry (Phase C taxonomy: observation/experience).

    Advisory only, never authority. Defense-scanned at the boundary.
    """
    _scan(content)
    return _tier_c_observe(db, content, mission_id=mission_id,
                           source_ids=source_ids, evidence_ids=evidence_ids)


# ---------------------------------------------------------------------------
# Phase F — research memory
# ---------------------------------------------------------------------------

def record_research(db: FoundryDatabase, *, mission_id: str,
                    question: str, findings: list[dict[str, Any]],
                    source_type: str, source_ref: str,
                    supersedes_claim_id: str | None = None,
                    confidence: float = 0.6,
                    fresh_until: str | None = None,
                    ) -> WritebackResult:
    """Record research through the canonical ingestion contract.

    ``findings`` items: {"statement", "claim_type"?, "confidence"?,
    "fresh_until"?, "evidence"?: [{"evidence_id", "locator", "hash"?}] }.
    External content stays DATA_ONLY: it enters as claims whose support is
    carried by explicit immutable evidence links, never as authority.
    Supersession marks the old claim SUPERSEDED (history preserved).
    """
    _scan(question)
    result = WritebackResult()
    from ..knowledge_ingestion import ingest_documents

    docs: list[dict[str, Any]] = []
    links: list[list[dict[str, Any]]] = []
    for item in findings:
        statement = str(item.get("statement") or "").strip()
        if not statement:
            raise WritebackRejected("finding without statement")
        _scan(statement)
        claim_type = item.get("claim_type", "research")
        if claim_type not in SOFTWARE_CLAIM_TYPES:
            raise WritebackRejected(f"invalid claim_type {claim_type!r}")
        doc: dict[str, Any] = {
            "statement": statement,
            "claim_type": claim_type,
            "confidence": float(item.get("confidence", confidence)),
            "fresh_until": item.get("fresh_until", fresh_until),
            "question": question,
            "source_ref": source_ref,
        }
        docs.append(doc)
        links.append(list(item.get("evidence") or []))

    ingestion = ingest_documents(db, mission_id=mission_id,
                                 source_type=source_type,
                                 source_ref=source_ref, docs=docs)
    result.ingestion_id = ingestion["ingestion_id"]
    # deterministic claim ids mirror ingest_documents' internal derivation
    for index, doc in enumerate(docs):
        payload = {k: v for k, v in doc.items() if k != "claim_id"}
        claim_id = f"claim-{_sha(__import__('json').dumps(payload, sort_keys=True))[:16]}"
        row = db.row("knowledge_claims", "claim_id", claim_id)
        if row is not None:
            result.claim_ids.append(claim_id)

    # immutable evidence links -> SUPPORTED (roadmap rule)
    from .knowledge_links import link_evidence

    for claim_id, evidences in zip(result.claim_ids, links):
        for ev in evidences:
            link_evidence(db, claim_id=claim_id,
                          evidence_id=str(ev.get("evidence_id")
                                            or _now_key("ev")),
                          locator=ev.get("locator"),
                          payload_hash=ev.get("hash"))
    if supersedes_claim_id:
        target = db.row("knowledge_claims", "claim_id", supersedes_claim_id)
        if target is None:
            raise WritebackRejected(
                f"superseded target missing: {supersedes_claim_id}")
        replacement = result.claim_ids[0] if result.claim_ids else None
        if not replacement:
            raise WritebackRejected("supersession requires at least one finding")
        db.append(Event.create(
            "knowledge.claim_superseded",
            {"claim_id": supersedes_claim_id,
             "superseded_by": replacement},
            source_type="supervisor", source_id="software-intelligence",
            idempotency_key=(f"knowledge.claim_superseded:"
                             f"{supersedes_claim_id}:{replacement}")))
        result.superseded_ids.append(supersedes_claim_id)
    # Tier C observation keeps the researcher's working note (advisory)
    summary = f"RESEARCH [{question}] via {source_ref}: " + \
        "; ".join(d["statement"] for d in docs[:3])
    result.memory_id = _tier_c_observe(
        db, summary, mission_id=mission_id, source_ids=[source_ref])
    return result


# ---------------------------------------------------------------------------
# Phase G / I — engineering outcomes and failed attempts
# ---------------------------------------------------------------------------

def record_engineering_outcome(db: FoundryDatabase, *, mission_id: str,
                               step_id: str, objective: str,
                               builder_run_id: str, verifier_run_id: str,
                               criteria: list[str], verdict: str,
                               findings: list[str],
                               evidence_bundle_id: str,
                               commit_sha: str | None,
                               predicted_minutes: float | None = None,
                               actual_minutes: float | None = None,
                               lessons: list[str] | None = None,
                               ) -> WritebackResult:
    """Phase G: accepted-path writeback after an independent verifier PASS.

    Requires verdict == 'PASS'; anything else must go through
    record_failed_attempt. Writes: verification outcome event, V3 outcome
    (predicted-vs-actual), Tier B decision/outcome claims, Tier C experience
    observations. Raw diffs are NOT copied anywhere; only pointers/hashes.
    """
    _scan(objective)
    if verdict != "PASS":
        raise WritebackRejected(
            "accepted-outcome writeback requires verifier PASS; use "
            "record_failed_attempt for FAIL verdicts")
    result = WritebackResult()
    record_verification_outcome(
        db, verdict_id=f"verdict-{step_id}", outcome="PASS",
        findings=findings,
        evidence={"bundle": evidence_bundle_id,
                  "commit": commit_sha or "unknown"})
    result.verdict_id = f"verdict-{step_id}"

    # predicted-vs-actual calibration rides the existing outcome window when
    # both values exist; missing measurements stay honestly UNKNOWN
    candidate_row = next(iter(db.all("candidates")), None)
    candidate_id = None
    mission = db.row("missions", "mission_id", mission_id)
    if mission is not None:
        created_from = str(mission.get("created_from") or "")
        if created_from.startswith("candidate:"):
            candidate_id = created_from.split(":", 1)[1]
            result.candidate_id = candidate_id
    if candidate_id is None and candidate_row is not None:
        candidate_id = candidate_row["candidate_id"]
    predicted: dict[str, Any] = {}
    actual: dict[str, Any] = {}
    if predicted_minutes is not None:
        predicted["minutes"] = predicted_minutes
    if actual_minutes is not None:
        actual["minutes"] = actual_minutes
    if candidate_id and mission is not None and \
            str(mission.get("state")) == "COMPLETE":
        try:
            recorded = record_candidate_outcome(
                db, candidate_id=candidate_id, mission_id=mission_id,
                mission_outcome="PASSED",
                predicted_value=predicted, actual_value=actual)
            result.outcome_id = recorded.get("outcome_id")
        except ValueError:
            # outcome honesty rules may refuse non-candidate missions;
            # the verification + claims below still carry acceptance truth
            pass

    # Tier B: durable decision + outcome claims (UNSUPPORTED until linked)
    decision_stmt = (f"DECISION {objective}: accepted via verifier "
                     f"{result.verdict_id} at commit {commit_sha or 'unknown'} "
                     f"(evidence {evidence_bundle_id})")
    outcome_stmt = (f"OUTCOME {objective}: verifier PASS on {mission_id}; "
                    f"actual={actual or 'UNKNOWN'} vs predicted="
                    f"{predicted or 'UNKNOWN'}")
    result.claim_ids.extend(_record_claims(
        db, mission_id=mission_id,
        statements=[(decision_stmt, "decision"), (outcome_stmt, "outcome")]))

    # Tier C: experience entries (advisory), each defense-scanned
    notes = [f"SUCCEEDED {objective} (verifier PASS, commit "
             f"{commit_sha or 'unknown'})"]
    for lesson in lessons or []:
        text = lesson.strip()
        if text:
            _scan(text)
            notes.append(f"LESSON {text}")
    for note in notes:
        mid = _tier_c_observe(db, note, mission_id=mission_id,
                              source_ids=[result.verdict_id])
        if result.memory_id is None:
            result.memory_id = mid
    return result


def record_failed_attempt(db: FoundryDatabase, *, mission_id: str,
                          step_id: str, attempt_summary: str,
                          defects: list[str], repair_plan: str,
                          builder_run_id: str, verifier_run_id: str,
                          verdict_id: str = "verdict-pending") -> WritebackResult:
    """Phase I: a verifier FAIL is valuable experience, NEVER accepted truth.

    Stores the failure as labeled lesson material only. Nothing here marks
    the attempt complete; the rejected output can never resurface as an
    accepted project state because no accepted-truth surface is written.
    """
    _scan(attempt_summary)
    result = WritebackResult()
    record_verification_outcome(
        db, verdict_id=verdict_id, outcome="FAIL",
        findings=list(defects),
        evidence={"attempt": attempt_summary})
    result.verdict_id = verdict_id
    defect_lines = "; ".join(d.strip() for d in defects if d.strip())
    stmts = [
        (f"FAILURE ATTEMPT {objective_of(attempt_summary)}: verifier FAIL "
         f"({verdict_id}); defects: {defect_lines}", "failure"),
        (f"REPAIR PLAN for {verdict_id}: {repair_plan}", "constraint"),
    ]
    result.claim_ids.extend(_record_claims(
        db, mission_id=mission_id, statements=stmts))
    lesson = (f"FAILED ATTEMPT (lesson, NOT accepted truth) "
              f"[{verdict_id}] {attempt_summary}; avoid repeating: "
              f"{defect_lines}")
    result.memory_id = _tier_c_observe(
        db, lesson, mission_id=mission_id,
        source_ids=[builder_run_id, verifier_run_id])
    return result


def objective_of(summary: str) -> str:
    return summary.split(":")[0].strip()[:80]


def _record_claims(db: FoundryDatabase, *, mission_id: str,
                   statements: list[tuple[str, str]]) -> list[str]:
    ids: list[str] = []
    for statement, claim_type in statements:
        _scan(statement)
        claim_id = f"claim-{_sha(_sha(statement))[:16]}"
        db.append(Event.create(
            "knowledge.claim_recorded",
            {"claim_id": claim_id, "mission_id": mission_id,
             "statement": statement, "claim_type": claim_type,
             "confidence": 0.75,
             "source": {"run_id": "software-intelligence",
                        "event_id": f"swi:{claim_id}"}},
            source_type="supervisor", source_id="software-intelligence",
            idempotency_key=f"knowledge.claim_recorded:{claim_id}"))
        ids.append(claim_id)
    return ids


# ---------------------------------------------------------------------------
# Phase J — procedure experience / trust maturity
# ---------------------------------------------------------------------------

def update_procedure_experience(db: FoundryDatabase, *, name: str,
                                description: str, body: str,
                                trigger_terms: list[str],
                                invocation_verdict: str,
                                mission_id: str,
                                procedure_id: str | None = None,
                                mark_stale: bool = False,
                                ) -> WritebackResult:
    """Accumulate verified experience onto a Tier C procedure.

    PROVEN_ONCE does NOT become TRUSTED: confidence moves only on real
    independently-verified invocations (bounded growth per PASS), and a
    reality change marks the procedure STALE instead of letting stale
    guidance silently ride forever.
    """
    _scan(body)
    result = WritebackResult()
    from ..hybrid_memory import ProcedureRegistry

    registry = ProcedureRegistry(db)
    pid = procedure_id
    if pid is None:
        hits = [h for h in registry.search(name)
                if h.get("name") == name]
        pid = hits[0]["procedure_id"] if hits else registry.add(
            name=name, description=description, body=body,
            trigger_terms=list(trigger_terms))
    result.procedure_id = pid
    row = db.row("tier_c_procedures", "procedure_id", pid)
    if row is None:
        raise WritebackRejected(f"procedure missing after upsert: {pid}")
    current = float(row.get("confidence") or 0.5)
    if mark_stale:
        new_confidence = max(0.0, current - 0.2)
        new_state = "STALE"
    elif invocation_verdict == "PASS":
        new_confidence = min(0.95, current + 0.1)
        new_state = str(row.get("state") or "ACTIVE")
    elif invocation_verdict == "FAIL":
        new_confidence = max(0.05, current - 0.15)
        new_state = str(row.get("state") or "ACTIVE")
    else:
        new_confidence = current
        new_state = str(row.get("state") or "ACTIVE")

    # Canonical seams only: procedure.updated flips lifecycle state;
    # the procedure.created UPSERT projection is the one sanctioned way
    # confidence/body mutate for an existing procedure id.
    if new_state != str(row.get("state") or "ACTIVE"):
        registry.set_state(pid, new_state,
                           actor="software-intelligence",
                           reason=(f"experience after "
                                   f"{invocation_verdict} ({mission_id})"))
    db.append(Event.create(
        "procedure.created",
        {"procedure_id": pid, "name": str(row.get("name") or name),
         "description": str(row.get("description") or description),
         "body": str(row.get("body") or body),
         "trigger_terms": list(trigger_terms),
         "state": new_state, "confidence": new_confidence,
         "mission_id": mission_id, "created_by": "software-intelligence"},
        source_type="memory", source_id="software-intelligence",
        idempotency_key=(
            "procedure.experience:"
            f"{_sha(f'{pid}:{invocation_verdict}:{mark_stale}:{new_confidence}')[:24]}")))
    return result


# ---------------------------------------------------------------------------
# Phase K — Obsidian project intelligence (governed V1 note surface)
# ---------------------------------------------------------------------------

def render_project_note(*, project_name: str, resource_id: str,
                        goal: str, current_state: str,
                        decisions: list[str], failures: list[str],
                        lessons: list[str], unknowns: list[str],
                        evidence_pointers: list[str]) -> str:
    """Human-readable project brain note following the governed contract."""
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {i}" for i in items) if items else "- (none recorded)"

    fm = "\n".join(f"{k}: {v}" for k, v in NOTE_FRONTMATTER.items())
    body = f"""---
{fm}
---

# Project Intelligence - {project_name}

## Identity
resource_id: {resource_id}

## Goal
{goal}

## Current State
{current_state}

## Decisions
{bullets(decisions)}

## Known Failures (lessons, NOT accepted truth)
{bullets(failures)}

## Lessons That Worked
{bullets(lessons)}

## Unknowns
{bullets(unknowns)}

## Evidence Pointers
{bullets(evidence_pointers)}
"""
    return body


def write_project_note(service: ObsidianService, *, note_name: str,
                       content: str, expected_hash: str | None = None,
                       mission_id: str | None = None) -> Any:
    """Governed write of a project intelligence note."""
    _scan(content)
    return service.write_note(note_name=note_name, content=content,
                              expected_hash=expected_hash,
                              mission_id=mission_id)


__all__ = [
    "EPISTEMIC_LABELS",
    "SOFTWARE_CLAIM_TYPES",
    "WritebackRejected",
    "WritebackResult",
    "record_engineering_outcome",
    "record_failed_attempt",
    "record_research",
    "record_software_observation",
    "render_project_note",
    "update_procedure_experience",
    "write_project_note",
]
