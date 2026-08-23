"""V6 lane JOB: baseline + counterfactual measurement layer.

Captures durable BEFORE/AFTER snapshots for an improvement hypothesis
over an EXPLICIT window, directly from the outcome projections:

- target metric (name/value/unit/source) aligned with the frozen
  hypothesis baseline_metric;
- sample size, distribution (sorted durations), latency mean/p95,
  error rate, repair rate, cost totals;
- recorded confounders (provider mix, workload ids,
  nondeterminism handling, schema version);
- source evidence refs (row ids).

Honesty contracts (fail loud, never guess):
- a snapshot whose sample is below its documented minimum is stored
  with status INSUFFICIENT_SAMPLE — never presented as a measurement;
- comparisons REFUSE incompatible inputs explicitly: unequal window
  spans or mismatched metric identities yield status UNKNOWN with the
  reason recorded — incompatible windows are never compared silently;
- identical re-captures dedupe to one content-derived measurement id.

Snapshots persist as ledger events (`measurement.captured`) using the
same store-in-payload/read-by-scan pattern as lab workloads, so they
survive restarts without new projection tables.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Any

from ..database import FoundryDatabase
from ..events import Event, canonical_json
from ..migrations import SCHEMA_VERSION

#: below this many evaluation runs in the window a snapshot is
#: INSUFFICIENT_SAMPLE (documented minimum, not a silent guess)
MIN_EVAL_SAMPLE = 2


class MeasurementError(ValueError):
    """A measurement operation was refused."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MeasurementError(message)


def _parse(value: str, label: str) -> datetime:
    _require(isinstance(value, str) and value.strip(),
             f"{label} must be a non-empty ISO timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MeasurementError(f"{label} is not an ISO timestamp") from error


def _rows_in_window(database: FoundryDatabase, table: str,
                    column: str, start: datetime,
                    end: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with database.connect() as connection:
        cursor = connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        names = [description[0] for description in cursor.description]
        for raw_row in cursor.fetchall():
            row = dict(zip(names, raw_row))
            value = row.get(column)
            if not isinstance(value, str) or not value:
                continue
            try:
                moment = datetime.fromisoformat(
                    value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if start <= moment <= end:
                rows.append(row)
    return rows


def _p95(sorted_values: list[float]) -> float | None:
    if not sorted_values:
        return None
    index = max(math.ceil(0.95 * len(sorted_values)) - 1, 0)
    return round(sorted_values[index], 4)


def _snapshot_identity(payload: dict[str, Any]) -> str:
    return "measurement-" + hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()[:12]


def capture_measurement(database: FoundryDatabase, *,
                        improvement_id: str,
                        subject_kind: str,
                        window_start: str,
                        window_end: str,
                        controller: str = "supervisor",
                        ) -> dict[str, Any]:
    """Capture ONE durable snapshot (kind baseline|candidate) of the
    outcome surface for ``improvement_id`` over the explicit window."""
    _require(subject_kind in ("baseline", "candidate"),
             "subject_kind must be baseline or candidate")
    start = _parse(window_start, "window_start")
    end = _parse(window_end, "window_end")
    _require(start < end, "window_start must precede window_end")
    row = database.row("improvement_records", "improvement_id",
                       improvement_id)
    _require(row is not None,
             f"improvement {improvement_id} does not exist — refused")

    evals = [r for r in _rows_in_window(database, "evaluation_runs",
                                        "created_at", start, end)
             if r.get("subject_kind") == subject_kind]
    runs = _rows_in_window(database, "agent_runs", "started_at",
                           start, end)

    durations = sorted(float(r["duration_s"] or 0.0) for r in evals)
    errors = [float(r["error_rate"] or 0.0) for r in evals]
    costs = [float(r["cost_usd"] or 0.0) for r in evals]

    # repair rate: failed agent runs followed by any later run on the
    # same mission inside the window
    failed_runs = [r for r in runs if r.get("status") == "FAILED"]
    repaired_missions: set[str] = set()
    for run in failed_runs:
        mission = str(run["mission_id"])
        if any(str(r["mission_id"]) == mission
               and r.get("status") in ("RUNNING", "COMPLETED")
               and str(r["started_at"]) > str(run["started_at"])
               for r in runs):
            repaired_missions.add(mission)
    repair_rate = round(len(repaired_missions) /
                        len({str(r["mission_id"]) for r in failed_runs}), 4) \
        if failed_runs else None

    provider_mix: dict[str, int] = {}
    for run in runs:
        key = str(run.get("provider") or "unknown")
        provider_mix[key] = provider_mix.get(key, 0) + 1
    workload_ids = sorted({str(r["workload_id"]) for r in evals})
    nondeterminism = sorted({str(r["nondeterminism_handling"])
                             for r in evals})

    import json as _json
    declared_baseline = _json.loads(str(row["baseline_metric_json"]))
    sample_size = len(evals)
    sufficient = sample_size >= MIN_EVAL_SAMPLE
    mean_duration = round(sum(durations) / len(durations), 4) \
        if durations else None

    payload: dict[str, Any] = {
        "improvement_id": improvement_id,
        "subject_kind": subject_kind,
        "window_start": window_start,
        "window_end": window_end,
        "target_metric": {
            "name": declared_baseline.get("name"),
            "unit": declared_baseline.get("unit"),
            "source": declared_baseline.get("source"),
        },
        "sample_size": sample_size,
        "distribution_durations_s": durations[:100],
        "latency_mean_s": mean_duration,
        "latency_p95_s": _p95(durations),
        "error_rate_mean": round(sum(errors) / len(errors), 4)
        if errors else None,
        "error_rate_max": round(max(errors), 4) if errors else None,
        "repair_rate": repair_rate,
        "cost_usd_total": round(sum(costs), 6),
        "confounders": {
            "provider_mix": provider_mix,
            "workload_ids": workload_ids,
            "nondeterminism_handling": nondeterminism,
            "schema_version": SCHEMA_VERSION,
        },
        "evidence_refs": ([f"evaluation_runs/{r['run_id']}"
                           for r in evals][:50] +
                          [f"agent_runs/{r['run_id']}"
                           for r in failed_runs][:10]),
    }
    payload["status"] = "MEASURED" if sufficient else "INSUFFICIENT_SAMPLE"
    measurement_id = _snapshot_identity(payload)
    payload["measurement_id"] = measurement_id
    database.append(Event.create(
        "measurement.captured", payload,
        source_type="supervisor", source_id="v6",
        idempotency_key=f"measurement.captured:{measurement_id}"))
    return payload


def measurement_by_id(database: FoundryDatabase,
                      measurement_id: str) -> dict[str, Any] | None:
    for event in database.events():
        if event.event_type == "measurement.captured" and \
                event.payload.get("measurement_id") == measurement_id:
            return dict(event.payload)
    return None


def compare_measurements(database: FoundryDatabase, *,
                         before_id: str, after_id: str,
                         ) -> dict[str, Any]:
    """Compare BEFORE vs AFTER snapshots for the same hypothesis.

    Returns a typed decision; UNKNOWN results always carry the reason.
    Refusals are explicit — incompatible windows or metric identities
    are never silently compared.
    """
    before = measurement_by_id(database, before_id)
    after = measurement_by_id(database, after_id)
    _require(before is not None,
             f"before measurement {before_id} does not exist — refused")
    _require(after is not None,
             f"after measurement {after_id} does not exist — refused")

    def _unknown(reason: str) -> dict[str, Any]:
        return {"comparison": "UNKNOWN", "reason": reason,
                "before_measurement_id": before_id,
                "after_measurement_id": after_id}

    before_span = _parse(before["window_end"], "before.window_end") - \
        _parse(before["window_start"], "before.window_start")
    after_span = _parse(after["window_end"], "after.window_end") - \
        _parse(after["window_start"], "after.window_start")
    if before_span != after_span:
        result = _unknown("incompatible windows: unequal spans "
                          f"({before_span} vs {after_span})")
    elif before["target_metric"]["name"] != \
            after["target_metric"]["name"]:
        result = _unknown("incompatible metric identities: "
                          f"{before['target_metric']['name']} vs "
                          f"{after['target_metric']['name']}")
    elif before["subject_kind"] != "baseline":
        result = _unknown("before measurement is not a baseline snapshot")
    elif after["subject_kind"] != "candidate":
        result = _unknown("after measurement is not a candidate snapshot")
    elif before["status"] != "MEASURED":
        result = _unknown("before snapshot is "
                          f"{before['status']} — cannot compare")
    elif after["status"] != "MEASURED":
        result = _unknown("after snapshot is "
                          f"{after['status']} — cannot compare")
    else:
        base_mean = float(before["latency_mean_s"])
        cand_mean = float(after["latency_mean_s"])
        delta = round(cand_mean - base_mean, 4) \
            if base_mean is not None and cand_mean is not None else None
        result = {
            "comparison": "MEASURED",
            "before_measurement_id": before_id,
            "after_measurement_id": after_id,
            "metric": before["target_metric"]["name"],
            "baseline_latency_mean_s": base_mean,
            "candidate_latency_mean_s": cand_mean,
            "delta_latency_s": delta,
            "baseline_error_rate": before["error_rate_mean"],
            "candidate_error_rate": after["error_rate_mean"],
            "delta_cost_usd": round(after["cost_usd_total"] -
                                    before["cost_usd_total"], 6),
            "confounder_drift": {
                key: {"before": before["confounders"].get(key),
                      "after": after["confounders"].get(key)}
                for key in ("provider_mix", "workload_ids",
                            "nondeterminism_handling")},
        }
    database.append(Event.create(
        "measurement.compared",
        {**result,
         "before_window": [before["window_start"], before["window_end"]],
         "after_window": [after["window_start"], after["window_end"]]},
        source_type="supervisor", source_id="v6",
        idempotency_key=(f"measurement.compared:{before_id}:{after_id}")))
    return result
