"""Classify standing owner goals vs executable task recipes.

A high-level mandate such as "Keep improving my active projects while I'm
away" must drive discovery and scoring. It must not become the mission
objective itself, and it must not require token overlap with a concrete
TODO before project-improvement work can score as aligned.
"""
from __future__ import annotations

import re
from typing import Any

from ..knowledge import canonicalize_statement


DISCOVERY_PHRASES = (
    "keep improving",
    "while i'm away",
    "while i am away",
    "while away",
    "find what is worth",
    "worth improving",
    "select useful work",
    "discover useful work",
    "best next step",
    "registered projects",
    "active projects",
    "among my",
    "take the best next",
    "next useful",
    "worthwhile work",
)

PROJECT_IMPROVEMENT_TYPES = frozenset({
    "tracked_work",
    "project_failure_indicator",
    "project_drift",
    "project_staleness",
    "generated_artifact_drift",
    "maintenance",
    "BUG",
    "CI_FAILURE",
    "MISSING_TEST",
    "PERFORMANCE",
    "DEPENDENCY_UPDATE",
    "DOC_DRIFT",
    "REFACTOR_WITH_MEASURABLE_VALUE",
    "MISSING_FEATURE",
    "UX_FRICTION",
    "github_issue",
    "github_workflow_failure",
})

_WEAK_WORK_TERMS = (
    "rename", "typo", "cosmetic", "whitespace", "comment only",
    "style only", "reformat", "lint nit",
)
_VALUE_WORK_TERMS = (
    "reliability", "regression", "fail", "test", "outage", "latency",
    "security", "incident", "crash", "data loss", "blocker", "fix",
)


def is_discovery_mandate(text: str) -> bool:
    """Return True when the goal is standing discovery intent, not a recipe."""
    lowered = " ".join(str(text or "").lower().split())
    if not lowered:
        return False
    return any(phrase in lowered for phrase in DISCOVERY_PHRASES)


def looks_like_project_improvement(
    objective: str,
    *,
    candidate_type: str = "",
) -> bool:
    kind = str(candidate_type or "").strip()
    if kind in PROJECT_IMPROVEMENT_TYPES:
        return True
    canonical = canonicalize_statement(objective)
    markers = (
        "todo", "fixme", "failing", "regression", "maintenance",
        "investigate", "reliability", "tracked",
    )
    return any(token in canonical.split() for token in markers)


def is_weak_performative(objective: str) -> bool:
    canonical = canonicalize_statement(objective)
    if any(term in canonical for term in _VALUE_WORK_TERMS):
        return False
    return any(term in canonical for term in _WEAK_WORK_TERMS)


def standing_goal_alignment(
    goal_text: str,
    objective: str,
    *,
    candidate_type: str = "",
    target_project: str = "",
) -> float | None:
    """Return a floor alignment for discovery mandates, else None.

    None means the caller should keep lexical alignment only.
    """
    if not is_discovery_mandate(goal_text):
        return None
    if is_weak_performative(objective):
        return 0.08
    if looks_like_project_improvement(objective, candidate_type=candidate_type):
        bonus = 0.08 if str(target_project or "").strip() else 0.0
        return min(0.85, 0.62 + bonus)
    return 0.12


_NEW_PROJECT_HINTS = re.compile(
    r"\b(new (app|project|product|tool)|greenfield|from scratch|build a)\b",
    re.IGNORECASE,
)


def looks_like_new_project_opportunity(objective: str, *, candidate_type: str = "") -> bool:
    if str(candidate_type or "") in {"NEW_PROJECT_OPPORTUNITY", "NEW_PROJECT"}:
        return True
    return bool(_NEW_PROJECT_HINTS.search(str(objective or "")))


def intent_kind(text: str) -> str:
    return "DISCOVERY_MANDATE" if is_discovery_mandate(text) else "EXECUTABLE_GOAL"


def standing_intent_payload(text: str, *, directive_id: str = "") -> dict[str, Any]:
    return {
        "directive_id": directive_id,
        "objective": str(text or ""),
        "kind": intent_kind(text),
        "drives_discovery": is_discovery_mandate(text),
        "executable_recipe": not is_discovery_mandate(text),
    }


# ---------------------------------------------------------------------------
# Standing strategic goal vs bounded one-shot child.
#
# A standing strategic owner goal (e.g. "Turn the demo product into a commercially
# credible workforce-management product ... learn from the outcome, and
# continue to the next highest-value improvement. ... Ask me only when there
# is a genuine owner-only decision or blocker.") authorises an unbounded
# sequence of bounded child objectives.  A one-shot request (e.g. "Report
# supervisor heartbeat age. success: one verified line ...") is satisfied by
# its single child outcome.
#
# Classification is derived from durable intent structure -- deliverable
# scope, continuation semantics, completion criteria, mandate type -- not
# from a single keyword.  Explicit standing-goal metadata, when present,
# wins; otherwise the text is parsed for structured continuation semantics.
# ---------------------------------------------------------------------------

_SUCCESS_CLAUSE_RE = re.compile(
    r"\bsuccess\s*(?:=|:)\s*(.+?)(?=\s+(?:priority|urgency|deadline|constraints?|depends?[_ -]?on|failure)\s*(?:=|:)|$)",
    re.IGNORECASE | re.DOTALL,
)

_STRATEGIC_STATE_PHRASES = (
    "commercially credible",
    "workforce-management",
    "workforce management",
    "workforce product",
    "credible product",
    "confidently demonstrate",
    "operations team",
    "toward strategic",
    "strategic state",
)

_IMPROVE_MANAGE_VERBS = (
    "improve",
    "improving",
    "manage",
    "managing",
    "maintain",
    "maintaining",
    "turn ",
    "credible",
)

_ITERATIVE_CONTINUATION_PHRASES = (
    "continue to the next",
    "continue to next",
    "next highest-value improvement",
    "next highest value improvement",
    "learn from the outcome",
    "continue to the next highest",
)

_AUTONOMOUS_SELECTION_PHRASES = (
    "choose the work yourself",
    "decide what the highest-value work is",
    "decide what the highest value work is",
    "inspect the current product and decide",
    "choose the work",
    "decide the highest-value work",
)

_OWNER_ONLY_GATE_PHRASES = (
    "ask me only when",
    "genuine owner-only decision",
    "genuine owner decision",
    "genuine owner-only blocker",
    "genuine owner only",
    "only when there is a genuine",
)


def _normalised(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def extract_continuation_semantics(text: str) -> dict[str, Any]:
    """Parse durable continuation semantics from owner directive text.

    Returns a structured signal map (not a boolean): deliverable scope,
    strategic-target presence, iterative-continuation presence,
    autonomous-selection presence, and owner-only-gate presence.  Callers
    combine these with explicit metadata and completion criteria; no single
    phrase decides standing.
    """
    lowered = _normalised(text)
    success_match = _SUCCESS_CLAUSE_RE.search(str(text or ""))
    success_clause = ""
    if success_match:
        success_clause = re.sub(r"\s+", " ", success_match.group(1)).strip(" ,;:")
    has_bounded_success = bool(success_clause) and len(success_clause) >= 8
    bounded_single_deliverable = bool(
        has_bounded_success
        and re.search(
            r"\b(one|single|1)\b.{0,40}\b(line|report|value|number|file|receipt)\b",
            success_clause,
            re.IGNORECASE,
        )
    )
    return {
        "has_strategic_target": any(p in lowered for p in _STRATEGIC_STATE_PHRASES),
        "has_improve_manage_verb": any(v in lowered for v in _IMPROVE_MANAGE_VERBS),
        "has_iterative_continuation": any(p in lowered for p in _ITERATIVE_CONTINUATION_PHRASES),
        "has_autonomous_selection": any(p in lowered for p in _AUTONOMOUS_SELECTION_PHRASES),
        "has_owner_only_gate": any(p in lowered for p in _OWNER_ONLY_GATE_PHRASES),
        "has_bounded_success_clause": has_bounded_success,
        "has_bounded_single_deliverable": bounded_single_deliverable,
        "success_clause": success_clause,
    }


def is_standing_strategic_text(text: str) -> bool:
    """Return True when text carries standing strategic continuation semantics.

    Requires structured continuation (iterative continuation plus autonomous
    selection plus owner-only gate, or strategic target plus iterative
    continuation) and no bounded single deliverable that would terminate the
    goal.  This keeps one-shot reports ("Report ... success: one verified
    line ...") one-shot while recognising strategic improvement mandates
    ("improve/manage/maintain product toward strategic state ... continue
    to the next ... learn ... ask only on genuine blocker").
    """
    signals = extract_continuation_semantics(text)
    if signals["has_bounded_single_deliverable"]:
        return False
    iterative = signals["has_iterative_continuation"]
    autonomous = signals["has_autonomous_selection"]
    gate = signals["has_owner_only_gate"]
    strategic = signals["has_strategic_target"] or signals["has_improve_manage_verb"]
    if iterative and autonomous and gate:
        return True
    if strategic and iterative:
        return True
    return False


def standing_directive_kind(
    text: str,
    *,
    metadata: Any | None = None,
) -> str:
    """Classify a directive as STANDING vs ONE_SHOT from durable structure.

    Explicit standing-goal metadata wins when present:
      metadata {"standing_goal": True} or {"continuation": "standing"} or
      {"mandate_type": "standing"} forces STANDING;
      {"standing_goal": False} forces ONE_SHOT.
    Otherwise the text continuation semantics decide.
    """
    try:
        meta = dict(metadata or {})
    except Exception:
        meta = {}
    for key in ("standing_goal", "standing", "is_standing"):
        if key in meta:
            try:
                return "STANDING" if bool(meta[key]) else "ONE_SHOT"
            except Exception:
                pass
    continuation = str(meta.get("continuation") or "").strip().lower()
    if continuation in {"standing", "standing_goal", "continuous"}:
        return "STANDING"
    if continuation in {"one_shot", "oneshot", "single", "bounded"}:
        return "ONE_SHOT"
    if str(meta.get("mandate_type") or "").strip().lower() in {"standing", "standing_goal"}:
        return "STANDING"
    return "STANDING" if is_standing_strategic_text(text) else "ONE_SHOT"


def is_standing_directive_text(text: str, *, metadata: Any | None = None) -> bool:
    return standing_directive_kind(text, metadata=metadata) == "STANDING"
