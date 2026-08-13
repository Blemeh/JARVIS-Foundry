"""V6-S6: zero-effect shadow mode.

Shadow candidates observe, reason and propose against COPIED inputs
with ZERO live effects: every effect goes through the ShadowSink which
records proposals and REFUSES capability use. Live capability attempts
and blocked-connector attempts fail the run (fail closed) with the
attempt recorded. STOP (sentinel or injected check) freezes shadow
runs before any proposal. Divergence against the baseline's decisions
is recorded per case. The live surface (runtime DB + optional live
marker) is hashed before/after and proven unchanged.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from ..clock import isoformat
from ..control.redaction import contains_secret_like
from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash

BLOCKED_CONNECTOR_PREFIXES = ("browser.", "email.", "sms.", "voice.",
                              "search.", "calendar.")


class ShadowError(ValueError):
    """A shadow run is refused or failed (zero-effect violation)."""


class ShadowSink:
    """The ONLY effect boundary a shadow candidate may touch. Proposals
    are recorded; capability use is refused and classified."""

    def __init__(self) -> None:
        self.proposals: list[dict[str, Any]] = []
        self.live_attempts: int = 0
        self.blocked_attempts: int = 0

    def propose(self, *, decision: str, expected_effect: str,
                cost_usd: float = 0.0, kind: str = "proposal") -> None:
        self.proposals.append({
            "decision": decision, "expected_effect": expected_effect,
            "cost_usd": cost_usd, "kind": kind,
        })

    def use_capability(self, capability: str, args: dict[str, Any]) -> None:
        """Refuse every capability use: live kinds fail the run,
        blocked-connector kinds fail the run with their own class."""
        if capability.startswith(BLOCKED_CONNECTOR_PREFIXES):
            self.blocked_attempts += 1
            raise _ShadowBlockedConnector(capability, args)
        self.live_attempts += 1
        raise _ShadowLiveEffect(capability, args)


class _ShadowLiveEffect(Exception):
    def __init__(self, capability: str, args: dict[str, Any]) -> None:
        super().__init__(f"live capability attempt: {capability}")
        self.capability = capability
        self.args = args


class _ShadowBlockedConnector(Exception):
    def __init__(self, capability: str, args: dict[str, Any]) -> None:
        super().__init__(f"blocked connector attempt: {capability}")
        self.capability = capability
        self.args = args


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ShadowError(message)


def _stop_active(database: FoundryDatabase,
                 stop_check: Callable[[], bool] | None) -> bool:
    if stop_check is not None:
        return bool(stop_check())
    return (Path(database.path).parent / "EMERGENCY_STOP").exists()


def _file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def shadow_run(
    database: FoundryDatabase, *, improvement_id: str,
    candidate_release_id: str, decision_fn: Callable[[Path, ShadowSink],
                                                     None],
    inputs_dir: str | Path, controller: str,
    baseline_decisions: list[dict[str, Any]] | None = None,
    live_marker: str | Path | None = None,
    stop_check: Callable[[], bool] | None = None,
    source_type: str = "supervisor", source_id: str = "v6",
) -> str:
    """Run the candidate's decision function in shadow mode."""
    _require(database.row("improvement_records", "improvement_id",
                          improvement_id) is not None,
             f"improvement {improvement_id} does not exist — refused")
    _require(database.row("release_manifests", "release_id",
                          candidate_release_id) is not None,
             f"release {candidate_release_id} has no manifest — refused")
    _require(callable(decision_fn),
             "decision_fn must be a callable(case_path, sink)")
    _require(isinstance(controller, str) and controller.strip(),
             "controller identity must be a non-empty string")
    inputs = Path(inputs_dir).expanduser().resolve()
    _require(inputs.is_dir(), f"inputs dir {inputs} is not a directory")

    run_id = "shadow-" + uuid.uuid4().hex[:12]
    run_dir = Path(database.path).parent / "shadow-scratch" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(inputs.rglob("*")):
        if path.is_file():
            target = run_dir / path.relative_to(inputs)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    stopped = _stop_active(database, stop_check)
    database.append(Event.create(
        "shadow.run_started",
        {"run_id": run_id, "improvement_id": improvement_id,
         "candidate_release_id": candidate_release_id,
         "controller": controller, "stop_observed": stopped},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"shadow.run_started:{run_id}"))
    if stopped:
        database.append(Event.create(
            "shadow.run_failed",
            {"run_id": run_id,
             "reason": "stop active — shadow frozen before any "
                       "proposal",
             "stop_observed": True, "decisions_count": 0,
             "live_attempts": 0, "blocked_attempts": 0,
             "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=f"shadow.run_failed:{run_id}"))
        raise ShadowError(
            "stop active — shadow run frozen (zero proposals made)")

    marker_path = Path(live_marker) if live_marker is not None else None
    db_before = _file_hash(Path(database.path))
    marker_before = _file_hash(marker_path) if marker_path else None

    cases = sorted(run_dir.iterdir()) if run_dir.is_dir() else []
    _require(cases, "inputs dir contains no files")
    decisions_count = 0
    live_attempts = 0
    blocked_attempts = 0
    divergences: list[dict[str, Any]] = []
    failed_reason: str | None = None
    for index, case in enumerate(cases):
        if not case.is_file():
            continue
        sink = ShadowSink()
        try:
            decision_fn(case, sink)
        except _ShadowLiveEffect as attempt:
            live_attempts += 1
            database.append(Event.create(
                "shadow.live_effect_attempted",
                {"run_id": run_id, "capability": attempt.capability,
                 "args": attempt.args, "case": case.name,
                 "controller": controller},
                source_type=source_type, source_id=source_id,
                idempotency_key=("shadow.live_effect_attempted:"
                                 f"{run_id}:{case.name}")))
            failed_reason = (
                f"live effect attempt: {attempt.capability} — zero "
                "effect violated")
            break
        except _ShadowBlockedConnector as attempt:
            blocked_attempts += 1
            database.append(Event.create(
                "shadow.blocked_connector_attempted",
                {"run_id": run_id, "capability": attempt.capability,
                 "args": attempt.args, "case": case.name,
                 "controller": controller},
                source_type=source_type, source_id=source_id,
                idempotency_key=("shadow.blocked_connector_attempted:"
                                 f"{run_id}:{case.name}")))
            failed_reason = (
                f"blocked connector attempt: {attempt.capability} — "
                "blocked connectors are never usable in shadow mode")
            break
        for proposal_index, proposal in enumerate(sink.proposals):
            if contains_secret_like(canonical_json(proposal)):
                failed_reason = ("proposal contains secret-like content "
                                 "— refused at the boundary")
                break
            database.append(Event.create(
                "shadow.decision_proposed",
                {"run_id": run_id, "case": case.name,
                 "proposal_index": proposal_index, **proposal,
                 "controller": controller},
                source_type=source_type, source_id=source_id,
                idempotency_key=("shadow.decision_proposed:"
                                 f"{run_id}:{case.name}:"
                                 f"{proposal_index}")))
            decisions_count += 1
        if failed_reason:
            break
        if baseline_decisions is not None:
            baseline = baseline_decisions[index] if index < len(
                baseline_decisions) else {}
            kinds: list[str] = []
            if baseline.get("decision") != sink.proposals[0].get(
                    "decision") if sink.proposals else False:
                kinds.append("decision")
            if sink.proposals and baseline.get("expected_effect") != \
                    sink.proposals[0].get("expected_effect"):
                kinds.append("expected_effect")
            if sink.proposals and baseline.get("cost_usd") != \
                    sink.proposals[0].get("cost_usd"):
                kinds.append("cost")
            if kinds:
                divergences.append({"case": case.name, "kinds": kinds})

    input_hash = payload_hash({"files": [
        (str(path.relative_to(run_dir)),
         hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(run_dir.rglob("*")) if path.is_file()]})
    zero_live_effects = (
        db_before == _file_hash(Path(database.path))
        and (marker_before is None
             or marker_before == _file_hash(marker_path)))

    if failed_reason is not None:
        database.append(Event.create(
            "shadow.run_failed",
            {"run_id": run_id, "reason": failed_reason,
             "stop_observed": stopped,
             "decisions_count": decisions_count,
             "live_attempts": live_attempts,
             "blocked_attempts": blocked_attempts,
             "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=f"shadow.run_failed:{run_id}"))
        raise ShadowError(failed_reason)

    database.append(Event.create(
        "shadow.run_completed",
        {"run_id": run_id, "improvement_id": improvement_id,
         "candidate_release_id": candidate_release_id,
         "decisions_count": decisions_count,
         "divergences": divergences,
         "zero_live_effects": bool(zero_live_effects),
         "stop_observed": stopped,
         "live_attempts": live_attempts,
         "blocked_attempts": blocked_attempts,
         "input_hash": input_hash, "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"shadow.run_completed:{run_id}"))
    return run_id
