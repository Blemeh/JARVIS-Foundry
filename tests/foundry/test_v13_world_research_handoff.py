from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.research_decision import (
    DecisionResult,
    record_decision_result,
)
from jarvis_foundry.research_dossier import (
    DOSSIER_CONTRACT,
    DOSSIER_SCORING_VERSION,
)
from jarvis_foundry.world_model.gaps import WorldKnowledgeGapStore
from jarvis_foundry.world_model.research_handoff import (
    consume_research_result,
    handoff_to_research,
)


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    assert database.initialize() == 41
    return database


def _gap(database: FoundryDatabase, suffix: str = "one") -> str:
    return WorldKnowledgeGapStore(database).record(
        "UNKNOWN_DEPENDENCY",
        f"Determine the verified dependency state for {suffix}",
        gap_id=f"gap-v13-{suffix}",
        subject_type="PROJECT",
        subject_id="JARVIS-Foundry",
        blocking=True,
    )


def _dossier() -> dict[str, object]:
    return {
        "contract": DOSSIER_CONTRACT,
        "scoring_version": DOSSIER_SCORING_VERSION,
        "dossier_revision": "dossier-" + "a" * 32,
        "authority": "DATA_ONLY",
        "human_summary": "Primary evidence remains incomplete.",
        "final_research_conclusion": {
            "verdict": "INSUFFICIENT_EVIDENCE",
        },
        "confidence": {"score": 0.35},
    }


def _decision_result() -> DecisionResult:
    return DecisionResult(
        result_id="decision-result-v13-world",
        recommendation_id="recommendation-v13-world",
        recommendation_hash="a" * 64,
        candidate_id="candidate-v13-world",
        candidate_created=True,
        candidate_fingerprint="fingerprint-v13-world",
        evaluated_by_executive=True,
        consumed_by_executive=True,
        executive_decision_id="decision-v13-world",
        executive_kind="SELECT",
        executive_candidate_id="candidate-v13-world",
    )


def _accepted_outcome(
    database: FoundryDatabase,
    result: DecisionResult,
) -> Event:
    mission_id = "mission-v13-world"
    verdict_id = "verdict-v13-world"
    acceptance = Event.create(
        "verification.accepted",
        {
            "mission_id": mission_id,
            "verdict_id": verdict_id,
            "decision": "ACCEPT",
        },
        source_type="supervisor",
        source_id="acceptance-gate",
        idempotency_key="verification.accepted:v13-world",
    )
    database.append(acceptance)
    outcome_id = "outcome-" + hashlib.sha256(
        f"{mission_id}:SUCCEEDED:{acceptance.event_id}".encode("utf-8")
    ).hexdigest()[:24]
    outcome = Event.create(
        "mission.outcome_finalized",
        {
            "schema": "jarvis.v11.outcome.v1",
            "outcome_id": outcome_id,
            "mission_id": mission_id,
            "candidate_id": result.candidate_id,
            "decision_id": result.executive_decision_id,
            "status": "SUCCEEDED",
            "truth_accepted": True,
            "acceptance_identity": acceptance.event_id,
            "acceptance_kind": acceptance.event_type,
            "verification": {"verdict_id": verdict_id},
        },
        source_type="supervisor",
        source_id="outcome-reconciler",
        idempotency_key=f"mission.outcome_finalized:{outcome_id}",
    )
    database.append(outcome)
    return outcome


def test_gap_handoff_uses_canonical_candidate_and_queue_once(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    gap_id = _gap(database)

    first = handoff_to_research(database, gap_id)
    replay = handoff_to_research(database, gap_id)

    assert first["candidate_created"] is True
    assert first["research_queued"] is True
    assert replay["candidate_id"] == first["candidate_id"]
    assert replay["research_id"] == first["research_id"]
    assert replay["candidate_created"] is False
    assert database.event_count(
        event_type="discovery.candidate_generated"
    ) == 1
    assert database.event_count(
        event_type="research.candidate_enqueued"
    ) == 1
    candidate = database.row(
        "candidates", "candidate_id", str(first["candidate_id"])
    )
    assert candidate is not None
    assert candidate["candidate_type"] == "world_signal"
    assert WorldKnowledgeGapStore(database).get(gap_id)["resolved"] == 0


def test_gap_handoff_rejects_custom_queue_and_obeys_stop(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    gap_id = _gap(database, "stopped")
    with pytest.raises(ValueError, match="canonical queue"):
        handoff_to_research(database, gap_id, queue=object())

    database.append(Event.create(
        "owner_directive.recorded",
        {
            "directive_id": "directive-v13-world-stop",
            "command_type": "STOP_EVERYTHING",
            "target": "GLOBAL",
            "exact_text": "stop everything",
            "reason": "world research handoff test",
        },
        source_type="owner",
        source_id="v13-world-test",
        idempotency_key=(
            "owner_directive.recorded:directive-v13-world-stop"
        ),
    ))
    stopped = handoff_to_research(database, gap_id)
    assert stopped["status"] == "STOPPED"
    assert stopped["research_queued"] is False
    assert database.event_count(
        event_type="discovery.candidate_generated"
    ) == 0


def test_advisory_dossier_records_once_without_resolving_gap(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    gap_id = _gap(database, "advisory")

    first = consume_research_result(database, gap_id, _dossier())
    replay = consume_research_result(database, gap_id, _dossier())

    assert first == replay
    assert first["resolved"] is False
    assert first["authority"] == "EXTERNAL_UNVERIFIED"
    assert database.event_count(event_type="world.entity_recorded") == 1
    assert WorldKnowledgeGapStore(database).get(gap_id)["resolved"] == 0


def test_forged_outcome_cannot_resolve_gap(tmp_path: Path) -> None:
    database = _database(tmp_path)
    gap_id = _gap(database, "forged")
    result = _decision_result()
    record_decision_result(database, result)
    forged = Event.create(
        "mission.outcome_finalized",
        {
            "schema": "jarvis.v11.outcome.v1",
            "outcome_id": "outcome-" + "0" * 24,
            "mission_id": "mission-v13-world",
            "candidate_id": result.candidate_id,
            "decision_id": result.executive_decision_id,
            "status": "SUCCEEDED",
            "truth_accepted": True,
            "acceptance_identity": "fabricated",
            "acceptance_kind": "verification.accepted",
            "verification": {"verdict_id": "fabricated"},
        },
        source_type="provider",
        source_id="untrusted-provider",
        idempotency_key="mission.outcome_finalized:v13-forged",
    )
    database.append(forged)
    payload = {
        **result.as_dict(),
        "verified_outcome_event_id": forged.event_id,
    }

    consumed = consume_research_result(database, gap_id, payload)

    assert consumed["resolved"] is False
    assert consumed["authority"] == "EXTERNAL_UNVERIFIED"
    assert WorldKnowledgeGapStore(database).get(gap_id)["resolved"] == 0


def test_matching_canonical_outcome_resolves_gap_once(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    gap_id = _gap(database, "verified")
    result = _decision_result()
    record_decision_result(database, result)
    outcome = _accepted_outcome(database, result)
    payload = {
        **result.as_dict(),
        "verified_outcome_event_id": outcome.event_id,
    }

    first = consume_research_result(database, gap_id, payload)
    replay = consume_research_result(database, gap_id, payload)

    assert first["resolved"] is True
    assert first["authority"] == "VERIFIED_EVIDENCE"
    assert replay == {"gap_id": gap_id, "already_resolved": True}
    gap = WorldKnowledgeGapStore(database).get(gap_id)
    assert gap["resolved"] == 1
    assert gap["resolved_by"] == first["entity_id"]
    assert database.event_count(event_type="world.entity_recorded") == 1
