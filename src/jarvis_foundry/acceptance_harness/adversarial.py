"""Negative controls that must prevent a forged PASS verdict."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import FaultRecord, InvariantRecord, ResourceEnvelope, ResourceSample, ScenarioSpec, ClockMode
from .verdict import derive_verdict


def _scenario(*, with_fault: bool = False) -> ScenarioSpec:
    from .contracts import FaultSpec
    return ScenarioSpec(
        scenario_id="FALSE_PASS_FACTORY",
        duration_seconds=1.0,
        clock_mode=ClockMode.ACCELERATED_DISPOSABLE,
        seed=22,
        initial_state={"stop_available": True},
        required_capabilities=("supervisor",),
        scheduled_faults=(FaultSpec("required-fault", 0.0, "PROCESS_CRASH", "owned-worker"),) if with_fault else (),
        expected_invariants=("STOP_AVAILABLE", "HEARTBEAT_CONTINUITY", "EVIDENCE_COMPLETE"),
        resource_envelope=ResourceEnvelope(),
        owner_spam_threshold=2,
        busywork_threshold=2,
        restart_checkpoints=("PRE_RESTART", "POST_RESTART"),
        required_end_state="CONTINUABLE",
        rollback_requirement="HEALTHY_AFTER_ROLLBACK",
        evidence_output="false-pass",
        sample_interval_seconds=1.0,
    )


def _invariant(identifier: str, status: str, reason: str) -> InvariantRecord:
    return InvariantRecord(identifier, "2026-08-24T00:00:00Z", status, status == "PASS", True, False, reason)


def run_false_pass_factory(output_root: str | Path) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    cases.append({
        "case_id": "missing-artifact",
        "verdict": derive_verdict(_scenario(), run_status="COMPLETED", faults=(), invariants=(_invariant("STOP_AVAILABLE", "PASS", "ok"),), resources=(), component_results={}, artifact_missing=("TIMELINE.jsonl",)),
    })
    cases.append({
        "case_id": "stale-heartbeat",
        "verdict": derive_verdict(_scenario(), run_status="COMPLETED", faults=(), invariants=(_invariant("HEARTBEAT_CONTINUITY", "FAIL", "stale"),), resources=(), component_results={}),
    })
    cases.append({
        "case_id": "forged-pass",
        "verdict": derive_verdict(_scenario(), run_status="COMPLETED", faults=(), invariants=(_invariant("HEARTBEAT_CONTINUITY", "FAIL", "forged PASS ignored"),), resources=(), component_results={}),
    })
    cases.append({
        "case_id": "required-fault-not-fired",
        "verdict": derive_verdict(_scenario(with_fault=True), run_status="COMPLETED", faults=(), invariants=(_invariant("STOP_AVAILABLE", "PASS", "ok"),), resources=(), component_results={}),
    })
    cases.append({
        "case_id": "hidden-duplicate-effect",
        "verdict": derive_verdict(_scenario(), run_status="COMPLETED", faults=(), invariants=(_invariant("NO_DUPLICATE_EFFECT", "FAIL", "duplicate provider call"),), resources=(), component_results={}),
    })
    cases.append({
        "case_id": "owner-spam-omitted",
        "verdict": derive_verdict(_scenario(), run_status="COMPLETED", faults=(), invariants=(_invariant("OWNER_BRIEFING_BOUND", "NOT_TESTED", "missing"),), resources=(), component_results={}),
    })
    cases.append({
        "case_id": "resource-samples-missing",
        "verdict": derive_verdict(_scenario(), run_status="COMPLETED", faults=(), invariants=(_invariant("RESOURCE_ENVELOPE", "NOT_TESTED", "missing"),), resources=(), component_results={}),
    })
    cases.append({
        "case_id": "interrupted-run-marked-complete",
        "verdict": derive_verdict(_scenario(), run_status="COMPLETED", faults=(), invariants=(_invariant("STOP_AVAILABLE", "PASS", "ok"),), resources=(), component_results={}, interrupted=True),
    })
    rows = []
    for case in cases:
        observed = str(case["verdict"]["verdict"])
        rows.append({
            "case_id": case["case_id"],
            "observed_verdict": observed,
            "expected_not_pass": True,
            "verdict": "PASS" if observed != "PASS" else "FAIL",
            "reasons": case["verdict"].get("reasons", []),
        })
    payload = {
        "harness": "false-pass-adversarial-factory",
        "scope": "verdict engine only; no production state",
        "rows": rows,
        "summary": {
            "total": len(rows),
            "pass": sum(row["verdict"] == "PASS" for row in rows),
            "fail": sum(row["verdict"] == "FAIL" for row in rows),
        },
        "verdict": "PASS" if all(row["verdict"] == "PASS" for row in rows) else "FAIL",
    }
    path = Path(output_root) / "FALSE_PASS_FACTORY.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload
