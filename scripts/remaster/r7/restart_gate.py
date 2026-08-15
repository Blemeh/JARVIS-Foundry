"""R7-G05 restart-safe cadence gate."""

from __future__ import annotations

import sys
import time
from typing import Any

from common import EVIDENCE_DIR, EVIDENCE_UTC, PROVING_ROOT, SNAPSHOT_DIR, write_json
from gate_helpers import (
    candidate_from_discovery,
    fixed_clock,
    mission_integration_report,
    monitor_proving_copy,
    new_budget,
    new_ledger,
    observations_from_manifest,
    project_from_manifest,
    research_source_from_staged,
    staged_manifest,
)

from jarvis_foundry.kernel.portfolio.discovery import DiscoveryService
from jarvis_foundry.kernel.portfolio.monitor import MonitorService
from jarvis_foundry.kernel.portfolio.research import ResearchService
from jarvis_foundry.kernel.portfolio.selection import SelectionService
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def _run_once(manifest, *, store_path, ledger_path, source_file, mutable) -> dict[str, Any]:
    store = PortfolioStore(store_path)
    clock = fixed_clock()
    for project_id in manifest["projects"]:
        store.append_project(project_from_manifest(project_id, manifest))
    observations = observations_from_manifest(manifest, EVIDENCE_UTC)
    for observation in observations:
        result = DiscoveryService(store).discover(observation)
        if result.created:
            project = store.project(observation.project_id)
            discovery = store.latest("discovered_candidate", result.candidate_id or "")
            if project is not None and discovery is not None:
                store.append_candidate(candidate_from_discovery(discovery, project, EVIDENCE_UTC))
    decision = SelectionService(store, budget=new_budget("restart"), clock=clock).run()
    monitor = MonitorService(store, clock=clock)
    monitor.register_source("restart-health", locator=str(mutable), freshness_seconds=900)
    monitor.poll("restart-health", lambda: (mutable.read_bytes(), EVIDENCE_UTC))
    change = monitor.poll("restart-health", lambda: (mutable.read_bytes(), EVIDENCE_UTC))
    research = ResearchService(store, snapshot_root=SNAPSHOT_DIR / "restart", clock=clock).run(
        question="Should restart-safe cadence be preserved?",
        project_id="jarvis-foundry",
        value_statement="no duplicate effects after restart",
        sources=[
            research_source_from_staged(
                "jarvis-foundry-git-status", "projects/jarvis-foundry/git_status.txt"
            )
        ],
        budget=new_budget("restart-research"),
    )
    ledger = new_ledger("kernel-restart")
    mission = None
    if decision.selected_candidate_id:
        project = store.project(decision.project_id or "")
        if project is not None:
            report = mission_integration_report(store, ledger, decision, project)
            mission = {
                "mission_id": report["mission_id"],
                "goal_created": report["goal_created"],
                "chain_valid": report["chain_valid"],
            }
    return {
        "decision_id": decision.decision_id,
        "decision_status": decision.status,
        "change_candidate_id": change.candidate_id,
        "research_id": research.research_id,
        "mission": mission,
        "discovery_count": len(store.discoveries()),
        "decision_count": len(store.decisions()),
        "research_count": len(store.research()),
        "ledger_events": len(ledger.replay()),
    }


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    manifest = staged_manifest()
    store_path = PROVING_ROOT / "portfolio-restart"
    ledger_path = PROVING_ROOT / "kernel-restart"
    _, mutable = monitor_proving_copy("restart-health")
    if not mutable.read_text(encoding="utf-8", errors="replace").strip():
        mutable.write_text("baseline\n", encoding="utf-8")
    first = _run_once(
        manifest,
        store_path=store_path,
        ledger_path=ledger_path,
        source_file=None,
        mutable=mutable,
    )
    second = _run_once(
        manifest,
        store_path=store_path,
        ledger_path=ledger_path,
        source_file=None,
        mutable=mutable,
    )
    passed = bool(
        first["decision_id"] == second["decision_id"]
        and first["change_candidate_id"] == second["change_candidate_id"]
        and first["research_id"] == second["research_id"]
        and first["mission"] == second["mission"]
        and first["discovery_count"] == second["discovery_count"]
        and first["decision_count"] == second["decision_count"]
        and first["research_count"] == second["research_count"]
        and first["ledger_events"] == second["ledger_events"]
    )
    payload: dict[str, Any] = {
        "schema": "jarvis-r7-restart-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": 0.0,
        "passed": passed,
        "evidence_utc": EVIDENCE_UTC,
        "staging_hash": manifest["staging_hash"],
        "first_run": first,
        "second_run": second,
        "equality": {
            key: first[key] == second[key]
            for key in (
                "decision_id",
                "change_candidate_id",
                "research_id",
                "mission",
                "discovery_count",
                "decision_count",
                "research_count",
                "ledger_events",
            )
        },
    }
    output = EVIDENCE_DIR / "R7_RESTART.json"
    write_json(output, payload)
    print(
        f"restart: passed={passed} decision={first['decision_id'] == second['decision_id']} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
