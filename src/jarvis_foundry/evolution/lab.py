"""V6-S4: reproducible evaluation laboratory.

Baseline and candidate subjects run against the SAME frozen
representative workload. Workloads are registered with content hashes
of their inputs plus config and seeds; every run gets a hash-verified
copy of the inputs (identical bytes for baseline and candidate), runs
every case (no cherry-picking), records correctness/latency/cost/error
rate and invariant violations, handles nondeterminism explicitly, and
comparisons fail closed: different workloads, different input hashes,
missing runs, kind mismatches or missing improvements refuse instead of
guessing.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from ..clock import isoformat
from ..control.redaction import contains_secret_like
from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash
from ..supervisor.authority import SUPERVISOR_CONTROLLER
from .builder import _is_reparse_point
from .records import improvement_eligibility

SUBJECT_KINDS = ("baseline", "candidate")


class LabError(ValueError):
    """An evaluation is invalid, refused, or incomparable."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LabError(message)


def _require_supervisor(controller: str) -> None:
    _require(
        isinstance(controller, str) and controller == SUPERVISOR_CONTROLLER,
        "only the exact Supervisor controller identity 'supervisor' is "
        "accepted - refused",
    )


def _hash_inputs(directory: Path) -> dict[str, str]:
    """Content hashes of every file under the workload dir (reparse
    points pruned, deterministic order)."""
    hashes: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        dirpath_path = Path(dirpath)
        dirnames[:] = [name for name in dirnames
                       if not _is_reparse_point(dirpath_path / name)]
        for filename in sorted(filenames):
            path = dirpath_path / filename
            if _is_reparse_point(path):
                continue
            rel = path.relative_to(directory).as_posix()
            hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _sanitized_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()
            if key not in ("HERMES_HOME", "JARVIS_HOME", "OPENAI_API_KEY")
            and not key.endswith(("_API_KEY", "_TOKEN", "_SECRET",
                                  "_PASSWORD"))}


def register_workload(
    database: FoundryDatabase, *, name: str, input_dir: str | Path,
    config: dict[str, Any] | None = None, seeds: list[int] | None = None,
    controller: str,
    source_type: str = "executive", source_id: str = "v6",
) -> str:
    """Register a frozen representative workload. Input content hashes,
    config and seeds are bound to the workload id; identical content
    dedupes."""
    _require_supervisor(controller)
    _require(isinstance(name, str) and name.strip(),
             "name must be a non-empty string")
    directory = Path(input_dir).expanduser().resolve()
    _require(directory.is_dir(), f"workload input dir {directory} is not "
             "a directory")
    _require(isinstance(controller, str) and controller.strip(),
             "controller identity must be a non-empty string")
    config = dict(config or {})
    seeds = list(seeds or [])
    if contains_secret_like(canonical_json({"config": config})):
        raise LabError(
            "workload config contains secret-like content — refused at "
            "the boundary (V5-S7 guard family)")
    input_hashes = _hash_inputs(directory)
    _require(input_hashes, "workload input dir contains no files")
    identity = {
        "name": name,
        "input_dir": str(directory),
        "input_hashes": input_hashes,
        "config": config,
        "seeds": seeds,
    }
    workload_id = "workload-" + payload_hash(identity)[:12]
    identity["workload_id"] = workload_id
    identity["controller"] = controller
    event = Event.create(
        "evaluation.workload_registered", identity,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"evaluation.workload_registered:{workload_id}")
    database.append(event)
    return workload_id


def workload_by_id(database: FoundryDatabase,
                   workload_id: str) -> dict[str, Any] | None:
    for event in database.events():
        if event.event_type == "evaluation.workload_registered" and \
                event.payload["workload_id"] == workload_id:
            return event.payload
    return None


def run_evaluation(
    database: FoundryDatabase, *, workload_id: str, subject_kind: str,
    subject_identity: str, command: list[str], controller: str,
    timeout_s: int = 120, cost_usd: float = 0.0,
    source_type: str = "executive", source_id: str = "v6",
) -> str:
    """Run one subject against a hash-verified copy of the workload
    inputs. One invocation per input file; every case is recorded."""
    _require_supervisor(controller)
    workload = workload_by_id(database, workload_id)
    _require(workload is not None,
             f"workload {workload_id} does not exist — refused")
    _require(subject_kind in SUBJECT_KINDS,
             f"subject_kind must be one of {SUBJECT_KINDS}")
    _require(isinstance(subject_identity, str) and subject_identity.strip(),
             "subject_identity must be a non-empty string")
    _require(isinstance(command, list) and command and all(
        isinstance(part, str) and part for part in command),
        "command must be a non-empty list of strings")
    _require(isinstance(timeout_s, int) and timeout_s > 0,
             "timeout_s must be a positive integer")
    _require(isinstance(cost_usd, (int, float)) and cost_usd >= 0.0,
             "cost_usd must be a non-negative number")

    run_id = "run-" + uuid.uuid4().hex[:12]
    scratch = Path(database.path).parent / "lab-scratch"
    run_dir = scratch / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for rel, _digest in workload["input_hashes"].items():
        source = Path(workload["input_dir"]) / rel if \
            "input_dir" in workload else None
        # inputs are copied from the registered directory (kept in the
        # event payload so re-registration is not required)
        if source is None or not source.is_file():
            raise LabError(
                f"workload input {rel} unavailable at run time — "
                "refused (fail closed)")
        target = run_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    copied_hash = _hash_inputs(run_dir)
    _require(copied_hash == workload["input_hashes"],
             "run input copy failed content verification — refused "
             "(fail closed on incomparable measurements)")

    command_hash = payload_hash({"command": list(command)})
    database.append(Event.create(
        "evaluation.run_started",
        {"run_id": run_id, "workload_id": workload_id,
         "subject_kind": subject_kind,
         "subject_identity": subject_identity,
         "command_hash": command_hash, "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"evaluation.run_started:{run_id}"))

    env = _sanitized_env()
    cases: list[dict[str, Any]] = []
    output_hashes: dict[str, str] = {}
    failures = 0
    t0 = time.monotonic()
    for rel in sorted(workload["input_hashes"]):
        case_path = run_dir / rel
        try:
            completed = subprocess.run(
                [*command, str(case_path)], cwd=str(run_dir), env=env,
                capture_output=True, timeout=timeout_s, shell=False)
            exit_code = completed.returncode
            output_hashes[rel] = hashlib.sha256(
                completed.stdout).hexdigest()
        except subprocess.TimeoutExpired:
            exit_code = None
            output_hashes[rel] = hashlib.sha256(b"").hexdigest()
        except OSError as error:
            exit_code = None
            output_hashes[rel] = hashlib.sha256(b"").hexdigest()
        if exit_code != 0:
            failures += 1
        cases.append({"case": rel, "exit_code": exit_code})
    duration_s = round(time.monotonic() - t0, 3)
    overall_exit = 0 if failures == 0 else 1
    error_rate = round(failures / len(cases), 4) if cases else 1.0

    nondeterminism = "seeded" if workload["seeds"] else "not_seeded"
    if workload["config"].get("nondeterministic"):
        treatment = workload["config"].get("treatment")
        nondeterminism = f"explicit: {treatment}" if treatment else \
            "explicit"

    database.append(Event.create(
        "evaluation.run_completed",
        {"run_id": run_id, "workload_id": workload_id,
         "subject_kind": subject_kind,
         "subject_identity": subject_identity,
         "command_hash": command_hash,
         "input_hash": payload_hash(workload["input_hashes"]),
         "exit_code": overall_exit, "duration_s": duration_s,
         "error_rate": error_rate, "cost_usd": cost_usd,
         "case_results": cases, "output_hashes": output_hashes,
         "nondeterminism_handling": nondeterminism,
         "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"evaluation.run_completed:{run_id}"))
    return run_id


def _metric_key(baseline_metric: dict[str, Any]) -> str:
    name = str(baseline_metric.get("name", "")).lower()
    if "error" in name or "rate" in name:
        return "error_rate"
    return "duration_s"


def compare_evaluations(
    database: FoundryDatabase, *, baseline_run_id: str,
    candidate_run_id: str, improvement_id: str,
    source_type: str = "executive", source_id: str = "v6",
) -> dict[str, Any]:
    """Compare a candidate run against the baseline run of the SAME
    workload, against the improvement's frozen MMI and guardrails.
    Refuses when the runs are not comparable (different workload,
    different input hash, kind mismatch) or the improvement is unknown
    or ineligible."""
    baseline = database.row("evaluation_runs", "run_id", baseline_run_id)
    candidate = database.row("evaluation_runs", "run_id", candidate_run_id)
    _require(baseline is not None,
             f"baseline run {baseline_run_id} does not exist — refused")
    _require(candidate is not None,
             f"candidate run {candidate_run_id} does not exist — refused")
    _require(baseline["subject_kind"] == "baseline",
             f"run {baseline_run_id} is not a baseline run — refused")
    _require(candidate["subject_kind"] == "candidate",
             f"run {candidate_run_id} is not a candidate run — refused")
    _require(baseline["workload_id"] == candidate["workload_id"],
             "runs used different workloads — incomparable, refused")
    _require(baseline["input_hash"] == candidate["input_hash"],
             "runs used different input hashes — incomparable, refused")
    improvement = database.row("improvement_records", "improvement_id",
                               improvement_id)
    _require(improvement is not None,
             f"improvement {improvement_id} does not exist — refused")
    eligible, _reason = improvement_eligibility(database, improvement_id)
    _require(eligible,
             f"improvement {improvement_id} is not promotion-eligible — "
             "refused")

    baseline_metric = json.loads(improvement["baseline_metric_json"])
    mmi = json.loads(improvement["mmi_json"])
    budget = json.loads(improvement["budget_json"])
    metric_key = _metric_key(baseline_metric)
    baseline_value = float(baseline[metric_key])
    candidate_value = float(candidate[metric_key])
    delta = round(candidate_value - baseline_value, 4)
    minimum_delta = float(mmi["minimum_delta"])
    direction = mmi["direction"]
    if direction == "decrease":
        improvement_met = delta <= -minimum_delta
    else:
        improvement_met = delta >= minimum_delta

    guardrails_violated: list[str] = []
    if candidate["exit_code"] != 0:
        guardrails_violated.append("correctness")
    if candidate["error_rate"] > baseline["error_rate"]:
        guardrails_violated.append("quality")
    if float(candidate["cost_usd"]) > float(budget["max_cost_usd"]):
        guardrails_violated.append("cost")

    result = {
        "baseline_run_id": baseline_run_id,
        "candidate_run_id": candidate_run_id,
        "workload_id": baseline["workload_id"],
        "improvement_id": improvement_id,
        "metric_key": metric_key,
        "baseline_value": baseline_value,
        "candidate_value": candidate_value,
        "delta": delta,
        "mmi_minimum_delta": minimum_delta,
        "mmi_direction": direction,
        "improvement_met": improvement_met,
        "guardrails_violated": guardrails_violated,
        "comparable": True,
    }
    database.append(Event.create(
        "evaluation.comparison_recorded", result,
        source_type=source_type, source_id=source_id,
        idempotency_key=("evaluation.comparison_recorded:"
                         f"{baseline_run_id}:{candidate_run_id}")))
    return result
