"""V13 owner-context durability, boundedness, and authority proof."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json
from jarvis_foundry.executive.owner_context import load_owner_context
from jarvis_foundry.owner_context import (
    ContextProvenance,
    ContextScope,
    OwnerContextQuery,
    OwnerContextStore,
    StandingOrderEvaluator,
    compile_owner_context,
    compile_standing_order,
    project_owner_context,
    project_standing_orders,
    resolve_owner_reference,
)
from jarvis_foundry.owner_controls import (
    OwnerCommandError,
    OwnerControlService,
    sign_command,
)


NOW = "2026-09-03T12:00:00Z"
SECRET = b"v13-owner-context-secret-32-bytes"


def _provenance(
    text: str,
    source_ref: str,
    *,
    captured_at: str,
    authority: str = "REPOSITORY_AUTHORITY",
) -> ContextProvenance:
    return ContextProvenance.from_content(
        source_type="repository",
        source_id="v13-owner-context-fixture",
        source_ref=source_ref,
        content=text,
        captured_at=captured_at,
        evidence_refs=(f"evidence:{source_ref}",),
        authority=authority,
    )


def _record(
    store: OwnerContextStore,
    context_id: str,
    kind: str,
    text: str,
    *,
    project: str | None = None,
    at: str = "2026-09-02T08:00:00Z",
    fresh_until: str | None = None,
    state: str = "ACTIVE",
    priority: float = 0.0,
    attributes: dict | None = None,
    contradicts: tuple[str, ...] = (),
) -> str:
    store.record(
        kind,
        text,
        context_id=context_id,
        scope=(
            ContextScope(project_ids=(project,))
            if project else ContextScope(global_scope=True)
        ),
        provenance=_provenance(
            text, f"fixture/{context_id}", captured_at=at,
        ),
        observed_at=at,
        effective_at=at,
        fresh_until=fresh_until,
        state=state,
        priority=priority,
        attributes=attributes,
        contradicts=contradicts,
    )
    return context_id


def _standing_order(
    service: OwnerControlService,
    text: str,
    *,
    directive_id: str,
    nonce: str,
    project: str,
) -> str:
    draft = compile_standing_order(
        text, scope=ContextScope(project_ids=(project,)),
    )
    metadata = draft.as_metadata()
    service.apply(
        "STANDING_ORDER",
        target=project,
        exact_text=text,
        nonce=nonce,
        signature=sign_command(
            SECRET,
            "STANDING_ORDER",
            project,
            text,
            nonce,
            metadata=metadata,
        ),
        directive_id=directive_id,
        metadata=metadata,
    )
    return f"directive:{directive_id}"


def _fixture_database(tmp_path: Path) -> tuple[FoundryDatabase, dict[str, str]]:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    store = OwnerContextStore(database)
    ids = {
        "goal": _record(
            store, "goal:jarvis-personal-operator", "GOAL",
            "JARVIS should understand the owner across projects and time",
        ),
        "program": _record(
            store, "program:jarvis-1.0", "PROGRAM",
            "JARVIS 1.0 personal operator program",
            project="jarvis",
        ),
        "jarvis_project": _record(
            store, "project:jarvis", "PROJECT",
            "JARVIS Foundry is the active autonomous executive project",
            project="jarvis",
        ),
        "leadgen_project": _record(
            store, "project:leadgen", "PROJECT",
            "LeadGen is an active contact-quality project",
            project="leadgen",
        ),
        "jarvis_objective": _record(
            store, "objective:jarvis-v13", "ACTIVE_OBJECTIVE",
            "Build durable owner context for JARVIS without a second Executive",
            project="jarvis", priority=90,
        ),
        "leadgen_objective": _record(
            store, "objective:leadgen-quality", "ACTIVE_OBJECTIVE",
            "Improve LeadGen contact point evidence quality",
            project="leadgen", priority=70,
        ),
        "commitment": _record(
            store, "commitment:jarvis-review", "COMMITMENT",
            "Review the JARVIS owner-context candidate on September 4",
            project="jarvis",
            attributes={
                "fact_key": "commitment:jarvis-review",
                "value": "review candidate",
                "due_at": "2026-09-04T09:00:00Z",
            },
        ),
        "waiting": _record(
            store, "waiting:provider-choice", "WAITING_ON_OWNER",
            "Waiting on owner to choose the final provider account",
            project="jarvis",
        ),
        "unresolved": _record(
            store, "unresolved:provider-http-500", "UNRESOLVED_ITEM",
            "The configured provider still returns HTTP 500",
            project="jarvis",
        ),
        "rejected": _record(
            store, "rejected:second-memory-platform", "REJECTED_APPROACH",
            "Do not create a second memory platform or vector database",
            project="jarvis", state="RESOLVED",
        ),
        "outcome": _record(
            store, "outcome:v11-provider-blocked", "HISTORICAL_OUTCOME",
            "V11 exit proof remained blocked by provider policy with zero effects",
            project="jarvis", state="RESOLVED",
            attributes={
                "fact_key": "outcome:v11-exit",
                "value": "BLOCKED_BY_PROVIDER",
            },
        ),
        "old_priority": _record(
            store, "priority:jarvis-old", "PRIORITY",
            "JARVIS UI polish is the immediate priority",
            project="jarvis",
            at="2026-08-20T08:00:00Z",
            fresh_until="2026-09-01T00:00:00Z",
            priority=40,
            attributes={
                "fact_key": "project:jarvis:priority",
                "value": "UI_POLISH",
            },
        ),
        "new_priority": _record(
            store, "priority:jarvis-current", "PRIORITY",
            "JARVIS owner-context durability is the immediate priority",
            project="jarvis",
            at="2026-09-03T07:00:00Z",
            fresh_until="2026-09-10T00:00:00Z",
            priority=95,
            attributes={
                "fact_key": "project:jarvis:priority",
                "value": "OWNER_CONTEXT",
            },
        ),
        "quiet_preference": _record(
            store, "preference:quiet", "PREFERENCE",
            "Prefer quiet recovery without routine interruption",
            project="jarvis",
            attributes={
                "preference_key": "briefing_detail",
                "preference_value": "QUIET",
            },
            contradicts=("preference:detailed",),
        ),
        "detailed_preference": _record(
            store, "preference:detailed", "PREFERENCE",
            "Prefer detailed progress reports during critical recovery",
            project="jarvis",
            at="2026-09-03T06:00:00Z",
            attributes={
                "preference_key": "briefing_detail",
                "preference_value": "DETAILED",
            },
            contradicts=("preference:quiet",),
        ),
        "decision": _record(
            store, "decision:isolate-v13", "DECISION",
            "Build V13 owner context only in an isolated source worktree",
            project="jarvis",
        ),
        "constraint": _record(
            store, "constraint:no-live-mutation", "CONSTRAINT",
            "Do not merge, push, deploy, or mutate the live runtime",
            project="jarvis",
            attributes={
                "fact_key": "constraint:live-mutation",
                "value": "DENY",
            },
        ),
        "permission": _record(
            store, "permission:isolated-source", "PERMISSION",
            "Source-only changes are permitted in the isolated V13 worktree",
            project="jarvis",
            attributes={
                "fact_key": "permission:isolated-source",
                "value": "ALLOW",
            },
        ),
    }
    service = OwnerControlService(database, secret=SECRET)
    ids["investigate_order"] = _standing_order(
        service,
        "If a project becomes blocked, investigate it.",
        directive_id="standing-investigate",
        nonce="standing-investigate-nonce",
        project="jarvis",
    )
    ids["clean_order"] = _standing_order(
        service,
        "Keep my projects clean.",
        directive_id="standing-clean",
        nonce="standing-clean-nonce",
        project="jarvis",
    )
    service.apply(
        "REVOKE",
        target="standing-clean",
        exact_text="Owner revoked the project-cleanliness standing order",
        nonce="standing-clean-revoke",
        signature=sign_command(
            SECRET,
            "REVOKE",
            "standing-clean",
            "Owner revoked the project-cleanliness standing order",
            "standing-clean-revoke",
        ),
    )
    return database, ids


def test_projection_reconstructs_durable_truth_after_restart_and_rebuild(
    tmp_path: Path,
) -> None:
    database, ids = _fixture_database(tmp_path)
    before = project_owner_context(database, now=NOW)
    reopened = project_owner_context(
        FoundryDatabase(database.path), now=NOW,
    )
    assert canonical_json(before.as_dict()) == canonical_json(reopened.as_dict())
    assert before.get(ids["jarvis_project"]).current(NOW)
    assert before.get(ids["outcome"]).state == "RESOLVED"
    for record in before.records:
        value = record.as_dict(now=NOW)
        assert value["observed_at"] and value["recorded_at"]
        assert value["provenance"]["source_ref"]
        assert len(value["provenance"]["content_hash"]) == 64

    database.rebuild_projections()
    rebuilt = project_owner_context(database, now=NOW)
    assert canonical_json(before.as_dict()) == canonical_json(rebuilt.as_dict())


def test_compiler_is_relevant_bounded_and_preserves_conflict_and_freshness(
    tmp_path: Path,
) -> None:
    database, ids = _fixture_database(tmp_path)
    package = compile_owner_context(
        database,
        OwnerContextQuery(
            project_id="jarvis",
            current_situation="The JARVIS project is blocked by provider HTTP 500",
            requested_task="Continue owner-context durability work",
            now=NOW,
        ),
        max_items=20,
        max_chars=30000,
    )
    serialized = canonical_json(package)
    assert "LeadGen" not in serialized
    assert ids["jarvis_objective"] in {
        item["context_id"] for item in package["items"]
    }
    assert package["status"] == "CONFLICTING"
    assert {
        ids["quiet_preference"], ids["detailed_preference"],
    } == set(package["contradictions"][0]["context_ids"])

    projection = project_owner_context(database, now=NOW)
    assert projection.get(ids["old_priority"]).state == "STALE"
    assert not projection.get(ids["old_priority"]).current(NOW)
    assert projection.get(ids["new_priority"]).current(NOW)
    assert ids["old_priority"] not in {
        item["context_id"] for item in package["items"]
    }

    bounded = compile_owner_context(
        database,
        {
            "project_id": "jarvis",
            "current_situation": "blocked provider owner context",
            "now": NOW,
        },
        max_items=4,
        max_chars=5000,
        per_kind_limit=2,
    )
    assert bounded["bounds"]["used_items"] <= 4
    assert bounded["bounds"]["used_chars"] <= 5000
    assert bounded["bounds"]["used_chars"] == len(canonical_json(bounded))
    assert bounded["bounds"]["truncated"] is True


def test_resolved_preference_stays_in_history_but_ends_live_conflict(
    tmp_path: Path,
) -> None:
    database, ids = _fixture_database(tmp_path)
    store = OwnerContextStore(database)
    store.resolve(
        ids["detailed_preference"],
        reason="Owner selected quiet routine recovery reporting",
        changed_at="2026-09-03T11:00:00Z",
    )
    projection = project_owner_context(database, now=NOW)
    assert projection.contradictions == ()
    assert projection.get(ids["detailed_preference"]).state == "RESOLVED"
    assert projection.get(ids["quiet_preference"]).current(NOW)


def test_standing_orders_are_authenticated_revocable_and_never_execute(
    tmp_path: Path,
) -> None:
    database, ids = _fixture_database(tmp_path)
    active = project_standing_orders(
        database, now=NOW, include_history=False,
    )
    assert [order["standing_order_id"] for order in active] == [
        ids["investigate_order"],
    ]
    history = {
        order["standing_order_id"]: order
        for order in project_standing_orders(database, now=NOW)
    }
    assert history[ids["clean_order"]]["state"] == "REVOKED"

    evaluator = StandingOrderEvaluator(database)
    match = evaluator.evaluate({
        "project_id": "jarvis",
        "current_situation": "The project became blocked",
        "requested_task": "Investigate the blocked project",
        "situation_state": "BLOCKED",
        "now": NOW,
    })
    assert match["status"] == "MATCHED"
    assert match["execution"] == {"allowed": False, "performed": False}
    assert match["matched"][0]["standing_order_id"] == ids["investigate_order"]

    stop_path = tmp_path / "EMERGENCY_STOP"
    stop_path.write_text("owner stop\n", encoding="utf-8")
    stopped = StandingOrderEvaluator(
        database, stop_path=stop_path,
    ).evaluate({
        "project_id": "jarvis",
        "current_situation": "The project became blocked",
        "requested_task": "Investigate the blocked project",
        "situation_state": "BLOCKED",
        "now": NOW,
    })
    assert stopped["status"] == "BLOCKED_BY_OWNER_CONTROL"
    assert stopped["owner_control_reason"] == "emergency stop marker is present"
    assert stopped["matched"] == []

    service = OwnerControlService(database, secret=SECRET)
    text = "veto investigation while owner reviews provider policy"
    service.apply(
        "VETO_ACTION",
        target="jarvis",
        exact_text=text,
        nonce="veto-investigate",
        signature=sign_command(
            SECRET, "VETO_ACTION", "jarvis", text, "veto-investigate",
        ),
        directive_id="veto-investigate",
    )
    blocked = evaluator.evaluate({
        "project_id": "jarvis",
        "current_situation": "The project became blocked",
        "requested_task": "Investigate the blocked project",
        "situation_state": "BLOCKED",
        "now": NOW,
    })
    assert blocked["status"] == "BLOCKED_BY_OWNER_CONTROL"
    assert "VETO_ACTION" in blocked["owner_control_reason"]
    assert blocked["matched"] == []


def test_direct_owner_control_defers_advisory_standing_order(
    tmp_path: Path,
) -> None:
    database, _ = _fixture_database(tmp_path)
    service = OwnerControlService(database, secret=SECRET)
    text = "Redirect blocked-project investigation to owner review"
    service.apply(
        "REDIRECT",
        target="jarvis",
        exact_text=text,
        nonce="redirect-investigation",
        signature=sign_command(
            SECRET, "REDIRECT", "jarvis", text, "redirect-investigation",
        ),
        directive_id="redirect-investigation",
    )
    result = StandingOrderEvaluator(database).evaluate({
        "project_id": "jarvis",
        "current_situation": "The project became blocked",
        "requested_task": "Investigate the blocked project",
        "situation_state": "BLOCKED",
        "now": NOW,
    })
    assert result["status"] == "DEFERRED_TO_OWNER_CONTROL"
    assert "REDIRECT" in result["owner_control_reason"]
    assert result["matched"] == []
    assert result["execution"] == {"allowed": False, "performed": False}


def test_unknown_standing_order_cannot_be_admitted(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    draft = compile_standing_order("Remember this somehow.")
    assert draft.status == "UNKNOWN"
    assert draft.as_dict()["state"] == "UNKNOWN"
    with pytest.raises(ValueError, match="cannot be admitted"):
        draft.as_metadata()

    malformed = {"standing_order": {
        "contract": "jarvis.standing-order.v1",
        "owner_text": "Remember this somehow.",
        "evaluation": "ADVISORY_ONLY",
        "parser": {"status": "UNKNOWN"},
    }}
    service = OwnerControlService(database, secret=SECRET)
    with pytest.raises(OwnerCommandError, match="STRUCTURED"):
        service.apply(
            "STANDING_ORDER",
            target="GLOBAL",
            exact_text="Remember this somehow.",
            nonce="unknown-standing-order",
            signature=sign_command(
                SECRET,
                "STANDING_ORDER",
                "GLOBAL",
                "Remember this somehow.",
                "unknown-standing-order",
                metadata=malformed,
            ),
            metadata=malformed,
        )
    assert database.event_count(event_type="owner_directive.recorded") == 0
    with pytest.raises(ValueError, match="OwnerControlService"):
        OwnerContextStore(database).record(
            "STANDING_ORDER",
            "If a project becomes blocked, investigate it.",
            context_id="standing:forged",
            provenance=_provenance(
                "If a project becomes blocked, investigate it.",
                "fixture/forged-standing-order",
                captured_at=NOW,
            ),
        )


@pytest.mark.parametrize("text", [
    "Never let a broken build sit overnight.",
    "If a project becomes blocked, investigate it.",
    "Keep my projects clean.",
    "Don't bother me with small recoverable errors.",
    "Before purchasing something, show me the final choice.",
])
def test_canonical_standing_order_examples_compile_to_structured_drafts(
    text: str,
) -> None:
    draft = compile_standing_order(text)
    assert draft.status == "STRUCTURED"
    assert draft.trigger
    assert draft.intended_behavior
    assert draft.as_dict()["evaluation"] == "ADVISORY_ONLY"


def test_malformed_context_event_remains_unknown(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "owner_context.item_recorded",
        {
            "context_id": "not a valid id",
            "kind": "PREFERENCE",
            "text": "malformed context without provenance",
        },
        source_type="import",
        source_id="untrusted-fixture",
        idempotency_key="owner_context.item_recorded:malformed",
    ))
    projection = project_owner_context(database, now=NOW)
    assert len(projection.records) == 1
    assert projection.records[0].state == "UNKNOWN"
    assert projection.records[0].current(NOW) is False
    assert projection.records[0].provenance.authority == "UNKNOWN"


def test_reference_resolution_resolves_strong_context_and_reports_ambiguity(
    tmp_path: Path,
) -> None:
    database, ids = _fixture_database(tmp_path)
    resolved = resolve_owner_reference(
        database,
        "continue this",
        context=OwnerContextQuery(
            project_id="jarvis",
            recent_context_ids=(ids["jarvis_objective"],),
            now=NOW,
        ),
    )
    assert resolved["status"] == "RESOLVED"
    assert resolved["selected_context_id"] == ids["jarvis_objective"]
    assert resolved["action_taken"] is False

    ambiguous_db = FoundryDatabase(tmp_path / "ambiguous.db")
    ambiguous_db.initialize()
    store = OwnerContextStore(ambiguous_db)
    for suffix in ("a", "b"):
        _record(
            store,
            f"unresolved:yesterday-{suffix}",
            "UNRESOLVED_ITEM",
            f"Provider recovery item {suffix}",
            at="2026-09-02T10:00:00Z",
        )
    ambiguous = resolve_owner_reference(
        ambiguous_db,
        "the thing from yesterday",
        now=NOW,
    )
    assert ambiguous["status"] == "AMBIGUOUS"
    assert ambiguous["selected_context_id"] is None
    assert len(ambiguous["candidates"]) == 2


def test_mapping_reference_context_preserves_recent_rank_order(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "ranked.db")
    database.initialize()
    store = OwnerContextStore(database)
    first = _record(
        store,
        "objective:z-first",
        "ACTIVE_OBJECTIVE",
        "First owner-selected objective",
        project="jarvis",
    )
    second = _record(
        store,
        "objective:a-second",
        "ACTIVE_OBJECTIVE",
        "Second objective in recent history",
        project="jarvis",
    )
    result = resolve_owner_reference(
        database,
        "continue this",
        context={
            "project_id": "jarvis",
            "recent_context_ids": [first, second],
            "now": NOW,
        },
    )
    assert result["status"] == "RESOLVED"
    assert result["selected_context_id"] == first
    assert "recent_context_rank:0" in result["candidates"][0]["evidence"]


def test_v11_scoring_bridge_reads_durable_context_without_a_second_store(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    store = OwnerContextStore(database)
    _record(
        store, "goal:durable-v13", "GOAL",
        "Keep JARVIS owner context durable and bounded",
    )
    _record(
        store, "project:durable-v13", "PROJECT",
        "JARVIS Foundry active project", project="jarvis",
    )
    context = load_owner_context(database)
    assert context["goals"][0]["text"].startswith("Keep JARVIS")
    assert context["projects"][0]["source_ref"] == "fixture/project:durable-v13"
    assert context["authority"] == "ADVISORY_OWNER_CONTEXT_V1_WITH_LEGACY_TIER_C"
