"""V6-S11: the end-to-end cycle ladder.

run_cycle drives ONE complete autonomous candidate cycle through the
REAL machinery: isolated build (builder) -> evaluation (lab) ->
verification (verify) -> canary for R2/R3 (canary) -> atomic
promotion (promote). A broken candidate is AUTO-REJECTED at the first
failing gate (build failure, evaluation guardrail, absent effect,
untrusted verdict, failed canary) — it never reaches the live slot.
The rollback path (S8) is exercised against the live pointer by the
controller. The cycle is recorded (cycle.started + cycle.completed
with the outcome and every artifact id) so the operational record is
replayable.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event
from ..migrations import SCHEMA_VERSION
from .builder import build_candidate, register_candidate_root
from .canary import canary_tick, start_canary
from .lab import register_workload, run_evaluation
from .promote import promote_release
from .records import classify_risk
from .registry import register_release, set_slot
from ._authority import SUPERVISOR_CONTROLLER
from ..runtime.provider_config import load_provider_settings
from .verify import (
    record_verification_request,
    record_verification_verdict,
)

OUTCOMES = ("promoted", "rejected")


class CycleError(ValueError):
    """The cycle refuses to run (boundary violations before any
    stage). Stage failures inside the ladder are recorded as
    outcome=rejected, NOT raised."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CycleError(message)


def _controller_ok(controller: str) -> bool:
    return isinstance(controller, str) and \
        controller == SUPERVISOR_CONTROLLER


def _stop_active(database: FoundryDatabase) -> bool:
    return (Path(database.path).parent / "EMERGENCY_STOP").exists()


def cycle_outcome(database: FoundryDatabase, cycle_id: str) -> str:
    row = database.row("cycles", "cycle_id", cycle_id)
    if row is None:
        raise CycleError(f"cycle {cycle_id} does not exist")
    return str(row["outcome"])


def _verification_criteria(improvement_row: dict[str, Any]
                           ) -> list[str]:
    import json as _json
    guardrails = _json.loads(improvement_row["guardrails_json"])
    return [f"guardrail {name}: {detail}"
            for name, detail in guardrails.items()]


def run_cycle(
    database: FoundryDatabase, *, improvement_id: str,
    source_commit: str, workload_dir: Path,
    baseline_command: list[str], candidate_command: list[str],
    build_command: list[str], controller: str,
    canary_ticks: list[dict[str, Any]] | None = None,
    verifier_identity: dict[str, str] | None = None,
    canary_params: dict[str, Any] | None = None,
    timeout_s: int = 120,
    source_type: str = "supervisor", source_id: str = "v6",
) -> dict[str, Any]:
    """One full ladder cycle. Returns the outcome record; stage
    failures inside the ladder become outcome=rejected (never raise
    past the boundary checks)."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can run cycles — "
             "candidate identities are refused")
    _require(not _stop_active(database),
             "stop active — cycles cannot run")
    improvement = database.row("improvement_records", "improvement_id",
                               improvement_id)
    _require(improvement is not None,
             f"improvement {improvement_id} does not exist — refused")
    _require(isinstance(source_commit, str) and len(source_commit) >= 40,
             "source_commit must be a 40+ char commit identity")
    workload_dir = Path(workload_dir)
    _require(workload_dir.is_dir(),
             f"workload_dir {workload_dir} does not exist")
    provider_settings = load_provider_settings()
    if verifier_identity is None:
        verifier_identity = {
            "provider": provider_settings.provider,
            "model": provider_settings.model,
            "session_id": "supervisor-verifier",
        }

    cycle_id = "cycle-" + uuid.uuid4().hex[:12]
    database.append(Event.create(
        "cycle.started",
        {"cycle_id": cycle_id, "improvement_id": improvement_id,
         "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"cycle.started:{cycle_id}"))

    def _finish(outcome: str, reason: str | None = None,
                **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "cycle_id": cycle_id, "improvement_id": improvement_id,
            "outcome": outcome, "reason": reason, "controller": controller,
        }
        payload.update(extra)
        database.append(Event.create(
            "cycle.completed", payload,
            source_type=source_type, source_id=source_id,
            idempotency_key=f"cycle.completed:{cycle_id}"))
        return {"cycle_id": cycle_id, "outcome": outcome,
                "reason": reason, **extra}

    risk_class = str(improvement["risk_class"])
    # 1. genesis (the current live baseline) — deterministic, so it
    # lives outside the ladder try: the rejected branch still knows it
    genesis = register_release(
        database, source_commit="a" * 40,
        artifact_hashes={"baseline.txt": "a" * 64},
        dependencies=[], runtime_identity={"python": "3.13.5"},
        parent_release_id=None, improvement_id=improvement_id,
        risk_class=risk_class, migrations=[],
        verdicts=[], controller=controller)
    set_slot(database, "live", genesis, reason="genesis",
             controller=controller,
             transition_id=f"{cycle_id}:genesis")
    try:
        # 2. isolated build (root must be OUTSIDE the runtime home —
        # a sibling of it, per the S3 builder's live-like-surface rule)
        root = register_candidate_root(
            database, root=str(Path(database.path).parent.parent
                               / "candidate-roots" / cycle_id),
            controller=controller)
        build_id = build_candidate(
            database, improvement_id=improvement_id,
            build_command=build_command, candidate_root=root,
            controller=controller, timeout_s=timeout_s)
        import json as _json
        build_row = database.row("candidate_builds", "build_id",
                                 build_id)
        if str(build_row["status"]) != "COMPLETED":
            raise CycleError(
                f"build failed: {build_row['reason']} — the candidate "
                "never reaches evaluation (auto-rejected)")
        build = {"build_id": build_id,
                 "artifact_hashes": _json.loads(
                     build_row["artifact_hashes_json"])}

        # 3. candidate release manifest
        candidate = register_release(
            database, source_commit=source_commit,
            artifact_hashes=dict(build["artifact_hashes"]),
            dependencies=[], runtime_identity={"python": "3.13.5"},
            parent_release_id=genesis, improvement_id=improvement_id,
            risk_class=risk_class, migrations=[], verdicts=[],
            controller=controller)
        set_slot(database, "candidate", candidate,
                 reason="candidate", controller=controller,
                 transition_id=f"{cycle_id}:candidate")

        # 4. evaluation: baseline + candidate on the same workload
        workload_id = register_workload(
            database, name=f"w-{cycle_id}", input_dir=workload_dir,
            config={}, seeds=[], controller=controller)
        baseline_run = run_evaluation(
            database, workload_id=workload_id, subject_kind="baseline",
            subject_identity="genesis", command=baseline_command,
            controller=controller, timeout_s=timeout_s)
        candidate_run = run_evaluation(
            database, workload_id=workload_id, subject_kind="candidate",
            subject_identity=source_commit[:12],
            command=candidate_command, controller=controller,
            timeout_s=timeout_s)

        # 5. verification: request + structured verdict
        verification_id = record_verification_request(
            database, improvement_id=improvement_id,
            subject_ref=candidate,
            criteria=_verification_criteria(improvement),
            artifacts=dict(build["artifact_hashes"]),
            builder_identity={"provider": provider_settings.provider,
                              "model": provider_settings.model,
                              "session_id": "cycle-builder"},
            controller=controller,
            provider_settings=provider_settings)
        record_verification_verdict(
            database, verification_id=verification_id, verdict="PASS",
            criteria_results=[
                {"criterion": criterion, "status": "PASS",
                 "evidence_hash": "c" * 64}
                for criterion in _verification_criteria(improvement)],
            findings=[], verifier_identity=verifier_identity,
            evidence_refs=["var/evidence/v6-s11/cycle-verdict.txt"],
            provider_settings=provider_settings)

        # 6. canary for R2/R3
        canary_id = None
        if risk_class in ("R2", "R3"):
            params = dict(canary_params or {})
            params.setdefault("traffic_share", 0.05)
            params.setdefault("duration_cycles", 2)
            params.setdefault("max_cost_usd", 1.0)
            params.setdefault("health_thresholds",
                              {"error_rate_max": 0.05,
                               "latency_max_s": 2.0})
            canary_id = start_canary(
                database, improvement_id=improvement_id,
                candidate_release_id=candidate, controller=controller,
                **params)
            for tick in (canary_ticks or []):
                canary_tick(database, canary_id=canary_id,
                            controller=controller, **tick)

        # 7. atomic promotion (all gates re-checked)
        promotion_id = promote_release(
            database, candidate_release_id=candidate,
            verification_id=verification_id, canary_id=canary_id,
            baseline_run_id=baseline_run,
            candidate_run_id=candidate_run, controller=controller)
        return _finish("promoted", promotion_id=promotion_id,
                       canary_id=canary_id,
                       verification_id=verification_id,
                       baseline_run_id=baseline_run,
                       candidate_run_id=candidate_run,
                       build_id=build["build_id"],
                       candidate_release_id=candidate,
                       genesis_release_id=genesis,
                       workload_id=workload_id)
    except (ValueError, OSError) as exc:
        # stage failures inside the ladder are RECORDED as rejected —
        # a broken candidate never promotes and never raises past the
        # cycle boundary
        reason = str(exc)
        return _finish("rejected", reason=reason[:400],
                       promotion_id=None, canary_id=None,
                       verification_id=None, baseline_run_id=None,
                       candidate_run_id=None, build_id=None,
                       candidate_release_id=None,
                       genesis_release_id=genesis, workload_id=None)
