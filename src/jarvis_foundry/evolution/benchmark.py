"""V6 lane JOB: typed benchmark/evaluation engine.

Aggregates the evaluation surface into a TYPED benchmark record for a
candidate vs baseline pair on the SAME workload:

- one row PER METRIC (latency, error rate, cost, case pass rate) with
  baseline value, candidate value, delta, direction, and
  pass/fail against the frozen MMI or guardrail — never a single
  scalar "AI score";
- guardrail failures are listed explicitly (correctness/quality/cost)
  and never averaged away;
- refuses incomparable inputs (different workload, different input
  hash, wrong subject kinds, unknown runs) — same fail-closed family
  as lab.compare_evaluations but with the full metric table;
- benchmark ids are content-derived (same runs -> same id);
- the benchmark record is ledger-durable (benchmark.recorded).

Mission-replay and outcome-quality metrics are recorded as
NOT_AVAILABLE with the reason when their sources are absent — an
honest gap, not a zero.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..database import FoundryDatabase
from ..events import Event, canonical_json
from .lab import compare_evaluations
from .records import improvement_eligibility


class BenchmarkError(ValueError):
    """A benchmark was refused."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BenchmarkError(message)


def _run_row(database: FoundryDatabase, run_id: str,
             expected_kind: str) -> dict[str, Any]:
    row = database.row("evaluation_runs", "run_id", run_id)
    _require(row is not None,
             f"evaluation run {run_id} does not exist — refused")
    _require(str(row["subject_kind"]) == expected_kind,
             f"run {run_id} is not a {expected_kind} run — refused")
    return row


def _case_pass_rate(row: dict[str, Any]) -> float:
    cases = json.loads(str(row.get("case_results_json") or "[]"))
    if not cases:
        return 0.0
    passed = sum(1 for case in cases
                 if case.get("exit_code") == 0)
    return round(passed / len(cases), 4)


def build_benchmark(database: FoundryDatabase, *, improvement_id: str,
                    baseline_run_id: str, candidate_run_id: str,
                    ) -> dict[str, Any]:
    """Build ONE typed benchmark record from a comparable run pair."""
    baseline = _run_row(database, baseline_run_id, "baseline")
    candidate = _run_row(database, candidate_run_id, "candidate")
    _require(baseline["workload_id"] == candidate["workload_id"],
             "runs used different workloads — incomparable, refused")
    _require(baseline["input_hash"] == candidate["input_hash"],
             "runs used different input hashes — incomparable, refused")
    improvement = database.row("improvement_records", "improvement_id",
                               improvement_id)
    _require(improvement is not None,
             f"improvement {improvement_id} does not exist — refused")
    eligible, reason = improvement_eligibility(database, improvement_id)
    _require(eligible,
             f"improvement {improvement_id} not eligible: {reason}")

    # the governed comparison (MMI + guardrails) stays the source of
    # truth for improvement_met; the benchmark adds the full table
    governed = compare_evaluations(
        database, baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        improvement_id=improvement_id)

    mmi = json.loads(str(improvement["mmi_json"]))
    budget = json.loads(str(improvement["budget_json"]))

    base_latency = float(baseline["duration_s"] or 0.0)
    cand_latency = float(candidate["duration_s"] or 0.0)
    base_error = float(baseline["error_rate"] or 0.0)
    cand_error = float(candidate["error_rate"] or 0.0)
    base_cost = float(baseline["cost_usd"] or 0.0)
    cand_cost = float(candidate["cost_usd"] or 0.0)
    base_pass = _case_pass_rate(baseline)
    cand_pass = _case_pass_rate(candidate)

    def _metric(name: str, base: float, cand: float, unit: str,
                *, direction: str, threshold: float | None,
                met: bool | None) -> dict[str, Any]:
        delta = round(cand - base, 6)
        return {"metric": name, "baseline": base, "candidate": cand,
                "delta": delta, "unit": unit, "direction": direction,
                "threshold": threshold, "met": met}

    latency_threshold = float(mmi["minimum_delta"]) \
        if str(mmi.get("unit")) in ("s", "seconds", "seconds ") else None
    latency_met: bool | None = None
    if latency_threshold is not None:
        latency_met = (cand_latency <=
                       base_latency - latency_threshold) \
            if mmi["direction"] == "decrease" else \
            (cand_latency >= base_latency + latency_threshold)

    metrics = [
        _metric("latency_s", base_latency, cand_latency, "s",
                direction=str(mmi["direction"]),
                threshold=latency_threshold, met=latency_met),
        _metric("error_rate", base_error, cand_error, "ratio",
                direction="decrease", threshold=0.0,
                met=cand_error <= 0.0),
        _metric("cost_usd", base_cost, cand_cost, "usd",
                direction="decrease",
                threshold=float(budget["max_cost_usd"]),
                met=cand_cost <= float(budget["max_cost_usd"])),
        _metric("case_pass_rate", base_pass, cand_pass, "ratio",
                direction="increase", threshold=1.0,
                met=cand_pass >= 1.0),
        # honest gap: mission replay needs the mission surface, which is
        # outside this lane's disposable benchmark scope today
        {"metric": "mission_replay_quality", "baseline": None,
         "candidate": None, "delta": None, "unit": "score",
         "direction": "increase", "threshold": None,
         "met": None, "status": "NOT_AVAILABLE",
         "reason": "mission replay surface not connected in this lane"},
    ]

    guardrail_failures: list[str] = list(
        governed["guardrails_violated"])
    if cand_error > 0.0 and "quality" not in guardrail_failures and \
            cand_error > base_error:
        guardrail_failures.append("quality")
    if not all(metric.get("met") in (True, None) or metric["metric"] ==
               "latency_s" for metric in metrics):
        # non-latency metric failures surface as guardrail families
        for metric in metrics:
            if metric["met"] is False and metric["metric"] != "latency_s":
                family = {"error_rate": "quality",
                          "cost_usd": "cost",
                          "case_pass_rate": "correctness"}.get(
                              metric["metric"])
                if family and family not in guardrail_failures:
                    guardrail_failures.append(family)

    payload = {
        "benchmark_id": "",
        "improvement_id": improvement_id,
        "baseline_run_id": baseline_run_id,
        "candidate_run_id": candidate_run_id,
        "workload_id": baseline["workload_id"],
        "metrics": metrics,
        "improvement_met": governed["improvement_met"],
        "guardrail_failures": sorted(set(guardrail_failures)),
        "governed_comparison_id": (
            f"{baseline_run_id}:{candidate_run_id}"),
    }
    benchmark_id = "benchmark-" + hashlib.sha256(
        canonical_json({k: v for k, v in payload.items()
                        if k != "benchmark_id"})
        .encode("utf-8")).hexdigest()[:12]
    payload["benchmark_id"] = benchmark_id
    database.append(Event.create(
        "benchmark.recorded", payload,
        source_type="supervisor", source_id="v6",
        idempotency_key=f"benchmark.recorded:{benchmark_id}"))
    return payload


def benchmark_by_id(database: FoundryDatabase,
                    benchmark_id: str) -> dict[str, Any] | None:
    for event in database.events():
        if event.event_type == "benchmark.recorded" and \
                event.payload.get("benchmark_id") == benchmark_id:
            return dict(event.payload)
    return None
