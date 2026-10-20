"""Focused V11 durable continuous-observation production tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_monitors import (
    configured_monitors,
    set_monitor_enabled,
)
from jarvis_foundry.discovery_sources import (
    SourceObservation,
    SourceReadResult,
    SourceSpec,
    content_hash,
)
from jarvis_foundry.executive.owner_context import seed_owner_context
from jarvis_foundry.fabric.catalog import record_outcome
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.initiative_a2 import (
    InitiativeA2Bounds,
    run_continuous_observation_cycle,
)
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle


def _database(tmp_path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "continuous.db")
    assert database.initialize() == 41
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
        standing_rejections=[],
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
            note="focused continuous-observation Fabric fixture",
            at="2026-09-02T00:00:00Z",
        )
    return database


def _source(source_id: str = "project-watch", *, interval: int = 60,
            enabled: bool = True, **config) -> SourceSpec:
    return SourceSpec(
        source_id, "project", f"project:{source_id}", interval,
        {"project_id": "JARVIS Foundry", **config}, enabled,
    )


def _observation(source_id: str, revision: str) -> SourceObservation:
    payload = {"kind": "project_delta", "revision": revision}
    return SourceObservation(
        "project", f"project:{source_id}:tracked-delta",
        f"C:/disposable/{source_id}", payload, content_hash(payload),
        proposed_outcome="Investigate failing JARVIS reliability regression",
        target_project="JARVIS Foundry",
        alias_key=f"project:{source_id}:reliability-regression",
        candidate_type="project_drift", confidence=0.9,
    )


def _research(_database, candidate):
    return {
        "status": "COMPLETED",
        "budget_used": 1.0,
        "content_hash": content_hash({"candidate": candidate["candidate_id"]})[7:],
        "evidence": {"authority": "DATA_ONLY", "bounded": True},
    }


def _forbidden_counts(database: FoundryDatabase) -> dict[str, int]:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT event_type, COUNT(*) FROM events GROUP BY event_type"
        ).fetchall()
    counts = {str(row[0]): int(row[1]) for row in rows}
    return {
        "objectives": sum(value for key, value in counts.items()
                          if key in {"executive.objective_created",
                                     "executive.objective_recorded"}),
        "missions": counts.get("mission.created", 0),
        "effects": sum(value for key, value in counts.items()
                       if key.startswith("effect.") or key.startswith("system.action_")),
        "external_writes": sum(value for key, value in counts.items()
                               if key.startswith("provider.write")
                               or key.endswith(".write_completed")),
    }


def test_due_unchanged_changed_restart_and_stop(tmp_path) -> None:
    database = _database(tmp_path)
    source = _source()
    revision = {"value": "one"}
    reads: list[str] = []

    def reader(spec, stop_check):
        assert not stop_check()
        reads.append(revision["value"])
        return SourceReadResult(
            "READY", (_observation(spec.source_id, revision["value"]),),
        )

    bounds = InitiativeA2Bounds(
        source_limit=2, research_candidate_limit=1,
        research_budget_per_cycle=1.0, do_nothing_score=0.20,
    )
    base = datetime.now(timezone.utc).replace(microsecond=0)
    cold = run_continuous_observation_cycle(
        database, sources=[source], bounds=bounds, now=base,
        source_readers={"project": reader}, research_reader=_research,
    )
    assert cold["outcome"] == "WAIT"
    assert cold["cycles_dispatched"] == 1
    assert cold["metrics"]["observations_recorded"] == 1
    assert cold["metrics"]["unique_candidates"] == 1
    assert cold["metrics"]["research_calls"] == 1
    assert reads == ["one"]

    # Hot worker ticks before the durable due time produce no source IO and
    # no ledger/projection churn at all.
    before_not_due = database.event_count()
    not_due = run_continuous_observation_cycle(
        database, sources=[source], bounds=bounds,
        now=base + timedelta(seconds=1),
        source_readers={"project": reader}, research_reader=_research,
    )
    assert not_due["outcome"] == "WAIT"
    assert not_due["cycles_dispatched"] == 0
    assert database.event_count() == before_not_due
    assert reads == ["one"]

    # A due but unchanged read advances cadence only. It records no new
    # observation, candidate, research, or judgment work.
    unchanged = run_continuous_observation_cycle(
        database, sources=[source], bounds=bounds,
        now=base + timedelta(seconds=60),
        source_readers={"project": reader}, research_reader=_research,
    )
    assert unchanged["cycles_dispatched"] == 1
    assert unchanged["metrics"]["observations_recorded"] == 0
    assert unchanged["metrics"]["unique_candidates"] == 0
    assert unchanged["metrics"]["candidates_refreshed"] == 0
    assert unchanged["metrics"]["research_calls"] == 0
    assert reads == ["one", "one"]

    # A meaningful revision refreshes the same replay-safe candidate and is
    # eligible for one new bounded research pass for that revision.
    revision["value"] = "two"
    changed = run_continuous_observation_cycle(
        database, sources=[source], bounds=bounds,
        now=base + timedelta(seconds=120),
        source_readers={"project": reader}, research_reader=_research,
    )
    assert changed["metrics"]["observations_recorded"] == 1
    assert changed["metrics"]["unique_candidates"] == 0
    assert changed["metrics"]["candidates_refreshed"] == 1
    assert changed["metrics"]["research_calls"] == 1
    assert database.count("candidates") == 1
    assert database.count("candidate_sources") == 2

    # Reopening the database preserves the exact next due time and does not
    # invoke the source until that time arrives.
    restarted = FoundryDatabase(database.path)
    restarted.initialize()
    reads_before_restart = len(reads)
    waiting = run_continuous_observation_cycle(
        restarted, bounds=bounds, now=base + timedelta(seconds=121),
        source_readers={"project": reader}, research_reader=_research,
    )
    assert waiting["cycles_dispatched"] == 0
    assert len(reads) == reads_before_restart
    resumed = run_continuous_observation_cycle(
        restarted, bounds=bounds, now=base + timedelta(seconds=180),
        source_readers={"project": reader}, research_reader=_research,
    )
    assert resumed["cycles_dispatched"] == 1
    assert len(reads) == reads_before_restart + 1

    stop = tmp_path / "EMERGENCY_STOP"
    stop.write_text("owner stop", encoding="utf-8")
    events_before_stop = restarted.event_count()
    reads_before_stop = len(reads)
    stopped = run_continuous_observation_cycle(
        restarted, bounds=bounds, now=base + timedelta(seconds=240),
        stop_path=stop, source_readers={"project": reader},
        research_reader=_research,
    )
    assert stopped["outcome"] == "STOPPED"
    assert stopped["cycles_dispatched"] == 0
    assert restarted.event_count() == events_before_stop
    assert len(reads) == reads_before_stop
    assert _forbidden_counts(restarted) == {
        "objectives": 0, "missions": 0, "effects": 0, "external_writes": 0,
    }


def test_interrupted_due_cycle_resumes_before_next_monitor_due(tmp_path) -> None:
    database = _database(tmp_path)
    source = _source(interval=300)
    reads: list[str] = []

    def reader(spec, stop_check):
        assert not stop_check()
        reads.append(spec.source_id)
        return SourceReadResult(
            "READY", (_observation(spec.source_id, "interrupted"),),
        )

    def interrupt(stage: str) -> None:
        if stage == "after_candidate_generation":
            raise RuntimeError("post-candidate interruption")

    base = datetime.now(timezone.utc).replace(microsecond=0)
    with pytest.raises(RuntimeError, match="post-candidate interruption"):
        run_continuous_observation_cycle(
            database,
            sources=[source],
            now=base,
            source_readers={"project": reader},
            research_reader=_research,
            fault_hook=interrupt,
        )

    restarted = FoundryDatabase(database.path)
    restarted.initialize()
    resumed = run_continuous_observation_cycle(
        restarted,
        sources=[source],
        now=base + timedelta(seconds=1),
        source_readers={"project": reader},
        research_reader=_research,
    )

    assert resumed["outcome"] == "WAIT"
    assert resumed["cycles_dispatched"] == 1
    assert resumed["resumed_interrupted_cycle"] is True
    assert resumed["metrics"]["duplicate_candidate_records"] == 0
    assert reads == [source.source_id]
    with restarted.connect(readonly=True) as connection:
        cycles = connection.execute(
            "SELECT stage, status FROM initiative_cycles"
        ).fetchall()
    assert [(row[0], row[1]) for row in cycles] == [("COMPLETED", "WAIT")]


def test_source_failure_backoff_and_readiness_recovery(tmp_path) -> None:
    database = _database(tmp_path)
    source = _source(interval=10, failure_backoff_s=60, max_backoff_s=300)
    state = {"ready": False, "calls": 0}

    def reader(spec, _stop):
        state["calls"] += 1
        if not state["ready"]:
            return SourceReadResult("UNKNOWN", reason="temporary source outage")
        return SourceReadResult("READY", (_observation(spec.source_id, "recovered"),))

    base = datetime.now(timezone.utc).replace(microsecond=0)
    first = run_continuous_observation_cycle(
        database, sources=[source], now=base,
        source_readers={"project": reader}, research_reader=_research,
    )
    row = configured_monitors(database)[0]
    assert first["cycles_dispatched"] == 1
    assert row["readiness_state"] == "UNKNOWN"
    assert row["consecutive_failures"] == 1
    assert row["backoff_until"] == (base + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")

    quiet = run_continuous_observation_cycle(
        database, sources=[source], now=base + timedelta(seconds=59),
        source_readers={"project": reader}, research_reader=_research,
    )
    assert quiet["cycles_dispatched"] == 0
    assert state["calls"] == 1
    second = run_continuous_observation_cycle(
        database, sources=[source], now=base + timedelta(seconds=60),
        source_readers={"project": reader}, research_reader=_research,
    )
    row = configured_monitors(database)[0]
    assert second["cycles_dispatched"] == 1
    assert row["consecutive_failures"] == 2
    assert row["backoff_until"] == (base + timedelta(seconds=180)).isoformat().replace("+00:00", "Z")

    state["ready"] = True
    recovered = run_continuous_observation_cycle(
        database, sources=[source], now=base + timedelta(seconds=180),
        source_readers={"project": reader}, research_reader=_research,
    )
    row = configured_monitors(database)[0]
    assert recovered["cycles_dispatched"] == 1
    assert row["readiness_state"] == "READY"
    assert row["consecutive_failures"] == 0
    assert row["backoff_until"] is None
    assert row["last_success_at"] is not None


def test_bounded_rotation_enable_disable_ttl_and_secret_guard(tmp_path) -> None:
    database = _database(tmp_path)
    calls: list[str] = []

    def reader(spec, _stop):
        calls.append(spec.source_id)
        return SourceReadResult("READY", (_observation(spec.source_id, "one"),))

    sources = [_source(f"watch-{index}", interval=1) for index in range(3)]
    bounds = InitiativeA2Bounds(
        source_limit=2, research_candidate_limit=0,
        research_budget_per_cycle=1.0, candidate_ttl_hours=24.0,
        do_nothing_score=0.20,
    )
    base = datetime.now(timezone.utc).replace(microsecond=0)
    first = run_continuous_observation_cycle(
        database, sources=sources, bounds=bounds, now=base,
        source_readers={"project": reader}, research_reader=_research,
    )
    second = run_continuous_observation_cycle(
        database, sources=sources, bounds=bounds, now=base,
        source_readers={"project": reader}, research_reader=_research,
    )
    assert first["cycles_dispatched"] == second["cycles_dispatched"] == 1
    assert sorted(calls) == ["watch-0", "watch-1", "watch-2"]
    assert len(calls) == 3

    set_monitor_enabled(database, "watch-2", False, reason="owner disabled", now=base)
    disabled = next(row for row in configured_monitors(database)
                    if row["source_id"] == "watch-2")
    assert disabled["enabled"] == 0
    assert disabled["readiness_state"] == "DISABLED"
    assert disabled["next_due_at"] is None

    with pytest.raises(ValueError, match="must reference, not persist"):
        run_continuous_observation_cycle(
            database,
            sources=[_source("unsafe", api_token="plaintext")],
            now=base + timedelta(seconds=10),
            source_readers={"project": reader},
        )


def test_candidate_ttl_rejection_uses_a_deterministic_clock(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path)
    source = _source("ttl-watch", interval=1)
    base = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
    clock = {"now": base}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = clock["now"]
            if tz is None:
                return value.replace(tzinfo=None)
            return value.astimezone(tz)

    monkeypatch.setattr(
        "jarvis_foundry.executive.scoring.datetime",
        FrozenDateTime,
    )

    def reader(spec, _stop):
        return SourceReadResult(
            "READY",
            (_observation(spec.source_id, "stable-revision"),),
        )

    bounds = InitiativeA2Bounds(
        source_limit=1,
        research_candidate_limit=0,
        research_budget_per_cycle=1.0,
        candidate_ttl_hours=0.001,
        do_nothing_score=0.20,
    )
    run_continuous_observation_cycle(
        database,
        sources=[source],
        bounds=bounds,
        now=base,
        source_readers={"project": reader},
        research_reader=_research,
    )
    with database.connect(readonly=True) as connection:
        first_stale = int(connection.execute(
            "SELECT COUNT(*) FROM candidate_judgments "
            "WHERE outcome = 'REJECTED' AND reason_code = 'STALE'"
        ).fetchone()[0])
    assert first_stale == 0

    clock["now"] = base + timedelta(seconds=5)
    run_continuous_observation_cycle(
        database,
        sources=[source],
        bounds=bounds,
        now=clock["now"],
        source_readers={"project": reader},
        research_reader=_research,
    )
    with database.connect(readonly=True) as connection:
        stale = int(connection.execute(
            "SELECT COUNT(*) FROM candidate_judgments "
            "WHERE outcome = 'REJECTED' AND reason_code = 'STALE'"
        ).fetchone()[0])
    assert stale == 1
    assert _forbidden_counts(database)["objectives"] == 0


def test_existing_worker_choke_point_and_flag_exclusion(tmp_path) -> None:
    home = tmp_path / "home"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    config = WorkerRuntimeConfig(
        enabled=True,
        continuous_observation_enabled=True,
        continuous_observation={
            "enabled": True,
            "sources": [{
                "source_id": "disabled-watch",
                "source_kind": "gmail",
                "source_identity": "gmail:owner-inbox",
                "poll_interval_s": 300,
                "enabled": False,
                "config": {},
            }],
        },
    )
    config.validate()
    result = run_cycle(
        database, FoundryPaths(home), config,
        loop_id="continuous-worker", instance_id="continuous-supervisor",
    )
    assert result["outcome"] == "WAITING"
    assert database.count("initiative_cycles") == 0
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0
    row = configured_monitors(database)[0]
    assert row["readiness_state"] == "DISABLED"

    with pytest.raises(ValueError, match="cannot be combined"):
        WorkerRuntimeConfig(
            continuous_observation_enabled=True,
            executive_autonomy_enabled=True,
        ).validate()
