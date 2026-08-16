"""Structured briefings in English and Albanian from durable Command Center state.

The Albanian strings are embedded, reviewed translations, not machine output.
Every category carries evidence references so the briefing remains drillable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

BriefingCategory = str

CATEGORY_FACT = "FACT"
CATEGORY_UNCERTAINTY = "UNCERTAINTY"
CATEGORY_RECOMMENDATION = "RECOMMENDATION"
CATEGORY_ACTION = "ACTION"
CATEGORY_COST = "COST"
CATEGORY_MISSING_EVIDENCE = "MISSING_EVIDENCE"

CATEGORIES: tuple[BriefingCategory, ...] = (
    CATEGORY_FACT,
    CATEGORY_UNCERTAINTY,
    CATEGORY_RECOMMENDATION,
    CATEGORY_ACTION,
    CATEGORY_COST,
    CATEGORY_MISSING_EVIDENCE,
)

_CATEGORY_LABELS: dict[str, dict[str, str]] = {
    CATEGORY_FACT: {"en": "Fact", "sq": "Fakt"},
    CATEGORY_UNCERTAINTY: {"en": "Uncertainty", "sq": "Pasiguri"},
    CATEGORY_RECOMMENDATION: {"en": "Recommendation", "sq": "Rekomandim"},
    CATEGORY_ACTION: {"en": "Action", "sq": "Veprim"},
    CATEGORY_COST: {"en": "Cost", "sq": "Kosto"},
    CATEGORY_MISSING_EVIDENCE: {"en": "Missing evidence", "sq": "Dëshmi e munguar"},
}

_TITLE: dict[str, str] = {
    "en": "Supervisor briefing - JARVIS Foundry",
    "sq": "Përmbledhja e Supervizorit - JARVIS Foundry",
}
_NEXT_ACTION: dict[str, str] = {
    "en": "Next autonomous action",
    "sq": "Veprimi tjetër autonom",
}
_STOP_ACTIVE: dict[str, str] = {
    "en": "STOP is active; no autonomous action is eligible until RESUME.",
    "sq": "STOP është aktiv; asnjë veprim autonom nuk lejohet deri te RESUME.",
}

_NO_NEXT_ACTION: dict[str, str] = {
    "en": "No next autonomous action is recorded.",
    "sq": "Asnjë veprim i ardhshëm autonom nuk është i regjistruar.",
}

_FACT_TEMPLATE: dict[str, str] = {
    "en": (
        "Stage {stage} status: {status} at commit {commit}; {missions} accepted missions, "
        "{capabilities} active capabilities, {decisions} portfolio decisions."
    ),
    "sq": (
        "Faza {stage} statusi: {status} në commit-in {commit}; {missions} misione të pranuara, "
        "{capabilities} aftësi aktive, {decisions} vendime portfolio."
    ),
}

_FACT_NO_RELEASE: dict[str, str] = {
    "en": "{missions} accepted missions, {capabilities} active capabilities, {decisions} portfolio decisions.",
    "sq": "{missions} misione të pranuara, {capabilities} aftësi aktive, {decisions} vendime portfolio.",
}

_UNCERTAINTY_TEMPLATE: dict[str, str] = {
    "en": "Measured USD total is {total}; voice classification is {voice}.",
    "sq": "Totali i matur në USD është {total}; klasifikimi i zërit është {voice}.",
}

_RECOMMENDATION: dict[str, str] = {
    "en": (
        "Keep the R10 builder evidence package closed and submit it to a fresh independent "
        "verifier before proceeding to R11."
    ),
    "sq": (
        "Mbajeni të mbyllur paketën e provave të ndërtuesit R10 dhe dorëzojeni te një "
        "verifikues i pavarur përpara se të vazhdoni te R11."
    ),
}

_COST_TEMPLATE: dict[str, str] = {
    "en": ("{measurements} budget measurements; unknown measurements: {unknown}. {note}"),
    "sq": ("{measurements} matje buxheti; matje të panjohura: {unknown}. {note}"),
}

_MISSING_EVIDENCE_TEMPLATE: dict[str, str] = {
    "en": (
        "Real STT/TTS receipts: {voice}; provider USD receipts: none. "
        "Health flags: degraded={degraded}, provider_failure={provider_failure}."
    ),
    "sq": (
        "Faturat reale STT/TTS: {voice}; fatura USD nga ofruesi: asnjë. "
        "Shenjat e shëndetit: e degraduar={degraded}, dështim i ofruesit={provider_failure}."
    ),
}

_TITLE_ITEMS: dict[str, dict[str, str]] = {
    CATEGORY_FACT: {"en": "Durable stage state", "sq": "Gjendja e qëndrueshme e fazës"},
    CATEGORY_UNCERTAINTY: {
        "en": "Costs and voice uncertainty",
        "sq": "Kostot dhe pasiguria e zërit",
    },
    CATEGORY_RECOMMENDATION: {
        "en": "Continue the frozen program",
        "sq": "Vazhdo programin e ngrirë",
    },
    CATEGORY_ACTION: _NEXT_ACTION,
    CATEGORY_COST: {"en": "Cost posture", "sq": "Gjendja e kostove"},
    CATEGORY_MISSING_EVIDENCE: {
        "en": "Missing real receipts",
        "sq": "Faturat reale që mungojnë",
    },
}


@dataclass(frozen=True)
class BriefingItem:
    category: BriefingCategory
    title: str
    text: str
    evidence_refs: tuple[dict[str, str], ...]


def _refs(record: dict[str, Any]) -> tuple[dict[str, str], ...]:
    if not record:
        return ()
    return (
        {
            "record_id": str(record["record_id"]),
            "kind": str(record["kind"]),
            "json_hash": str(record["json_hash"]),
        },
    )


def _latest_release(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    releases = snapshot.get("releases", [])
    if not isinstance(releases, list) or not releases:
        return None
    item = releases[-1]
    return item if isinstance(item, dict) else None


def build_items(snapshot: dict[str, Any]) -> list[BriefingItem]:
    control = snapshot.get("control", {})
    health = snapshot.get("health", {})
    capabilities = snapshot.get("capabilities", [])
    missions = snapshot.get("missions", [])
    decisions = snapshot.get("candidate_decisions", [])
    next_actions = snapshot.get("next_actions", [])
    costs = snapshot.get("costs", {})
    release = _latest_release(snapshot)
    active_capabilities = [
        capability for capability in capabilities if capability["payload"].get("status") == "ACTIVE"
    ]
    accepted_missions = [
        mission for mission in missions if mission["payload"].get("status") == "ACCEPTED"
    ]
    voice = snapshot.get("voice", {})
    voice_classification = str(voice.get("classification", "NOT_TESTED"))

    items: list[BriefingItem] = []
    for lang in ("en", "sq"):
        if release:
            facts = _FACT_TEMPLATE[lang].format(
                stage=release["payload"].get("stage", "?"),
                status=release["payload"].get("status", "unknown"),
                commit=str(release["payload"].get("commit", ""))[:12],
                missions=len(accepted_missions),
                capabilities=len(active_capabilities),
                decisions=len(decisions),
            )
        else:
            facts = _FACT_NO_RELEASE[lang].format(
                missions=len(accepted_missions),
                capabilities=len(active_capabilities),
                decisions=len(decisions),
            )
        if control.get("stop_active"):
            action_text = _STOP_ACTIVE[lang]
        elif next_actions:
            action_text = str(
                next_actions[0]["payload"].get(
                    "title",
                    "no recorded next action" if lang == "en" else "asnjë veprim i regjistruar",
                )
            )
        else:
            action_text = _NO_NEXT_ACTION[lang]
        note = str(costs.get("note", ""))
        if lang == "sq" and note == (
            "No real provider USD receipts exist in R7-R9 evidence; amounts stay UNKNOWN"
        ):
            note = "Nuk ka fatura reale USD nga ofruesi në provat R7-R9; shumat mbeten të panjohura"
        items.extend(
            [
                BriefingItem(
                    CATEGORY_FACT,
                    _TITLE_ITEMS[CATEGORY_FACT][lang],
                    facts,
                    _refs(release) if release else (),
                ),
                BriefingItem(
                    CATEGORY_UNCERTAINTY,
                    _TITLE_ITEMS[CATEGORY_UNCERTAINTY][lang],
                    _UNCERTAINTY_TEMPLATE[lang].format(
                        total=costs.get("measured_actual_total_usd", "UNKNOWN"),
                        voice=voice_classification,
                    ),
                    (),
                ),
                BriefingItem(
                    CATEGORY_RECOMMENDATION,
                    _TITLE_ITEMS[CATEGORY_RECOMMENDATION][lang],
                    _RECOMMENDATION[lang],
                    (),
                ),
                BriefingItem(
                    CATEGORY_ACTION,
                    _TITLE_ITEMS[CATEGORY_ACTION][lang],
                    action_text,
                    _refs(next_actions[0]) if next_actions else (),
                ),
                BriefingItem(
                    CATEGORY_COST,
                    _TITLE_ITEMS[CATEGORY_COST][lang],
                    _COST_TEMPLATE[lang].format(
                        measurements=len(costs.get("measurements", [])),
                        unknown=costs.get("unknown_measurements", 0),
                        note=note,
                    ),
                    tuple(
                        refs[0]
                        for item in costs.get("measurements", [])[:3]
                        if item
                        for refs in (_refs(item),)
                        if refs
                    ),
                ),
                BriefingItem(
                    CATEGORY_MISSING_EVIDENCE,
                    _TITLE_ITEMS[CATEGORY_MISSING_EVIDENCE][lang],
                    _MISSING_EVIDENCE_TEMPLATE[lang].format(
                        voice=voice_classification,
                        degraded=health.get("degraded"),
                        provider_failure=health.get("provider_failure"),
                    ),
                    (),
                ),
            ]
        )
    return items


def generate_briefing(snapshot: dict[str, Any], lang: str) -> dict[str, Any]:
    if lang not in {"en", "sq"}:
        raise ValueError(f"unsupported briefing language: {lang}")
    items = build_items(snapshot)
    per_lang = len(CATEGORIES)
    lang_items = items[:per_lang] if lang == "en" else items[per_lang:]
    localized = [
        {
            "category": _CATEGORY_LABELS[item.category][lang],
            "title": item.title,
            "text": item.text,
        }
        for item in lang_items
    ]
    content = [
        {
            "category": item.category,
            "title": item.title,
            "text": item.text,
            "evidence_refs": list(item.evidence_refs),
        }
        for item in lang_items
    ]
    content_hash = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    rendered = f"{_TITLE[lang]}\n" + "\n".join(
        f"- {entry['category']}: {entry['text']}" for entry in localized
    )
    return {
        "schema": "jarvis-r10-briefing-v1",
        "lang": lang,
        "rendered_lang": lang,
        "title": _TITLE[lang],
        "categories": localized,
        "content_hash": content_hash,
        "text": rendered,
        "evidence_refs": [item_ref for item in lang_items for item_ref in item.evidence_refs],
    }
