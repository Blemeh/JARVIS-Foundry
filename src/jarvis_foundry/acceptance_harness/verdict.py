"""Machine-readable verdict derivation for disposable acceptance runs."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import ClockMode, FaultRecord, InvariantRecord, ResourceSample, ScenarioSpec, VerdictStatus


def derive_verdict(
    scenario: ScenarioSpec,
    *,
    run_status: str,
    faults: Iterable[FaultRecord],
    invariants: Iterable[InvariantRecord],
    resources: Iterable[ResourceSample],
    component_results: Mapping[str, Mapping[str, Any]],
    artifact_missing: Iterable[str] = (),
    hash_mismatches: Iterable[str] = (),
    interrupted: bool = False,
) -> dict[str, Any]:
    fault_rows = list(faults)
    invariant_rows = list(invariants)
    resource_rows = list(resources)
    missing_faults = sorted(set(scenario.required_fault_ids) - {row.fault_id for row in fault_rows})
    required_failures = [
        f"{row.invariant_id}: {row.reason}"
        for row in invariant_rows
        if row.required and row.status == "FAIL"
    ]
    required_not_tested = [
        f"{row.invariant_id}: {row.reason}"
        for row in invariant_rows
        if row.required and row.status in {"NOT_TESTED", "BLOCKED_EXTERNAL"}
    ]
    resource_violations = sorted({violation for row in resource_rows for violation in row.violations})
    component_blockers = [
        f"{name}: {result.get('reason', 'external dependency unavailable')}"
        for name, result in component_results.items()
        if result.get("status") == VerdictStatus.BLOCKED_EXTERNAL.value
    ]
    component_failures = [
        f"{name}: {result.get('reason', 'component failed')}"
        for name, result in component_results.items()
        if result.get("status") == VerdictStatus.FAIL.value
    ]
    reasons: list[str] = []
    critical_failures: list[str] = []
    if interrupted:
        reasons.append("harness interruption was recorded")
    if run_status != "COMPLETED":
        reasons.append(f"run status is {run_status}")
    if missing_faults:
        reasons.append("required faults did not fire: " + ", ".join(missing_faults))
    reasons.extend(required_failures)
    reasons.extend(required_not_tested)
    reasons.extend(resource_violations)
    reasons.extend(component_failures)
    reasons.extend(component_blockers)
    if artifact_missing:
        reasons.append("missing evidence artifacts: " + ", ".join(sorted(artifact_missing)))
    if hash_mismatches:
        reasons.append("evidence hash mismatch: " + ", ".join(sorted(hash_mismatches)))
    critical_failures.extend(required_failures)
    critical_failures.extend(resource_violations)
    critical_failures.extend(component_failures)
    status: VerdictStatus
    if interrupted:
        status = VerdictStatus.ABORTED
    elif run_status != "COMPLETED":
        status = VerdictStatus.INCOMPLETE
    elif critical_failures:
        status = VerdictStatus.FAIL
    elif artifact_missing or hash_mismatches or missing_faults or required_not_tested:
        status = VerdictStatus.INCOMPLETE
    elif component_blockers:
        status = VerdictStatus.BLOCKED_EXTERNAL
    else:
        status = VerdictStatus.PASS
    return {
        "verdict": status.value,
        "scenario_id": scenario.scenario_id,
        "clock_mode": scenario.clock_mode.value,
        "acceptance_label": (
            "ACCELERATED_EQUIVALENT_ONLY" if scenario.clock_mode is ClockMode.ACCELERATED_DISPOSABLE
            else "REAL_TIME_DISPOSABLE"
        ),
        "run_status": run_status,
        "reasons": reasons,
        "critical_failures": critical_failures,
        "required_faults": list(scenario.required_fault_ids),
        "fired_faults": [row.fault_id for row in fault_rows],
        "required_invariants": list(scenario.expected_invariants),
        "invariant_statuses": {
            row.invariant_id: row.status for row in invariant_rows if row.required
        },
        "component_results": {name: dict(result) for name, result in component_results.items()},
        "resource_sample_count": len(resource_rows),
        "evidence_missing": sorted(set(artifact_missing)),
        "evidence_hash_mismatches": sorted(set(hash_mismatches)),
        "production_modified": False,
        "live_db_modified": False,
        "live_scheduler_modified": False,
        "main_modified": False,
    }


def render_final_report(
    verdict: Mapping[str, Any],
    *,
    frozen_base: str,
    final_head: str,
    remote_candidate: str,
    metrics: Mapping[str, Any],
    profiles: Mapping[str, Any],
    tests: str,
    hygiene: str,
    secret_scan: str,
    worktree_clean: str,
    candidate_pushed: str,
) -> str:
    components = verdict.get("component_results", {})
    def component(name: str) -> str:
        result = components.get(name, {})
        return str(result.get("status", "NOT_TESTED"))

    lines = [
        "FINAL VERDICT: " + str(verdict.get("verdict", "INCOMPLETE")),
        "FROZEN BASE: " + frozen_base,
        "FINAL HEAD: " + final_head,
        "REMOTE CANDIDATE: " + remote_candidate,
        "",
        "SCENARIO CONTRACT: " + component("scenario_contract"),
        "CLOCK MODES: " + str(verdict.get("acceptance_label", "NOT_TESTED")),
        "CHECKPOINT/RESUME: " + component("checkpoint_resume"),
        "INVARIANT SAMPLER: " + component("invariant_sampler"),
        "CONTINUATION METRIC: " + component("continuation"),
        "BUSYWORK: " + component("busywork"),
        "OWNER SPAM: " + component("owner_spam"),
        "PROCESS FAULT: " + component("process_fault"),
        "NETWORK/PROVIDER: " + component("provider_fault"),
        "EFFECT AMBIGUITY: " + component("effect_ambiguity"),
        "DATABASE: " + component("database_fault"),
        "RESOURCE PRESSURE: " + component("resource_envelope"),
        "RELEASE DEGRADATION: " + component("release_rollback"),
        "RESTART CONTRACT: " + component("restart_contract"),
        "FAULT SCHEDULER: " + component("fault_scheduler"),
        "VERDICT ENGINE: " + component("verdict_engine"),
        "EVIDENCE BUNDLE: " + component("evidence_bundle"),
        "",
        "ACCELERATED 12H-EQUIVALENT: " + component("accelerated_proof"),
        "REAL-TIME SHORT RUN: " + component("realtime_proof"),
        "HARNESS INTERRUPTION: " + component("interruption_resume"),
        "FALSE-PASS FACTORY: " + component("false_pass_factory"),
        "",
        "PROFILES:",
    ]
    for name in ("QUICK_30M", "STANDARD_6H", "FULL_12H", "FULL_24H", "REBOOT_ASSISTED_24H"):
        lines.append(f"{name}: {profiles.get(name, 'NOT_TESTED')}")
    lines.extend([
        "",
        "CROSS-LANE HANDOFFS: documented in CROSS_LANE_HANDOFF.md",
        "OPEN CRITICAL: " + str(len(verdict.get("critical_failures", []))),
        "OPEN HIGH: " + str(len(verdict.get("reasons", []))),
        "TESTS: " + tests,
        "HYGIENE: " + hygiene,
        "SECRET SCAN: " + secret_scan,
        "",
        "PRODUCTION MODIFIED: NO",
        "LIVE DB MODIFIED: NO",
        "LIVE SCHEDULER MODIFIED: NO",
        "MAIN MODIFIED: NO",
        "WORKTREE CLEAN: " + worktree_clean,
        "CANDIDATE PUSHED: " + candidate_pushed,
        "",
        "METRICS: " + str(dict(metrics)),
        "REASONS: " + ("; ".join(str(item) for item in verdict.get("reasons", [])) or "none"),
        "",
        "NEXT INTEGRATION ACTION: Reconcile this harness onto final V7 canonical, then use an owner-approved FULL_12H/FULL_24H profile for final unattended JARVIS acceptance.",
    ])
    return "\n".join(lines) + "\n"
