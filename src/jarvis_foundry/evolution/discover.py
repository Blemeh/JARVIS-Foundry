"""V6-S10: autonomous improvement discovery.

Improvement candidates are discovered ONLY from recorded observations:
metric exceedances (measured against thresholds/MMI), guardrail
breaches, verifier findings, and task failures. A candidate without an
evidence reference is REFUSED (no fabricated problems); hypotheses are
template-derived from the observation (no invented prose); the MMI
direction derives from the threshold semantics; risk class derives
from the surfaces (records classifier); everything records a
discovery.observed event with a sources hash. Candidates can never
drive discovery.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from ..control.redaction import contains_secret_like
from ..database import FoundryDatabase
from ..events import Event, canonical_json
from ..migrations import SCHEMA_VERSION
from .records import classify_risk, record_improvement
from ._authority import SUPERVISOR_CONTROLLER

OBSERVATION_TYPES = ("metric_exceedances", "guardrail_breaches",
                     "verifier_findings", "task_failures")


class DiscoveryError(ValueError):
    """A discovery operation is refused."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DiscoveryError(message)


def _controller_ok(controller: str) -> bool:
    return isinstance(controller, str) and \
        controller == SUPERVISOR_CONTROLLER


def _sources_hash(sources: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(sources).encode("utf-8")).hexdigest()


def _num(value: object) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(
        value, bool), "observation value/threshold must be numeric")
    return float(value)


def _one_candidate(observation_type: str, observation: dict[str, Any]
                   ) -> dict[str, Any]:
    """Derive ONE candidate strictly from the recorded observation —
    template-derived problem text, MMI direction from threshold
    semantics, risk from the surfaces classifier."""
    evidence = observation.get("evidence_ref")
    _require(isinstance(evidence, str) and evidence.strip(),
             f"{observation_type} observation without an evidence_ref "
             "is refused — problems are only discovered from recorded "
             "evidence, never fabricated")
    surfaces = observation.get("surfaces", ["tests/foundry",
                                            "pyproject.toml"])
    _require(isinstance(surfaces, list) and surfaces,
             "surfaces must be a non-empty list")

    if observation_type == "metric_exceedances":
        metric = observation["metric"]
        value = _num(observation.get("value"))
        threshold = _num(observation.get("threshold"))
        unit = observation.get("unit", "")
        direction = "decrease" if value > threshold else "increase"
        problem = (f"observed {metric} = {value}{unit} vs threshold "
                   f"{threshold}{unit} (evidence {evidence})")
        hypothesis = (f"reduce {metric} toward/below the "
                      f"{threshold}{unit} threshold")
        mechanism = (f"the recorded observation shows {metric} outside "
                     f"its threshold; address the measured cause")
        baseline_metric = {"name": metric, "value": value,
                           "unit": unit, "source": evidence}
        mmi = {"minimum_delta": abs(value - threshold) * 0.1,
               "direction": direction, "unit": unit}
        guardrails = {"correctness": "no behavior regression",
                      "safety": "no live mutation", "cost": "no paid "
                      "calls", "latency": "no latency regression"}
    elif observation_type == "guardrail_breaches":
        guardrail = observation["guardrail"]
        detail = observation.get("detail", "")
        problem = (f"guardrail {guardrail!r} breached: {detail} "
                   f"(evidence {evidence})")
        hypothesis = f"restore the {guardrail} guardrail"
        mechanism = ("the recorded breach indicates the guardrail is "
                     "not enforced on this path")
        baseline_metric = {"name": f"guardrail_{guardrail}",
                           "value": 1.0, "unit": "breaches",
                           "source": evidence}
        mmi = {"minimum_delta": 1.0, "direction": "decrease",
               "unit": "breaches"}
        guardrails = {"correctness": "no behavior regression",
                      "safety": "no live mutation", "cost": "no paid "
                      "calls", "latency": "no latency regression"}
    elif observation_type == "verifier_findings":
        severity = observation.get("severity", "INFO")
        finding = observation.get("finding", "")
        problem = (f"verifier finding [{severity}]: {finding} "
                   f"(evidence {evidence})")
        hypothesis = f"resolve the {severity} verifier finding"
        mechanism = ("the independent verifier recorded a finding "
                     "against the machinery; harden the reported path")
        baseline_metric = {"name": f"verifier_{severity.lower()}_open",
                           "value": 1.0, "unit": "findings",
                           "source": evidence}
        mmi = {"minimum_delta": 1.0, "direction": "decrease",
               "unit": "findings"}
        guardrails = {"correctness": "no behavior regression",
                      "safety": "no live mutation", "cost": "no paid "
                      "calls", "latency": "no latency regression"}
    elif observation_type == "task_failures":
        task = observation["task"]
        detail = observation.get("detail", "")
        problem = (f"task {task!r} failed: {detail} (evidence "
                   f"{evidence})")
        hypothesis = f"make {task} succeed reliably"
        mechanism = ("the recorded failure indicates a reliability "
                     "gap in the task path")
        baseline_metric = {"name": f"task_{task}_failures",
                           "value": 1.0, "unit": "failures",
                           "source": evidence}
        mmi = {"minimum_delta": 1.0, "direction": "decrease",
               "unit": "failures"}
        guardrails = {"correctness": "no behavior regression",
                      "safety": "no live mutation", "cost": "no paid "
                      "calls", "latency": "no latency regression"}
    else:  # unreachable — validated at the entry point
        raise DiscoveryError(f"unknown observation type "
                             f"{observation_type}")

    risk_class = classify_risk(surfaces)
    # Optional caller-supplied provenance (e.g. the V6 lane detector's
    # rule/dedupe identity) is MERGED under the template provenance so
    # hypotheses stay linked to their observation lineage. Caller keys
    # never overwrite the template source/observed_at contract.
    provenance: dict[str, Any] = {
        "source": f"discovery:{observation_type}",
        "observed_at": observation.get("observed_at", "recorded"),
    }
    extra_provenance = observation.get("provenance")
    if isinstance(extra_provenance, dict):
        for key, value in extra_provenance.items():
            provenance.setdefault(key, value)
    return {
        "problem": problem,
        "evidence_refs": [evidence],
        "provenance": provenance,
        "hypothesis": hypothesis,
        "expected_mechanism": mechanism,
        "baseline_metric": baseline_metric,
        "mmi": mmi,
        "guardrails": guardrails,
        "workload": {"ref": "discovery",
                     "hash": _sources_hash({observation_type:
                                            [observation]})},
        "observation": {"requirement": "1 verification cycle",
                        "cycles": 1},
        "declared_risk": risk_class,
        "surfaces": surfaces,
        "budget": {"max_cost_usd": 0.0,
                   "capability_scope": ["pytest"]},
        "reversibility": {"plan": "git revert",
                          "rollback_threshold": "5m"},
    }


def candidate_improvements(
    database: FoundryDatabase, *, sources: dict[str, Any],
    controller: str,
) -> list[dict[str, Any]]:
    """Derive improvement candidates from the recorded observations."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can run discovery — "
             "candidate identities are refused")
    _require(isinstance(sources, dict),
             "sources must be a mapping of observation type -> list")
    candidates: list[dict[str, Any]] = []
    for observation_type, observations in sources.items():
        _require(observation_type in OBSERVATION_TYPES,
                 f"unknown observation type {observation_type!r} — "
                 "refused (discovery only consumes recorded "
                 "observation types)")
        _require(isinstance(observations, list),
                 f"{observation_type} must be a list")
        for observation in observations:
            _require(isinstance(observation, dict),
                     "each observation must be a mapping")
            _require(not contains_secret_like(canonical_json(
                observation)), "observation contains secret-like "
                "content — refused")
            candidates.append(_one_candidate(observation_type,
                                             observation))
    return candidates


def record_discovered_improvements(
    database: FoundryDatabase, *, candidates: list[dict[str, Any]],
    sources: dict[str, Any], controller: str,
    source_type: str = "supervisor", source_id: str = "v6",
) -> list[str]:
    """Record the discovered candidates as improvements (S1 machinery)
    and record the discovery run. Identical re-runs dedupe. ALL
    candidates are validated BEFORE any is recorded (all-or-nothing —
    a mixed valid/invalid list never leaves partial state; S10
    verifier deleg_b65820c2)."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can record discovery "
             "results")
    _require(isinstance(candidates, list), "candidates must be a list")
    for candidate in candidates:  # validate ALL first
        _require(isinstance(candidate, dict),
                 "each candidate must be a mapping")
        _require("evidence_refs" in candidate and candidate[
            "evidence_refs"], "candidate without evidence refs is "
            "refused — never record fabricated problems")
        _require("problem" in candidate and isinstance(
            candidate["problem"], str) and candidate["problem"].strip(),
            "candidate problem must be a non-empty string")
        _require("baseline_metric" in candidate and isinstance(
            candidate["baseline_metric"], dict),
            "candidate baseline_metric must be a mapping")
        _require("mmi" in candidate and isinstance(candidate["mmi"],
                                                   dict),
                 "candidate mmi must be a mapping")
    recorded: list[str] = []
    for candidate in candidates:
        improvement_id = record_improvement(
            database, problem=candidate["problem"],
            evidence_refs=candidate["evidence_refs"],
            provenance=candidate["provenance"],
            hypothesis=candidate["hypothesis"],
            expected_mechanism=candidate["expected_mechanism"],
            baseline_metric=candidate["baseline_metric"],
            mmi=candidate["mmi"],
            guardrails=candidate["guardrails"],
            workload=candidate["workload"],
            observation=candidate["observation"],
            declared_risk=candidate["declared_risk"],
            surfaces=candidate["surfaces"],
            budget=candidate["budget"],
            reversibility=candidate["reversibility"],
            source_type="discovery", source_id="v6")
        recorded.append(improvement_id)
    # content-derived run id: an identical re-run of the same sources
    # dedupes (the discovery run IS its sources)
    run_id = "discovery-" + _sources_hash(sources)[:12]
    database.append(Event.create(
        "discovery.observed",
        {"run_id": run_id, "sources_hash": _sources_hash(sources),
         "candidates_count": len(candidates),
         "recorded_count": len(recorded),
         "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"discovery.observed:{run_id}"))
    return recorded
