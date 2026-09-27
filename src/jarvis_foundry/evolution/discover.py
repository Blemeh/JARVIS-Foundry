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
import json
import uuid
from typing import Any

from ..control.redaction import contains_secret_like
from ..database import FoundryDatabase
from ..events import Event, canonical_json
from ..migrations import SCHEMA_VERSION
from .records import classify_risk, record_improvement
from ._authority import SUPERVISOR_CONTROLLER

OBSERVATION_TYPES = ("metric_exceedances", "guardrail_breaches",
                     "verifier_findings", "task_failures",
                     "capability_gaps")


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


def sources_fingerprint(sources: dict[str, Any]) -> str:
    """Stable public identity for one detector source snapshot."""
    return _sources_hash(sources)


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
    evidence_refs = observation.get("evidence_refs", [evidence])
    _require(
        isinstance(evidence_refs, list) and evidence_refs
        and all(isinstance(ref, str) and ref.strip()
                for ref in evidence_refs),
        "evidence_refs must be a non-empty list of recorded references",
    )
    if evidence not in evidence_refs:
        evidence_refs = [*evidence_refs, evidence]
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
    elif observation_type == "capability_gaps":
        capability_id = observation["capability_id"]
        gap_id = observation["gap_id"]
        status = observation["status"]
        problem = (f"Fabric capability {capability_id!r} remains "
                   f"unresolved in gap {gap_id} ({status}; evidence "
                   f"{evidence})")
        hypothesis = (f"make Fabric capability {capability_id!r} available "
                      "under the existing readiness and trust gates")
        mechanism = ("the durable Fabric gap lifecycle ended in a blocking "
                     "state, so the required capability still cannot run")
        baseline_metric = {
            "name": "unresolved_fabric_capability_gaps",
            "value": 1.0, "unit": "gaps", "source": evidence,
        }
        mmi = {"minimum_delta": 1.0, "direction": "decrease",
               "unit": "gaps"}
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
        "evidence_refs": list(dict.fromkeys(evidence_refs)),
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
    window: dict[str, str] | None = None,
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
    _require(isinstance(sources, dict), "sources must be a mapping")
    if window is not None:
        _require(
            isinstance(window, dict)
            and isinstance(window.get("start"), str)
            and bool(window["start"].strip())
            and isinstance(window.get("end"), str)
            and bool(window["end"].strip()),
            "window must contain explicit start and end timestamps",
        )
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
    existing_by_identity: dict[str, dict[str, Any]] = {}
    for row in database.all("improvement_records"):
        try:
            provenance = json.loads(str(row.get("provenance_json") or "{}"))
        except (TypeError, ValueError):
            continue
        identity = provenance.get("detector_identity") \
            if isinstance(provenance, dict) else None
        if isinstance(identity, str) and identity:
            existing_by_identity[identity] = row

    extension_state: dict[str, tuple[str, list[str]]] = {}
    for event in database.events():
        if event.event_type != "improvement.evidence_extended":
            continue
        improvement_id = str(event.payload.get("improvement_id") or "")
        snapshot_hash = str(event.payload.get("snapshot_hash") or "")
        refs = event.payload.get("evidence_refs")
        if not improvement_id or not snapshot_hash or not isinstance(refs, list):
            continue
        prior_hash, prior_refs = extension_state.get(
            improvement_id, ("", []))
        extended = list(prior_refs)
        for ref in refs:
            if isinstance(ref, str) and ref.strip() and ref not in extended:
                extended.append(ref)
        extension_state[improvement_id] = (
            snapshot_hash,
            extended,
        )

    for candidate in candidates:
        provenance = candidate["provenance"]
        detector_identity = provenance.get("detector_identity")
        snapshot_hash = _sources_hash(candidate)
        if isinstance(detector_identity, str) and detector_identity:
            existing = existing_by_identity.get(detector_identity)
            if existing is not None:
                improvement_id = str(existing["improvement_id"])
                try:
                    base_refs = json.loads(
                        str(existing.get("evidence_refs_json") or "[]"))
                    base_provenance = json.loads(
                        str(existing.get("provenance_json") or "{}"))
                except (TypeError, ValueError):
                    base_refs, base_provenance = [], {}
                if not isinstance(base_refs, list):
                    base_refs = []
                if not isinstance(base_provenance, dict):
                    base_provenance = {}
                prior_hash, extended_refs = extension_state.get(
                    improvement_id,
                    (str(base_provenance.get("source_snapshot_hash") or ""),
                     []),
                )
                if snapshot_hash != prior_hash:
                    all_refs = list(dict.fromkeys(
                        [str(ref) for ref in base_refs if isinstance(ref, str)]
                        + list(extended_refs)
                        + [str(ref) for ref in candidate["evidence_refs"]]
                    ))
                    extension_hash = _sources_hash({
                        "improvement_id": improvement_id,
                        "snapshot_hash": snapshot_hash,
                    })[:16]
                    database.append(Event.create(
                        "improvement.evidence_extended",
                        {
                            "improvement_id": improvement_id,
                            "detector_identity": detector_identity,
                            "snapshot_hash": snapshot_hash,
                            "evidence_refs": all_refs[:50],
                            "window": dict(window or {}),
                            "candidate_snapshot": {
                                "problem": candidate["problem"],
                                "baseline_metric": candidate["baseline_metric"],
                                "workload": candidate["workload"],
                                "provenance": candidate["provenance"],
                            },
                        },
                        source_type=source_type, source_id=source_id,
                        idempotency_key=(
                            f"improvement.evidence_extended:{improvement_id}:"
                            f"{extension_hash}"),
                    ))
                    extension_state[improvement_id] = (
                        snapshot_hash, all_refs[:50])
                recorded.append(improvement_id)
                continue
            provenance["source_snapshot_hash"] = snapshot_hash
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
        if isinstance(detector_identity, str) and detector_identity:
            existing_by_identity[detector_identity] = database.row(
                "improvement_records", "improvement_id", improvement_id
            ) or {}
    # content-derived run id: an identical re-run of the same sources
    # dedupes (the discovery run IS its sources)
    run_identity: dict[str, Any] = {"sources": sources}
    if window is not None:
        run_identity["window"] = dict(window)
    run_id = "discovery-" + _sources_hash(run_identity)[:12]
    run_payload = {
        "run_id": run_id,
        "sources_hash": _sources_hash(sources),
        "candidates_count": len(candidates),
        "recorded_count": len(recorded),
        "controller": controller,
    }
    if window is not None:
        run_payload["window"] = dict(window)
    database.append(Event.create(
        "discovery.observed", run_payload,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"discovery.observed:{run_id}"))
    return recorded
