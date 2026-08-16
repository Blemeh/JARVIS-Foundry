"""Run equivalent relevant inputs through the FROZEN V7 release tree.

Executed as a subprocess with the snapshot src directory prepended to
sys.path. The snapshot is a read-only git archive of the protected main
tag v8-pre-promotion-93a3ddc; nothing in the protected main is mutated.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

snapshot = Path(sys.argv[1])
sys.path.insert(0, str(snapshot / "src"))

from jarvis_foundry.capabilities.registry import (
    broker_mission_need,
    discover_capabilities,
    register_capability,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.cycle import run_cycle
from jarvis_foundry.evolution.portfolio import (
    register_project,
    score_projects,
    select_projects,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.scheduler import (
    enable_scheduler,
    register_schedule_item,
    scheduler_state,
)
from jarvis_foundry.reporting.briefing import generate_briefing
from jarvis_foundry.supervisor.leases import LeaseManager


def _workload_hash(workload_dir: Path) -> str:
    entries: dict[str, str] = {}
    for path in sorted(workload_dir.rglob("*")):
        if path.is_file():
            entries[path.relative_to(workload_dir).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()


def _portfolio(database: FoundryDatabase) -> dict[str, Any]:
    register_project(
        database,
        controller="supervisor",
        project_id="p-alpha",
        name="Alpha goal",
        goals=[{"goal_id": "g-alpha", "objective": "alpha objective"}],
        ev=0.9,
        urgency=0.8,
        information_gain=0.7,
        reversibility=0.6,
        opportunity_cost=0.2,
        status="PLANNED",
        provenance={"source": "comparison-input"},
        source_type="comparison",
        source_id="v7",
    )
    register_project(
        database,
        controller="supervisor",
        project_id="p-beta",
        name="Beta goal",
        goals=[{"goal_id": "g-beta", "objective": "beta objective"}],
        ev=0.4,
        urgency=0.3,
        information_gain=0.2,
        reversibility=0.9,
        opportunity_cost=0.4,
        status="PLANNED",
        provenance={"source": "comparison-input"},
        source_type="comparison",
        source_id="v7",
    )
    scored = score_projects(database)
    selected = select_projects(
        database,
        controller="supervisor",
        available_slots=1,
        source_type="comparison",
        source_id="v7",
    )
    return {
        "scored": scored,
        "selected_project_ids": [item["project_id"] for item in selected],
    }


def _orchestration(database: FoundryDatabase) -> dict[str, Any]:
    enable_scheduler(
        database,
        controller="supervisor",
        source_type="comparison",
        source_id="v7",
    )
    register_schedule_item(
        database,
        item_id="mission-alpha",
        priority=1,
        cooldown_seconds=60.0,
        budget_usd=0.0,
        controller="supervisor",
        source_type="comparison",
        source_id="v7",
    )
    leases = LeaseManager(database)
    leases.acquire("worker-alpha", "supervisor", 30)
    leases.release("worker-alpha", "supervisor")
    return {
        "scheduler": scheduler_state(database),
        "lease_acquired_and_released": True,
    }


def _capability_lifecycle(database: FoundryDatabase) -> dict[str, Any]:
    register_capability(
        database,
        capability_id="capability.files",
        name="File capability",
        family="files",
        surfaces=["fixtures/target"],
        effect_class="file-write",
        risk="R1",
        verification_hint="artifact hash + read-back",
        controller="supervisor",
        source_type="comparison",
        source_id="v7",
    )
    discovered = discover_capabilities(database)
    matched = broker_mission_need(
        database,
        need_id="need-alpha",
        surfaces=["fixtures/target"],
        controller="supervisor",
        source_type="comparison",
        source_id="v7",
    )
    return {
        "capabilities": discovered,
        "matched_capability_ids": matched,
    }


def _briefings(database: FoundryDatabase) -> dict[str, Any]:
    en = generate_briefing(database, locale="en", source_type="comparison", source_id="v7")
    sq = generate_briefing(database, locale="sq", source_type="comparison", source_id="v7")
    return {
        "en_present": bool(en),
        "sq_present": bool(sq),
        "en_sample": en.splitlines()[:3],
        "sq_sample": sq.splitlines()[:3],
    }


def _evolution_cycle(database: FoundryDatabase, workload_dir: Path) -> dict[str, Any]:
    improvement_id = record_improvement(
        database,
        problem="micro target scoring flag needs verification",
        evidence_refs=["fixtures/target/probe.py"],
        provenance={"source": "comparison-input"},
        hypothesis="a broken candidate is auto-rejected",
        expected_mechanism="build gate fails before evaluation",
        baseline_metric={
            "name": "probe_passed",
            "unit": "count",
            "source": "fixtures/target/probe.py",
            "value": 0.0,
        },
        mmi={"minimum_delta": 1, "direction": "increase", "unit": "count"},
        guardrails={
            "correctness": "pytest passed count must not regress",
            "safety": "no external systems touched",
            "cost": "zero USD",
            "latency": "bounded",
        },
        workload={"ref": "fixtures/target", "hash": _workload_hash(workload_dir)},
        observation={"requirement": "measure probe result", "cycles": 1},
        declared_risk="R1",
        surfaces=["fixtures/target/config"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["fixtures/target"]},
        reversibility={
            "plan": "rollback release",
            "rollback_threshold": "no measured improvement",
        },
        source_type="comparison",
        source_id="v7",
    )
    result = run_cycle(
        database,
        improvement_id=improvement_id,
        source_commit="a" * 40,
        workload_dir=workload_dir,
        baseline_command=[sys.executable, "probe.py"],
        candidate_command=[sys.executable, "probe.py"],
        build_command=[sys.executable, "-c", "raise SystemExit(1)"],
        controller="supervisor",
        canary_ticks=[],
        source_type="comparison",
        source_id="v7",
    )
    return {
        "improvement_id": improvement_id,
        "outcome": result.get("outcome"),
        "reason": (result.get("reason") or "")[:160],
    }


def main() -> int:
    root = Path(snapshot) / "r12-v7-proving"
    root.mkdir(parents=True, exist_ok=True)
    workload_dir = root / "workload"
    workload_dir.mkdir(parents=True, exist_ok=True)
    (workload_dir / "probe.py").write_text(
        "import sys\nprint('probe ok')\nraise SystemExit(0 if len(sys.argv) > 1 else 1)\n",
        encoding="utf-8",
    )
    database = FoundryDatabase(root / "v7.db")
    database.initialize()
    output = {
        "schema": "jarvis-r12-v7-comparison-v1",
        "release_tag": "v8-pre-promotion-93a3ddc",
        "snapshot": str(snapshot),
        "domains": {
            "goals_portfolio": _portfolio(database),
            "mission_orchestration": _orchestration(database),
            "capability_lifecycle": _capability_lifecycle(database),
            "command_center_briefing": _briefings(database),
            "evolution_cycle": _evolution_cycle(database, workload_dir),
        },
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
