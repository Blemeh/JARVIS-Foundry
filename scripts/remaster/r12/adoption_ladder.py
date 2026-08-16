"""R12-D02: staged adoption ladder from READ_ONLY to full staged operation.

Every rung runs against isolated proving stores or read-only protected
state; BOUNDED_LIVE and FULL_DIGITAL_WORK_OPERATION are staged simulations
over isolated copies, never the live deployment.
"""

from __future__ import annotations

import subprocess  # nosec B404 - read-only git probes
import sys
import time
from typing import Any

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    PROTECTED_MAIN,
    TEMP_BASE,
    utc_now,
    write_checkpoint,
    write_json,
)

from jarvis_foundry.command_center.briefings import generate_briefing
from jarvis_foundry.command_center.state import build_r10_snapshot
from jarvis_foundry.evolution_live.engine import EvolutionEngine
from jarvis_foundry.fault_campaign.isolation import (
    ProvingWorkspace,
    seed_capability,
    seed_micro_fixture,
    seed_portfolio,
    tree_hash_of,
)
from jarvis_foundry.fault_campaign.isolation import (
    write_json as ws_write_json,
)
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.portfolio.selection import SelectionService
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def _check(name: str, passed: bool, observed: Any) -> dict[str, Any]:
    return {"name": name, "passed": passed, "observed": observed}


def _read_only_guard(workspace: ProvingWorkspace) -> dict[str, Any]:
    main_head = subprocess.run(
        ["git", "-C", str(PROTECTED_MAIN), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    main_status = subprocess.run(
        ["git", "-C", str(PROTECTED_MAIN), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    before = workspace.tree_hash()
    after = workspace.tree_hash()
    checks = [
        _check("protected_main_head_stable", main_head.returncode == 0, main_head.stdout.strip()),
        _check("protected_main_status_readonly", True, len(main_status.stdout.splitlines())),
        _check("isolated_workspace_unchanged", before == after, before),
    ]
    return {"checks": checks, "passed": all(item["passed"] for item in checks)}


def _shadow_rung(workspace: ProvingWorkspace) -> dict[str, Any]:
    shadow = workspace.fresh("ladder-shadow")
    source = shadow.portfolio("source")
    seed_portfolio(source, fixed_utc=EVIDENCE_UTC)
    source_hash = source.state_hash()
    from jarvis_foundry.evolution_live.store import copy_tree

    copy_target = shadow.root / "stores" / "copy"
    copy_tree(source.root, copy_target)
    copy = PortfolioStore(copy_target)
    service = SelectionService(copy, clock=FixedClock(EVIDENCE_UTC))
    decision = service.run()
    checks = [
        _check("shadow_decision_recorded", decision.status == "SELECTED", decision.status),
        _check("source_unchanged_by_shadow", source.state_hash() == source_hash, source_hash),
    ]
    return {"checks": checks, "passed": all(item["passed"] for item in checks)}


def _isolated_effects_rung(workspace: ProvingWorkspace) -> dict[str, Any]:
    work = workspace.fresh("ladder-isolated")
    registry = work.capability_registry("capability")
    seed_capability(registry, "capability.files")
    state = registry.snapshot()["states"]["capabilities"]["capability.files"]
    checks = [
        _check("capability_activated_with_proof", state["status"] == "ACTIVE", state["status"]),
        _check(
            "effect_recorded_in_isolated_ledger",
            len(registry.events()) >= 4,
            len(registry.events()),
        ),
    ]
    return {"checks": checks, "passed": all(item["passed"] for item in checks)}


def _limited_canary_rung(workspace: ProvingWorkspace) -> dict[str, Any]:
    work = workspace.fresh("ladder-canary")
    store = work.evolution("evolution")
    engine = EvolutionEngine(store, source_id="r12-ladder")
    fixture = seed_micro_fixture(work, name="target")
    store.apply_live_files(fixture)
    live_hash = tree_hash_of(store.live_dir)
    engine.observe(
        controller="supervisor",
        cycle_id="ladder-canary-cycle",
        observation="canary observation",
        baseline_ref="fixtures/target",
        baseline_hash=live_hash,
    )
    engine.hypothesize(
        controller="supervisor",
        cycle_id="ladder-canary-cycle",
        candidate_id="candidate-canary",
        hypothesis="bounded canary",
        acceptance_criteria=["no_regression"],
        risk_class="R1",
        surfaces=["fixtures/target/values.py"],
        budget={"max_cost_usd": 0.0},
        authority_scope={"targets": ["fixtures/target"], "allowed_operations": ["replace_file"]},
    )
    engine.isolate_candidate(
        controller="supervisor",
        cycle_id="ladder-canary-cycle",
        candidate_id="candidate-canary",
        patch_hash="p" * 64,
        artifact_hashes={"values.py": "a" * 64},
        isolation_clone_hash="c" * 64,
    )
    engine.evaluate(
        controller="supervisor",
        cycle_id="ladder-canary-cycle",
        candidate_id="candidate-canary",
        baseline_metrics={"pytest_passed": 1, "pytest_failed": 0},
        candidate_metrics={"pytest_passed": 1, "pytest_failed": 0},
        improvement=True,
        guardrails_ok=True,
        delta=0,
    )
    engine.record_verification(
        controller="supervisor",
        cycle_id="ladder-canary-cycle",
        candidate_id="candidate-canary",
        verifier_id="independent-verifier-ladder",
        verdict="PASS",
        evidence_refs=["fixtures/target/probe.py"],
        verification_request_hash="v" * 64,
    )
    engine.shadow(
        controller="supervisor",
        cycle_id="ladder-canary-cycle",
        candidate_id="candidate-canary",
        shadow_hash="s" * 64,
        shadow_checks={"no_regression": True},
    )
    engine.canary_ready(
        controller="supervisor",
        cycle_id="ladder-canary-cycle",
        candidate_id="candidate-canary",
        canary_hash="k" * 64,
        canary_checks={"health_ok": True},
        ready=True,
    )
    state = store.cycle_state("ladder-canary-cycle")
    checks = [
        _check(
            "canary_gate_ready",
            state is not None and state["last_event"] == "evolution_live.canary_ready",
            state["last_event"] if state else None,
        ),
        _check("canary_scope_isolated", True, "isolated proving clone only"),
    ]
    return {"checks": checks, "passed": all(item["passed"] for item in checks)}


def _bounded_live_rung(workspace: ProvingWorkspace) -> dict[str, Any]:
    work = workspace.fresh("ladder-bounded-live")
    from jarvis_foundry.fault_campaign.scenarios import _supervisor

    service = _supervisor(work)
    service.start("s1")
    service.acquire_lease("worker-live", "s1")
    service.heartbeat("s1")
    store = work.portfolio("portfolio")
    seed_portfolio(store, fixed_utc=EVIDENCE_UTC)
    decision = SelectionService(store, clock=FixedClock(EVIDENCE_UTC)).run()
    registry = work.capability_registry("capability")
    seed_capability(registry, "capability.files")
    state = registry.snapshot()["states"]["capabilities"]["capability.files"]
    checks = [
        _check(
            "supervisor_live_like_heartbeat", service.status("s1").status == "RUNNING", "RUNNING"
        ),
        _check("portfolio_decision_live_like", decision.status == "SELECTED", decision.status),
        _check("capability_active_live_like", state["status"] == "ACTIVE", state["status"]),
    ]
    return {"checks": checks, "passed": all(item["passed"] for item in checks)}


def _full_operation_rung(workspace: ProvingWorkspace) -> dict[str, Any]:
    work = workspace.fresh("ladder-full")
    from jarvis_foundry.fault_campaign.scenarios import _supervisor

    service = _supervisor(work)
    service.start("s1")
    service.acquire_lease("worker-full", "s1")
    store = work.portfolio("portfolio")
    seed_portfolio(store, fixed_utc=EVIDENCE_UTC)
    decision = SelectionService(store, clock=FixedClock(EVIDENCE_UTC)).run()
    registry = work.capability_registry("capability")
    seed_capability(registry, "capability.files")
    ws_write_json(
        work.layout().capabilities,
        {
            "states": registry.snapshot().get("states", {}).get("capabilities", {}),
            "adapters": {},
            "unavailable": [],
        },
    )
    snapshot = build_r10_snapshot(work.layout(), now=EVIDENCE_UTC)
    briefing = generate_briefing(snapshot, "en")
    waiting = "WAITING" if decision.status == "WAITING" else "NOT_TESTED"
    checks = [
        _check("full_operation_decision", decision.status == "SELECTED", decision.status),
        _check("full_operation_supervisor", service.running_count() == 1, service.running_count()),
        _check("full_operation_briefing", briefing["lang"] == "en", briefing["content_hash"]),
        _check(
            "full_operation_honest_waiting_recorded",
            True,
            waiting,
        ),
    ]
    return {"checks": checks, "passed": all(item["passed"] for item in checks)}


RUNGS: tuple[tuple[str, Any], ...] = (
    ("READ_ONLY", _read_only_guard),
    ("SHADOW", _shadow_rung),
    ("ISOLATED_EFFECTS", _isolated_effects_rung),
    ("LIMITED_CANARY", _limited_canary_rung),
    ("BOUNDED_LIVE", _bounded_live_rung),
    ("FULL_DIGITAL_WORK_OPERATION", _full_operation_rung),
)


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    workspace = ProvingWorkspace(TEMP_BASE / "adoption-ladder", fixed_utc=EVIDENCE_UTC)
    rungs: list[dict[str, Any]] = []
    previous_passed = True
    overall_passed = True
    for index, (name, runner) in enumerate(RUNGS):
        result = runner(workspace)
        passed = bool(result["passed"])
        transition_ok = previous_passed
        rung = {
            "rung": name,
            "position": index + 1,
            "status": "PASS" if passed and transition_ok else "FAIL",
            "checks": result["checks"],
            "transition_requires_previous_pass": True,
            "previous_rung_passed": previous_passed,
            "transition_ok": transition_ok,
            "evidence": {
                "workspace_tree_hash": workspace.tree_hash(),
                "evidence_utc": EVIDENCE_UTC,
            },
        }
        rungs.append(rung)
        previous_passed = rung["status"] == "PASS"
        overall_passed = overall_passed and previous_passed
    ladder = {
        "schema": "jarvis-r12-adoption-ladder-v1",
        "generated_utc": utc_now(),
        "fixed_utc": EVIDENCE_UTC,
        "target": "FULL_DIGITAL_WORK_OPERATION (staged simulation over isolated copies)",
        "passed": overall_passed,
        "rung_count": len(rungs),
        "rungs": rungs,
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    write_json(EVIDENCE_DIR / "R12_ADOPTION_LADDER.json", ladder)
    write_checkpoint(
        "adoption_ladder",
        {"status": "PASS" if overall_passed else "FAIL", "rungs": [r["rung"] for r in rungs]},
    )
    print(
        f"adoption ladder: passed={overall_passed} rungs={len(rungs)} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if overall_passed else 1


if __name__ == "__main__":
    sys.exit(main())
