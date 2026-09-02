from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_sources import SourceSpec
from jarvis_foundry.executive.owner_context import seed_owner_context
from jarvis_foundry.fabric.catalog import record_outcome
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.initiative_a2 import InitiativeA2Bounds, run_initiative_a2_cycle
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.worker_loop import run_cycle


def _project(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "a2@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "A2 Test"], check=True)
    (root / "work.md").write_text(
        "TODO: investigate failing regression risk in JARVIS Foundry\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(root), "add", "work.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True)


def _seed(database: FoundryDatabase) -> None:
    digest = "a" * 64
    seed_owner_context(
        database,
        goals=[{
            "text": "Make JARVIS reliable and evidence driven",
            "source_ref": "IDEA.md",
            "source_hash": digest,
        }],
        projects=[{
            "text": "JARVIS Foundry",
            "source_ref": "docs/CURRENT_STATE.md",
            "source_hash": digest,
        }],
        standing_rejections=[{
            "text": "Do not create objectives or execute discovered work in A2",
            "source_ref": "docs/handoffs/frozen-roadmap.md",
            "source_hash": digest,
        }],
    )
    seed_builtins(database)
    for capability_id in (
        "cap.file.read", "cap.file.write", "cap.git.inspect", "cap.git.diff",
        "cap.git.commit", "cap.script.run", "cap.tests.run",
    ):
        record_outcome(
            database,
            capability_id,
            success=True,
            verified=VerifyVerdict.MATCH,
            note="focused A2 local-candidate fixture",
            at="2026-09-02T00:00:00Z",
        )


def _source(root: Path, *, interval: int = 1) -> SourceSpec:
    return SourceSpec(
        "project-a2",
        "project",
        str(root),
        interval,
        {"root": str(root), "project_id": "JARVIS Foundry", "max_files": 20},
    )


def _events(database: FoundryDatabase, event_type: str) -> int:
    return database.event_count(event_type=event_type)


def test_cycle_researches_judges_waits_and_replays_without_execution(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root)
    database = FoundryDatabase(tmp_path / "a2.db")
    assert database.initialize() == 40
    _seed(database)
    result = run_initiative_a2_cycle(
        database,
        cycle_id="cold",
        sources=[_source(root)],
        bounds=InitiativeA2Bounds(do_nothing_score=0.20),
    )
    assert result["outcome"] == "WAIT"
    assert result["metrics"]["observations_recorded"] >= 2
    assert result["metrics"]["candidates_researched"] == 1
    assert result["metrics"]["objective_creations"] == 0
    assert result["metrics"]["mission_creations"] == 0
    assert result["metrics"]["mutating_effects"] == 0
    assert result["metrics"]["external_writes"] == 0
    assert result["metrics"]["duplicate_candidate_records"] == 0
    assert result["metrics"]["unbounded_loop_detected"] is False
    with database.connect(readonly=True) as connection:
        research = connection.execute(
            "SELECT evidence_json FROM research_queue "
            "WHERE candidate_id IS NOT NULL ORDER BY pass_number DESC LIMIT 1"
        ).fetchone()
        dossier = json.loads(research[0])["dossier"]
        assert dossier["contract"] == "jarvis.v11.research-dossier.v1"
        assert dossier["source_provenance"]
        assert dossier["authority"] == "DATA_ONLY"
        row = connection.execute(
            "SELECT provenance_json FROM world_observations "
            "WHERE source_kind = 'project' ORDER BY rowid LIMIT 1"
        ).fetchone()
        assert json.loads(row[0])["authority"] == "DATA_ONLY"
    before = database.event_count()
    replay = run_initiative_a2_cycle(
        database,
        cycle_id="cold",
        sources=[_source(root)],
        bounds=InitiativeA2Bounds(do_nothing_score=0.20),
    )
    assert replay["replayed"] is True
    assert database.event_count() == before
    assert _events(database, "executive.candidate_admitted") == 0
    assert _events(database, "executive.objective_created") == 0
    assert _events(database, "mission.created") == 0


def test_stop_before_cycle_records_no_observation_or_research(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root)
    database = FoundryDatabase(tmp_path / "a2.db")
    database.initialize()
    stop = tmp_path / "EMERGENCY_STOP"
    stop.write_text("owner stop", encoding="utf-8")
    result = run_initiative_a2_cycle(
        database, cycle_id="stopped-before", sources=[_source(root)], stop_path=stop,
    )
    assert result["outcome"] == "STOPPED"
    assert database.count("initiative_cycles") == 0
    assert database.count("world_observations") == 0
    assert database.count("research_queue") == 0


def test_stop_after_observation_preserves_truth_without_research(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root)
    database = FoundryDatabase(tmp_path / "a2.db")
    database.initialize()
    stop = tmp_path / "EMERGENCY_STOP"

    def hook(stage: str) -> None:
        if stage == "after_observations":
            stop.write_text("owner stop", encoding="utf-8")

    result = run_initiative_a2_cycle(
        database,
        cycle_id="stopped-after",
        sources=[_source(root)],
        stop_path=stop,
        fault_hook=hook,
    )
    assert result["outcome"] == "STOPPED"
    assert database.count("world_observations") >= 2
    assert database.count("candidates") == 1
    assert database.count("research_queue") == 0
    assert _events(database, "mission.created") == 0


def test_interruption_after_candidates_restarts_without_duplicate_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root)
    database = FoundryDatabase(tmp_path / "a2.db")
    database.initialize()

    def interrupt(stage: str) -> None:
        if stage == "after_candidate_generation":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        run_initiative_a2_cycle(
            database,
            cycle_id="interrupted",
            sources=[_source(root)],
            bounds=InitiativeA2Bounds(do_nothing_score=0.20),
            fault_hook=interrupt,
        )
    assert database.count("candidates") == 1
    resumed = run_initiative_a2_cycle(
        database,
        cycle_id="interrupted",
        sources=[_source(root)],
        bounds=InitiativeA2Bounds(do_nothing_score=0.20),
    )
    assert resumed["outcome"] == "WAIT"
    assert database.count("candidates") == 1
    assert resumed["metrics"]["duplicate_candidate_records"] == 0


def test_changed_project_input_adds_only_meaningful_delta(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root)
    database = FoundryDatabase(tmp_path / "a2.db")
    database.initialize()
    _seed(database)
    start = datetime.now(timezone.utc)
    first = run_initiative_a2_cycle(
        database,
        cycle_id="before-change",
        sources=[_source(root)],
        bounds=InitiativeA2Bounds(do_nothing_score=0.20),
        now=start,
    )
    assert first["metrics"]["unique_candidates"] == 1
    (root / "new-work.md").write_text(
        "FIXME: investigate failing provider regression risk in JARVIS Foundry\n",
        encoding="utf-8",
    )
    second = run_initiative_a2_cycle(
        database,
        cycle_id="after-change",
        sources=[_source(root)],
        bounds=InitiativeA2Bounds(do_nothing_score=0.20),
        now=start + timedelta(seconds=2),
    )
    assert second["metrics"]["unique_candidates"] == 1
    assert second["metrics"]["candidate_duplicates_suppressed"] == 0
    assert second["metrics"]["observations_recorded"] >= 1
    assert second["metrics"]["duplicate_candidate_records"] == 0


def test_fabric_unavailable_candidate_consumes_no_research_connector(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root)
    database = FoundryDatabase(tmp_path / "a2.db")
    database.initialize()
    _seed(database)
    calls = 0

    def reader(_database, _candidate):
        nonlocal calls
        calls += 1
        return {"status": "COMPLETED", "evidence": {"authority": "DATA_ONLY"}}

    source = _source(root)
    source = SourceSpec(
        source.source_id, source.source_kind, source.source_identity,
        source.poll_interval_s,
        {**source.config, "required_capabilities": ["cap.missing.a2"]},
    )
    result = run_initiative_a2_cycle(
        database,
        cycle_id="missing-capability",
        sources=[source],
        bounds=InitiativeA2Bounds(do_nothing_score=0.20),
        research_reader=reader,
    )
    assert calls == 0
    assert result["metrics"]["candidates_researched"] == 0
    assert result["metrics"]["candidates_deferred"] == 1
    with database.connect(readonly=True) as connection:
        assert connection.execute(
            "SELECT reason_code FROM candidate_judgments LIMIT 1"
        ).fetchone()[0] == "CAPABILITY_UNAVAILABLE"


def test_interrupted_research_reader_is_not_retried_on_restart(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root)
    database = FoundryDatabase(tmp_path / "a2.db")
    database.initialize()
    _seed(database)
    calls = 0

    def interrupted_reader(_database, _candidate):
        nonlocal calls
        calls += 1
        raise RuntimeError("controlled reader interruption")

    with pytest.raises(RuntimeError, match="controlled reader interruption"):
        run_initiative_a2_cycle(
            database,
            cycle_id="research-interrupted",
            sources=[_source(root)],
            bounds=InitiativeA2Bounds(do_nothing_score=0.20),
            research_reader=interrupted_reader,
        )
    assert database.all("research_queue")[0]["status"] == "IN_PROGRESS"
    resumed = run_initiative_a2_cycle(
        database,
        cycle_id="research-interrupted",
        sources=[_source(root)],
        bounds=InitiativeA2Bounds(do_nothing_score=0.20),
        research_reader=interrupted_reader,
    )
    assert calls == 1
    assert resumed["outcome"] == "WAIT"
    assert resumed["metrics"]["candidates_researched"] == 0


def test_runtime_config_forbids_a2_with_execution_flags() -> None:
    with pytest.raises(ValueError, match="zero-execution"):
        WorkerRuntimeConfig(
            initiative_a2_enabled=True,
            executive_selection_enabled=True,
        ).validate()


def test_existing_worker_choke_point_returns_wait_before_mission_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root)
    home = tmp_path / "home"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    config = WorkerRuntimeConfig(
        enabled=True,
        initiative_a2_enabled=True,
        initiative_a2={
            "enabled": True,
            "sources": [{
                "source_id": "project-a2",
                "source_kind": "project",
                "source_identity": str(root),
                "poll_interval_s": 300,
                "config": {
                    "root": str(root),
                    "project_id": "JARVIS Foundry",
                    "max_files": 20,
                },
            }],
            "bounds": {"do_nothing_score": 0.20},
        },
    )
    config.validate()
    result = run_cycle(
        database,
        FoundryPaths(home),
        config,
        loop_id="scheduled-a2",
        instance_id="supervisor-a2",
    )
    assert result["outcome"] == "WAITING"
    assert database.count("initiative_cycles") == 1
    assert _events(database, "mission.created") == 0
    assert _events(database, "executive.objective_created") == 0
    assert not any(event.event_type.startswith("system.action_") for event in database.events())
