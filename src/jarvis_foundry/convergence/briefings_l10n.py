"""EN/SQ localized briefings with R10 semantic category distinctions.

Forward-ported R10 semantics adapted onto canonical main's existing
`BriefingPlanner._payload` surface. The directive/authority model is NOT
changed: this only adds (a) a per-item semantic category and (b) reviewed
English + Albanian strings for each category, so briefings can be produced
in either language with the same durable payload.
"""

from __future__ import annotations

from typing import Any

CATEGORY_FACT = "FACT"
CATEGORY_UNCERTAINTY = "UNCERTAINTY"
CATEGORY_RECOMMENDATION = "RECOMMENDATION"
CATEGORY_ACTION = "ACTION"
CATEGORY_COST = "COST"
CATEGORY_MISSING_EVIDENCE = "MISSING_EVIDENCE"

_CATEGORY_LABELS: dict[str, dict[str, str]] = {
    CATEGORY_FACT: {"en": "Fact", "sq": "Fakt"},
    CATEGORY_UNCERTAINTY: {"en": "Uncertainty", "sq": "Pasiguri"},
    CATEGORY_RECOMMENDATION: {"en": "Recommendation", "sq": "Rekomandim"},
    CATEGORY_ACTION: {"en": "Action", "sq": "Veprim"},
    CATEGORY_COST: {"en": "Cost", "sq": "Kosto"},
    CATEGORY_MISSING_EVIDENCE: {"en": "Missing evidence", "sq": "Dëshmi e munguar"},
}

_SEMANTIC_TITLES: dict[str, dict[str, str]] = {
    CATEGORY_FACT: {
        "en": "Current verified state",
        "sq": "Gjendja e verifikuar aktuale",
    },
    CATEGORY_UNCERTAINTY: {
        "en": "What is uncertain",
        "sq": "Çfarë është e pasigurt",
    },
    CATEGORY_RECOMMENDATION: {
        "en": "Recommended next step",
        "sq": "Hapi i rekomanduar tjetër",
    },
    CATEGORY_ACTION: {
        "en": "Action being taken",
        "sq": "Veprimi që po ndërmerret",
    },
    CATEGORY_COST: {
        "en": "Cost posture",
        "sq": "Gjendja e kostove",
    },
    CATEGORY_MISSING_EVIDENCE: {
        "en": "Missing real evidence",
        "sq": "Dëshmi reale që mungon",
    },
}


def _facts(snapshot: dict[str, Any]) -> str:
    missions = snapshot.get("missions", [])
    active_missions = [
        m for m in missions if m.get("state") not in {"ACCEPTED", "BLOCKED"}
    ]
    return (
        f"{len(active_missions)} active missions, {len(missions)} total missions, "
        f"{len(snapshot.get('timeline', []))} timeline events."
    )


def _uncertainty(snapshot: dict[str, Any]) -> str:
    recovery = snapshot.get("recovery", {})
    unknowns = recovery.get("provider_or_connector_unknowns", [])
    return (
        f"{len(unknowns)} provider/connector unknowns; "
        f"cost measured_actual={snapshot.get('costs', {}).get('measured_actual_total_usd')}."
    )


def _recommendation(snapshot: dict[str, Any]) -> str:
    waiting = [
        m for m in snapshot.get("missions", []) if m.get("state") == "ACTION_SELECTION"
    ]
    if waiting:
        return "Continue the next action whose acceptance criteria are ready for independent verification."
    return "No next action has a blocked acceptance gate; continue monitoring without invented work."


def _next_actions(snapshot: dict[str, Any]) -> list[str]:
    return [
        str(m.get("next_action") or "waiting")
        for m in snapshot.get("missions", [])[:20]
    ]


def _cost(snapshot: dict[str, Any]) -> str:
    costs = snapshot.get("costs", {})
    return (
        f"actual={costs.get('measured_actual_total_usd')} "
        f"estimated={costs.get('measured_estimated_total_usd')} "
        f"unknown={costs.get('unknown_measurements')}"
    )


def _missing_evidence(snapshot: dict[str, Any]) -> str:
    recovery = snapshot.get("recovery", {})
    blocked = recovery.get("blocked_missions", [])
    return f"{len(blocked)} blocked missions without an independent verifier receipt."


def semantic_items(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Return the six R10 semantic categories with EN/SQ labels and text."""
    facts = _facts(snapshot)
    uncertainty = _uncertainty(snapshot)
    recommendation = _recommendation(snapshot)
    actions = "; ".join(_next_actions(snapshot))
    cost = _cost(snapshot)
    missing = _missing_evidence(snapshot)
    return [
        {
            "category": CATEGORY_FACT,
            "text": {"en": facts, "sq": facts},
        },
        {
            "category": CATEGORY_UNCERTAINTY,
            "text": {"en": uncertainty, "sq": uncertainty},
        },
        {
            "category": CATEGORY_RECOMMENDATION,
            "text": {
                "en": recommendation,
                "sq": "Vazhdo veprimin tjetër, kriteret e pranimit të të cilit janë gati për verifikim të pavarur.",
            },
        },
        {
            "category": CATEGORY_ACTION,
            "text": {"en": actions, "sq": actions},
        },
        {
            "category": CATEGORY_COST,
            "text": {
                "en": cost,
                "sq": f"Kosto: {cost}",
            },
        },
        {
            "category": CATEGORY_MISSING_EVIDENCE,
            "text": {
                "en": missing,
                "sq": f"Dëshmi që mungon: {missing}",
            },
        },
    ]


def render_briefing(snapshot: dict[str, Any], lang: str) -> dict[str, Any]:
    """Produce a localized semantic briefing for `lang` in {'en','sq'}."""
    if lang not in {"en", "sq"}:
        raise ValueError(f"unsupported briefing language: {lang}")
    items = semantic_items(snapshot)
    rendered = []
    for item in items:
        label = _CATEGORY_LABELS[item["category"]][lang]
        title = _SEMANTIC_TITLES[item["category"]][lang]
        text = item["text"][lang]
        rendered.append(
            {
                "category": label,
                "category_id": item["category"],
                "title": title,
                "text": text,
            }
        )
    title = {
        "en": "Supervisor briefing - JARVIS Foundry",
        "sq": "Përmbledhja e Supervizorit - JARVIS Foundry",
    }[lang]
    return {
        "lang": lang,
        "title": title,
        "categories": rendered,
        "source": "canonical command center snapshot (durable, read-only)",
    }
