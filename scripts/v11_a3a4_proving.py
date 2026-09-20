"""Prepare and run the fail-closed V11 A3/A4 unattended proving harness.

This is acceptance tooling, not an initiative implementation.  A frozen V11
candidate supplies a driver command that executes/resumes one durable cycle and
returns a JSON snapshot.  The harness owns scheduling, fault requests,
checkpointing, independent metric derivation, and the final evidence verdict.

The 48-hour profile is deliberately guarded by exact Git identity, a clean
worktree, an explicit final-integrated-SHA assertion, and ``--allow-48h``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

A2_SHA = "00ee2e148c12151699a796649a1cb87ed892a69b"

from jarvis_foundry.migrations import SCHEMA_VERSION  # noqa: E402 - SRC is on sys.path above

# The barrier records the schema of the CURRENT candidate and keeps the A2
# ancestry check above it.  Deriving it from the product's own constant makes a
# future bump carry here automatically: a duplicated literal silently pins this
# file to an obsolete number and turns the barrier's own verdict into a false
# FAIL, which is exactly how the 40 -> 41 bump broke this barrier.
A2_BARRIER_SCHEMA = SCHEMA_VERSION
HARNESS_SCHEMA = "jarvis.v11.a3a4.proving.v1"
DRIVER_SCHEMA = "jarvis.v11.a3a4.driver.v1"
FINAL_DURATION_HOURS = 48.0
FAULT_POINTS = (
    "post-candidate",
    "post-selection-objective",
    "mid-build",
    "post-effect-pre-acceptance",
    "stop-resume",
    "process-restart",
)
RECORD_KINDS = (
    "observations",
    "candidates",
    "objectives",
    "missions",
    "builder_runs",
    "effects",
    "acceptances",
    "outcomes",
    "learning",
    "repairs",
)
REQUIRED_FLOW = (
    "OBSERVATION",
    "CANDIDATES",
    "SELECTION",
    "OBJECTIVE",
    "MISSION",
    "BUILD",
    "EFFECT",
    "ACCEPTANCE",
    "LEARNING",
    "WAIT",
)
TERMINAL_SUCCESS = {"PASS", "PASSED", "SUCCESS", "SUCCEEDED", "VERIFIED"}
MISSION_COMPLETE = {"COMPLETE", "COMPLETED", "SUCCEEDED", "PASSED"}
_SECRET_TEXT = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{12,}|"
    r"(?i:(?:token|password|secret|api[_-]?key))\s*[=:]\s*\S+)"
)
_SECRET_KEY = re.compile(r"(?:secret|token|password|credential|api[_-]?key|private[_-]?key)", re.I)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(_redact(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _redact(value: Any, *, key: str | None = None) -> Any:
    if key and _SECRET_KEY.search(key):
        return "<REDACTED>"
    if isinstance(value, Mapping):
        return {str(name): _redact(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(_redact(dict(value)), sort_keys=True, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _redact_text(value: str) -> str:
    return _SECRET_TEXT.sub("<REDACTED>", value)


def _git(*args: str, root: Path = ROOT, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if check and completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def git_identity(root: Path = ROOT) -> dict[str, Any]:
    return {
        "root": str(root.resolve()),
        "head": _git("rev-parse", "HEAD", root=root),
        "branch": _git("branch", "--show-current", root=root),
        "status_porcelain": _git("status", "--porcelain=v1", root=root),
    }


def _identity_key(record: Mapping[str, Any]) -> str:
    value = record.get("identity_key") or record.get("fingerprint") or record.get("id")
    if not value:
        raise ValueError("each record requires id and identity_key (or fingerprint)")
    return str(value)


def _normalise_records(snapshot: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    records = snapshot.get("records")
    if not isinstance(records, Mapping):
        raise ValueError("driver snapshot records must be an object")
    normalised: dict[str, list[dict[str, Any]]] = {}
    for kind in RECORD_KINDS:
        raw = records.get(kind, [])
        if not isinstance(raw, list):
            raise ValueError(f"records.{kind} must be a list")
        rows: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, Mapping) or not item.get("id"):
                raise ValueError(f"records.{kind} entries require a stable id")
            row = dict(item)
            _identity_key(row)
            rows.append(row)
        normalised[kind] = rows
    return normalised


def blank_state(*, run_id: str, expected_sha: str, duration_hours: float) -> dict[str, Any]:
    return {
        "schema": HARNESS_SCHEMA,
        "run_id": run_id,
        "expected_sha": expected_sha,
        "prepared_at": utc_now(),
        "started_at": None,
        "deadline_at": None,
        "duration_hours": duration_hours,
        "cycle_sequence": 0,
        "completed_cycles": [],
        "fault_cursor": 0,
        "faults": {},
        "identity_registry": {kind: {} for kind in RECORD_KINDS},
        "records": {kind: {} for kind in RECORD_KINDS},
        "stages_seen": [],
        "last_status": "PREPARED",
        "last_snapshot_at": None,
        "recovery_durations_seconds": [],
        "unbounded_loop_cycles": [],
    }


def record_snapshot(state: MutableMapping[str, Any], snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("schema") != DRIVER_SCHEMA:
        raise ValueError(f"driver snapshot schema must be {DRIVER_SCHEMA}")
    cycle_id = str(snapshot.get("cycle_id") or "")
    if not cycle_id:
        raise ValueError("driver snapshot requires cycle_id")
    records = _normalise_records(snapshot)
    identity_registry = state["identity_registry"]
    accumulated = state["records"]
    for kind, rows in records.items():
        for row in rows:
            record_id = str(row["id"])
            key = _identity_key(row)
            known = identity_registry[kind].setdefault(key, [])
            if record_id not in known:
                known.append(record_id)
            accumulated[kind][record_id] = row
    for stage in snapshot.get("stages", []):
        stage_name = str(stage).upper()
        if stage_name not in state["stages_seen"]:
            state["stages_seen"].append(stage_name)
    state["last_status"] = str(snapshot.get("status") or "UNKNOWN").upper()
    state["last_snapshot_at"] = utc_now()
    if snapshot.get("unbounded_loop_detected") is True:
        unbounded = state.setdefault("unbounded_loop_cycles", [])
        if cycle_id not in unbounded:
            unbounded.append(cycle_id)
    if state["last_status"] == "WAIT" and cycle_id not in state["completed_cycles"]:
        state["completed_cycles"].append(cycle_id)


def _duplicates(state: Mapping[str, Any], kind: str) -> int:
    return sum(max(0, len(set(ids)) - 1) for ids in state["identity_registry"][kind].values())


def derive_metrics(state: Mapping[str, Any]) -> dict[str, Any]:
    rows = {
        kind: list(state["records"][kind].values())
        for kind in RECORD_KINDS
    }
    builder_run_ids = {str(row["id"]) for row in rows["builder_runs"]}
    accepted = {
        str(row["id"]): row for row in rows["acceptances"]
        if str(row.get("verdict") or row.get("status") or "").upper() in TERMINAL_SUCCESS
        and row.get("independent") is True
        and row.get("builder_run_id")
        and str(row["builder_run_id"]) in builder_run_ids
        and row.get("verifier_run_id")
        and str(row["builder_run_id"]) != str(row["verifier_run_id"])
    }
    false_successes = 0
    verified_outcomes = 0
    for outcome in rows["outcomes"]:
        if str(outcome.get("status") or "").upper() not in {"SUCCEEDED", "SUCCESS", "PASSED"}:
            continue
        acceptance_id = str(outcome.get("acceptance_id") or "")
        if acceptance_id in accepted and outcome.get("truth_accepted") is True:
            verified_outcomes += 1
        else:
            false_successes += 1
    unverified_completions = sum(
        1 for mission in rows["missions"]
        if str(mission.get("state") or mission.get("status") or "").upper() in MISSION_COMPLETE
        and not any(
            str(acceptance.get("mission_id") or "") == str(mission["id"])
            for acceptance in accepted.values()
        )
    )
    candidates = rows["candidates"]
    recovery = [float(value) for value in state.get("recovery_durations_seconds", [])]
    started_value = state.get("started_at")
    wall_elapsed = 0.0
    if started_value:
        started = _parse_time(str(started_value))
        wall_elapsed = max(0.0, (datetime.now(UTC) - started).total_seconds())
    return {
        "observations": len(rows["observations"]),
        "candidates": len(candidates),
        "rejected": sum(str(row.get("status") or "").upper() == "REJECTED" for row in candidates),
        "eligible": sum(str(row.get("status") or "").upper() in {"ELIGIBLE", "SELECTED"} for row in candidates),
        "objectives": len(rows["objectives"]),
        "missions": len(rows["missions"]),
        "builder_runs": len(rows["builder_runs"]),
        "effects": len(rows["effects"]),
        "acceptances": len(rows["acceptances"]),
        # Distinct mission steps that earned an independent PASS. Each one
        # legitimately commits once, so this is the count the effect
        # criterion is compared against.
        # Completed commits must equal the steps that earned an independent PASS;
        # an interrupted claim may add a reconciled sibling, never a duplicate commit.
        "completed_effects": sum(1 for row in rows["effects"] if str(row.get("status") or "").upper() == "COMPLETED"),
        "nonterminal_effects": sum(1 for row in rows["effects"] if str(row.get("status") or "").upper() not in {"COMPLETED", "FAILED", "FROZEN"}),
        "verified_steps": sum(1 for row in rows["acceptances"] if row.get("independent") is True),
        "verified_outcomes": verified_outcomes,
        "learning_updates": len(rows["learning"]),
        "duplicate_candidates": _duplicates(state, "candidates"),
        "duplicate_objectives": _duplicates(state, "objectives"),
        "duplicate_missions": _duplicates(state, "missions"),
        "duplicate_builder_runs": _duplicates(state, "builder_runs"),
        "duplicate_effects": _duplicates(state, "effects"),
        "duplicate_learning": _duplicates(state, "learning"),
        "false_successes": false_successes,
        "unverified_completions": unverified_completions,
        "unbounded_loops": len(state.get("unbounded_loop_cycles", [])),
        "repair_attempts": len(rows["repairs"]),
        "completed_cycles": len(state.get("completed_cycles", [])),
        "faults_injected": sum(row.get("injected") is True for row in state.get("faults", {}).values()),
        "faults_recovered": sum(row.get("recovered") is True for row in state.get("faults", {}).values()),
        "cycle_duration_seconds": wall_elapsed,
        "recovery_duration_seconds": {
            "count": len(recovery),
            "max": max(recovery, default=0.0),
            "total": sum(recovery),
        },
    }


def evaluate(state: Mapping[str, Any], *, require_full_gate: bool) -> dict[str, Any]:
    metrics = derive_metrics(state)
    hard_zero = (
        "duplicate_candidates", "duplicate_objectives", "duplicate_missions",
        "duplicate_builder_runs", "duplicate_effects", "duplicate_learning", "false_successes",
        "unverified_completions", "unbounded_loops",
    )
    reasons = [f"{key}={metrics[key]}" for key in hard_zero if metrics[key] != 0]
    if require_full_gate:
        checks = {
            "wall_clock_48h": metrics["cycle_duration_seconds"] >= FINAL_DURATION_HOURS * 3600,
            "three_consecutive_cycles": metrics["completed_cycles"] >= 3,
            "real_observations": metrics["observations"] >= 1 and all(
                row.get("real") is True and row.get("provenance")
                for row in state["records"]["observations"].values()
            ),
            "ten_candidates": metrics["candidates"] >= 10,
            "majority_rejected": metrics["rejected"] > metrics["candidates"] / 2,
            "eligible_finalist": metrics["eligible"] >= 1,
            "objective_created": metrics["objectives"] >= 1,
            "mission_created": metrics["missions"] >= 1,
            "builder_ran": metrics["builder_runs"] >= 1,
            "effect_recorded": metrics["effects"] >= 1,
            "independent_acceptance": metrics["verified_outcomes"] >= 1,
            "learning_recorded": metrics["learning_updates"] >= 1,
            "all_faults_injected": metrics["faults_injected"] == len(FAULT_POINTS),
            "all_faults_recovered": metrics["faults_recovered"] == len(FAULT_POINTS),
            "full_flow_seen": all(stage in state["stages_seen"] for stage in REQUIRED_FLOW),
            "returned_to_wait": state.get("last_status") == "WAIT",
        }
        reasons.extend(name for name, passed in checks.items() if not passed)
    return {
        "schema": HARNESS_SCHEMA,
        "verdict": "PASS" if not reasons else "FAIL",
        "require_full_gate": require_full_gate,
        "hard_assertions": {key: metrics[key] == 0 for key in hard_zero},
        "metrics": metrics,
        "reasons": reasons,
    }


def evaluate_immediate_a4(state: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate the complete fault campaign without starting elapsed time."""
    verdict = evaluate(state, require_full_gate=False)
    metrics = verdict["metrics"]
    checks = {
        "real_observations": metrics["observations"] >= 1 and all(
            row.get("real") is True and row.get("provenance")
            for row in state["records"]["observations"].values()
        ),
        "ten_candidates": metrics["candidates"] >= 10,
        "majority_rejected": metrics["rejected"] > metrics["candidates"] / 2,
        "one_eligible_finalist": metrics["eligible"] == 1,
        "one_objective": metrics["objectives"] == 1,
        "one_mission": metrics["missions"] == 1,
        "completed_builder": metrics["builder_runs"] >= 1,
        "one_completed_effect_per_verified_step": (
            # The canonical A3 mission runs an ordered two-step plan and each
            # verified step legitimately commits once; asserting a single effect
            # for the whole mission contradicted the product contract the same
            # harness requires elsewhere.
            metrics["completed_effects"] == metrics["verified_steps"]
        ),
        "every_effect_terminal": metrics["nonterminal_effects"] == 0,
        "independent_acceptance": metrics["verified_outcomes"] == 1,
        "one_learning_update": metrics["learning_updates"] == 1,
        "all_faults_injected": metrics["faults_injected"] == len(FAULT_POINTS),
        "all_faults_recovered": metrics["faults_recovered"] == len(FAULT_POINTS),
        "full_flow_seen": all(stage in state["stages_seen"] for stage in REQUIRED_FLOW),
        "returned_to_wait": state.get("last_status") == "WAIT",
        "elapsed_gate_not_started": state.get("started_at") is None,
    }
    reasons = [*verdict["reasons"], *(name for name, passed in checks.items() if not passed)]
    return {
        **verdict,
        "verdict": "PASS" if not reasons else "FAIL",
        "mode": "IMMEDIATE_A4_FAULT_CAMPAIGN",
        "checks": checks,
        "reasons": reasons,
    }


def _driver_argv(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    argv = payload.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("driver spec must contain a non-empty string argv list")
    return argv


def call_driver(
    argv: Sequence[str], request: Mapping[str, Any], output: Path,
    *, timeout_seconds: float = 1800.0,
) -> tuple[dict[str, Any], int]:
    request_path = output / "DRIVER_REQUEST.json"
    _atomic_json(request_path, request)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [*argv, "--request", str(request_path)],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        _append_jsonl(output / "DRIVER_CALLS.jsonl", {
            "at": utc_now(), "action": request.get("action"),
            "cycle_id": request.get("cycle_id"),
            "fault_point": request.get("fault_point"),
            "resume": request.get("resume"), "status": "TIMEOUT",
            "timeout_seconds": timeout_seconds,
        })
        raise RuntimeError(f"driver exceeded {timeout_seconds} seconds") from error
    duration = time.monotonic() - started
    _append_jsonl(output / "DRIVER_CALLS.jsonl", {
        "at": utc_now(),
        "action": request.get("action"),
        "cycle_id": request.get("cycle_id"),
        "fault_point": request.get("fault_point"),
        "resume": request.get("resume"),
        "returncode": completed.returncode,
        "duration_seconds": duration,
        "stderr": _redact_text(completed.stderr[-4000:]),
    })
    try:
        snapshot = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"driver did not return one JSON snapshot (exit {completed.returncode}): "
            f"{completed.stdout[-1000:]} {completed.stderr[-1000:]}"
        ) from error
    if not isinstance(snapshot, dict):
        raise RuntimeError("driver output must be one JSON object")
    _append_jsonl(output / "SNAPSHOTS.jsonl", {
        "captured_at": utc_now(),
        "action": request.get("action"),
        "resume": request.get("resume"),
        "snapshot": snapshot,
    })
    return snapshot, completed.returncode


def _next_cycle_id(state: MutableMapping[str, Any]) -> str:
    state["cycle_sequence"] = int(state["cycle_sequence"]) + 1
    seed = f"{state['run_id']}:{state['cycle_sequence']}"
    return "cycle-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _next_fault(state: Mapping[str, Any]) -> str | None:
    cursor = int(state.get("fault_cursor", 0))
    return FAULT_POINTS[cursor] if cursor < len(FAULT_POINTS) else None


def run_one_cycle(
    state: MutableMapping[str, Any], argv: Sequence[str], output: Path,
    *, driver_timeout_seconds: float = 1800.0,
) -> None:
    fault_point = _next_fault(state)
    cycle_id = _next_cycle_id(state)
    base_request = {
        "schema": DRIVER_SCHEMA,
        "action": "run_cycle",
        "run_id": state["run_id"],
        "cycle_id": cycle_id,
        "expected_sha": state["expected_sha"],
        "fault_point": fault_point,
    }
    if fault_point:
        injected_at = time.monotonic()
        first, first_code = call_driver(
            argv, {**base_request, "resume": False}, output,
            timeout_seconds=driver_timeout_seconds,
        )
        record_snapshot(state, first)
        acknowledged = first.get("fault") or {}
        if acknowledged.get("point") != fault_point or acknowledged.get("status") != "INJECTED":
            raise RuntimeError(f"driver did not acknowledge injected fault {fault_point}")
        state["faults"][fault_point] = {
            "cycle_id": cycle_id,
            "injected": True,
            "recovered": False,
            "injected_at": utc_now(),
            "injection_returncode": first_code,
        }
        _atomic_json(output / "CHECKPOINT.json", state)
        resumed, resumed_code = call_driver(
            argv, {**base_request, "resume": True}, output,
            timeout_seconds=driver_timeout_seconds,
        )
        record_snapshot(state, resumed)
        recovery = resumed.get("fault") or {}
        if recovery.get("point") != fault_point or recovery.get("status") != "RECOVERED":
            raise RuntimeError(f"driver did not prove recovery for {fault_point}")
        if str(resumed.get("cycle_id")) != cycle_id:
            raise RuntimeError("restart changed durable cycle identity")
        if fault_point == "stop-resume" and resumed.get("stop_new_dispatch_blocked") is not True:
            raise RuntimeError("STOP/RESUME fault did not prove new dispatch was blocked")
        duration = time.monotonic() - injected_at
        state["recovery_durations_seconds"].append(duration)
        state["faults"][fault_point].update({
            "recovered": True,
            "recovered_at": utc_now(),
            "resume_returncode": resumed_code,
            "recovery_duration_seconds": duration,
        })
        state["fault_cursor"] = int(state["fault_cursor"]) + 1
        _append_jsonl(output / "FAULTS.jsonl", state["faults"][fault_point] | {"fault_point": fault_point})
    else:
        snapshot, returncode = call_driver(
            argv, {**base_request, "resume": False}, output,
            timeout_seconds=driver_timeout_seconds,
        )
        if returncode:
            raise RuntimeError(f"unfaulted driver cycle failed with exit {returncode}")
        record_snapshot(state, snapshot)
    _append_jsonl(output / "SAMPLES.jsonl", {
        "at": utc_now(),
        "cycle_id": cycle_id,
        "fault_point": fault_point,
        "status": state["last_status"],
        "metrics": derive_metrics(state),
    })
    _atomic_json(output / "CHECKPOINT.json", state)


def _manifest(output: Path, state: Mapping[str, Any], status: str) -> dict[str, Any]:
    names = (
        "HARNESS_CONTRACT.json", "FAULT_PLAN.json", "CHECKPOINT.json",
        "RUNNER_PROCESS.json",
        "DRIVER_CALLS.jsonl", "SNAPSHOTS.jsonl", "SAMPLES.jsonl", "FAULTS.jsonl",
        "IMMEDIATE_A4_METRICS.json", "IMMEDIATE_A4_VERDICT.json",
        "FINAL_METRICS.json", "FINAL_VERDICT.json", "FINAL_REPORT.md",
    )
    hashes = {name: _sha256(output / name) for name in names if (output / name).exists()}
    payload = {
        "schema": HARNESS_SCHEMA,
        "run_id": state["run_id"],
        "expected_sha": state["expected_sha"],
        "status": status,
        "generated_at": utc_now(),
        "artifact_hashes": hashes,
    }
    _atomic_json(output / "RUN_MANIFEST.json", payload)
    return payload


def prepare_bundle(output: Path, *, expected_sha: str, duration_hours: float, run_id: str | None = None) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty evidence root: {output}")
    output.mkdir(parents=True, exist_ok=True)
    state = blank_state(
        run_id=run_id or "v11-a3a4-" + uuid.uuid4().hex[:16],
        expected_sha=expected_sha,
        duration_hours=duration_hours,
    )
    contract = {
        "schema": DRIVER_SCHEMA,
        "description": "candidate-neutral full-snapshot driver contract",
        "request": {
            "action": "run_cycle",
            "run_id": "stable harness identity",
            "cycle_id": "stable across fault restart",
            "expected_sha": "exact frozen final V11 SHA",
            "fault_point": list(FAULT_POINTS) + [None],
            "resume": "false for injection, true for same-identity recovery",
        },
        "response_required": {
            "schema": DRIVER_SCHEMA,
            "cycle_id": "request cycle_id",
            "status": "WAIT, RUNNING, STOPPED, BLOCKED, or FAILED",
            "stages": list(REQUIRED_FLOW),
            "records": {kind: [{"id": "stable id", "identity_key": "semantic idempotency key"}] for kind in RECORD_KINDS},
            "fault": {"point": "requested point", "status": "INJECTED or RECOVERED"},
            "stop_new_dispatch_blocked": "required true after stop-resume recovery",
            "unbounded_loop_detected": "true only when a bounded settle limit is exhausted",
        },
        "independent_acceptance": {
            "acceptance_fields": ["mission_id", "builder_run_id", "verifier_run_id", "independent", "verdict"],
            "rule": "builder_run_id differs from verifier_run_id; independent=true; verdict passes",
        },
        "prohibited": ["hardcoded winning candidate", "driver-asserted aggregate metrics", "unaccepted success learning"],
    }
    fault_plan = {
        "schema": HARNESS_SCHEMA,
        "points": list(FAULT_POINTS),
        "policy": "each point injected once; resume reuses cycle_id and durable record identities",
        "stop_assertion": "STOP blocks new dispatch",
        "restart_assertion": "restart resumes the same durable identity",
    }
    _atomic_json(output / "HARNESS_CONTRACT.json", contract)
    _atomic_json(output / "FAULT_PLAN.json", fault_plan)
    _atomic_json(output / "CHECKPOINT.json", state)
    _manifest(output, state, "PREPARED_NOT_STARTED")
    return {"status": "PREPARED_NOT_STARTED", "output": str(output), "state": state}


def _load_state(output: Path) -> dict[str, Any]:
    path = output / "CHECKPOINT.json"
    if not path.exists():
        raise FileNotFoundError(f"missing prepared checkpoint: {path}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema") != HARNESS_SCHEMA:
        raise ValueError("checkpoint schema mismatch")
    return state


def _write_final(output: Path, state: Mapping[str, Any], verdict: Mapping[str, Any]) -> None:
    _atomic_json(output / "FINAL_METRICS.json", verdict["metrics"])
    _atomic_json(output / "FINAL_VERDICT.json", verdict)
    report = (
        "# V11 A3/A4 unattended proving\n\n"
        f"- Verdict: **{verdict['verdict']}**\n"
        f"- Run: `{state['run_id']}`\n"
        f"- Expected SHA: `{state['expected_sha']}`\n"
        f"- Started: `{state['started_at']}`\n"
        f"- Last status: `{state['last_status']}`\n"
        f"- Reasons: `{', '.join(verdict['reasons']) or 'none'}`\n\n"
        "Metrics and hard assertions are machine-readable in `FINAL_METRICS.json` "
        "and `FINAL_VERDICT.json`.\n"
    )
    (output / "FINAL_REPORT.md").write_text(report, encoding="utf-8")
    _manifest(output, state, "COMPLETE" if verdict["verdict"] == "PASS" else "FAILED")


def run_harness(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    state = _load_state(output)
    identity = git_identity(ROOT)
    if identity["head"] != args.expected_sha or state["expected_sha"] != args.expected_sha:
        raise RuntimeError("exact candidate SHA does not match prepared/working-tree identity")
    if identity["status_porcelain"]:
        raise RuntimeError("48-hour proving requires a clean frozen worktree")
    if args.final_integrated_sha != args.expected_sha:
        raise RuntimeError("--final-integrated-sha must equal --expected-sha")
    changed_from_a2 = _git("diff", "--name-only", f"{A2_SHA}..HEAD").splitlines()
    if args.expected_sha == A2_SHA or not any(
        path.startswith("src/jarvis_foundry/") for path in changed_from_a2
    ):
        raise RuntimeError("48-hour proving is blocked until an integrated V11 product SHA replaces A2")
    if float(state["duration_hours"]) < FINAL_DURATION_HOURS:
        raise RuntimeError("final A3/A4 gate requires at least 48 wall-clock hours")
    if not args.allow_48h:
        raise PermissionError("48-hour runner is prepared only; pass --allow-48h after final V11 integration freezes")
    argv = _driver_argv(args.driver_spec.resolve())
    _atomic_json(output / "RUNNER_PROCESS.json", {
        "schema": HARNESS_SCHEMA,
        "pid": os.getpid(),
        "recorded_at": utc_now(),
        "expected_sha": args.expected_sha,
        "allow_48h": bool(args.allow_48h),
        "driver_spec_sha256": _sha256(args.driver_spec.resolve()),
    })
    if not state.get("started_at"):
        started = datetime.now(UTC)
        state["started_at"] = started.isoformat().replace("+00:00", "Z")
        state["deadline_at"] = (
            started + timedelta(hours=float(state["duration_hours"]))
        ).isoformat().replace("+00:00", "Z")
        state["last_status"] = "RUNNING"
        _atomic_json(output / "CHECKPOINT.json", state)
        _manifest(output, state, "RUNNING")
    while True:
        deadline = _parse_time(state["deadline_at"])
        if datetime.now(UTC) >= deadline:
            break
        run_one_cycle(
            state, argv, output,
            driver_timeout_seconds=float(args.driver_timeout_seconds),
        )
        if args.once:
            return {"status": "CHECKPOINTED", "metrics": derive_metrics(state)}
        remaining = max(0.0, (deadline - datetime.now(UTC)).total_seconds())
        if remaining:
            time.sleep(min(float(args.interval_seconds), remaining))
    verdict = evaluate(state, require_full_gate=True)
    _write_final(output, state, verdict)
    return verdict


def run_fault_campaign(args: argparse.Namespace) -> dict[str, Any]:
    """Run all prepared faults now, leaving the 48-hour clock untouched."""
    output = args.output.resolve()
    state = _load_state(output)
    identity = git_identity(ROOT)
    if identity["head"] != args.expected_sha or state["expected_sha"] != args.expected_sha:
        raise RuntimeError("exact candidate SHA does not match prepared/working-tree identity")
    if identity["status_porcelain"]:
        raise RuntimeError("immediate A4 proving requires a clean frozen worktree")
    if state.get("started_at") is not None:
        raise RuntimeError("immediate A4 bundle already has an elapsed-time start")
    argv = _driver_argv(args.driver_spec.resolve())
    while int(state.get("fault_cursor", 0)) < len(FAULT_POINTS):
        run_one_cycle(
            state,
            argv,
            output,
            driver_timeout_seconds=float(args.driver_timeout_seconds),
        )
    verdict = evaluate_immediate_a4(state)
    _atomic_json(output / "IMMEDIATE_A4_METRICS.json", verdict["metrics"])
    _atomic_json(output / "IMMEDIATE_A4_VERDICT.json", verdict)
    _manifest(output, state, "IMMEDIATE_A4_COMPLETE" if verdict["verdict"] == "PASS" else "IMMEDIATE_A4_FAILED")
    return verdict


def a2_barrier(args: argparse.Namespace) -> dict[str, Any]:
    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.discovery_sources import SourceSpec
    from jarvis_foundry.initiative_a2 import InitiativeA2Bounds, run_initiative_a2_cycle

    identity = git_identity(ROOT)
    if identity["head"] != A2_SHA:
        descendant = subprocess.run(
            ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", A2_SHA, identity["head"]],
            check=False, capture_output=True, timeout=30,
        ).returncode == 0
        if not descendant:
            raise RuntimeError(
                f"A2 barrier requires a descendant of historical A2 {A2_SHA}")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite A2 evidence root: {output}")
    output.mkdir(parents=True, exist_ok=True)
    before_worktrees = _git("worktree", "list", "--porcelain")
    database = FoundryDatabase(output / "a2-barrier.db")
    schema = database.initialize()
    source = SourceSpec(
        "a2-harness-real-project",
        "project",
        str(ROOT),
        1,
        {"root": str(ROOT), "project_id": "JARVIS-Foundry-A2", "max_files": 200, "max_dirs": 80},
    )
    result = run_initiative_a2_cycle(
        database,
        cycle_id="a2-harness-cold",
        sources=[source],
        bounds=InitiativeA2Bounds(do_nothing_score=0.20),
    )
    event_types = Counter(event.event_type for event in database.events())
    metrics = {
        "observations": database.count("world_observations"),
        "candidates": database.count("candidates"),
        "rejected": int(result.get("metrics", {}).get("candidates_rejected", 0)),
        "eligible": int(result.get("metrics", {}).get("candidates_eligible", 0)),
        "objectives": event_types["executive.objective_created"],
        "missions": event_types["mission.created"],
        "effects": sum(
            count for name, count in event_types.items()
            if name.startswith("effect.") or name.startswith("system.action_")
        ),
    }
    after_worktrees = _git("worktree", "list", "--porcelain")
    passed = (
        schema == A2_BARRIER_SCHEMA
        and metrics["objectives"] == metrics["missions"] == metrics["effects"] == 0
        and result.get("outcome") in {"WAIT", "IDLE"}
        and before_worktrees == after_worktrees
        and database.quick_check() == "ok"
    )
    report = {
        "schema": HARNESS_SCHEMA,
        "verdict": "PASS" if passed else "FAIL",
        "mode": "A2_EXECUTION_BARRIER_DRY_RUN",
        "git": identity,
        "product_base_sha": A2_SHA,
        "database_schema": schema,
        "cycle_outcome": result.get("outcome"),
        "metrics": metrics,
        "worktree_creations": 0 if before_worktrees == after_worktrees else "CHANGED",
        "database_quick_check": database.quick_check(),
        "48h_started": False,
    }
    _atomic_json(output / "A2_BARRIER_RESULT.json", report)
    _atomic_json(output / "RUN_MANIFEST.json", {
        "schema": HARNESS_SCHEMA,
        "status": report["verdict"],
        "generated_at": utc_now(),
        "artifact_hashes": {"A2_BARRIER_RESULT.json": _sha256(output / "A2_BARRIER_RESULT.json")},
    })
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="v11-a3a4-proving")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="write a not-started evidence/checkpoint bundle")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--expected-sha", required=True)
    prepare.add_argument("--duration-hours", type=float, default=FINAL_DURATION_HOURS)
    prepare.add_argument("--run-id")

    a2 = commands.add_parser("a2-barrier", help="run the A2 zero-execution dry run")
    a2.add_argument("--output", type=Path, required=True)

    run = commands.add_parser("run", help="run/resume only after final integrated V11 is frozen")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--expected-sha", required=True)
    run.add_argument("--final-integrated-sha", required=True)
    run.add_argument("--driver-spec", type=Path, required=True)
    run.add_argument("--interval-seconds", type=float, default=300.0)
    run.add_argument("--driver-timeout-seconds", type=float, default=1800.0)
    run.add_argument("--once", action="store_true")
    run.add_argument("--allow-48h", action="store_true")

    campaign = commands.add_parser(
        "fault-campaign",
        help="run the six immediate A4 faults without starting the 48-hour clock",
    )
    campaign.add_argument("--output", type=Path, required=True)
    campaign.add_argument("--expected-sha", required=True)
    campaign.add_argument("--driver-spec", type=Path, required=True)
    campaign.add_argument("--driver-timeout-seconds", type=float, default=1800.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            if args.duration_hours < FINAL_DURATION_HOURS:
                raise ValueError("A3/A4 proving preparation requires at least 48 hours")
            result = prepare_bundle(
                args.output.resolve(), expected_sha=args.expected_sha,
                duration_hours=args.duration_hours, run_id=args.run_id,
            )
        elif args.command == "a2-barrier":
            result = a2_barrier(args)
        elif args.command == "run":
            result = run_harness(args)
        else:
            result = run_fault_campaign(args)
    except (FileExistsError, FileNotFoundError, PermissionError, RuntimeError, ValueError) as error:
        result = {"verdict": "BLOCKED", "reason": str(error), "48h_started": False}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    if result.get("verdict") in {None, "PASS"}:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
