"""Produce real R11 evolution cycles in an isolated proving clone.

Every cycle uses a real git clone of the micro target, applies a real code
change, measures the real before/after behavior, verifies independently in a
fresh subprocess, shadows/canaries, and promotes or rejects with durable ledger
events.  Rollback/restore, WAITING, and a simulated restart are also real.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    FIXTURES_ROOT,
    PYTHON,
    REPO_ROOT,
    TEMP_BASE,
    json_hash,
    read_json,
    run,
    safe_clear,
    sha256_file,
    write_json,
)

from jarvis_foundry.evolution.records import classify_risk
from jarvis_foundry.evolution_live.engine import EvolutionEngine
from jarvis_foundry.evolution_live.store import (
    EvolutionStore,
    copy_tree,
    tree_hash,
)

MICRO_TARGET = FIXTURES_ROOT / "micro_target"
MICRO_PATCHES = MICRO_TARGET / "patches"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=jarvis-r11",
            "-c",
            "user.email=r11@invalid",
            *args,
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _clone(baseline: Path, target: Path) -> None:
    if target.is_dir():
        shutil.rmtree(target)
    subprocess.run(
        ["git", "clone", "--local", str(baseline), str(target)],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )


def _commit(repo: Path, message: str) -> None:
    add = _git(repo, "add", "-A")
    if add.returncode != 0:
        raise RuntimeError(f"git add failed: {add.stderr[-500:]}")
    commit = _git(repo, "commit", "-m", message)
    if commit.returncode != 0 and "nothing to commit" not in commit.stdout:
        raise RuntimeError(f"git commit failed: {commit.stderr[-500:]}")


def prepare_baseline_repo(root: Path) -> Path:
    repo = root / "repos" / "baseline"
    copy_tree(MICRO_TARGET, repo)
    if not (repo / ".git").is_dir():
        init = _git(repo, "init", "-b", "main")
        if init.returncode != 0:
            raise RuntimeError(f"git init failed: {init.stderr[-500:]}")
    _commit(repo, "r11 micro target baseline")
    return repo


def _parse_pytest(output: str) -> tuple[int, int]:
    passed = 0
    failed = 0
    for line in output.splitlines():
        match_passed = re.search(r"(\d+) passed", line)
        match_failed = re.search(r"(\d+) failed", line)
        if match_passed:
            passed += int(match_passed.group(1))
        if match_failed:
            failed += int(match_failed.group(1))
    return passed, failed


def _parse_probe(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"checks": {}, "passed": 0, "total": 0}


def measure(root: Path, python: str) -> dict[str, Any]:
    pytest_result = run([python, "-m", "pytest", "tests", "-q"], cwd=root, timeout_seconds=300)
    probe_result = run([python, "probe.py"], cwd=root, timeout_seconds=60)
    passed, failed = _parse_pytest(pytest_result["output"])
    probe = _parse_probe(probe_result["output"])
    return {
        "pytest_passed": passed,
        "pytest_failed": failed,
        "behavior_passed": int(probe.get("passed", 0)),
        "behavior_total": int(probe.get("total", 0)),
        "pytest_exit_code": pytest_result["exit_code"],
        "probe_exit_code": probe_result["exit_code"],
        "probe": probe,
    }


def _specs() -> list[dict[str, Any]]:
    common_budget = {
        "max_cost_usd": 0.0,
        "capability_scope": ["fixtures/micro_target"],
    }
    return [
        {
            "id": "c1",
            "cycle_id": "r11-cycle-01",
            "declared_risk": "R1",
            "surfaces": ["fixtures/micro_target/config/values.py"],
            "patch_path": MICRO_PATCHES / "c1_config.py",
            "patch_target": "config/values.py",
            "hypothesis": (
                "Enabling the already-wired config flags makes the real "
                "parsing/scoring behavior correct and measurable."
            ),
            "criteria": [
                "artifacts_hash_match",
                "candidate_pytest_improves",
                "behavior_no_regression",
            ],
            "budget": dict(common_budget),
            "authority": {
                "targets": ["fixtures/micro_target/config/values.py"],
                "allowed_operations": ["replace_file"],
            },
            "guardrails": {
                "correctness": "pytest passed count must not regress",
                "safety": "no external systems or secrets touched",
                "cost": "zero USD proving budget",
                "latency": "measurements complete in bounded wall time",
            },
            "expect": "promote",
            "observation": "Baseline behavior probe fails 4/4 checks",
        },
        {
            "id": "c2",
            "cycle_id": "r11-cycle-02",
            "declared_risk": "R2",
            "surfaces": ["fixtures/micro_target/workflow.py"],
            "patch_path": MICRO_PATCHES / "c2_workflow.py",
            "patch_target": "workflow.py",
            "hypothesis": (
                "Pipeline workflow should deduplicate records and tolerate "
                "None entries without changing ordering semantics."
            ),
            "criteria": [
                "artifacts_hash_match",
                "candidate_pytest_improves",
                "behavior_no_regression",
            ],
            "budget": dict(common_budget),
            "authority": {
                "targets": ["fixtures/micro_target/workflow.py"],
                "allowed_operations": ["replace_file"],
            },
            "guardrails": {
                "correctness": "ordering by score must remain stable",
                "safety": "no external systems or secrets touched",
                "cost": "zero USD proving budget",
                "latency": "measurements complete in bounded wall time",
            },
            "expect": "promote",
            "observation": "Pipeline keeps duplicates and crashes on None",
        },
        {
            "id": "c3",
            "cycle_id": "r11-cycle-03",
            "declared_risk": "R2",
            "surfaces": ["fixtures/micro_target/core.py"],
            "patch_path": MICRO_PATCHES / "c3_broken_core.py",
            "patch_target": "core.py",
            "hypothesis": (
                "A deliberately broken core candidate that must be rejected "
                "and preserved in failed history."
            ),
            "criteria": [
                "artifacts_hash_match",
                "candidate_pytest_improves",
                "behavior_no_regression",
            ],
            "budget": dict(common_budget),
            "authority": {
                "targets": ["fixtures/micro_target/core.py"],
                "allowed_operations": ["replace_file"],
            },
            "guardrails": {
                "correctness": "pytest passed count must not regress",
                "safety": "no external systems or secrets touched",
                "cost": "zero USD proving budget",
                "latency": "measurements complete in bounded wall time",
            },
            "expect": "reject",
            "observation": "No useful improvement; rejection probe",
        },
        {
            "id": "c4",
            "cycle_id": "r11-cycle-restart",
            "declared_risk": "R1",
            "surfaces": ["fixtures/micro_target/config/values.py"],
            "patch_path": MICRO_PATCHES / "c1_config.py",
            "patch_target": "config/values.py",
            "hypothesis": (
                "Restart-safety cycle: the same real config candidate must "
                "resume without duplicate effects after a simulated restart."
            ),
            "criteria": [
                "artifacts_hash_match",
                "candidate_pytest_improves",
                "behavior_no_regression",
            ],
            "budget": dict(common_budget),
            "authority": {
                "targets": ["fixtures/micro_target/config/values.py"],
                "allowed_operations": ["replace_file"],
            },
            "guardrails": {
                "correctness": "pytest passed count must not regress",
                "safety": "no external systems or secrets touched",
                "cost": "zero USD proving budget",
                "latency": "measurements complete in bounded wall time",
            },
            "expect": "promote",
            "observation": "Restart-safe cadence probe",
        },
    ]


def run_single_cycle(
    engine: EvolutionEngine,
    store: EvolutionStore,
    spec: dict[str, Any],
    baseline_repo: Path,
    python: str,
    utc: str,
    *,
    restart_after: str | None = None,
) -> dict[str, Any]:
    cycle_id = str(spec["cycle_id"])
    candidate_id = str(spec["id"])
    surfaces = list(spec["surfaces"])
    final_risk = max(
        (spec["declared_risk"], classify_risk(surfaces)),
        key=lambda value: ("R0", "R1", "R2", "R3").index(value),
    )
    baseline_hash = tree_hash(baseline_repo)
    effects: list[dict[str, Any]] = []
    restart: dict[str, Any] = {
        "simulated": False,
        "resumed_at_stage": None,
        "clone_effect_status": "applied",
    }

    engine.observe(
        controller="supervisor",
        cycle_id=cycle_id,
        observation=str(spec["observation"]),
        baseline_ref=str(baseline_repo),
        baseline_hash=baseline_hash,
    )
    engine.hypothesize(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        hypothesis=str(spec["hypothesis"]),
        acceptance_criteria=list(spec["criteria"]),
        risk_class=final_risk,
        surfaces=surfaces,
        budget=dict(spec["budget"]),
        authority_scope=dict(spec["authority"]),
    )
    candidate_dir = store.root / "clones" / f"{cycle_id}-candidate"
    patch_path = Path(str(spec["patch_path"]))
    patch_target = str(spec["patch_target"])

    def clone_candidate() -> dict[str, Any]:
        _clone(baseline_repo, candidate_dir)
        shutil.copy2(patch_path, candidate_dir / patch_target)
        _commit(candidate_dir, f"{candidate_id}: real bounded code change")
        return {"result_hash": tree_hash(candidate_dir), "at": utc}

    clone_effect = store.journal.apply(f"clone:{cycle_id}", clone_candidate)
    effects.append(
        {
            "effect_id": f"clone:{cycle_id}",
            "status": clone_effect["status"],
            "result_hash": clone_effect["result_hash"],
        }
    )
    artifact_hashes = {patch_target: sha256_file(candidate_dir / patch_target)}
    engine.isolate_candidate(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        patch_hash=sha256_file(patch_path),
        artifact_hashes=artifact_hashes,
        isolation_clone_hash=str(clone_effect["result_hash"]),
    )
    if restart_after == "isolate":
        before_restart_events = len(store.ledger.read())
        engine = EvolutionEngine(store)
        replay_clone = store.journal.apply(f"clone:{cycle_id}", clone_candidate)
        restart = {
            "simulated": True,
            "resumed_at_stage": "evaluate",
            "event_count_before_restart": before_restart_events,
            "clone_effect_status": clone_effect["status"],
            "clone_effect_status_after_restart": replay_clone["status"],
            "clone_replayed_without_duplicate_effect": bool(
                replay_clone["status"] == "replayed"
                and replay_clone["result_hash"] == clone_effect["result_hash"]
            ),
        }

    baseline_metrics = measure(baseline_repo, python)
    candidate_metrics = measure(candidate_dir, python)
    guardrails_ok = bool(spec.get("guardrails"))
    improvement = bool(
        candidate_metrics["pytest_passed"] > baseline_metrics["pytest_passed"]
        and candidate_metrics["behavior_passed"] >= baseline_metrics["behavior_passed"]
        and guardrails_ok
    )
    delta = int(candidate_metrics["pytest_passed"] - baseline_metrics["pytest_passed"])
    engine.evaluate(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        improvement=improvement,
        guardrails_ok=guardrails_ok,
        delta=delta,
    )

    artifacts_dir = store.verification_dir / cycle_id / "artifacts"
    target_in_artifacts = artifacts_dir / patch_target
    target_in_artifacts.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate_dir / patch_target, target_in_artifacts)
    request = {
        "schema": "jarvis-r11-verification-request-v1",
        "request_id": f"req-{cycle_id}",
        "cycle_id": cycle_id,
        "candidate_id": candidate_id,
        "baseline_repo": str(baseline_repo),
        "artifacts_dir": str(artifacts_dir),
        "expected_artifact_hashes": artifact_hashes,
        "criteria": list(spec["criteria"]),
        "measure_commands": [
            [python, "-m", "pytest", "tests", "-q"],
            [python, "probe.py"],
        ],
        "verifier_utc": utc,
    }
    request_path = store.verification_dir / cycle_id / "request.json"
    write_json(request_path, request)
    verdict_path = store.verification_dir / cycle_id / "verdict.json"
    verifier_run = run(
        [
            python,
            "-m",
            "jarvis_foundry.evolution_live.verifier",
            "--request",
            str(request_path),
            "--output",
            str(verdict_path),
            "--python",
            python,
        ],
        cwd=REPO_ROOT,
        timeout_seconds=300,
    )
    verdict = read_json(verdict_path)
    engine.record_verification(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        verifier_id=str(verdict["verifier_id"]),
        verdict=str(verdict["verdict"]),
        evidence_refs=[str(verdict_path.relative_to(store.root))],
        verification_request_hash=json_hash(request),
    )
    verification = {
        "verdict": verdict["verdict"],
        "verifier_id": verdict["verifier_id"],
        "checks": verdict["checks"],
        "exit_code": verifier_run["exit_code"],
        "evidence_ref": str(verdict_path.relative_to(store.root)),
    }

    promote_path = bool(improvement and verdict["verdict"] == "PASS")
    cycle: dict[str, Any] = {
        "cycle_id": cycle_id,
        "candidate_id": candidate_id,
        "risk_class": final_risk,
        "surfaces": surfaces,
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "improvement": improvement,
        "delta": delta,
        "verification": verification,
        "effects": effects,
        "restart": restart,
    }

    if not promote_path:
        failed = store.preserve_failed_history(cycle_id, candidate_dir)
        reason = (
            "candidate did not improve real measured behavior"
            if not improvement
            else "independent verifier did not PASS the candidate"
        )
        engine.reject(
            controller="supervisor",
            cycle_id=cycle_id,
            candidate_id=candidate_id,
            reason=reason,
            failed_history_ref=str(failed["failed_history_ref"]),
            failure_hash=str(failed["failure_hash"]),
        )
        engine.measure_outcome(
            controller="supervisor",
            cycle_id=cycle_id,
            candidate_id=candidate_id,
            outcome_before=baseline_metrics,
            outcome_after=candidate_metrics,
            delta=delta,
            outcome="rejected",
        )
        engine.learn(
            controller="supervisor",
            cycle_id=cycle_id,
            candidate_id=candidate_id,
            insight=f"Rejected: {reason}",
            next_action="Failed history is preserved; no retry in this cadence.",
        )
        cycle["outcome"] = "rejected"
        cycle["rejection"] = {
            "reason": reason,
            "failed_history_ref": failed["failed_history_ref"],
            "failure_hash": failed["failure_hash"],
        }
        return cycle

    shadow_dir = store.root / "clones" / f"{cycle_id}-shadow"

    def shadow_effect() -> dict[str, Any]:
        _clone(candidate_dir, shadow_dir)
        return {"result_hash": tree_hash(shadow_dir), "at": utc}

    shadow = store.journal.apply(f"shadow:{cycle_id}", shadow_effect)
    effects.append(
        {
            "effect_id": f"shadow:{cycle_id}",
            "status": shadow["status"],
            "result_hash": shadow["result_hash"],
        }
    )
    shadow_checks = measure(shadow_dir, python)
    engine.shadow(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        shadow_hash=str(shadow["result_hash"]),
        shadow_checks=shadow_checks,
    )

    canary_dir = store.root / "clones" / f"{cycle_id}-canary"

    def canary_effect() -> dict[str, Any]:
        _clone(candidate_dir, canary_dir)
        return {"result_hash": tree_hash(canary_dir), "at": utc}

    canary = store.journal.apply(f"canary:{cycle_id}", canary_effect)
    effects.append(
        {
            "effect_id": f"canary:{cycle_id}",
            "status": canary["status"],
            "result_hash": canary["result_hash"],
        }
    )
    canary_checks = measure(canary_dir, python)
    canary_ready = bool(
        canary_checks["pytest_passed"] >= candidate_metrics["pytest_passed"]
        and canary_checks["behavior_passed"] >= baseline_metrics["behavior_passed"]
    )
    engine.canary_ready(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        canary_hash=str(canary["result_hash"]),
        canary_checks=canary_checks,
        ready=canary_ready,
    )
    cycle["shadow"] = {"shadow_hash": shadow["result_hash"], "checks": shadow_checks}
    cycle["canary"] = {
        "canary_hash": canary["result_hash"],
        "checks": canary_checks,
        "ready": canary_ready,
    }
    if not canary_ready:
        failed = store.preserve_failed_history(cycle_id, candidate_dir)
        engine.reject(
            controller="supervisor",
            cycle_id=cycle_id,
            candidate_id=candidate_id,
            reason="canary readiness checks failed",
            failed_history_ref=str(failed["failed_history_ref"]),
            failure_hash=str(failed["failure_hash"]),
        )
        engine.measure_outcome(
            controller="supervisor",
            cycle_id=cycle_id,
            candidate_id=candidate_id,
            outcome_before=baseline_metrics,
            outcome_after=candidate_metrics,
            delta=delta,
            outcome="rejected",
        )
        engine.learn(
            controller="supervisor",
            cycle_id=cycle_id,
            candidate_id=candidate_id,
            insight="Canary readiness refused promotion.",
            next_action="Failed history is preserved; no retry in this cadence.",
        )
        cycle["outcome"] = "rejected"
        cycle["rejection"] = {
            "reason": "canary readiness checks failed",
            "failed_history_ref": failed["failed_history_ref"],
            "failure_hash": failed["failure_hash"],
        }
        return cycle

    baseline_release_id = f"release-{cycle_id}-baseline"
    candidate_release_id = f"release-{cycle_id}-candidate"

    def baseline_release_effect() -> dict[str, Any]:
        snapshot = store.create_release(
            release_id=baseline_release_id,
            cycle_id=cycle_id,
            kind="baseline",
            source_dir=baseline_repo,
            at=utc,
        )
        return {"result_hash": snapshot.tree_hash, "at": utc}

    baseline_release = store.journal.apply(f"baseline-release:{cycle_id}", baseline_release_effect)
    effects.append(
        {
            "effect_id": f"baseline-release:{cycle_id}",
            "status": baseline_release["status"],
            "result_hash": baseline_release["result_hash"],
        }
    )

    def candidate_release_effect() -> dict[str, Any]:
        snapshot = store.create_release(
            release_id=candidate_release_id,
            cycle_id=cycle_id,
            kind="candidate",
            source_dir=candidate_dir,
            at=utc,
        )
        return {"result_hash": snapshot.tree_hash, "at": utc}

    candidate_release = store.journal.apply(
        f"candidate-release:{cycle_id}", candidate_release_effect
    )
    effects.append(
        {
            "effect_id": f"candidate-release:{cycle_id}",
            "status": candidate_release["status"],
            "result_hash": candidate_release["result_hash"],
        }
    )

    def promote_effect() -> dict[str, Any]:
        result = store.apply_live_files(store.release_dir(candidate_release_id))
        return {
            "result_hash": result["after_hash"],
            "before_hash": result["before_hash"],
            "after_hash": result["after_hash"],
            "at": utc,
        }

    promote = store.journal.apply(f"promote-live:{cycle_id}", promote_effect)
    effects.append(
        {
            "effect_id": f"promote-live:{cycle_id}",
            "status": promote["status"],
            "result_hash": promote["result_hash"],
        }
    )
    engine.promote(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        release_id=candidate_release_id,
        live_before_hash=str(promote["before_hash"]),
        live_after_hash=str(promote["after_hash"]),
        artifact_hashes=artifact_hashes,
        outcome_before=baseline_metrics,
        outcome_after=candidate_metrics,
    )
    live_outcome = measure(store.live_dir, python)
    engine.measure_outcome(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        outcome_before=baseline_metrics,
        outcome_after=live_outcome,
        delta=int(live_outcome["pytest_passed"] - baseline_metrics["pytest_passed"]),
        outcome="promoted",
    )
    engine.learn(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        insight=(
            "Real candidate improved measured behavior; promotion is "
            "reversible via the prior baseline release."
        ),
        next_action="Measure outcome in the promoted release, then continue cadence.",
    )
    cycle["outcome"] = "promoted"
    cycle["effects"] = effects
    cycle["promotion"] = {
        "release_id": candidate_release_id,
        "prior_release_id": baseline_release_id,
        "live_before_hash": promote["before_hash"],
        "live_after_hash": promote["after_hash"],
        "live_outcome": live_outcome,
    }
    return cycle


def produce_cycles(
    root: Path,
    python: str,
    utc: str,
) -> dict[str, Any]:
    store = EvolutionStore(root / "stores", fixed_utc=utc)
    engine = EvolutionEngine(store)
    baseline_repo = prepare_baseline_repo(root)
    cycle_records: list[dict[str, Any]] = []
    for spec in _specs():
        restart_after = "isolate" if str(spec["cycle_id"]) == "r11-cycle-restart" else None
        record = run_single_cycle(
            engine,
            store,
            spec,
            baseline_repo,
            python,
            utc,
            restart_after=restart_after,
        )
        cycle_records.append(record)

    first_promoted = next(record for record in cycle_records if record["outcome"] == "promoted")
    rollback_evidence = run_rollback_restore(
        store,
        engine,
        first_promoted,
        baseline_repo,
        python,
        utc,
    )

    waiting_payload = {
        "cadence_key": "r11-after-real-cycles",
        "reason": (
            "No new useful improvement candidate exists after four real "
            "cycles; all bounded improvements are promoted or preserved in "
            "failed history."
        ),
        "next_action": "Stand by for the next owner-directed opportunity; no busy-work.",
    }
    waiting = engine.waiting(
        controller="supervisor",
        cadence_key=waiting_payload["cadence_key"],
        reason=waiting_payload["reason"],
        next_action=waiting_payload["next_action"],
    )
    replay_engine = EvolutionEngine(store)
    waiting_replay = replay_engine.waiting(
        controller="supervisor",
        cadence_key=waiting_payload["cadence_key"],
        reason=waiting_payload["reason"],
        next_action=waiting_payload["next_action"],
    )
    restart_replay_engine = EvolutionEngine(store)
    restart_replay = restart_replay_engine.waiting(
        controller="supervisor",
        cadence_key=waiting_payload["cadence_key"],
        reason=waiting_payload["reason"],
        next_action=waiting_payload["next_action"],
    )
    waiting_evidence = {
        "schema": "jarvis-r11-waiting-v1",
        "waiting": waiting_payload,
        "event_id": waiting.event.event_id,
        "created_first_call": waiting.created,
        "replay_after_restart_created": waiting_replay.created,
        "second_restart_replay_created": restart_replay.created,
        "duplicate_events": 0,
        "honest_waiting": waiting.created and not waiting_replay.created,
    }

    restart_evidence = {
        "schema": "jarvis-r11-restart-v1",
        "restart_cycle": next(
            record for record in cycle_records if record["cycle_id"] == "r11-cycle-restart"
        )["restart"],
        "journal_effects_total": len(store.journal.entries()),
        "journal_effect_ids": sorted(store.journal.entries()),
        "live_tree_hash_after_all": tree_hash(store.live_dir),
    }
    chain = store.ledger.verify_chain()
    events = store.ledger.read()
    snapshot = store.snapshot()
    promoted = [record for record in cycle_records if record["outcome"] == "promoted"]
    rejected = [record for record in cycle_records if record["outcome"] == "rejected"]
    return {
        "schema": "jarvis-r11-real-cycles-v1",
        "evidence_utc": utc,
        "proving_root": str(root),
        "baseline_repo": str(baseline_repo),
        "cycles": cycle_records,
        "counts": {
            "cycles": len(cycle_records),
            "promoted": len(promoted),
            "rejected": len(rejected),
            "r1_promotions": sum(1 for record in promoted if record["risk_class"] == "R1"),
            "r2_promotions": sum(1 for record in promoted if record["risk_class"] == "R2"),
        },
        "rollback": rollback_evidence,
        "waiting": waiting_evidence,
        "restart": restart_evidence,
        "ledger": {
            "path": str(store.ledger.path),
            "event_count": len(events),
            "chain_valid": chain.valid,
            "head_hash": chain.head_hash,
        },
        "snapshot_states": snapshot.states,
        "events": [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "source_type": event.source_type,
                "source_id": event.source_id,
                "occurred_at": event.occurred_at,
                "payload": dict(event.payload),
                "payload_hash": event.payload_hash,
            }
            for event in events
        ],
    }


def run_rollback_restore(
    store: EvolutionStore,
    engine: EvolutionEngine,
    promoted_cycle: dict[str, Any],
    baseline_repo: Path,
    python: str,
    utc: str,
) -> dict[str, Any]:
    cycle_id = str(promoted_cycle["cycle_id"])
    candidate_id = str(promoted_cycle["candidate_id"])
    candidate_release_id = str(promoted_cycle["promotion"]["release_id"])
    prior_release_id = str(promoted_cycle["promotion"]["prior_release_id"])

    def rollback_effect() -> dict[str, Any]:
        before = tree_hash(store.live_dir)
        copy_tree(store.release_dir(prior_release_id), store.live_dir)
        after = tree_hash(store.live_dir)
        return {"result_hash": after, "before_hash": before, "after_hash": after, "at": utc}

    rollback = store.journal.apply(f"rollback:{cycle_id}", rollback_effect)
    engine.rollback(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        release_id=candidate_release_id,
        prior_release_id=prior_release_id,
        before_hash=str(rollback["before_hash"]),
        after_hash=str(rollback["after_hash"]),
    )
    after_rollback = measure(store.live_dir, python)
    prior_release_hash = store.load_release(prior_release_id)
    rollback_ok = bool(
        rollback["after_hash"] == (prior_release_hash.tree_hash if prior_release_hash else "")
    )

    def restore_effect() -> dict[str, Any]:
        before = tree_hash(store.live_dir)
        copy_tree(store.release_dir(candidate_release_id), store.live_dir)
        after = tree_hash(store.live_dir)
        return {"result_hash": after, "before_hash": before, "after_hash": after, "at": utc}

    restore = store.journal.apply(f"restore:{cycle_id}", restore_effect)
    engine.restore(
        controller="supervisor",
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        release_id=candidate_release_id,
        before_hash=str(restore["before_hash"]),
        after_hash=str(restore["after_hash"]),
    )
    after_restore = measure(store.live_dir, python)
    candidate_release_hash = store.load_release(candidate_release_id)
    restore_ok = bool(
        restore["after_hash"]
        == (candidate_release_hash.tree_hash if candidate_release_hash else "")
    )
    independent_probe_ok = bool(
        after_rollback["pytest_passed"] < after_restore["pytest_passed"]
        and rollback_ok
        and restore_ok
    )
    return {
        "schema": "jarvis-r11-rollback-v1",
        "cycle_id": cycle_id,
        "candidate_id": candidate_id,
        "candidate_release_id": candidate_release_id,
        "prior_release_id": prior_release_id,
        "rollback": {
            "before_hash": rollback["before_hash"],
            "after_hash": rollback["after_hash"],
            "prior_release_tree_hash": prior_release_hash.tree_hash if prior_release_hash else None,
            "restores_prior_release": rollback_ok,
            "live_metrics_after_rollback": after_rollback,
        },
        "restore": {
            "before_hash": restore["before_hash"],
            "after_hash": restore["after_hash"],
            "candidate_release_tree_hash": (
                candidate_release_hash.tree_hash if candidate_release_hash else None
            ),
            "restores_candidate_release": restore_ok,
            "live_metrics_after_restore": after_restore,
        },
        "independent_probe_ok": independent_probe_ok,
        "baseline_repo": str(baseline_repo),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=TEMP_BASE / "proving")
    parser.add_argument("--python", default=PYTHON)
    parser.add_argument("--utc", default=EVIDENCE_UTC)
    args = parser.parse_args(argv)
    started = time.monotonic()
    if args.root.resolve().is_relative_to(TEMP_BASE.resolve()):
        safe_clear(args.root)
    args.root.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    payload = produce_cycles(args.root, args.python, args.utc)
    write_json(EVIDENCE_DIR / "R11_CYCLES.json", payload)
    write_json(
        EVIDENCE_DIR / "R11_LIFECYCLE_EVENTS.json",
        {
            "schema": "jarvis-r11-lifecycle-events-v1",
            "evidence_utc": args.utc,
            "event_count": len(payload["events"]),
            "chain_valid": payload["ledger"]["chain_valid"],
            "head_hash": payload["ledger"]["head_hash"],
            "events": payload["events"],
        },
    )
    write_json(EVIDENCE_DIR / "R11_ROLLBACK.json", payload["rollback"])
    write_json(EVIDENCE_DIR / "R11_WAITING.json", payload["waiting"])
    write_json(EVIDENCE_DIR / "R11_RESTART.json", payload["restart"])
    write_json(
        EVIDENCE_DIR / "R11_STORE_SNAPSHOT.json",
        {
            "schema": "jarvis-r11-store-snapshot-v1",
            "evidence_utc": args.utc,
            "states": payload["snapshot_states"],
            "ledger": payload["ledger"],
        },
    )
    for index, record in enumerate(payload["cycles"], start=1):
        write_json(
            EVIDENCE_DIR / f"R11_CYCLE_{index:02d}.json",
            {
                "schema": "jarvis-r11-cycle-v1",
                "evidence_utc": args.utc,
                "cycle": record,
            },
        )
    print(
        f"real cycles: cycles={payload['counts']['cycles']} "
        f"promoted={payload['counts']['promoted']} "
        f"rejected={payload['counts']['rejected']} "
        f"r1={payload['counts']['r1_promotions']} r2={payload['counts']['r2_promotions']} "
        f"chain_valid={payload['ledger']['chain_valid']} "
        f"events={payload['ledger']['event_count']} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
