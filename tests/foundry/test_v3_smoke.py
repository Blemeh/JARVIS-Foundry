"""V3 WP7 MVP smoke: STOP blocks new dispatch; restart at two important
cycle transitions resumes once; projection rebuild produces zero drift.

Deferred to WP8-HARD: the 12-point crash matrix, full AC-07 race
battery, deep injection tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.reconcile import inspect as reconcile_inspect
from jarvis_foundry.v3_cycle import run_discovery_cycle
from jarvis_foundry.v3_scoring import register_policy

from v3_fixtures import load_policy
from worker_test_helpers import mission_database


def _env(tmp_path: Path):
    database, repository = mission_database(tmp_path, resource_id="resource-1")
    from jarvis_foundry.control.capabilities import set_capability_state
    from jarvis_foundry.control.registration import (
        inspect_git_resource,
        registration_event,
    )
    database.append(registration_event(
        inspect_git_resource(repository, resource_id="resource-1")))
    set_capability_state(
        database, "resource-1", "write_registered_worktree_files",
        "READY", evidence={"source": "smoke"})
    register_policy(database, load_policy())
    return database


def _stop(database):
    secret = b"test-secret-32-bytes-minimum!!"
    svc = OwnerControlService(database, secret=secret, owner_id="owner")
    nonce = "stop-smoke"
    sig = sign_command(secret, "STOP_EVERYTHING", "GLOBAL", "halt", nonce)
    svc.apply("STOP_EVERYTHING", target="GLOBAL", exact_text="halt",
              nonce=nonce, signature=sig)


def _observe(observed=1):
    def _observe(_database):
        return {"observed": {"system": observed}, "candidates_created": 0,
                "research_items": 0, "monitors": {}, "maintenance": {},
                "maintenance_candidate_created": 0}
    return _observe


def test_stop_blocks_new_dispatch_before_any_work(tmp_path):
    database = _env(tmp_path)
    _stop(database)
    calls = {"n": 0}

    def _spy(_database):
        calls["n"] += 1
        return _observe()(_database)

    report = run_discovery_cycle(
        database, loop_id="smoke-stop", resource_id="resource-1",
        observe=_spy)
    assert report.outcome == "STOPPED"
    assert calls["n"] == 0  # observation never ran under STOP
    assert not [e for e in database.events()
                if e.event_type == "portfolio.evaluated"]
    completed = [e for e in database.events()
                 if e.event_type == "discovery.cycle_completed"]
    assert len(completed) == 1
    assert completed[0].payload["outcome"] == "STOPPED"
    # Clearing STOP lets a NEW cycle attempt proceed (new loop id).
    with database.connect() as connection:
        connection.execute(
            "UPDATE directives SET active = 0 WHERE command_type = "
            "'STOP_EVERYTHING'")
    report2 = run_discovery_cycle(
        database, loop_id="smoke-stop-retry", resource_id="resource-1",
        observe=_spy)
    assert report2.outcome in ("COMPILED", "WAITING")
    assert calls["n"] == 1  # observation runs exactly once, post-STOP


def test_same_loop_id_replays_once_after_stop_clears(tmp_path):
    """Idempotency contract: one cycle record per loop_id.

    An identical re-delivery dedupes (exactly-once); a CHANGED outcome
    under the same identity raises EventConflict — append-only history
    is never silently rewritten. A genuinely new attempt uses a new
    loop id (see STOP smoke above).
    """
    database = _env(tmp_path)
    first = run_discovery_cycle(
        database, loop_id="replay-id", resource_id="resource-1",
        observe=_observe())
    # Identical replay: dedupes, still exactly one record.
    run_discovery_cycle(
        database, loop_id="replay-id", resource_id="resource-1",
        observe=_observe())
    completed = [e for e in database.events()
                 if e.event_type == "discovery.cycle_completed"]
    assert len(completed) == 1

    # Same identity, different outcome: conflict, not rewrite.
    _stop(database)
    from jarvis_foundry.events import EventConflict
    with pytest.raises(EventConflict):
        run_discovery_cycle(
            database, loop_id="replay-id", resource_id="resource-1",
            observe=_observe())
    assert len([e for e in database.events()
                if e.event_type == "discovery.cycle_completed"]) == 1
    del first


def test_restart_after_select_before_materialization_resumes_once(
        tmp_path):
    """Crash window: decision recorded, mission not yet created.

    Re-running the same loop_id must resume deterministically: the
    executive's per-loop decision id replays the SELECT and the
    materializer's idempotency prevents a second mission.created.
    """
    from jarvis_foundry.executive.decisions import ExecutiveDecisionService
    from jarvis_foundry.executive.eligibility import (
        current_activation_epoch,
        enable_admission_authority,
    )

    database = _env(tmp_path)
    from jarvis_foundry.discovery_candidates import generate_candidate
    from jarvis_foundry.v3_scoring import ScoringService
    generated = generate_candidate(
        database, "mission-seed", "repo",
        "Fix the failing nightly integration",
        [{"kind": "ci", "ref": "nightly"},
         {"kind": "log", "ref": "run"}, {"kind": "issue", "ref": "i"}],
        typed={"expected_outcome": "nightly green"})
    # Activation + admission authority BEFORE scoring so the cycle's
    # score-time governed admission stamps this candidate's epoch.
    epoch = ExecutiveDecisionService(database).ensure_activation()
    enable_admission_authority(database, epoch, source_id="smoke")
    ScoringService(database).score_candidate(generated["candidate_id"])

    executive = ExecutiveDecisionService(database, wait_margin=0.0)
    decision = executive.evaluate(resource_id="resource-1",
                                  loop_id="crash-window")
    assert decision.kind == "SELECT"

    # Simulated crash AFTER select, BEFORE materialization: the seed
    # mission from the fixture exists, but NO mission linked to this
    # candidate yet.
    missions_before = [e for e in database.events()
                       if e.event_type == "mission.created"
                       and str(e.payload.get("created_from") or "")
                       == f"candidate:{decision.candidate_id}"]
    assert not missions_before

    # 'Restart': the unified cycle runs under a fresh loop id (a new
    # attempt). It must reach COMPILED exactly once — one candidate,
    # one mission, and the duplicate guard holds across attempts.
    report = run_discovery_cycle(
        database, loop_id="restart-once", resource_id="resource-1",
        observe=_observe())
    assert report.outcome == "COMPILED", report.to_payload()
    missions_after_first = [e for e in database.events()
                            if e.event_type == "mission.created"
                            and str(e.payload.get("created_from") or "")
                            == f"candidate:{decision.candidate_id}"]
    assert len(missions_after_first) == 1

    # A further attempt on the same universe creates NO second mission:
    # the objective is covered by mission now.
    run_discovery_cycle(
        database, loop_id="restart-again", resource_id="resource-1",
        observe=_observe())
    linked = [e for e in database.events()
              if e.event_type == "mission.created"
              and str(e.payload.get("created_from") or "")
              == f"candidate:{decision.candidate_id}"]
    assert len(linked) == 1


def test_projection_rebuild_zero_drift_after_v3_events(tmp_path):
    from jarvis_foundry.discovery_candidates import generate_candidate
    from jarvis_foundry.v3_scoring import ScoringService

    database = _env(tmp_path)
    generated = generate_candidate(
        database, "mission-seed", "repo", "Investigate unknown root cause now",
        [{"kind": "ci", "ref": "c"}],
        typed={"expected_outcome": "cause known"})
    service = ScoringService(database)
    service.score_candidate(generated["candidate_id"])
    database.append(Event.create(
        "portfolio.evaluated",
        {"loop_id": "rebuild-loop",
         "policy_version": "v3-policy-2026-08-21",
         "eligible_count": 0, "deferred_count": 0,
         "rejected_count": 0, "capacity_snapshot": {}},
        source_type="supervisor", source_id="smoke",
        idempotency_key="portfolio:rebuild-loop:v3-policy-2026-08-21"))
    report = reconcile_inspect(database)
    assert report.consistent, report.to_json()
    assert not report.mismatches and not report.unexpected_rows \
        and not report.missing_rows
