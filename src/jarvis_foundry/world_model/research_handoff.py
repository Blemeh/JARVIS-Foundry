"""Research handoff - when knowledge is missing, hand off to research systems."""
from __future__ import annotations

from typing import Any

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.world_model.gaps import WorldKnowledgeGapStore


def handoff_to_research(
    db: FoundryDatabase,
    gap_id: str,
    *,
    queue: Any | None = None,
    source_id: str = "world-model",
) -> dict[str, Any]:
    """Hand off a knowledge gap to the research system.

    Creates a research queue entry for the gap. The gap stays unresolved
    until research returns evidence. Provenance tracks the handoff.
    """
    gaps = WorldKnowledgeGapStore(db)
    gap = gaps.get(gap_id)
    if gap is None:
        raise ValueError(f"unknown gap {gap_id}")
    if gap["resolved"]:
        return {"gap_id": gap_id, "already_resolved": True}

    # Try to use the existing research queue if available
    try:
        from jarvis_foundry.research_queue import ResearchQueue
        rq = queue or ResearchQueue(db)
        # Research queue expects a question/mission context
        result = rq.enqueue(
            question=f"Resolve knowledge gap [{gap['gap_kind']}]: {gap['description']}",
            mission_id=gap.get("subject_id") or gap_id,
            source="world_model",
        )
        return {"gap_id": gap_id, "research_queued": True, "result": result}
    except (ImportError, AttributeError, TypeError, ValueError) as e:
        # Research system not available - mark gap as needing external research
        return {"gap_id": gap_id, "research_queued": False, "reason": str(e), "gap": gap}


def consume_research_result(
    db: FoundryDatabase,
    gap_id: str,
    result: dict[str, Any],
    *,
    source_id: str = "world-model",
) -> dict[str, Any]:
    """Consume a research result to resolve a knowledge gap.

    Preserves source, timestamp, confidence, and external/unverified marking.
    """
    gaps = WorldKnowledgeGapStore(db)
    gap = gaps.get(gap_id)
    if gap is None:
        raise ValueError(f"unknown gap {gap_id}")

    # Record the external evidence as a world entity
    from jarvis_foundry.world_model.entities import WorldEntityStore
    entities = WorldEntityStore(db)
    eid = entities.record(
        "KNOWLEDGE_CLAIM",
        result.get("summary") or result.get("statement") or gap["description"],
        attributes={"gap_id": gap_id, "research_result": result},
        provenance={
            "source": result.get("source", "research"),
            "kind": "EXTERNAL_UNVERIFIED",
            "gap_id": gap_id,
            "confidence": result.get("confidence", 0.5),
        },
        authority="EXTERNAL_UNVERIFIED",
        record_kind="EXTERNAL_CLAIM",
        confidence=result.get("confidence", 0.5),
        source_id=source_id,
    )

    # Resolve the gap
    gaps.resolve(gap_id, resolved_by=eid, source_id=source_id)
    return {"gap_id": gap_id, "entity_id": eid, "resolved": True}