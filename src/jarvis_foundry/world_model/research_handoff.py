"""Fail-closed World Model handoff to the canonical research pipeline."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from jarvis_foundry.control.broker import emergency_stop_reason
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import ingest_observed_candidate
from jarvis_foundry.events import canonical_json
from jarvis_foundry.mission.verification import (
    validate_final_outcome_contract,
)
from jarvis_foundry.research_decision import (
    DECISION_RECOMMENDATION_CONTRACT,
    DECISION_RESULT_CONTRACT,
    DecisionRecommendation,
    DecisionResult,
    load_decision_recommendation,
    load_decision_result,
)
from jarvis_foundry.research_dossier import (
    DOSSIER_CONTRACT,
    DOSSIER_SCORING_VERSION,
)
from jarvis_foundry.research_queue import enqueue_candidate_research
from jarvis_foundry.world_model.entities import WorldEntityStore
from jarvis_foundry.world_model.gaps import WorldKnowledgeGapStore


def _digest(value: Any) -> str:
    return hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def handoff_to_research(
    db: FoundryDatabase,
    gap_id: str,
    *,
    queue: Any | None = None,
    source_id: str = "world-model",
) -> dict[str, Any]:
    """Create one idempotent candidate and canonical research-queue pass."""

    if queue is not None:
        raise ValueError(
            "custom research queues are not supported; use the canonical queue"
        )
    gaps = WorldKnowledgeGapStore(db)
    gap = gaps.get(gap_id)
    if gap is None:
        raise ValueError(f"unknown gap {gap_id}")
    if gap["resolved"]:
        return {"gap_id": gap_id, "already_resolved": True}
    stop = emergency_stop_reason(db, None, {"GLOBAL", gap_id})
    if stop:
        return {
            "gap_id": gap_id,
            "research_queued": False,
            "status": "STOPPED",
            "reason": stop,
        }

    material = {
        "gap_id": gap_id,
        "gap_kind": gap["gap_kind"],
        "subject_type": gap.get("subject_type"),
        "subject_id": gap.get("subject_id"),
        "description": gap["description"],
        "blocking": bool(gap["blocking"]),
    }
    content_hash = _digest(material)
    objective = (
        f"Resolve world knowledge gap [{gap['gap_kind']}]: "
        f"{gap['description']}"
    )
    candidate = ingest_observed_candidate(
        db,
        f"world-gap:{gap_id}",
        observation_id=f"world-gap:{gap_id}:{content_hash[:16]}",
        source_kind="WORLD_MODEL",
        source_identity=f"world-gap:{gap_id}",
        source_ref=f"world-gap:{gap_id}",
        content_hash=f"sha256:{content_hash}",
        objective=objective,
        authority="DATA_ONLY",
        target_project=str(gap.get("subject_id") or "JARVIS World Model"),
        alias_key=f"world-gap:{gap_id}",
        evidence=[{
            "record_type": "jarvis.v13.world-gap-research.v1",
            "gap_id": gap_id,
            "gap_kind": gap["gap_kind"],
            "subject_type": gap.get("subject_type"),
            "subject_id": gap.get("subject_id"),
            "blocking": bool(gap["blocking"]),
            "authority": "DATA_ONLY",
        }],
        candidate_type="world_signal",
        expected_outcome=(
            "Evidence-backed gap resolution or explicit insufficient evidence"
        ),
        confidence=0.30,
    )
    candidate_id = candidate.get("candidate_id")
    if not candidate_id:
        return {
            "gap_id": gap_id,
            "research_queued": False,
            "status": "ALREADY_COVERED",
            "covered_by": candidate.get("duplicate_of"),
        }
    queued = enqueue_candidate_research(
        db,
        candidate_id=str(candidate_id),
        cycle_id=f"world-gap:{gap_id}",
        pass_number=1,
        budget_used=0.5,
        per_candidate_budget=0.5,
        cycle_budget=1.0,
        max_normal_passes=1,
        source_ref=f"world-gap:{gap_id}",
        evidence={
            "authority": "DATA_ONLY",
            "gap_id": gap_id,
            "gap_hash": content_hash,
            "requested_by": source_id,
        },
        input_hash=content_hash,
    )
    return {
        "gap_id": gap_id,
        "candidate_id": str(candidate_id),
        "candidate_created": bool(candidate.get("created")),
        "research_id": queued.get("research_id"),
        "research_status": queued.get("status"),
        "research_queued": queued.get("status") == "ENQUEUED",
        "authority": "CANONICAL_RESEARCH_QUEUE",
    }


def _accepted_outcome(
    db: FoundryDatabase,
    *,
    event_id: str,
    decision_result: DecisionResult,
) -> Mapping[str, Any] | None:
    durable_result = load_decision_result(db, decision_result.result_id)
    if (
        durable_result is None
        or durable_result.as_dict() != decision_result.as_dict()
    ):
        return None
    for event in db.events(event_types=("mission.outcome_finalized",)):
        if event.event_id != event_id:
            continue
        payload = {
            **event.payload,
            "outcome_event_id": event.event_id,
            "outcome_source_type": event.source_type,
            "outcome_source_id": event.source_id,
        }
        if (
            validate_final_outcome_contract(db, payload)
            and payload.get("status") == "SUCCEEDED"
            and payload.get("candidate_id") == decision_result.candidate_id
            and (
                not decision_result.executive_decision_id
                or payload.get("decision_id")
                == decision_result.executive_decision_id
            )
        ):
            return payload
        return None
    return None


def _bounded_result(
    db: FoundryDatabase,
    result: Mapping[str, Any],
) -> tuple[str, str, float, str | None, DecisionResult | None]:
    contract = str(result.get("contract") or "")
    if contract == DOSSIER_CONTRACT:
        if result.get("scoring_version") != DOSSIER_SCORING_VERSION:
            raise ValueError("unsupported dossier scoring version")
        if result.get("authority") != "DATA_ONLY":
            raise ValueError("research dossier must remain DATA_ONLY")
        conclusion = result.get("final_research_conclusion")
        conclusion = conclusion if isinstance(conclusion, Mapping) else {}
        confidence = result.get("confidence")
        confidence = confidence if isinstance(confidence, Mapping) else {}
        return (
            str(
                result.get("human_summary")
                or conclusion.get("summary")
                or "Research dossier recorded"
            ),
            str(conclusion.get("verdict") or "UNKNOWN"),
            float(confidence.get("score") or 0.0),
            str(result.get("dossier_revision") or "") or None,
            None,
        )
    if contract == DECISION_RECOMMENDATION_CONTRACT:
        recommendation = DecisionRecommendation.from_mapping(result)
        return (
            recommendation.rationale[0],
            recommendation.status.value,
            {"LOW": 0.35, "MEDIUM": 0.65, "HIGH": 0.85}[
                recommendation.confidence_band.value
            ],
            recommendation.recommendation_id,
            None,
        )
    if contract == DECISION_RESULT_CONTRACT:
        decision_result = DecisionResult.from_mapping(result)
        recommendation = load_decision_recommendation(
            db, decision_result.recommendation_id,
        )
        summary = (
            recommendation.rationale[0]
            if recommendation is not None
            else "Executive consumed a research recommendation"
        )
        return (
            summary,
            decision_result.executive_kind or "UNEVALUATED",
            0.75 if decision_result.consumed_by_executive else 0.40,
            decision_result.result_id,
            decision_result,
        )
    raise ValueError(f"unsupported research result contract {contract!r}")


def consume_research_result(
    db: FoundryDatabase,
    gap_id: str,
    result: dict[str, Any],
    *,
    source_id: str = "world-model",
) -> dict[str, Any]:
    """Record bounded evidence and resolve only from accepted mission truth."""

    gaps = WorldKnowledgeGapStore(db)
    gap = gaps.get(gap_id)
    if gap is None:
        raise ValueError(f"unknown gap {gap_id}")
    if gap["resolved"]:
        return {"gap_id": gap_id, "already_resolved": True}

    summary, status, confidence, reference_id, decision_result = \
        _bounded_result(db, result)
    result_hash = _digest(result)
    outcome_event_id = str(
        result.get("verified_outcome_event_id") or ""
    ).strip()
    accepted = (
        _accepted_outcome(
            db,
            event_id=outcome_event_id,
            decision_result=decision_result,
        )
        if outcome_event_id and decision_result is not None
        else None
    )
    authority = "VERIFIED_EVIDENCE" if accepted else "EXTERNAL_UNVERIFIED"
    entity_id = "we-research-" + _digest({
        "gap_id": gap_id,
        "result_hash": result_hash,
    })[:16]
    entities = WorldEntityStore(db)
    if entities.get(entity_id) is None:
        entity_id = entities.record(
            "KNOWLEDGE_CLAIM",
            summary[:500],
            entity_id=entity_id,
            attributes={
                "gap_id": gap_id,
                "result_contract": result.get("contract"),
                "result_hash": f"sha256:{result_hash}",
                "reference_id": reference_id,
                "status": status,
                "verified_outcome_event_id": outcome_event_id or None,
            },
            provenance={
                "source": "research",
                "kind": authority,
                "gap_id": gap_id,
                "result_hash": f"sha256:{result_hash}",
                "verified_outcome_event_id": outcome_event_id or None,
            },
            authority=authority,
            record_kind="RESULT" if accepted else "EXTERNAL_CLAIM",
            confidence=max(0.0, min(1.0, confidence)),
            source_id=source_id,
            idempotency_key=(
                f"world.entity_recorded:research-result:{gap_id}:{result_hash}"
            ),
        )
    if accepted:
        gaps.resolve(gap_id, resolved_by=entity_id, source_id=source_id)
        return {
            "gap_id": gap_id,
            "entity_id": entity_id,
            "resolved": True,
            "verified_outcome_event_id": outcome_event_id,
            "authority": "VERIFIED_EVIDENCE",
        }
    return {
        "gap_id": gap_id,
        "entity_id": entity_id,
        "resolved": False,
        "status": "EVIDENCE_RECORDED",
        "reason": (
            "research evidence is advisory until a matching accepted "
            "mission.outcome_finalized event is supplied"
        ),
        "authority": "EXTERNAL_UNVERIFIED",
    }
