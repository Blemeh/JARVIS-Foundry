from __future__ import annotations

from pathlib import Path

from r7_helpers import FIXED_UTC, kernel_ledger, make_budget, make_candidate, make_project

from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.portfolio.missions import MissionIntegrator
from jarvis_foundry.kernel.portfolio.monitor import MonitorService
from jarvis_foundry.kernel.portfolio.research import ResearchService
from jarvis_foundry.kernel.portfolio.selection import SelectionService
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def _first_run(tmp_path: Path) -> dict[str, object]:
    store = PortfolioStore(tmp_path / "portfolio")
    for project in (make_project(), make_project(project_id="operators")):
        store.append_project(project)
    for candidate in (
        make_candidate("leadgen-repair", project_id="leadgen"),
        make_candidate("operators-repair", project_id="operators"),
    ):
        store.append_candidate(candidate)
    decision = SelectionService(store, budget=make_budget()).run()

    source_file = tmp_path / "health.txt"
    source_file.write_text("healthy\n", encoding="utf-8")
    monitor = MonitorService(store, clock=FixedClock(FIXED_UTC))
    monitor.register_source("health-feed", locator=str(source_file), freshness_seconds=900)
    monitor.poll("health-feed", lambda: (b"healthy\n", FIXED_UTC))
    source_file.write_text("regression detected\n", encoding="utf-8")
    change = monitor.poll("health-feed", lambda: (source_file.read_bytes(), FIXED_UTC))

    research_source_file = tmp_path / "research.md"
    research_source_file.write_text(
        "[SUPPORTS] export lineage should be repaired before the next export\n",
        encoding="utf-8",
    )
    from jarvis_foundry.kernel.portfolio.records import ResearchSource

    research = ResearchService(
        store, snapshot_root=tmp_path / "snapshots", clock=FixedClock(FIXED_UTC)
    ).run(
        question="Should the export lineage repair proceed?",
        project_id="leadgen",
        value_statement="restore customer-facing lineage",
        sources=[
            ResearchSource(
                source_id="research.md",
                locator=str(research_source_file),
                capture_method="read-only local source",
                captured_at=FIXED_UTC,
                content_hash="",
                trust_class="owner-controlled",
                freshness_seconds=3600,
            )
        ],
        budget=make_budget(),
    )

    ledger = kernel_ledger(tmp_path / "kernel")
    integrator = MissionIntegrator(ledger)
    integrator.sync_goal("goal-leadgen", "Advance LeadGen", "restore lineage", "ACTIVE")
    mission = integrator.create_mission(
        goal_id="goal-leadgen",
        objective="Repair the sales worklist export lineage",
        candidate_id=decision.selected_candidate_id or "leadgen-repair",
        decision_id=decision.decision_id,
        project_target="proving/leadgen",
        expected_target="proving/leadgen",
    )
    return {
        "store": store,
        "ledger": ledger,
        "decision": decision,
        "change": change,
        "research": research,
        "mission": mission,
        "monitor": monitor,
    }


def test_simulated_restart_produces_no_duplicate_effects(tmp_path: Path) -> None:
    first = _first_run(tmp_path)
    assert len(first["store"].decisions()) == 1
    assert len(first["store"].discoveries()) == 1
    assert len(first["store"].research()) == 1
    assert len(first["ledger"].replay()) == 2  # goal.created + mission.created

    reopened_store = PortfolioStore(tmp_path / "portfolio")
    reopened_ledger = kernel_ledger(tmp_path / "kernel")
    decision = SelectionService(reopened_store, budget=make_budget()).run()
    assert decision.decision_id == first["decision"].decision_id
    assert len(reopened_store.decisions()) == 1

    source_file = tmp_path / "health.txt"
    monitor = MonitorService(reopened_store, clock=FixedClock(FIXED_UTC))
    again = monitor.poll("health-feed", lambda: (source_file.read_bytes(), FIXED_UTC))
    assert again.candidate_id == first["change"].candidate_id
    assert len(reopened_store.discoveries()) == 1

    research_file = tmp_path / "research.md"
    from jarvis_foundry.kernel.portfolio.records import ResearchSource

    research = ResearchService(
        reopened_store,
        snapshot_root=tmp_path / "snapshots",
        clock=FixedClock(FIXED_UTC),
    ).run(
        question="Should the export lineage repair proceed?",
        project_id="leadgen",
        value_statement="restore customer-facing lineage",
        sources=[
            ResearchSource(
                source_id="research.md",
                locator=str(research_file),
                capture_method="read-only local source",
                captured_at=FIXED_UTC,
                content_hash="",
                trust_class="owner-controlled",
                freshness_seconds=3600,
            )
        ],
        budget=make_budget(),
    )
    assert research.research_id == first["research"].research_id
    assert len(reopened_store.research()) == 1

    integrator = MissionIntegrator(reopened_ledger)
    mission = integrator.create_mission(
        goal_id="goal-leadgen",
        objective="Repair the sales worklist export lineage",
        candidate_id=decision.selected_candidate_id or "leadgen-repair",
        decision_id=decision.decision_id,
        project_target="proving/leadgen",
        expected_target="proving/leadgen",
    )
    assert mission.mission_id == first["mission"].mission_id
    assert len(reopened_ledger.replay()) == len(first["ledger"].replay())
    assert reopened_ledger.verify_chain().valid is True
