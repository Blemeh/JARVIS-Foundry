"""V6-S1: hypothesis, metric, and risk-class contract.

Durable, fail-closed improvement records. Every accepted change in V6
requires a measured problem, evidence and provenance, a frozen
hypothesis with expected mechanism, a baseline metric with a minimum
meaningful improvement, correctness/safety/cost/latency guardrails, an
evaluation workload, an observation requirement, a deterministic risk
class (the MAXIMUM of the declared class and the surface-derived class;
unknown surfaces fail closed to R3), budget and capability scope,
reversibility and a rollback threshold.

The record freezes BEFORE any candidate implementation. Missing or
mutable metrics make a candidate promotion-ineligible
(:func:`improvement_eligibility`). A builder may propose a risk class
but can never lower it (:func:`raise_improvement_risk` is monotonic and
strictly-ascending only). Records are append-only: identical content
dedupes to the same improvement id; changed content creates a new
record; supersession is a lineage pointer on the NEW record and never
rewrites the old one.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from ..clock import isoformat
from ..control.redaction import contains_secret_like
from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash

RISK_ORDER = ("R0", "R1", "R2", "R3")

#: Deterministic surface -> risk-class tokens (normalized lowercase,
#: forward-slash paths). The classifier takes the HIGHEST class matched
#: by any surface; a surface matching NO token fails closed to R3.
_R3_TOKENS = (
    "supervisor/", "control/", "owner_controls", "estop", "events.py",
    "database.py", "migrations.py", "schema", "credential", "redaction",
    "evolution/", "release", "rollback", "ledger",
)
_R2_TOKENS = (
    "mission/", "projections.py", "reconcile.py", "knowledge",
    "retrieval", "scheduler", "workflow", "tool", "discovery",
    "evaluation", "shadow", "canary", "execution/", "roles/",
    "connectors/", "obsidian", "correlation", "worker_context",
    "systems.py", "v3_loop", "command_center", "live_ingestion",
    "vault_indexer", "vault_retrieval", "research_queue",
)
_R1_TOKENS = (
    "skill", "prompt", "briefing", "voice", "tests/", "config/",
    "pyproject", "installer",
)
_R0_TOKENS = ("docs/", ".md", "readme", "assets/", "comment")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ImprovementError(ValueError):
    """The improvement record is invalid, incomplete, or a lower-risk
    reclassification was attempted."""


def _normalize(surface: str) -> str:
    return surface.replace("\\", "/").strip().lower()


def classify_risk(surfaces: list[str]) -> str:
    """Deterministic risk class from changed surfaces.

    The result is the highest class matched by any surface token.
    Unknown surfaces fail closed to R3 (master plan section 4; a
    candidate may never lower its class via an unclassified path).
    An empty surface list means nothing changes -> R0.
    """
    highest = 0
    for raw in surfaces:
        surface = _normalize(str(raw))
        matched = False
        for index, tokens in enumerate(
                (_R3_TOKENS, _R2_TOKENS, _R1_TOKENS, _R0_TOKENS)):
            if any(token in surface for token in tokens):
                highest = max(highest, 3 - index)
                matched = True
        if not matched:
            highest = max(highest, 3)  # unknown fails closed to R3
    return RISK_ORDER[highest]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ImprovementError(message)


def _require_text(value: Any, label: str) -> None:
    _require(isinstance(value, str) and value.strip(),
             f"{label} must be a non-empty string")


def _require_finite_number(value: Any, label: str) -> float:
    _require(isinstance(value, (int, float)) and
             math.isfinite(float(value)),
             f"{label} must be a finite number")
    return float(value)


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    _require(isinstance(value, dict) and value,
             f"{label} must be a non-empty mapping")
    return dict(value)


def _validate_metric(metric: Any, label: str) -> dict[str, Any]:
    metric = _require_mapping(metric, label)
    for key in ("name", "unit", "source"):
        _require_text(metric.get(key), f"{label}.{key}")
    _require_finite_number(metric.get("value"), f"{label}.value")
    return metric


def _validate_record_payload(payload: dict[str, Any]) -> None:
    """Fail-closed validation of a complete improvement record. Any
    missing or malformed field refuses the record (nothing partial is
    ever stored). Validated mappings replace their raw values in the
    payload so the stored record only ever carries validated content.
    """
    _require_text(payload.get("problem"), "problem")
    refs = payload.get("evidence_refs")
    _require(isinstance(refs, list) and refs and all(
        isinstance(r, str) and r for r in refs),
        "evidence_refs must be a non-empty list of strings")
    payload["provenance"] = _require_mapping(payload.get("provenance"),
                                             "provenance")
    _require_text(payload.get("hypothesis"), "hypothesis")
    _require_text(payload.get("expected_mechanism"), "expected_mechanism")
    payload["baseline_metric"] = _validate_metric(
        payload.get("baseline_metric"), "baseline_metric")
    mmi = _require_mapping(payload.get("mmi"), "mmi")
    _require_finite_number(mmi.get("minimum_delta"), "mmi.minimum_delta")
    _require(mmi.get("direction") in ("increase", "decrease"),
             "mmi.direction must be increase or decrease")
    _require_text(mmi.get("unit"), "mmi.unit")
    payload["mmi"] = mmi
    guardrails = _require_mapping(payload.get("guardrails"), "guardrails")
    for key in ("correctness", "safety", "cost", "latency"):
        _require_text(guardrails.get(key), f"guardrails.{key}")
    payload["guardrails"] = guardrails
    workload = _require_mapping(payload.get("workload"), "workload")
    _require_text(workload.get("ref"), "workload.ref")
    _require(isinstance(workload.get("hash"), str) and
             _HEX64.match(workload["hash"]),
             "workload.hash must be a 64-char sha256 hex string")
    payload["workload"] = workload
    observation = _require_mapping(payload.get("observation"),
                                   "observation")
    _require_text(observation.get("requirement"), "observation.requirement")
    cycles = observation.get("cycles")
    _require(isinstance(cycles, int) and cycles > 0 or
             isinstance(observation.get("duration"), str) and
             observation["duration"].strip(),
             "observation requires cycles > 0 or a duration string")
    payload["observation"] = observation
    declared = payload.get("declared_risk")
    _require(declared in RISK_ORDER,
             f"declared_risk must be one of {RISK_ORDER}")
    surfaces = payload.get("surfaces")
    _require(isinstance(surfaces, list) and surfaces and all(
        isinstance(s, str) and s.strip() for s in surfaces),
        "surfaces must be a non-empty list of strings")
    budget = _require_mapping(payload.get("budget"), "budget")
    _require_finite_number(budget.get("max_cost_usd"),
                           "budget.max_cost_usd")
    _require(float(budget["max_cost_usd"]) >= 0.0,
             "budget.max_cost_usd must be >= 0")
    _require(isinstance(budget.get("capability_scope"), list),
             "budget.capability_scope must be a list")
    payload["budget"] = budget
    reversibility = _require_mapping(payload.get("reversibility"),
                                     "reversibility")
    _require_text(reversibility.get("plan"), "reversibility.plan")
    _require_text(reversibility.get("rollback_threshold"),
                  "reversibility.rollback_threshold")
    payload["reversibility"] = reversibility


def record_improvement(
    database: FoundryDatabase, *,
    problem: str,
    evidence_refs: list[str],
    provenance: dict[str, Any],
    hypothesis: str,
    expected_mechanism: str,
    baseline_metric: dict[str, Any],
    mmi: dict[str, Any],
    guardrails: dict[str, Any],
    workload: dict[str, Any],
    observation: dict[str, Any],
    declared_risk: str,
    surfaces: list[str],
    budget: dict[str, Any],
    reversibility: dict[str, Any],
    supersedes: str | None = None,
    source_type: str = "executive",
    source_id: str = "v6",
) -> str:
    """Freeze an improvement record (append-only, content-derived id).

    Returns the improvement id. Identical content dedupes to the same
    id without appending a second event. Any missing/invalid field,
    secret-like content, or unknown supersedes target refuses the
    record.
    """
    payload: dict[str, Any] = {
        "problem": problem,
        "evidence_refs": list(evidence_refs),
        "provenance": provenance,
        "hypothesis": hypothesis,
        "expected_mechanism": expected_mechanism,
        "baseline_metric": baseline_metric,
        "mmi": mmi,
        "guardrails": guardrails,
        "workload": workload,
        "observation": observation,
        "declared_risk": declared_risk,
        "surfaces": list(surfaces),
        "budget": budget,
        "reversibility": reversibility,
    }
    if supersedes is not None:
        payload["supersedes"] = supersedes
    _validate_record_payload(payload)
    if contains_secret_like(canonical_json(payload)):
        raise ImprovementError(
            "improvement record contains secret-like content — refused "
            "at the boundary (V5-S7 guard family)")
    if supersedes is not None and database.row(
            "improvement_records", "improvement_id", supersedes) is None:
        raise ImprovementError(
            f"supersedes target {supersedes} does not exist — refused")
    final_risk = classify_risk(payload["surfaces"])
    final_risk = RISK_ORDER[max(RISK_ORDER.index(payload["declared_risk"]),
                                RISK_ORDER.index(final_risk))]
    payload["risk_class"] = final_risk
    payload["risk_class_declared"] = payload["declared_risk"]
    del payload["declared_risk"]
    improvement_id = "improvement-" + payload_hash(
        payload)[:12]
    payload["improvement_id"] = improvement_id
    event = Event.create(
        "improvement.recorded", payload,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"improvement.recorded:{improvement_id}")
    database.append(event)
    return improvement_id


def improvement_eligibility(database: FoundryDatabase,
                            improvement_id: str) -> tuple[bool, str]:
    """Promotion-eligibility: the record must exist, be frozen, and
    carry every metric field (fail closed — an absent or empty metric
    makes a candidate promotion-ineligible)."""
    row = database.row("improvement_records", "improvement_id",
                       improvement_id)
    if row is None:
        return False, "unknown improvement"
    if row["state"] != "FROZEN":
        return False, f"state is {row['state']}, not FROZEN"
    try:
        metric = json.loads(row["baseline_metric_json"])
        _require_finite_number(metric.get("value"), "baseline_metric.value")
        mmi = json.loads(row["mmi_json"])
        _require_finite_number(mmi.get("minimum_delta"),
                               "mmi.minimum_delta")
        guardrails = json.loads(row["guardrails_json"])
        for key in ("correctness", "safety", "cost", "latency"):
            _require_text(guardrails.get(key), f"guardrails.{key}")
        workload = json.loads(row["workload_json"])
        _require(isinstance(workload.get("hash"), str) and
                 _HEX64.match(workload["hash"]), "workload.hash")
        observation = json.loads(row["observation_json"])
        _require_text(observation.get("requirement"),
                      "observation.requirement")
        budget = json.loads(row["budget_json"])
        _require_finite_number(budget.get("max_cost_usd"),
                               "budget.max_cost_usd")
        reversibility = json.loads(row["reversibility_json"])
        _require_text(reversibility.get("plan"), "reversibility.plan")
        _require_text(reversibility.get("rollback_threshold"),
                      "reversibility.rollback_threshold")
    except ImprovementError as error:
        return False, str(error)
    return True, "eligible"


def raise_improvement_risk(
    database: FoundryDatabase, improvement_id: str, new_risk: str, *,
    reason: str, source_type: str = "executive",
    source_id: str = "v6",
) -> None:
    """Monotonic risk reclassification: strictly ascending only.

    A builder can never lower a record's risk class; equal-class
    re-raises are also refused (idempotent re-delivery of an already
    applied raise is handled by the event's idempotency key, not by
    re-application here).
    """
    _require(new_risk in RISK_ORDER,
             f"new_risk must be one of {RISK_ORDER}")
    _require_text(reason, "reason")
    row = database.row("improvement_records", "improvement_id",
                       improvement_id)
    if row is None:
        raise ImprovementError(
            f"improvement {improvement_id} does not exist — refused")
    current = row["risk_class"]
    if RISK_ORDER.index(new_risk) <= RISK_ORDER.index(current):
        raise ImprovementError(
            f"risk reclassification {current} -> {new_risk} is not "
            "strictly ascending — refused (a builder can never lower "
            "a risk class)")
    event = Event.create(
        "improvement.risk_raised",
        {"improvement_id": improvement_id,
         "risk_class": new_risk,
         "risk_class_previous": current,
         "reason": reason},
        source_type=source_type, source_id=source_id,
        idempotency_key=(
            f"improvement.risk_raised:{improvement_id}:{new_risk}"))
    database.append(event)
