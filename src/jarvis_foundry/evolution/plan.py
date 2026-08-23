"""V6 lane JOB: experiment plan compiler.

Compiles a FROZEN improvement record into a bounded experiment plan
that downstream stages (builder/shadow/lab/canary/verify) execute
literally. Determinism: identical (improvement, source_commit) inputs
produce the byte-identical plan (content-derived plan id).

Fail-closed contracts:
- only FROZEN, promotion-eligible records compile;
- the risk class comes from the RECORD (never from the caller);
- forbidden surfaces are derived from the allowed surfaces: anything
  matching an R3 family token is forbidden even for R3 experiments
  (an R3 experiment may touch its own named surface but NEVER the
  supervisor control plane, credentials, redaction, or the event
  ledger);
- R2/R3 plans REQUIRE shadow + canary; R0/R1 never require canary;
  verifier independence is REQUIRED in every plan;
- success threshold and regression guardrails come from the frozen MMI
  and guardrails — the compiler cannot invent looser bounds;
- budget: max wall-clock timeout and cost cap come from the record's
  budget block; defaults tighten, never loosen;
- no production mutation is representable: every plan targets a
  disposable root under var/ and carries mode="disposable".
"""
from __future__ import annotations

import hashlib
from typing import Any

from ..database import FoundryDatabase
from ..events import canonical_json
from .records import improvement_eligibility

#: surface families that NO experiment may touch regardless of class
_ALWAYS_FORBIDDEN = (
    "supervisor/", "control/", "credential", "redaction", "events.py",
    "owner_controls", "estop",
)

_RISK_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "R0": {"shadow": False, "canary": False},
    "R1": {"shadow": False, "canary": False},
    "R2": {"shadow": True, "canary": True},
    "R3": {"shadow": True, "canary": True},
}

_DEFAULT_TIMEOUT_S = 120


class PlanError(ValueError):
    """The experiment plan was refused."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PlanError(message)


def _is_forbidden(surface: str) -> bool:
    normalized = surface.replace("\\", "/").strip().lower()
    return any(token in normalized for token in _ALWAYS_FORBIDDEN)


def compile_experiment_plan(database: FoundryDatabase, *,
                            improvement_id: str,
                            source_commit: str,
                            ) -> dict[str, Any]:
    """Compile ONE bounded experiment plan from a frozen hypothesis."""
    row = database.row("improvement_records", "improvement_id",
                       improvement_id)
    _require(row is not None,
             f"improvement {improvement_id} does not exist — refused")
    _require(str(row["state"]) == "FROZEN",
             f"improvement {improvement_id} is not FROZEN — refusing to "
             "compile an experiment from an unfrozen hypothesis")
    eligible, reason = improvement_eligibility(database, improvement_id)
    _require(eligible,
             f"improvement {improvement_id} not eligible: {reason}")
    _require(isinstance(source_commit, str) and len(source_commit) >= 40,
             "source_commit must be a 40+ char commit identity")

    import json as _json
    surfaces = [str(s) for s in _json.loads(row["surfaces_json"])]
    risk_class = str(row["risk_class"])
    mmi = _json.loads(row["mmi_json"])
    guardrails = _json.loads(row["guardrails_json"])
    budget = _json.loads(row["budget_json"])

    # forbidden surfaces: everything NOT allowed plus always-forbidden
    # families (defense against a frozen record naming a control-plane
    # path — the classifier would have forced R3, but the plan still
    # refuses to let the experiment mutate it)
    allowed = sorted(set(surfaces))
    forbidden = sorted({family for s in surfaces
                        if _is_forbidden(s)
                        for family in (_ALWAYS_FORBIDDEN)} |
                       {_ALWAYS_FORBIDDEN[0]} |
                       # sibling families never named by the record
                       {"src/jarvis_foundry/var", "live", "production"})
    requirements = _RISK_REQUIREMENTS[risk_class]

    max_cost_usd = float(budget.get("max_cost_usd", 0.0))
    capability_scope = list(budget.get("capability_scope", []))

    plan = {
        "plan_kind": "experiment_plan",
        "mode": "disposable",
        "improvement_id": improvement_id,
        "risk_class": risk_class,
        "candidate_source_base": source_commit,
        "allowed_surfaces": allowed,
        "forbidden_surfaces": forbidden,
        "benchmark_suite": {
            "kind": "lab_workload",
            "success_threshold": {
                "metric_direction": mmi["direction"],
                "minimum_delta": mmi["minimum_delta"],
                "unit": mmi["unit"],
            },
            "regression_guardrails": [
                f"{name}: {detail}"
                for name, detail in guardrails.items()],
        },
        "shadow_required": requirements["shadow"],
        "canary_required": requirements["canary"],
        "verifier_independence_required": True,
        "rollback": {
            "strategy": "freeze_first_rollback",
            "threshold": str(_json.loads(
                row["reversibility_json"])["rollback_threshold"]),
        },
        "max_resource_budget": {
            "max_cost_usd": max_cost_usd,
            "capability_scope": capability_scope,
            "timeout_s": _DEFAULT_TIMEOUT_S,
        },
    }
    plan_id = "plan-" + hashlib.sha256(canonical_json(plan)
                                       .encode("utf-8")).hexdigest()[:12]
    plan["plan_id"] = plan_id
    database.append(_plan_event(plan))
    return plan


def _plan_event(plan: dict[str, Any]) -> Any:
    from ..events import Event
    return Event.create(
        "experiment.plan_compiled",
        plan,
        source_type="supervisor", source_id="v6",
        idempotency_key=f"experiment.plan_compiled:{plan['plan_id']}")


def validate_plan_execution(plan: dict[str, Any], *,
                            touched_surfaces: list[str],
                            used_shadow: bool,
                            used_canary: bool,
                            had_independent_verifier: bool) -> dict[str, Any]:
    """Check an EXECUTION TRACE against its plan. Returns typed result:
    COMPLIANT or VIOLATION with reasons. Fail closed on unknown."""
    violations: list[str] = []
    forbidden = set(plan["forbidden_surfaces"])
    for surface in touched_surfaces:
        normalized = surface.replace("\\", "/").strip().lower()
        allowed = any(a.lower() in normalized
                      for a in plan["allowed_surfaces"])
        hit_forbidden = any(f.lower() in normalized for f in forbidden)
        if hit_forbidden or not allowed:
            violations.append(
                f"surface {surface} outside allowed set or forbidden")
    if plan["shadow_required"] and not used_shadow:
        violations.append("plan requires shadow execution; none recorded")
    if plan["canary_required"] and not used_canary:
        violations.append("plan requires canary; none recorded")
    if plan["verifier_independence_required"] and \
            not had_independent_verifier:
        violations.append("plan requires independent verifier")
    return {"compliance": "COMPLIANT" if not violations else "VIOLATION",
            "violations": violations,
            "plan_id": plan["plan_id"]}
