"""Regression: the A4 effect criterion must match the mission contract.

The harness asserted one effect for a whole campaign, but the canonical A3
mission runs an ordered two-step plan (01-plan, 02-execute) and each
verified step legitimately commits once. The campaign that exposed this had
three effect records: two completed commits, one per verified step, plus one
interrupted claim correctly frozen and reconciled against its own receipt.
The criterion now asserts one completed commit per verified step and that no
effect is left non-terminal.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "v11_a3a4_proving.py"
SPEC = importlib.util.spec_from_file_location("v11_a3a4_proving_effect", SCRIPT)
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HARNESS
SPEC.loader.exec_module(HARNESS)


def _effect(effect_id: str, *, status: str, mission_id: str = "mission-1"):
    return {"id": effect_id, "identity_key": effect_id,
            "mission_id": mission_id, "status": status}


def _acceptance(acceptance_id: str, *, builder: str, verifier: str, mission: str = "mission-1"):
    return {"id": acceptance_id, "identity_key": acceptance_id,
            "mission_id": mission, "builder_run_id": builder,
            "verifier_run_id": verifier, "independent": True, "verdict": "PASS"}


def _state(*, effects, acceptances):
    state = HARNESS.blank_state(run_id="effect-run", expected_sha="f" * 40, duration_hours=48)
    state["records"]["effects"] = {row["id"]: row for row in effects}
    state["records"]["acceptances"] = {row["id"]: row for row in acceptances}
    return state


def test_two_verified_steps_expect_two_completed_commits():
    """The two-step canonical plan commits once per verified step."""
    state = _state(
        effects=[
            _effect("effect-1", status="COMPLETED"),
            _effect("effect-2", status="COMPLETED"),
        ],
        acceptances=[
            _acceptance("acceptance-1", builder="builder-1", verifier="verifier-1"),
            _acceptance("acceptance-2", builder="builder-2", verifier="verifier-2"),
        ],
    )
    metrics = HARNESS.derive_metrics(state)
    assert metrics["effects"] == 2
    assert metrics["completed_effects"] == 2
    assert metrics["verified_steps"] == 2
    assert metrics["nonterminal_effects"] == 0


def test_a_reconciled_interrupted_claim_is_not_a_duplicate_commit():
    """Three records, two steps: the frozen sibling is reconciled, not extra."""
    state = _state(
        effects=[
            _effect("effect-frozen", status="FROZEN"),
            _effect("effect-1", status="COMPLETED"),
        ],
        acceptances=[
            _acceptance("acceptance-1", builder="builder-1", verifier="verifier-1"),
        ],
    )
    metrics = HARNESS.derive_metrics(state)
    assert metrics["effects"] == 2
    assert metrics["completed_effects"] == 1
    assert metrics["verified_steps"] == 1
    assert metrics["nonterminal_effects"] == 0


def test_a_started_effect_is_flagged_nonterminal():
    """An unreconciled claim must fail the ambiguity criterion."""
    state = _state(
        effects=[_effect("effect-stuck", status="STARTED")],
        acceptances=[_acceptance("a", builder="b", verifier="v")],
    )
    metrics = HARNESS.derive_metrics(state)
    assert metrics["nonterminal_effects"] == 1
    assert metrics["completed_effects"] == 0
    assert metrics["completed_effects"] != metrics["verified_steps"]


def test_more_completed_commits_than_verified_steps_is_rejected():
    """Extra commits for the same step are the duplicate the rule guards."""
    state = _state(
        effects=[
            _effect("effect-1", status="COMPLETED"),
            _effect("effect-2", status="COMPLETED"),
        ],
        acceptances=[_acceptance("a", builder="b", verifier="v")],
    )
    metrics = HARNESS.derive_metrics(state)
    assert metrics["completed_effects"] == 2
    assert metrics["verified_steps"] == 1
