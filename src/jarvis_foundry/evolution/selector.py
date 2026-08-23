"""V6 lane JOB: continuous improvement selector.

Answers "what should JARVIS improve next?" from EVIDENCE ONLY:

inputs per candidate weakness (all produced by the detector):
  impact, confidence, cost, risk class, reversibility, evidence
  quality (sample size vs minimum), owner constraints/vetoes,
  current mission load;

outputs:
  - the selected improvement (deterministic score, ties broken by
    dedupe identity for stability);
  - every rejected alternative WITH its reason (no silent drops);
  - WAITING when no candidate justifies action — never performative
    self-improvement.

Hard vetoes (checked first, regardless of score):
- an owner vetoed component is unselectable;
- R3 components require explicit supervisor override flag;
- mission load at/over capacity forces WAITING regardless of scores.
"""
from __future__ import annotations

from typing import Any

RISK_WEIGHT = {"R0": 0.0, "R1": 0.05, "R2": 0.1, "R3": 0.25}
REVERSIBILITY_BONUS = {"high": 0.1, "medium": 0.0, "low": -0.15}


class SelectionError(ValueError):
    """Selector refused (bad inputs)."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SelectionError(message)


def select_next_improvement(candidates: list[dict[str, Any]], *,
                            vetoed_components: list[str] | None = None,
                            allow_r3: bool = False,
                            mission_load: int = 0,
                            mission_capacity: int = 2,
                            ) -> dict[str, Any]:
    """Deterministic selection over evidence-backed candidates.

    Score = impact * confidence * evidence_quality
            - cost_penalty + risk_weight + reversibility_bonus.
    All inputs come from detector observations; nothing is invented.
    """
    vetoes = {v.lower() for v in (vetoed_components or [])}
    reasons_by_candidate: list[tuple[dict[str, Any], str]] = []
    eligible: list[dict[str, Any]] = []

    if mission_load >= mission_capacity:
        return {"selected": None, "waiting": True,
                "reason": f"mission load {mission_load} at/over "
                          f"capacity {mission_capacity} — no new "
                          "improvement missions justified",
                "rejected": [{"candidate": c,
                              "reason": "mission capacity saturated"}
                             for c in candidates]}

    for candidate in candidates:
        component = str(candidate.get("affected_component", "")).lower()
        if component and any(v in component or component in v
                             for v in vetoes if v):
            reasons_by_candidate.append(
                (candidate, "owner veto on this component"))
            continue
        risk = str(candidate.get("risk_class", "R3"))
        if risk == "R3" and not allow_r3:
            reasons_by_candidate.append(
                (candidate, "R3 requires explicit supervisor override"))
            continue
        impact = float(candidate.get("impact", 0.0))
        confidence = float(candidate.get("confidence", 0.5))
        sample = int(candidate.get("sample_size", 0))
        minimum = max(int(candidate.get("min_sample", 3)), 1)
        evidence_quality = min(sample / minimum, 1.5)
        cost = float(candidate.get("cost_estimate_usd", 0.0))
        reversibility = str(candidate.get("reversibility",
                                          "medium")).lower()
        score = (impact * confidence * evidence_quality) \
            - (cost * 10.0) \
            + RISK_WEIGHT.get(risk, 0.25) \
            + REVERSIBILITY_BONUS.get(reversibility, 0.0)
        eligible.append({**candidate, "score": round(score, 4),
                         "evidence_quality": round(evidence_quality, 3)})

    # low expected value -> WAITING rather than performative work
    worthwhile = [c for c in eligible if c["score"] > 0]
    for candidate in eligible:
        if candidate["score"] <= 0:
            reasons_by_candidate.append(
                (candidate, f"expected value {candidate['score']} "
                            "<= 0 — action creates less value than "
                            "waiting"))

    worthwhile.sort(key=lambda c: (-c["score"],
                                   str(c.get("dedupe_identity", ""))))
    selected = worthwhile[0] if worthwhile else None
    rejected = [{"candidate": candidate, "reason": reason}
                for candidate, reason in reasons_by_candidate]
    # every NON-SELECTED alternative also carries a reason (outranked)
    if selected is not None:
        for candidate in worthwhile[1:]:
            rejected.append({
                "candidate": candidate,
                "reason": f"outranked: score {candidate['score']} < "
                          f"{selected['score']}"})
    return {
        "selected": selected,
        "rejected": rejected,
        "waiting": selected is None,
        "reason": None if selected else
        ("no candidate justifies action now"),
    }
