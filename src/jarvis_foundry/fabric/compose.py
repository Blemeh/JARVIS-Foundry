"""Generic multi-capability composition.

Workflows are sequences of fabric actions. Later payloads bind to earlier
observations with ``$step.field`` references. Combinations stay data; this
module does not hardcode research-to-commit or diagnose-to-repair paths.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from ..database import FoundryDatabase
from ..world_model import WorldModel
from .catalog import catalog_snapshot
from .executors import ExecutorContext
from .loop import execute_action, seed_builtins, try_acquire
from .model import (
    ClaimOutcome,
    FabricError,
    TaskIntent,
    WorkflowStep,
)
from .providers import route_provider


def lookup(path: str, context: Mapping[str, Any]) -> Any:
    current: Any = context
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise FabricError(f"workflow binding ${path} is missing")
        current = current[part]
    return current


_EMBEDDED_BINDING = __import__("re").compile(r"\$\{([A-Za-z0-9_.:/-]+)\}")
_ESCAPED_EMBEDDED = "\x00ESCAPED_BINDING\x00"


def _render_embedded(bound: Any) -> str:
    if isinstance(bound, str):
        return bound
    return json.dumps(bound, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def bind_value(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        if value.startswith("$$") and "${" not in value:
            return value
        if value.startswith("$") and not value.startswith("$$") and "${" not in value:
            return lookup(value[1:], context)
        if value.startswith("${") and value.endswith("}") and value.count("${") == 1:
            return lookup(value[2:-1], context)
        if "${" in value:
            sentinel = _ESCAPED_EMBEDDED
            working = value.replace("$${", sentinel)
            def _replace(match: Any) -> str:
                return _render_embedded(lookup(match.group(1), context))
            rendered = _EMBEDDED_BINDING.sub(_replace, working)
            return rendered.replace(sentinel, "${")
        return value
    if isinstance(value, Mapping):
        return {str(key): bind_value(item, context) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [bind_value(item, context) for item in value]
    return value


def bind_payload(payload: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    return bind_value(dict(payload), context)


def _write_checkpoint_file(path: Any, payload: Mapping[str, Any]) -> None:
    from pathlib import Path as _Path

    target = _Path(str(path)).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    tmp.replace(target)


def run_workflow(
    database: FoundryDatabase,
    ctx: ExecutorContext,
    intent: TaskIntent,
    steps: tuple[WorkflowStep, ...] | list[WorkflowStep],
    *,
    workflow_id: str | None = None,
    write_world_model: bool = True,
    resume_checkpoint: Mapping[str, Any] | None = None,
    skip_completed: bool = True,
    checkpoint_path: Any = None,
) -> dict[str, Any]:
    """Execute ordered steps, binding each payload against prior observations.

    ``resume_checkpoint`` is a prior checkpoint mapping as produced by
    :mod:`jarvis_foundry.fabric.recipes.checkpoint` (``{"steps": {...}}``).
    Verified-completed steps are skipped without redispatch so a resumed
    workflow never replays an already-completed irreversible effect; their
    verified observations are restored into the binding context.  When
    ``checkpoint_path`` is given, a deterministic checkpoint is written
    after every step (atomic tmp+rename).
    """
    seed_builtins(database)
    ordered = tuple(steps)
    if not ordered:
        raise FabricError("workflow requires at least one step")
    ids = [step.step_id for step in ordered]
    if len(ids) != len(set(ids)):
        raise FabricError("workflow step_id values must be unique")
    resolved_workflow_id = str(workflow_id or f"workflow:{intent.task_id}")
    # ``context`` is the diagnostic record returned to the caller and keeps
    # truthful failed observations (for example a test return code).  Only
    # ``binding_context`` is executable composition input; it receives a
    # step after independent verification succeeds.
    context: dict[str, Any] = {}
    binding_context: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    stopped_at: str | None = None
    soft_failed: list[str] = []
    resumed_steps: list[str] = []
    checkpoint_steps: dict[str, Any] = {}
    if isinstance(resume_checkpoint, Mapping):
        raw_steps = resume_checkpoint.get("steps")
        if isinstance(raw_steps, Mapping):
            for key, value in raw_steps.items():
                if isinstance(value, Mapping):
                    checkpoint_steps[str(key)] = dict(value)
    def _emit_checkpoint(current_step: str | None) -> None:
        if checkpoint_path is None:
            return
        _write_checkpoint_file(checkpoint_path, {
            "workflow_id": resolved_workflow_id,
            "task_id": intent.task_id,
            "current_step": current_step,
            "completed": sorted(binding_context.keys()),
            "steps": {
                step_id: dict(record)
                for step_id, record in checkpoint_steps.items()
            },
            "stopped_at": stopped_at,
        })

    for step in ordered:
        prior = checkpoint_steps.get(step.step_id)
        if skip_completed and prior is not None and prior.get("verified") == "MATCH" and prior.get("claimed") == "COMPLETED":
            observed = dict(prior.get("observed") or {})
            receipt = dict(prior.get("receipt") or {})
            context[step.step_id] = observed
            binding_context[step.step_id] = observed
            resumed_row: dict[str, Any] = {
                "capability_id": str(prior.get("capability_id") or "resumed"),
                "action_kind": step.action_kind,
                "claimed": "COMPLETED",
                "verified": "MATCH",
                "observed": observed,
                "claimed_effect": {},
                "note": "resumed without replay",
                "receipt": receipt,
                "step_id": step.step_id,
                "resumed": True,
                "attempts": int(prior.get("attempts") or 1),
            }
            results.append(resumed_row)
            resumed_steps.append(step.step_id)
            continue
        payload = bind_payload(step.payload, binding_context)
        if step.postconditions:
            payload["postconditions"] = list(step.postconditions)
        requested_attempt = payload.get("attempt", 1)
        attempt = requested_attempt if isinstance(requested_attempt, int) and requested_attempt > 0 else 1
        attempts_used = 1
        result = execute_action(
            database,
            ctx,
            intent,
            step.action_kind,
            payload,
            workflow_id=resolved_workflow_id,
            step_id=step.step_id,
            step_version=step.step_version,
            attempt=attempt,
        )
        if result.claimed is ClaimOutcome.BLOCKED and result.capability_id == "missing":
            acquired = try_acquire(database, ctx, step.action_kind)
            if acquired.get("status") == "REGISTERED":
                result = execute_action(
                    database,
                    ctx,
                    intent,
                    step.action_kind,
                    payload,
                    workflow_id=resolved_workflow_id,
                    step_id=step.step_id,
                    step_version=step.step_version,
                    attempt=attempt,
                )
        if step.on_failure == "retry" and not result.success:
            attempts_used = 2
            result = execute_action(
                database,
                ctx,
                intent,
                step.action_kind,
                payload,
                workflow_id=resolved_workflow_id,
                step_id=step.step_id,
                step_version=step.step_version,
                attempt=attempt + 1,
            )
        row = result.as_dict()
        row["step_id"] = step.step_id
        row["resumed"] = False
        row["attempts"] = attempts_used
        if step.on_failure in {"soft", "optional"}:
            row["soft"] = True
        results.append(row)
        context[step.step_id] = dict(result.observed)
        checkpoint_steps[step.step_id] = {
            "step_id": step.step_id,
            "action_kind": step.action_kind,
            "claimed": result.claimed.value,
            "verified": result.verified.value,
            "observed": dict(result.observed),
            "receipt": dict(row.get("receipt") or {}),
            "effect_id": str((row.get("receipt") or {}).get("effect_id") or ""),
            "attempts": attempts_used,
        }
        _emit_checkpoint(step.step_id)
        if result.success:
            # Only independently verified observations become composition
            # inputs. Claims, blocked results, and ambiguous outcomes cannot
            # silently drive a later effect.
            binding_context[step.step_id] = dict(result.observed)
            continue
        if step.on_failure in {"continue", "soft", "optional"}:
            if step.on_failure in {"soft", "optional"}:
                soft_failed.append(step.step_id)
            continue
        stopped_at = step.step_id
        break
    success = (
        stopped_at is None
        and all(
            item["claimed"] == "COMPLETED" and item["verified"] == "MATCH"
            for item in results
        )
        and len(results) == len(ordered)
    )
    if success:
        outcome = "PASS"
    elif any(item["claimed"] == "BLOCKED" for item in results):
        outcome = "BLOCKED"
    elif any(
        (item.get("receipt") or {}).get("state") in {"FROZEN", "UNKNOWN"}
        or item.get("verified") == "UNKNOWN"
        for item in results
    ):
        outcome = "UNKNOWN"
    else:
        outcome = "FAILED"

    world_report = None
    if write_world_model:
        safe_receipts = [
            dict(item["receipt"])
            for item in results
            if isinstance(item.get("receipt"), Mapping)
        ]
        evidence_refs = tuple(
            str(receipt["receipt_id"])
            for receipt in safe_receipts
            if receipt.get("receipt_id")
        )
        verified_terminal = bool(results) and all(
            item.get("verified") in {"MATCH", "MISMATCH"}
            and item.get("claimed") != "BLOCKED"
            and (item.get("receipt") or {}).get("state") not in {"FROZEN", "UNKNOWN"}
            for item in results
        )
        if verified_terminal:
            # Fabric verification is authoritative for the observed workflow,
            # but it is not an AcceptanceGate decision for a mission. Preserve
            # the V10 world observation without promoting it into terminal
            # mission learning or Executive calibration.
            observation_identity = hashlib.sha256(
                json.dumps(
                    {
                        "workflow_id": resolved_workflow_id,
                        "outcome": outcome,
                        "evidence_refs": evidence_refs,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            observation = WorldModel(database).observe(
                "fabric:verified_workflow",
                resolved_workflow_id,
                {
                    "task_id": intent.task_id,
                    "workflow_id": resolved_workflow_id,
                    "outcome": outcome,
                    "terminal": len(results) == len(ordered) or stopped_at is not None,
                    "verified_success": success,
                    "receipts": safe_receipts,
                    "evidence_refs": list(evidence_refs),
                    "mission_terminal_truth": False,
                },
                provenance={
                    "authority": "fabric-independent-verification",
                    "evidence_refs": list(evidence_refs),
                    "mission_terminal_truth": False,
                },
                source_id="v10-fabric-workflow",
                observation_id=f"fabric-workflow:{observation_identity}",
                idempotency_key=(
                    f"world.observation_recorded:fabric-workflow:{observation_identity}"
                ),
            )
            world_report = {
                "status": "OBSERVED",
                "outcome": outcome,
                "verified_success": success,
                "mission_terminal_truth": False,
                "observation": observation,
                "deduplicated": bool(observation.get("deduplicated")),
            }
            observation_id = observation.get("observation_id")
            if observation_id:
                for item in results:
                    receipt = item.get("receipt")
                    if isinstance(receipt, dict):
                        receipt["world_model_refs"] = [str(observation_id)]
        else:
            world_report = {
                "status": "NOT_EMITTED",
                "reason": "terminal outcome is not independently verified",
                "outcome": outcome,
                "verified_success": False,
            }

    checkpoint = {
        "workflow_id": resolved_workflow_id,
        "task_id": intent.task_id,
        "current_step": results[-1]["step_id"] if results else None,
        "completed": sorted(binding_context.keys()),
        "steps": checkpoint_steps,
        "stopped_at": stopped_at,
        "resumed_steps": list(resumed_steps),
        "soft_failed_steps": list(soft_failed),
    }
    _emit_checkpoint(checkpoint.get("current_step"))
    return {
        "task_id": intent.task_id,
        "workflow_id": resolved_workflow_id,
        "success": success,
        "outcome": outcome,
        "results": results,
        "context": context,
        "verified_context": binding_context,
        "stopped_at": stopped_at,
        "soft_failed_steps": list(soft_failed),
        "resumed_steps": list(resumed_steps),
        "checkpoint": checkpoint,
        "catalog": catalog_snapshot(database),
        "provider": route_provider(unattended=intent.unattended).as_dict(),
        "world_report": world_report,
        "receipts": [item["receipt"] for item in results if item.get("receipt")],
    }
