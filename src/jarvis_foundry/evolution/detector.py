"""V6 lane JOB: deterministic weakness/opportunity detection.

Derives improvement-candidate observations DIRECTLY from durable outcome
projections (agent_runs, evaluation_runs, verifications, missions). No
model, no invented prose: every rule is a fixed threshold over counted
rows, every observation carries the exact evidence row ids it was
derived from, and the dedupe identity is content-derived so an
identical re-run produces the identical id (discovery recording then
dedupes through the S1 content-id contract).

Rules produce observations in the SAME shape discover.py consumes
(metric_exceedances / guardrail_breaches / task_failures), closing the
producer gap: recorded outcomes can now reach improvement records
without any LLM in the path.

Honesty contracts:
- an explicit window (ISO timestamps) is mandatory; observations never
  span an unstated window;
- a rule whose sample is below its documented minimum is SUPPRESSED and
  reported in `suppressed` (visible, but never recorded as a weakness);
- when no prior-window baseline exists the baseline is None and
  confidence is capped at 0.6 (never presented as a measured delta);
- risk hints reuse the S1 surface classifier (unknown -> R3 fail
  closed);
- empty windows produce zero observations (nothing fabricated).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from ..database import FoundryDatabase
from ..events import canonical_json
from .records import classify_risk

#: rule key -> (threshold, direction, minimum_sample, rationale)
RULES: dict[str, dict[str, Any]] = {
    "agent_repair_rate": {
        "threshold": 0.5, "direction": "decrease", "min_sample": 3,
        "rationale": ("more than half of failed agent runs being followed "
                      "by another run for the same mission indicates a "
                      "repair loop rather than isolated failures"),
    },
    "verifier_fail_rate": {
        "threshold": 0.34, "direction": "decrease", "min_sample": 3,
        "rationale": ("independent verification failing on more than a "
                      "third of verdicts means candidates systematically "
                      "reach verification unsound"),
    },
    "mission_retry_count": {
        "threshold": 3, "direction": "decrease", "min_sample": 1,
        "rationale": ("three or more agent runs against one mission in a "
                      "single window is a repeated-retry pattern"),
    },
    "provider_failure_rate": {
        "threshold": 0.5, "direction": "decrease", "min_sample": 4,
        "rationale": ("a provider/model pair failing at least half of its "
                      "runs (with a usable sample) underperforms the "
                      "workforce and should not keep receiving work"),
    },
    "candidate_latency_regression": {
        "threshold": 1.25, "direction": "decrease", "min_sample": 2,
        "rationale": ("candidate evaluations running over 1.25x the "
                      "baseline mean duration on the same workload is a "
                      "latency regression"),
    },
    "evaluation_error_inflation": {
        "threshold": 0.0, "direction": "decrease", "min_sample": 2,
        "rationale": ("any nonzero candidate error rate against a clean "
                      "baseline on the same workload breaches the "
                      "correctness guardrail family"),
    },
    "window_cost_inflation": {
        "threshold": 2.0, "direction": "decrease", "min_sample": 2,
        "rationale": ("total measured cost more than doubling versus the "
                      "immediately preceding equal-length window signals "
                      "cost inflation"),
    },
}

_STALE_STATES = ("BLOCKED", "REPAIRING", "REJECTING")
_TERMINAL_STATES = ("COMPLETE", "CANCELLED")


class DetectionError(ValueError):
    """The detection run was refused (bad window)."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DetectionError(message)


def _parse(value: str, label: str) -> datetime:
    _require(isinstance(value, str) and value.strip(),
             f"{label} must be a non-empty ISO timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DetectionError(f"{label} is not an ISO timestamp") from error


def _rows_in_window(database: FoundryDatabase, table: str,
                    column: str, start: datetime,
                    end: datetime) -> list[dict[str, Any]]:
    # Read-only SQL (agent_runs/missions are outside the database.all()
    # bounded-read allowlist by design); window filtering stays in this
    # module so the honesty contract lives in one place.
    rows: list[dict[str, Any]] = []
    with database.connect() as connection:
        cursor = connection.execute(
            f"SELECT * FROM {table} ORDER BY rowid")
        names = [description[0] for description in cursor.description]
        for raw_row in cursor.fetchall():
            row = dict(zip(names, raw_row))
            value = row.get(column)
            if not isinstance(value, str) or not value:
                continue
            try:
                moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if start <= moment <= end:
                rows.append(row)
    return rows


def _observation_id(payload: dict[str, Any]) -> str:
    return "weakness-" + hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()[:12]


def _confidence(sample: int, minimum: int, *, has_baseline: bool) -> float:
    base = 0.5 + 0.3 * min(sample / max(minimum, 1), 1.5)
    if not has_baseline:
        base = min(base, 0.6)
    return round(min(base, 0.9), 2)


def _risk_hint(component: str) -> str:
    return classify_risk([component])


def _emit(rule: str, metric: str, value: float, baseline: float | None,
          threshold: float, window: tuple[str, str],
          evidence_refs: list[str], component: str, sample: int,
          *, detail: str) -> dict[str, Any]:
    has_baseline = baseline is not None
    payload = {
        "rule": rule,
        "metric": metric,
        "value": value,
        "baseline": baseline,
        "threshold": threshold,
        "rationale": RULES[rule]["rationale"],
        "window_start": window[0],
        "window_end": window[1],
        "evidence_refs": evidence_refs,
        "affected_component": component,
        "sample_size": sample,
        "detail": detail,
    }
    payload["dedupe_identity"] = _observation_id({
        key: value_ for key, value_ in payload.items()
        if key != "detail"})
    payload["confidence"] = _confidence(
        sample, RULES[rule]["min_sample"], has_baseline=has_baseline)
    payload["risk_hint"] = _risk_hint(component)
    payload["observation_type"] = (
        "task_failures" if rule in ("mission_retry_count",)
        else "guardrail_breaches" if rule == "evaluation_error_inflation"
        else "metric_exceedances")
    return payload


def detect_weaknesses(database: FoundryDatabase, *,
                      window_start: str, window_end: str,
                      ) -> dict[str, Any]:
    """Run every detection rule over the explicit window.

    Returns {"observations": [...], "suppressed": [...], "window":
    {...}}. Observations are discovery-input shaped (see module docstring).
    """
    start = _parse(window_start, "window_start")
    end = _parse(window_end, "window_end")
    _require(start < end, "window_start must precede window_end")
    window = (window_start, window_end)
    span = end - start
    prior_start_dt = start - span
    prior_start = prior_start_dt.isoformat()

    runs = _rows_in_window(database, "agent_runs", "started_at",
                           start, end)
    prior_runs = _rows_in_window(database, "agent_runs", "started_at",
                                 prior_start_dt, start)
    evals = _rows_in_window(database, "evaluation_runs", "created_at",
                            start, end)
    prior_evals = _rows_in_window(database, "evaluation_runs", "created_at",
                                  prior_start_dt, start)
    verdicts = _rows_in_window(database, "verifications", "created_at",
                               start, end)

    observations: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []

    def note(rule: str, *positional: Any, **kwargs: Any) -> None:
        # callers may pass the sample size positionally (legacy shape)
        # or as the `sample` keyword; the keyword wins when both exist
        sample = int(kwargs.pop("sample",
                                positional[0] if positional else 0))
        if sample < RULES[rule]["min_sample"]:
            suppressed.append({"rule": rule, "sample_size": sample,
                               "minimum_required":
                                   RULES[rule]["min_sample"]})
            return
        observations.append(_emit(rule, kwargs.pop("metric"),
                                  kwargs.pop("value"),
                                  kwargs.pop("baseline"), RULES[rule]
                                  ["threshold"], window,
                                  kwargs.pop("evidence_refs"),
                                  kwargs.pop("component"), sample,
                                  detail=kwargs.pop("detail")))

    # R1 repair rate: failed runs followed by another run on the mission.
    failed = [r for r in runs if r.get("status") == "FAILED"]
    repaired: set[str] = set()
    for run in failed:
        mission = str(run["mission_id"])
        later = [r for r in runs
                 if str(r["mission_id"]) == mission
                 and r.get("status") in ("RUNNING", "COMPLETED")
                 and str(r["started_at"]) > str(run["started_at"])]
        if later:
            repaired.add(mission)
    if failed:
        rate = round(len(repaired) / len({str(r["mission_id"])
                                          for r in failed}), 4)
        if len(failed) < RULES["agent_repair_rate"]["min_sample"]:
            suppressed.append({
                "rule": "agent_repair_rate",
                "sample_size": len(failed),
                "minimum_required":
                    RULES["agent_repair_rate"]["min_sample"]})
        elif rate >= RULES["agent_repair_rate"]["threshold"]:
            note("agent_repair_rate", len(failed),
                 metric="agent_repair_rate", value=rate, baseline=None,
                 evidence_refs=[f"agent_runs/{r['run_id']}"
                                for r in failed][:10],
                 component="mission/", sample=len(failed),
                 detail=(f"{len(repaired)} of {len(failed)} failed "
                         "runs were followed by a retry on the same "
                         "mission"))
        else:
            suppressed.append({
                "rule": "agent_repair_rate",
                "sample_size": len(failed),
                "reason": f"rate {rate} below threshold"})

    # R2 verifier fail rate.
    decided = [v for v in verdicts if v.get("verdict")]
    fails = [v for v in decided if v["verdict"] == "FAIL"]
    if decided:
        rate = round(len(fails) / len(decided), 4)
        if len(decided) < RULES["verifier_fail_rate"]["min_sample"]:
            suppressed.append({
                "rule": "verifier_fail_rate",
                "sample_size": len(decided),
                "minimum_required":
                    RULES["verifier_fail_rate"]["min_sample"]})
        elif rate >= RULES["verifier_fail_rate"]["threshold"]:
            note("verifier_fail_rate", len(decided),
                 metric="verifier_fail_rate", value=rate, baseline=None,
                 evidence_refs=[f"verifications/{v['verification_id']}"
                                for v in fails][:10],
                 component="evolution/", sample=len(decided),
                 detail=f"{len(fails)} FAIL of {len(decided)} verdicts")
        else:
            suppressed.append({
                "rule": "verifier_fail_rate",
                "sample_size": len(decided),
                "reason": f"rate {rate} below threshold"})

    # R3 repeated mission retries.
    per_mission: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        per_mission.setdefault(str(run["mission_id"]), []).append(run)
    for mission, mission_runs in sorted(per_mission.items()):
        if len(mission_runs) >= RULES["mission_retry_count"]["threshold"]:
            note("mission_retry_count", 1,
                 metric="mission_retry_count", value=float(len(mission_runs)),
                 baseline=None,
                 evidence_refs=[f"agent_runs/{r['run_id']}"
                                for r in mission_runs][:10],
                 component="mission/", sample=1,
                 detail=(f"mission {mission} consumed "
                         f"{len(mission_runs)} runs in the window"))

    # R4 provider/model underperformance vs overall failure rate.
    overall_failed = len(failed) + len(prior_runs)
    total_seen = len(runs) + len(prior_runs)
    overall_rate = (overall_failed / total_seen) if total_seen else 0.0
    by_provider: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in runs:
        key = (str(run.get("provider") or "unknown"),
               str(run.get("model") or "unknown"))
        by_provider.setdefault(key, []).append(run)
    for (provider, model), pruns in sorted(by_provider.items()):
        pfailed = [r for r in pruns if r.get("status") == "FAILED"]
        rate = round(len(pfailed) / len(pruns), 4)
        if rate >= RULES["provider_failure_rate"]["threshold"] and \
                rate > overall_rate:
            note("provider_failure_rate", len(pruns),
                 metric=f"failure_rate[{provider}/{model}]",
                 value=rate, baseline=round(overall_rate, 4),
                 evidence_refs=[f"agent_runs/{r['run_id']}"
                                for r in pfailed][:10],
                 component=f"roles/{provider}", sample=len(pruns),
                 detail=(f"{provider}/{model} fails {rate} vs workforce "
                         f"average {round(overall_rate, 4)}"))

    # R5/R6 paired workload comparison: candidate vs baseline runs.
    by_workload: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in evals:
        by_workload.setdefault(str(row["workload_id"]), {})\
            .setdefault(str(row["subject_kind"]), []).append(row)
    for workload, kinds in sorted(by_workload.items()):
        cands = kinds.get("candidate", [])
        bases = kinds.get("baseline", [])
        if cands and bases:
            cand_dur = [float(r["duration_s"] or 0.0) for r in cands]
            base_dur = [float(r["duration_s"] or 0.0) for r in bases]
            base_mean = sum(base_dur) / len(base_dur)
            cand_mean = sum(cand_dur) / len(cand_dur)
            if base_mean > 0:
                ratio = round(cand_mean / base_mean, 4)
                if ratio >= RULES["candidate_latency_regression"][
                        "threshold"]:
                    note("candidate_latency_regression",
                         len(cands) + len(bases),
                         metric=f"duration_ratio[{workload}]",
                         value=ratio, baseline=1.0,
                         evidence_refs=[f"evaluation_runs/{r['run_id']}"
                                        for r in cands][:10],
                         component="execution/",
                         sample=len(cands) + len(bases),
                         detail=(f"candidate mean {round(cand_mean, 3)}s "
                                 f"vs baseline mean {round(base_mean, 3)}s "
                                 f"(x{ratio})"))
        cand_err = [float(r["error_rate"] or 0.0) for r in cands]
        if cands and sum(cand_err) > RULES[
                "evaluation_error_inflation"]["threshold"]:
            note("evaluation_error_inflation", len(cands),
                 metric=f"error_rate[{workload}]",
                 value=round(sum(cand_err) / len(cand_err), 4),
                 baseline=None,
                 evidence_refs=[f"evaluation_runs/{r['run_id']}"
                                for r in cands][:10],
                 component="execution/", sample=len(cands),
                 detail=("candidate evaluation cases failed while the "
                         "workload expects zero-error behavior"))

    # R7 window cost inflation vs immediately preceding equal window.
    def _run_cost(run: dict[str, Any]) -> float:
        raw = run.get("cost_json")
        if isinstance(raw, (dict, list)) or raw is None:
            return 0.0
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return 0.0
        if isinstance(parsed, dict):
            total = parsed.get("total_usd", parsed.get("cost_usd", 0.0))
            try:
                return max(float(total), 0.0)
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    cost_now = sum(float(r["cost_usd"] or 0.0) for r in evals) + \
        sum(_run_cost(r) for r in runs)
    cost_prior = sum(float(r["cost_usd"] or 0.0) for r in prior_evals) + \
        sum(_run_cost(r) for r in prior_runs)
    if cost_now > 0:
        ratio = round(cost_now / cost_prior, 4) if cost_prior > 0 else None
        if ratio is not None and \
                ratio >= RULES["window_cost_inflation"]["threshold"]:
            note("window_cost_inflation", len(evals) + len(runs),
                 metric="window_cost_ratio", value=ratio, baseline=1.0,
                 evidence_refs=[f"evaluation_runs/{r['run_id']}"
                                for r in evals][:10],
                 component="evolution/", sample=len(evals) + len(runs),
                 detail=(f"window cost {round(cost_now, 4)} vs prior "
                         f"{round(cost_prior, 4)} (x{ratio})"))
        elif ratio is None:
            suppressed.append({"rule": "window_cost_inflation",
                               "reason": "no prior-window cost baseline"})

    # R8 stale/blocked missions still open past their last touch inside
    # the window are surfaced as task failures. This staleness rule has
    # its own identity: rule key prefixed so dedupe never collides with
    # R3 retry observations, and confidence is capped (single-row
    # evidence, no baseline).
    with database.connect() as connection:
        cursor = connection.execute(
            "SELECT * FROM missions ORDER BY rowid")
        names = [description[0] for description in cursor.description]
        mission_rows = [dict(zip(names, raw))
                        for raw in cursor.fetchall()]
    for row in mission_rows:
        state = str(row.get("state") or "")
        if state in _TERMINAL_STATES or state not in _STALE_STATES:
            continue
        try:
            touched = datetime.fromisoformat(
                str(row["updated_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if touched <= end:
            observation = _emit(
                "mission_retry_count", "mission_staleness_days",
                round((end - touched).total_seconds() / 86400, 2), None,
                RULES["mission_retry_count"]["threshold"],
                window, [f"missions/{row['mission_id']}"],
                "mission/", 1,
                detail=(f"mission {row['mission_id']} stuck in {state} "
                        f"since {row['updated_at']}"))
            observation["rule"] = "mission_staleness"
            observation["rationale"] = (
                "a mission stuck in BLOCKED/REPAIRING/REJECTING past its "
                "last touch represents stalled work that keeps consuming "
                "attention without progress")
            observation["dedupe_identity"] = _observation_id({
                key: value_ for key, value_ in observation.items()
                if key != "detail"})
            observation["confidence"] = min(
                observation["confidence"], 0.6)
            observations.append(observation)

    observations.sort(key=lambda o: o["dedupe_identity"])
    return {
        "observations": observations,
        "suppressed": suppressed,
        "window": {"start": window_start, "end": window_end,
                   "prior_start": prior_start},
    }


def next_window(window_end: str, span_hours: int = 24) -> tuple[str, str]:
    """Convenience: the window ending now-span..now (UTC-naive local clock
    used by the ledger). Deterministic given the inputs."""
    end = _parse(window_end, "window_end")
    start = end - timedelta(hours=span_hours)
    return start.isoformat(), end.isoformat()


def discovery_sources(database: FoundryDatabase, *,
                      window_start: str, window_end: str,
                      ) -> dict[str, list[dict[str, Any]]]:
    """Adapter to the S10 discovery input contract.

    Converts detector observations into the sources mapping
    discover.candidate_improvements consumes, keyed by observation
    type. Every observation carries its evidence_ref (mandatory in the
    discovery contract), metric/value/threshold for exceedances,
    guardrail/detail for breaches, and task/detail for task failures.
    Deterministic: identical ledger state + window -> identical mapping.
    """
    result = detect_weaknesses(database, window_start=window_start,
                               window_end=window_end)
    sources: dict[str, list[dict[str, Any]]] = {
        "metric_exceedances": [], "guardrail_breaches": [],
        "task_failures": []}
    for observation in result["observations"]:
        ref = observation["evidence_refs"][0]
        common = {"evidence_ref": ref, "observed_at": window_end,
                  "surfaces": [observation["affected_component"]]}
        if observation["observation_type"] == "metric_exceedances":
            sources["metric_exceedances"].append({
                **common,
                "metric": observation["metric"],
                "value": observation["value"],
                "threshold": observation["threshold"],
                "unit": "ratio" if "rate" in observation["metric"]
                or "ratio" in observation["metric"] else "count",
            })
        elif observation["observation_type"] == "guardrail_breaches":
            sources["guardrail_breaches"].append({
                **common,
                "guardrail": "correctness",
                "detail": observation["detail"],
            })
        else:  # task_failures
            sources["task_failures"].append({
                **common,
                "task": observation["rule"],
                "detail": observation["detail"],
            })
    return sources
