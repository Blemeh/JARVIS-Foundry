"""V6 lane: continuous improvement selector.

Covers: deterministic selection with reasons for every rejected
alternative, owner-veto unselectability, R3 override requirement,
mission-capacity WAITING, negative-expected-value WAITING, and tie
stability.
"""
from __future__ import annotations

from jarvis_foundry.evolution.selector import select_next_improvement


def _candidate(**overrides) -> dict:
    base = {"rule": "agent_repair_rate", "impact": 5.0,
            "confidence": 0.8, "sample_size": 6, "min_sample": 3,
            "risk_class": "R1", "reversibility": "high",
            "cost_estimate_usd": 0.0,
            "affected_component": "mission/",
            "dedupe_identity": "weakness-abc"}
    base.update(overrides)
    return base


def test_selects_highest_expected_value() -> None:
    weak = _candidate(impact=1.0, dedupe_identity="w1")
    strong = _candidate(impact=9.0, dedupe_identity="w2",
                        affected_component="execution/")
    result = select_next_improvement([weak, strong])
    assert result["selected"] is not None
    assert result["selected"]["dedupe_identity"] == "w2"
    assert result["waiting"] is False
    rejected_ids = {r["candidate"]["dedupe_identity"]
                    for r in result["rejected"]}
    assert "w1" in rejected_ids and \
        all(r["reason"] for r in result["rejected"]), \
        "every rejected alternative must carry a reason"


def test_owner_veto_is_unselectable() -> None:
    vetoed = _candidate(dedupe_identity="vetoed")
    other = _candidate(impact=2.0, dedupe_identity="other",
                       affected_component="execution/")
    result = select_next_improvement(
        [vetoed, other], vetoed_components=["mission/"])
    assert result["selected"]["dedupe_identity"] == "other"
    assert any("veto" in r["reason"]
               for r in result["rejected"]
               if r["candidate"]["dedupe_identity"] == "vetoed")


def test_r3_requires_explicit_override() -> None:
    risky = _candidate(risk_class="R3", impact=10.0)
    result = select_next_improvement([risky])
    assert result["waiting"] is True
    assert any("override" in r["reason"]
               for r in result["rejected"])
    allowed = select_next_improvement([risky], allow_r3=True)
    assert allowed["selected"] is not None


def test_mission_capacity_forces_waiting() -> None:
    strong = [_candidate(impact=100.0)]
    result = select_next_improvement(strong, mission_load=2,
                                     mission_capacity=2)
    assert result["waiting"] is True
    assert result["selected"] is None
    assert "capacity" in result["reason"]


def test_negative_expected_value_waits() -> None:
    expensive = _candidate(cost_estimate_usd=50.0)  # -500 score term
    result = select_next_improvement([expensive])
    assert result["waiting"] is True
    assert any("value" in r["reason"] for r in result["rejected"])


def test_low_evidence_quality_downgrades_score() -> None:
    thin = _candidate(sample_size=1, impact=5.0)
    solid = _candidate(sample_size=30, impact=5.0,
                       dedupe_identity="solid")
    result = select_next_improvement([thin, solid])
    assert result["selected"]["dedupe_identity"] == "solid"


def test_deterministic_tie_break() -> None:
    one = _candidate(dedupe_identity="aaa")
    two = _candidate(dedupe_identity="bbb")
    forward = select_next_improvement([one, two])
    backward = select_next_improvement([two, one])
    assert forward["selected"]["dedupe_identity"] == \
        backward["selected"]["dedupe_identity"]


def test_empty_candidates_wait() -> None:
    result = select_next_improvement([])
    assert result["waiting"] is True and result["selected"] is None
