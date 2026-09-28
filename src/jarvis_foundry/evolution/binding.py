"""Durable execution bindings: Improvement -> exact executable authority.

An Improvement may become executable ONLY when it carries an explicit
durable binding with enough authority to execute safely.  Without a
complete binding the Executive must keep failing closed (see
``executive/portfolio_bridge.py`` ``IMPROVEMENT`` branch); this module is
what a complete binding looks like and how it is verified fresh at
materialization time.

Storage is event-sourced on the canonical ledger
(``improvement.binding_recorded`` / ``improvement.binding_consumed``), so
no schema migration is required.  The latest recorded binding per
improvement wins; consumption is idempotent per (improvement, mission) so
an Executive REPLAN retry may consume the same binding for a new mission
while a duplicate delivery for the same mission dedupes.

Risk-class translation is ordinal and total: improvement records use
R0-R3 (``evolution/records.py``), Workforce contracts and mission step
contracts use C0-C3.  ``R<n>`` maps to ``C<n>``; anything else fails
closed.  The mapping is deliberately boring: risk classes are severity
ordinals on both scales, and inventing a cleverer crosswalk would be
authorization logic hiding inside a lookup table.
"""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash

BINDING_RECORDED_EVENT = "improvement.binding_recorded"
BINDING_CONSUMED_EVENT = "improvement.binding_consumed"
CANDIDATE_RECORDED_EVENT = "improvement.candidate_recorded"

#: Improvement-scale risk classes (evolution/records.py RISK_ORDER).
R_CLASSES = ("R0", "R1", "R2", "R3")

#: Ordinal translation to the Workforce / step-contract scale (C0-C3).
RISK_TO_WORKFORCE = {"R0": "C0", "R1": "C1", "R2": "C2", "R3": "C3"}

_SHA40 = re.compile(r"^[0-9a-f]{40}$")

#: Local-commit capability the commit seam requires.  Registration leaves it
#: PROBING; only a verified bound improvement promotes it to READY, so an
#: operator-packaged worktree can never silently gain autonomous commits.
COMMIT_CAPABILITY = "git_local_commit"

try:
    from ..workers.result_contract import RESULT_FILENAME
except ImportError:  # noqa: BLE001 - the literal is stable contract
    RESULT_FILENAME = ".jarvis-result.json"

#: Plan artifact the bound plan step must record.  Builders durably emit
#: plans as worktree files (observed live); prescribing the exact name
#: keeps the output bounded instead of arid or sprawling.
PLAN_FILENAME = "01-plan.md"

#: Build-artifact directory names never treated as scope changes.
_SCOPE_IGNORED_DIRS = frozenset({"__pycache__", ".pytest_cache"})


def scope_ignored(path: str) -> bool:
    """True when a worktree path is a control/build artifact, not scope.

    The worker result file, the prescribed plan artifact, and Python
    cache directories are never committable scope changes: they are
    tolerated as untracked evidence and never staged.  Anything else —
    including lookalikes in other directories — stays in scope.
    """
    raw = str(path or "").strip().replace("\\", "/")
    parts = tuple(part for part in raw.split("/") if part not in ("", "."))
    if not parts:
        return True
    if parts[-1] in (RESULT_FILENAME, PLAN_FILENAME):
        return True
    return any(part in _SCOPE_IGNORED_DIRS for part in parts)


#: Mission ``created_from`` values carrying an improvement lineage match this.
_IMPROVEMENT_REF = re.compile(r"improvement-[0-9a-f]{12,}")

#: Capability bound improvement steps execute under.  Both steps use the
#: write capability — not because the plan step writes (its instructions
#: forbid writes), but because the worker bridge routes write-capability
#: contracts to model-backed workers while read-capability contracts may
#: route to non-model local workers, whose result identity (fabric/local)
#: fails provider reconciliation for a provider-backed mission.  This
#: mirrors the proven owner-goal shape (both 01-plan and 02-execute under
#: write_registered_worktree_files).
EXECUTE_CAPABILITY = "write_registered_worktree_files"


class ImprovementBindingError(ValueError):
    """A binding is missing, incomplete, or no longer verifiable."""


def map_risk_class(risk_class: str) -> str:
    """Translate an improvement R-class to the workforce C-class."""
    mapped = RISK_TO_WORKFORCE.get(str(risk_class or "").strip().upper())
    if mapped is None:
        raise ImprovementBindingError(
            f"risk_class {risk_class!r} is not a mappable improvement class "
            f"(expected one of {sorted(RISK_TO_WORKFORCE)})"
        )
    return mapped


def _require_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ImprovementBindingError(f"{label} must be a non-empty string")
    return text


def _require_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ImprovementBindingError(f"{label} must be a list of strings")
    items = [str(item).strip() for item in value]
    if not items or any(not item for item in items):
        raise ImprovementBindingError(f"{label} must be a non-empty list of strings")
    return items


def _canonical_relative(path: str, label: str) -> str:
    """Repo-relative scope path in the same canonical form the step-contract gate uses."""
    raw = str(path or "").strip().replace("\\", "/")
    parts = tuple(part for part in raw.split("/") if part not in ("", "."))
    if not parts or ".." in parts:
        raise ImprovementBindingError(
            f"{label} {path!r} must stay below the repository root"
        )
    if raw.startswith("/") or raw.startswith("//"):
        raise ImprovementBindingError(f"{label} {path!r} must be repo-relative")
    first = parts[0]
    if ":" in first:
        raise ImprovementBindingError(f"{label} {path!r} must be repo-relative")
    return "/".join(parts)


def _validate_binding_payload(binding: Mapping[str, Any]) -> dict[str, Any]:
    """Fail-closed validation of a complete execution binding."""
    payload = dict(binding)
    payload["improvement_id"] = _require_text(payload.get("improvement_id"), "improvement_id")
    payload["resource_id"] = _require_text(payload.get("resource_id"), "resource_id")
    sha = _require_text(payload.get("baseline_sha"), "baseline_sha").lower()
    if not _SHA40.fullmatch(sha):
        raise ImprovementBindingError("baseline_sha must be a full 40-character Git SHA")
    payload["baseline_sha"] = sha
    allowed = [_canonical_relative(path, "allowed_paths item") for path in _require_list(
        payload.get("allowed_paths"), "allowed_paths")]
    forbidden = [_canonical_relative(path, "forbidden_paths item") for path in (
        payload.get("forbidden_paths") or ())]
    if set(allowed) & set(forbidden):
        raise ImprovementBindingError("allowed_paths and forbidden_paths must not overlap")
    payload["allowed_paths"] = allowed
    payload["forbidden_paths"] = forbidden
    payload["workload_objective"] = _require_text(
        payload.get("workload_objective"), "workload_objective")
    payload["required_tests"] = _require_list(payload.get("required_tests"), "required_tests")
    risk = _require_text(payload.get("risk_class"), "risk_class").upper()
    if risk not in RISK_TO_WORKFORCE:
        raise ImprovementBindingError(
            f"risk_class {risk!r} must be one of {sorted(RISK_TO_WORKFORCE)}")
    payload["risk_class"] = risk
    payload["evidence_refs"] = _require_list(payload.get("evidence_refs"), "evidence_refs")
    payload["measurement_ref"] = _require_text(payload.get("measurement_ref"), "measurement_ref")
    payload["success_criterion"] = _require_text(
        payload.get("success_criterion"), "success_criterion")
    return payload


def _git(root: Path, *args: str) -> str:
    try:
        from ..platform.windows_subprocess import headless_run_kwargs
        extra: dict[str, Any] = headless_run_kwargs()
    except Exception:  # noqa: BLE001 - portable fallback outside the managed runtime
        extra = {}
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=False, **extra,
    )
    if result.returncode != 0:
        raise ImprovementBindingError(
            f"git {' '.join(args)} failed: "
            f"{(result.stderr or result.stdout).strip()[:200]}"
        )
    return result.stdout.strip()


def record_execution_binding(
    database: FoundryDatabase,
    *,
    improvement_id: str,
    resource_id: str,
    baseline_sha: str,
    allowed_paths: list[str] | tuple[str, ...],
    forbidden_paths: list[str] | tuple[str, ...] = (),
    workload_objective: str,
    required_tests: list[str] | tuple[str, ...],
    risk_class: str,
    evidence_refs: list[str] | tuple[str, ...],
    measurement_ref: str,
    success_criterion: str,
    source_type: str = "supervisor",
    source_id: str = "v6-binding",
) -> dict[str, Any]:
    """Freeze one execution binding for an improvement (append-only).

    Fails closed on any missing/invalid field, on an unknown improvement,
    or on an unregistered resource.  Returns the recorded binding payload.
    """
    payload = _validate_binding_payload({
        "improvement_id": improvement_id,
        "resource_id": resource_id,
        "baseline_sha": baseline_sha,
        "allowed_paths": list(allowed_paths),
        "forbidden_paths": list(forbidden_paths or ()),
        "workload_objective": workload_objective,
        "required_tests": list(required_tests),
        "risk_class": risk_class,
        "evidence_refs": list(evidence_refs),
        "measurement_ref": measurement_ref,
        "success_criterion": success_criterion,
    })
    if database.row("improvement_records", "improvement_id", payload["improvement_id"]) is None:
        raise ImprovementBindingError(
            f"improvement {payload['improvement_id']!r} is unknown: "
            "a binding needs a recorded Improvement"
        )
    if database.row("resources", "resource_id", payload["resource_id"]) is None:
        raise ImprovementBindingError(
            f"resource {payload['resource_id']!r} is not registered: "
            "a binding needs a registered repository/resource"
        )
    content_key = f"{BINDING_RECORDED_EVENT}:{payload['improvement_id']}:{payload_hash(payload)}"
    latest = get_execution_binding(database, payload["improvement_id"])
    if latest is not None:
        prior = {key: value for key, value in latest.items() if key != "recorded_at"}
        if prior == payload:
            return latest
    payload["recorded_at"] = isoformat()
    database.append(Event.create(
        BINDING_RECORDED_EVENT, payload,
        source_type=source_type, source_id=source_id, idempotency_key=content_key,
    ))
    return payload


def get_execution_binding(
    database: FoundryDatabase, improvement_id: str,
) -> dict[str, Any] | None:
    """Return the latest recorded binding for an improvement, if any."""
    wanted = str(improvement_id or "").strip()
    if not wanted:
        return None
    latest: dict[str, Any] | None = None
    for event in database.events(event_types=(BINDING_RECORDED_EVENT,), readonly=True):
        if str(event.payload.get("improvement_id") or "") != wanted:
            continue
        latest = dict(event.payload)
    return latest


def verify_binding_for_execution(
    database: FoundryDatabase, improvement_id: str,
) -> dict[str, Any]:
    """Fresh verification of a binding against current registry + repository truth.

    A recorded binding is durable intent; this is the execution-time check
    that the authority it names still holds.  Anything stale or unverifiable
    fails closed.
    """
    binding = get_execution_binding(database, improvement_id)
    if binding is None:
        raise ImprovementBindingError(
            f"improvement {improvement_id!r} carries no execution binding"
        )
    payload = _validate_binding_payload(binding)
    if database.row("improvement_records", "improvement_id", payload["improvement_id"]) is None:
        raise ImprovementBindingError("bound improvement is no longer recorded")
    resource = database.row("resources", "resource_id", payload["resource_id"])
    if resource is None:
        raise ImprovementBindingError(
            f"bound resource {payload['resource_id']!r} is no longer registered"
        )
    root = Path(str(resource["resolved_root"])).resolve()
    if not root.is_dir():
        raise ImprovementBindingError(
            f"bound resource root {root} is not reachable"
        )
    try:
        capabilities = json.loads(str(resource.get("capabilities_json") or "{}"))
    except Exception:  # noqa: BLE001 - a corrupt map is no authority
        raise ImprovementBindingError("bound resource capability map is unreadable")
    if not isinstance(capabilities, dict) or EXECUTE_CAPABILITY not in capabilities:
        raise ImprovementBindingError(
            f"bound resource lacks the {EXECUTE_CAPABILITY!r} capability"
        )
    head = _git(root, "rev-parse", "HEAD").lower()
    if head != payload["baseline_sha"]:
        raise ImprovementBindingError(
            f"resource HEAD {head[:12]}... is not the pinned baseline "
            f"{payload['baseline_sha'][:12]}...: re-pin before executing"
        )
    _git(root, "cat-file", "-e", f"{payload['baseline_sha']}^{{commit}}")
    for path in payload["allowed_paths"]:
        resolved = (root / Path(*path.split("/"))).resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            raise ImprovementBindingError(
                f"allowed path {path!r} escapes the registered resource root"
            )
    measurement = str(payload["measurement_ref"])
    if measurement != "improvement-baseline":
        rows = database.all("evaluation_runs", run_id=measurement)
        if not rows:
            raise ImprovementBindingError(
                f"measurement_ref {measurement!r} names no evaluation run "
                "(use 'improvement-baseline' for the record's own baseline metric)"
            )
    verified = dict(payload)
    verified["workforce_risk_class"] = map_risk_class(payload["risk_class"])
    verified["resource_root"] = str(root)
    verified["execute_capability_state"] = str(capabilities.get(EXECUTE_CAPABILITY) or "")
    return verified


def record_binding_consumed(
    database: FoundryDatabase,
    *,
    improvement_id: str,
    mission_id: str,
    decision_id: str,
    binding_event_id: str = "",
    source_type: str = "supervisor",
    source_id: str = "v6-binding",
) -> None:
    """Link a bound improvement to the mission materialized from it."""
    database.append(Event.create(
        BINDING_CONSUMED_EVENT,
        {
            "improvement_id": _require_text(improvement_id, "improvement_id"),
            "mission_id": _require_text(mission_id, "mission_id"),
            "decision_id": _require_text(decision_id, "decision_id"),
            "binding_event_id": str(binding_event_id or ""),
            "consumed_at": isoformat(),
        },
        source_type=source_type, source_id=source_id,
        idempotency_key=f"{BINDING_CONSUMED_EVENT}:{improvement_id}:{mission_id}",
    ))


def _bounded_instructions(binding: Mapping[str, Any], *, step: str) -> str:
    allowed = "\n".join(f"- {path}" for path in binding["allowed_paths"])
    forbidden = "\n".join(f"- {path}" for path in binding["forbidden_paths"]) or "- (none)"
    tests = "\n".join(f"- {test}" for test in binding["required_tests"])
    evidence = "\n".join(f"- {ref}" for ref in binding["evidence_refs"])
    if step == "plan":
        return (
            f"Bound improvement {binding['improvement_id']} (baseline "
            f"{binding['baseline_sha']}).\n"
            f"Produce the bounded change plan for this objective:\n{binding['workload_objective']}\n"
            f"Scope is EXACTLY these repo-relative paths:\n{allowed}\n"
            f"Forbidden (never touch):\n{forbidden}\n"
            f"The plan must name these required tests and where their evidence goes:\n{tests}\n"
            "Record the plan as a worktree file named exactly 01-plan.md at "
            "the resource root (this name is tolerated evidence, never "
            "committed). Do not edit files in this step and do not create "
            "any other file; record the plan so 02-execute can follow it."
        )
    return (
        f"Bound improvement {binding['improvement_id']} (baseline "
        f"{binding['baseline_sha']}).\n"
        f"Implement EXACTLY this objective:\n{binding['workload_objective']}\n"
        f"Change ONLY these repo-relative paths:\n{allowed}\n"
        f"Forbidden (never touch, fail the step rather than widen scope):\n{forbidden}\n"
        f"Then run these required tests and keep their evidence:\n{tests}\n"
        f"Evidence references:\n{evidence}\n"
        f"Success criterion: {binding['success_criterion']}"
    )


def materialize_bound_improvement(
    database: FoundryDatabase,
    *,
    item_id: str,
    decision_id: str,
    improvement_id: str,
    objective: str,
    source_id: str = "v6-improvement-binding",
) -> dict[str, Any]:
    """Materialize a verified bound improvement as mission + step contracts.

    Mirrors the owner-goal materialization shape (mission.created, ordered
    immutable contracts, portfolio.selection_materialized) with binding
    content: 01-plan and 02-execute both under the write capability so the worker bridge routes both steps to model-backed workers,
    matching the proven owner-goal contract shape, both scoped to the bound paths.  Execution, verification,
    and recovery stay on the canonical MissionLoop; nothing here dispatches
    work or invents worker authority.
    """
    from ..mission.contracts import (
        MissionStepContract,
        MissionStepContractError,
        record_step_contract,
    )

    for event in reversed(database.events(
            event_types=("portfolio.selection_materialized",), readonly=True)):
        if str(event.payload.get("decision_id") or "") == decision_id:
            prior = str(event.payload.get("mission_id") or "")
            if prior:
                return {"materialized": True, "mission_id": prior,
                        "blocked_reason": None}
    binding = verify_binding_for_execution(database, improvement_id)
    if improvement_id not in str(item_id or ""):
        raise ImprovementBindingError(
            "portfolio item carries no improvement lineage for "
            f"{improvement_id}: the commit seam resolves scope from mission "
            "lineage, so an unlinkable item would commit unbounded"
        )
    # Promote the commit capability BEFORE any mission write: a dirty or
    # off-baseline worktree refuses here with nothing created and no
    # orphan mission left behind.
    ensure_commit_capability(
        database, resource_id=str(binding["resource_id"]),
        baseline_sha=str(binding["baseline_sha"]),
        improvement_id=improvement_id,
        binding_id=f"v6-binding:{improvement_id}",
    )
    resource_id = str(binding["resource_id"])
    risk_class = str(binding["workforce_risk_class"])
    primary_target = str(binding["allowed_paths"][0])
    forbidden = tuple(binding["forbidden_paths"])
    tests = tuple(binding["required_tests"])
    success = str(binding["success_criterion"])

    mission_id = f"mission-{uuid.uuid4().hex[:12]}"
    provenance = {
        "decision_id": decision_id,
        "portfolio_item_id": item_id,
        "portfolio_class": "IMPROVEMENT",
        "improvement_id": improvement_id,
        "baseline_sha": binding["baseline_sha"],
        "measurement_ref": binding["measurement_ref"],
    }
    # Construct both contracts BEFORE any ledger write: constructor
    # validation is pure, so binding content that cannot become contracts
    # fails here with nothing created and no orphan mission left behind.
    try:
        plan = MissionStepContract(
            mission_id=mission_id,
            step_id="01-plan",
            version=1,
            resource_id=resource_id,
            capability=EXECUTE_CAPABILITY,
            target_relative_path=primary_target,
            instructions=_bounded_instructions(binding, step="plan"),
            expected_effects=(
                "bounded change plan recorded for the bound improvement",
                "plan names the required tests and their evidence",
            ),
            rollback={"method": "no-op: the planning step performs no writes",
                      "reversible": True},
            risk_class=risk_class,
            verification_criteria=(
                "bounded plan covers exactly the bound scope",
                f"plan names required tests: {', '.join(tests)}",
            ),
            expected_artifacts=(),
            forbidden_paths=forbidden,
            recorded_by="supervisor",
            source="v6-improvement-binding",
            provenance={**provenance, "step": "plan"},
        )
        execute = MissionStepContract(
            mission_id=mission_id,
            step_id="02-execute",
            version=1,
            resource_id=resource_id,
            capability=EXECUTE_CAPABILITY,
            target_relative_path=primary_target,
            instructions=_bounded_instructions(binding, step="execute"),
            expected_effects=(
                "bounded change applied inside the bound scope",
                "required tests executed with evidence",
            ),
            rollback={"method": "revert the improvement commit in the isolated worktree",
                      "reversible": True},
            risk_class=risk_class,
            verification_criteria=(
                success,
                f"required tests executed with evidence: {', '.join(tests)}",
            ),
            expected_artifacts=(),
            forbidden_paths=forbidden,
            recorded_by="supervisor",
            source="v6-improvement-binding",
            provenance={**provenance, "step": "execute"},
        )
    except MissionStepContractError as error:
        raise ImprovementBindingError(
            f"binding content cannot become step contracts: {error}"
        ) from error

    from ..evolution.executive_portfolio import register_work_item
    try:
        register_work_item(
            database,
            item_id=item_id,
            kind="IMPROVEMENT",
            project_id=resource_id,
            objective=objective,
            expected_value=None,
            urgency=None,
            confidence=None,
            reversibility=None,
            requires_verifier=True,
            evidence_refs=tuple(binding["evidence_refs"]),
            provenance={"improvement_id": improvement_id},
            metadata={"improvement_id": improvement_id},
            source_type="supervisor",
            source_id=source_id,
        )
    except Exception:  # noqa: BLE001 - mirror failure is recorded, never fatal here
        pass

    acceptance = [
        f"{objective} completed within declared capacity/budget",
        "independent verification PASS or honest BLOCKED with evidence",
    ]
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": objective,
            "resource_id": resource_id,
            "state": "PLANNED",
            "acceptance_criteria": acceptance,
            "next_action": f"execute: {objective}",
            "created_from": f"portfolio:{item_id}",
            "portfolio_item_id": item_id,
            "evidence_refs": list(binding["evidence_refs"]),
            "provenance": {
                "improvement_id": improvement_id,
                "baseline_sha": binding["baseline_sha"],
                "measurement_ref": binding["measurement_ref"],
            },
        },
        source_type="supervisor",
        source_id=source_id,
        correlation_id=resource_id,
        idempotency_key=f"mission.created:portfolio:{item_id}:{decision_id}",
    ))
    for state, next_action in (
        ("ORIENTING", f"orient: {objective}"),
        ("ACTION_SELECTION", f"execute: {objective}"),
        ("READY", f"execute: {objective}"),
    ):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": next_action},
            source_type="supervisor",
            source_id=source_id,
            idempotency_key=f"mission.state_changed:{mission_id}:{state}:portfolio",
        ))
    # Contracts were constructed (and validated) before the first ledger
    # write above.  Recording them can still refuse on database binding;
    # that failure converts to a closed refusal, never a crash.
    try:
        record_step_contract(database, plan)
        record_step_contract(database, execute)
    except MissionStepContractError as error:
        raise ImprovementBindingError(
            f"bound contracts refused by the ledger: {error}"
        ) from error
    database.append(Event.create(
        "portfolio.selection_materialized",
        {
            "decision_id": decision_id,
            "item_id": item_id,
            "kind": "IMPROVEMENT",
            "mission_id": mission_id,
            "contract_ids": [plan.contract_id, execute.contract_id],
            "improvement_id": improvement_id,
            "resolution": "v6-bound-improvement",
        },
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"portfolio.selection_materialized:{decision_id}",
    ))
    record_binding_consumed(
        database, improvement_id=improvement_id, mission_id=mission_id,
        decision_id=decision_id, source_id=source_id,
    )
    return {"materialized": True, "mission_id": mission_id, "blocked_reason": None}


def _porcelain_path(line: str) -> str:
    """Extract the worktree-relative path from a porcelain v1 status line."""
    body = line.strip()
    if len(body) > 3 and body[2] == " ":
        body = body[3:].strip()
    if " -> " in body:  # renames report old -> new; the new path matters
        body = body.split(" -> ", 1)[1]
    return body.strip().strip('"')


def ensure_commit_capability(
    database: FoundryDatabase,
    *,
    resource_id: str,
    baseline_sha: str,
    improvement_id: str,
    binding_id: str = "",
) -> dict[str, Any]:
    """Promote ``git_local_commit`` to READY for a bound resource.

    This is the autonomous-packaging gate the V6 proof lacked: the loop's
    ``commit_after_pass`` only fires when the resource carries a READY
    commit capability, and registration leaves it PROBING.  Promotion
    happens here — after the binding is verified and the mission is
    materialized — and fails closed when the worktree is not a clean git
    checkout exactly at the pinned baseline.  Evidence names the
    improvement so a later audit can tell JARVIS-owned packaging from an
    operator's manual commit.
    """
    from ..control import capabilities as _capabilities

    resource = database.row("resources", "resource_id", resource_id)
    if resource is None:
        raise ImprovementBindingError(
            f"resource {resource_id!r} is not registered: "
            "no commit capability without a registered repository"
        )
    root = Path(str(resource["resolved_root"])).resolve()
    try:
        head = _git(root, "rev-parse", "HEAD").lower()
    except ImprovementBindingError as error:
        raise ImprovementBindingError(
            f"resource root {root} is not a git checkout: {error}"
        ) from error
    if head != str(baseline_sha or "").lower():
        raise ImprovementBindingError(
            f"resource HEAD {head[:12]}... is not the pinned baseline "
            f"{str(baseline_sha)[:12]}...: no commit capability off-baseline"
        )
    if _git(root, "status", "--porcelain=v1").strip():
        dirty = [
            line for line in
            _git(root, "status", "--porcelain=v1").splitlines()
            if line.strip() and not scope_ignored(_porcelain_path(line))
        ]
        if dirty:
            raise ImprovementBindingError(
                "resource worktree is dirty at materialization: no commit "
                "capability until the baseline is clean"
            )
    event = _capabilities.set_capability_state(
        database, resource_id, COMMIT_CAPABILITY, "READY",
        source_id=binding_id or "v6-binding",
        evidence={
            "improvement_id": improvement_id,
            "baseline_sha": str(baseline_sha).lower(),
            "head": head,
            "clean": True,
        },
    )
    return {"resource_id": resource_id, "head": head, "state": "READY",
            "event_id": event.event_id}


def commit_scope_for_mission(
    database: FoundryDatabase, mission_id: str,
) -> dict[str, Any] | None:
    """Return the bound commit scope governing a mission, if it has one.

    ``None`` means the mission carries no improvement lineage and keeps
    the legacy unbounded commit path.  An improvement-lineage mission
    whose binding cannot be resolved fails closed — committing it
    unbounded would silently exceed the authorized scope.
    """
    mission = database.row("missions", "mission_id", mission_id)
    if mission is None:
        return None
    created_from = str(mission.get("created_from") or "")
    match = _IMPROVEMENT_REF.search(created_from)
    if match is None:
        return None
    improvement_id = match.group(0)
    binding = get_execution_binding(database, improvement_id)
    if binding is None:
        raise ImprovementBindingError(
            f"mission {mission_id} descends from {improvement_id} but no "
            "execution binding resolves: refusing an unbounded commit"
        )
    return {
        "improvement_id": improvement_id,
        "resource_id": str(binding["resource_id"]),
        "baseline_sha": str(binding["baseline_sha"]),
        "allowed_paths": tuple(binding["allowed_paths"]),
    }


def check_commit_scope(
    allowed_paths: tuple[str, ...] | list[str], changed_paths: set[str],
) -> tuple[str, ...]:
    """Return the changed paths that fall outside the allowed scope."""
    allowed = {_canonical_relative(path, "allowed_paths item") for path in allowed_paths}
    return tuple(sorted(path for path in changed_paths if path not in allowed))


def record_candidate(
    database: FoundryDatabase,
    *,
    mission_id: str,
    step_id: str,
    run_id: str,
    verdict_id: str,
    resource_id: str,
    improvement_id: str,
    baseline_sha: str,
    commit_head: str,
    tree_sha: str,
    worktree: str,
    files_changed: list[str],
) -> dict[str, Any]:
    """Persist the JARVIS-owned candidate envelope for a committed step.

    Idempotent per (mission, step, commit): replaying the same packaging
    step replays the recorded envelope instead of minting a duplicate
    candidate.  The payload carries no volatile fields, so a byte-identical
    retry dedupes at the ledger layer.
    """
    candidate_id = f"candidate:{mission_id}:{step_id}:{commit_head[:12]}"
    payload = {
        "candidate_id": candidate_id,
        "mission_id": _require_text(mission_id, "mission_id"),
        "step_id": _require_text(step_id, "step_id"),
        "producer_run_id": _require_text(run_id, "run_id"),
        "verdict_id": _require_text(verdict_id, "verdict_id"),
        "resource_id": _require_text(resource_id, "resource_id"),
        "improvement_id": _require_text(improvement_id, "improvement_id"),
        "baseline_sha": _require_text(baseline_sha, "baseline_sha"),
        "commit_head": _require_text(commit_head, "commit_head"),
        "tree_sha": _require_text(tree_sha, "tree_sha"),
        "worktree": _require_text(worktree, "worktree"),
        "files_changed": list(files_changed),
        "state": "RECORDED",
    }
    content_key = f"{CANDIDATE_RECORDED_EVENT}:{mission_id}:{step_id}:{commit_head}"
    for event in database.events(event_types=(CANDIDATE_RECORDED_EVENT,), readonly=True):
        if str(event.payload.get("candidate_id") or "") != candidate_id:
            continue
        prior = {key: value for key, value in event.payload.items()}
        if prior == payload:
            return {"candidate_id": candidate_id, "deduplicated": True,
                    "event_id": event.event_id}
        raise ImprovementBindingError(
            f"candidate {candidate_id} already recorded with different "
            "content: refusing a conflicting envelope"
        )
    event = Event.create(
        CANDIDATE_RECORDED_EVENT, payload,
        source_type="supervisor", source_id=f"v6-candidate:{run_id}",
        correlation_id=mission_id, idempotency_key=content_key,
    )
    database.append(event)
    return {"candidate_id": candidate_id, "deduplicated": False,
            "event_id": event.event_id}
