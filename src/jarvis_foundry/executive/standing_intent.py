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
