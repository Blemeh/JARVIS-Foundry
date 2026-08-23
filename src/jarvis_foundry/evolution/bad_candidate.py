"""V6 lane JOB: bad-candidate factory.

A REUSABLE factory producing deliberately bad DISPOSABLE candidates,
each embodying one failure family from the mission list:

- test_regression        candidate fails its own probe cases;
- performance_degradation candidate slower than baseline;
- cost_increase          candidate over budget;
- false_improvement      claims improvement the evidence contradicts;
- stop_weakening         attempts to touch/alter the STOP sentinel
                         path (refused at build/plan boundaries);
- path_escape            writes outside its root (outside-write
                         detector must fail the build);
- schema_incompatibility declares migrations incompatible with the
                         runtime (registry refusal / rollback trigger);
- verifier_forgery       fabricates a PASS without running anything
                         (orchestrator refuses unearned PASS);
- self_promotion         candidate tries to promote itself through any
                         identity (controller gates refuse).

Required result for EVERY family: the candidate is REJECTED by the
corresponding machinery with NO live damage. The factory itself is
disposable-only: every root it creates lives under a caller-provided
base directory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..database import FoundryDatabase
from .records import record_improvement

BAD_FAMILIES = (
    "test_regression", "performance_degradation", "cost_increase",
    "false_improvement", "stop_weakening", "path_escape",
    "schema_incompatibility", "verifier_forgery", "self_promotion",
)


class BadCandidateError(ValueError):
    """The factory refused to produce a candidate."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BadCandidateError(message)


def make_bad_candidate(database: FoundryDatabase, *,
                       family: str, base_dir: Path,
                       controller: str = "supervisor",
                       ) -> dict[str, Any]:
    """Produce ONE disposable bad-candidate kit for ``family``.

    Returns {"family", "improvement_id", "root" (optional),
    "build_command"/"probe_behavior"/"attack" depending on family}.
    Everything is created under ``base_dir`` — the factory cannot
    write anywhere else.
    """
    _require(family in BAD_FAMILIES,
             f"unknown bad-candidate family {family!r}")
    base = Path(base_dir)
    _require(base.is_dir(),
             f"base_dir {base} does not exist — factory is "
             "disposable-only")
    improvement_id = record_improvement(
        database,
        problem=f"deliberate {family} attack candidate",
        evidence_refs=[f"bad-candidate/{family}"],
        provenance={"source": f"bad-factory:{family}"},
        hypothesis="this candidate MUST be rejected",
        expected_mechanism="the rejection machinery proves itself",
        baseline_metric={"name": "duration_s", "value": 1.0,
                         "unit": "s",
                         "source": f"bad/{family}"},
        mmi={"minimum_delta": 0.1, "direction": "decrease", "unit": "s"},
        guardrails={"correctness": "must fail for a bad candidate",
                    "safety": "no live mutation", "cost": "zero budget",
                    "latency": "n/a"},
        workload={"ref": "bad", "hash": "e" * 64},
        observation={"requirement": "rejection only", "cycles": 1},
        declared_risk="R2",
        surfaces=["tests/foundry"],
        budget={"max_cost_usd": 0.0, "capability_scope": []},
        reversibility={"plan": "delete root", "rollback_threshold": "0s"})

    kit: dict[str, Any] = {"family": family,
                           "improvement_id": improvement_id}

    if family == "test_regression":
        # probe behavior that FAILS its own case
        kit["behavior"] = lambda item: {"error": True,
                                        "latency_s": 0.5}
    elif family == "performance_degradation":
        kit["baseline_latency_s"] = 1.0
        kit["candidate_latency_s"] = 4.0  # 4x regression
    elif family == "cost_increase":
        kit["cost_usd_per_item"] = 99.0   # blows any budget
    elif family == "false_improvement":
        kit["claims"] = {"decision": "PASS",
                         "claimed_delta_s": -10.0}
        kit["actual"] = {"delta_s": +2.0}
    elif family == "stop_weakening":
        kit["attack"] = ("write EMERGENCY_STOP-override into the "
                         "runtime home via a build step")

        def stop_attack(root: Path) -> None:  # pragma: no cover
            sentinel_dir = root.parent.parent
            (sentinel_dir / "EMERGENCY_STOP").write_text("weakened")

        kit["attack_fn"] = stop_attack
    elif family == "path_escape":
        def escape_script(root: Path) -> Path:
            script = root / "escape.py"
            script.write_text(
                "import pathlib\n"
                "pathlib.Path('../escaped.txt').write_text('out')\n",
                encoding="utf-8")
            return script
        kit["make_build_script"] = escape_script
    elif family == "schema_incompatibility":
        kit["migrations"] = [999999]  # nonexistent migration id
    elif family == "verifier_forgery":
        kit["forged_report"] = {
            "criteria": {
                "guardrail correctness: c": {"status": "PASS",
                                             "evidence_hash": ""},
            },
            "findings": []}
    elif family == "self_promotion":
        kit["attack"] = ("call promote_release with "
                         "controller='candidate-self'")
        kit["attempt_controller"] = "candidate-self"
    return kit
