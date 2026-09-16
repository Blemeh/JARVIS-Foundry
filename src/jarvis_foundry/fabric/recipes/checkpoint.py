"""Workflow checkpoint/resume without a second database.

A checkpoint is a deterministic JSON sidecar derived from verified step
results plus their canonical effect ids. Resume never replays a
verified-completed step: ``compose.run_workflow`` skips those steps and
restores their verified observations into the binding context. Ambiguous
or failed steps re-execute, and the canonical effect journal reconciles
any already-journaled mutation instead of duplicating it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def build_checkpoint(result: Mapping[str, Any]) -> dict[str, Any]:
    checkpoint = result.get("checkpoint")
    if isinstance(checkpoint, Mapping):
        return json.loads(json.dumps(dict(checkpoint), sort_keys=True))
    steps: dict[str, Any] = {}
    for row in result.get("results") or ():
        if not isinstance(row, Mapping):
            continue
        step_id = str(row.get("step_id") or "")
        if not step_id:
            continue
        receipt = dict(row.get("receipt") or {})
        steps[step_id] = {
            "step_id": step_id,
            "action_kind": str(row.get("action_kind") or ""),
            "claimed": str(row.get("claimed") or ""),
            "verified": str(row.get("verified") or ""),
            "observed": dict(row.get("observed") or {}),
            "receipt": receipt,
            "effect_id": str(receipt.get("effect_id") or ""),
            "attempts": int(row.get("attempts") or 1),
        }
    return {
        "workflow_id": str(result.get("workflow_id") or ""),
        "task_id": str(result.get("task_id") or ""),
        "current_step": str((result.get("results") or [{}])[-1].get("step_id") or "") if result.get("results") else None,
        "completed": sorted(str(key) for key, value in steps.items() if value.get("verified") == "MATCH" and value.get("claimed") == "COMPLETED"),
        "steps": steps,
        "stopped_at": result.get("stopped_at"),
        "resumed_steps": list(result.get("resumed_steps") or ()),
        "soft_failed_steps": list(result.get("soft_failed_steps") or ()),
    }


def save_checkpoint(checkpoint: Mapping[str, Any], path: str | Path) -> Path:
    target = Path(str(path)).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(checkpoint), ensure_ascii=True, sort_keys=True, indent=2)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(target)
    return target


def load_checkpoint(path: str | Path) -> dict[str, Any]:
    target = Path(str(path)).expanduser()
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("checkpoint file must contain a JSON object")
    steps = raw.get("steps")
    if not isinstance(steps, Mapping):
        raise ValueError("checkpoint file is missing steps")
    return dict(raw)


def summarize_checkpoint(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    steps = checkpoint.get("steps") if isinstance(checkpoint.get("steps"), Mapping) else {}
    completed = [key for key, value in steps.items() if isinstance(value, Mapping) and value.get("claimed") == "COMPLETED" and value.get("verified") == "MATCH"]
    effect_ids = [str(value.get("effect_id")) for value in steps.values() if isinstance(value, Mapping) and value.get("effect_id")]
    return {
        "workflow_id": checkpoint.get("workflow_id"),
        "current_step": checkpoint.get("current_step"),
        "completed": sorted(completed),
        "terminal": checkpoint.get("stopped_at"),
        "effect_ids": effect_ids,
        "step_count": len(steps),
    }
