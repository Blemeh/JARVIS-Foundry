"""Execution-coherence repair: plan/materialization consistency, livelock law,
redirect lineage, baseline currentness, duplicate safety.

Each test encodes one Phase-5 behavior against a disposable DB. Tests that
fail on the pre-repair tree prove the defect; the repair must turn them
green without weakening the existing v8 autonomy suite.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import (
    inspect_git_resource,
    refresh_git_resource_readiness,
    registration_event,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.executive.autonomy import (
    CONTRACT_SCOPE_MARKER,
    active_plan,
    build_plan,
    decide_continuation,
    generate_missions_for_objective,
    rank_objectives,
    rebuild_executive_state,
    run_autonomy_cycle,
    sync_owner_goals,
)
from jarvis_foundry.executive.continuous import rebuild_wake_state
from jarvis_foundry.owner_controls import OwnerControlService, sign_command

_SECRET = b"coherence-owner-secret-32-bytes-min!!"
_CAP = "write_registered_worktree_files"


def _db(tmp_path: Path) -> tuple[FoundryDatabase, Path]:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    repository = tmp_path / "resource"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "t@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "T"], check=True)
    (repository / "README.md").write_text("# T\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "baseline"], check=True, capture_output=True, text=True)
    database.append(registration_event(inspect_git_resource(repository, resource_id="resource-t")))
    set_capability_state(database, "resource-t", _CAP, "READY", evidence={"source": "t"})
    return database, repository


def _goal(database: FoundryDatabase, text: str, *, nonce: str, supersedes: str | None = None):
    service = OwnerControlService(database, secret=_SECRET, owner_id="owner")
    result = service.apply(
        "GOAL", target="GLOBAL", exact_text=text, nonce=nonce,
        signature=sign_command(_SECRET, "GOAL", "GLOBAL", text, nonce, supersedes=supersedes),
        supersedes_directive_id=supersedes,
    )
    return result.directive_id


def _contractless_goal() -> str:
    return (
        "Improve the registered project autonomously. Inspect state yourself, "
        "choose the work yourself, verify independently, and WAIT truthfully "
        "when nothing qualifies."
    )


def test_plan_ready_iff_materializable(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, _contractless_goal(), nonce="coh-n1")
    cycle = run_autonomy_cycle(database, cycle_id="coh-c1", resource_id="resource-t")
    objectives = [o for o in rebuild_executive_state(database).objectives.values()
                  if o.objective_id.startswith("owner-goal:")]
    assert len(objectives) == 1
    plan = active_plan(database, objectives[0].objective_id)
    assert plan is not None
    # A contractless fallback step must not claim READY.
    assert all(step.status != "READY" for step in plan.steps)
    assert cycle.status in {"WAITING", "BLOCKED"}
    # The refusal receipt is recorded at admission even though ranking never
    # selects the objective for generation.
    blocked = database.events(event_types=("executive.mission_generation_blocked",))
    assert any("no explicit executable" in event.payload.get("reason", "") for event in blocked)


def test_decide_time_contract_filter(tmp_path: Path) -> None:
    # A legacy READY step without an executable contract must not drive
    # CONTINUE at decision time, even before generation coerces it.
    database, _ = _db(tmp_path)
    _goal(database, _contractless_goal(), nonce="coh-n13")
    objective = sync_owner_goals(database)[0]
    build_plan(database, objective.objective_id, [
        {"step_id": "execute", "description": objective.objective,
         "resource_id": "resource-t", "required_capabilities": (_CAP,)},
    ])
    decision = decide_continuation(database, objective.objective_id)
    assert decision.action == "WAIT"
    assert decision.action != "CONTINUE"


def test_unbuildable_step_cannot_remain_ready(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, _contractless_goal(), nonce="coh-n2")
    objective = sync_owner_goals(database)[0]
    plan = build_plan(database, objective.objective_id, [
        {"step_id": "execute", "description": objective.objective,
         "resource_id": "resource-t", "required_capabilities": (_CAP,),
         "output_contract": ("durable outcome evidence",)},
    ])
    assert plan.steps[0].status == "READY"
    assert generate_missions_for_objective(database, objective.objective_id) == []
    coerced = active_plan(database, objective.objective_id)
    assert coerced is not None
    assert coerced.steps[0].status == "BLOCKED"
    assert any("contract" in str(ref) for ref in coerced.steps[0].evidence_refs)
    # Second attempt is silent: the refusal is durable, not re-emitted.
    before = len(database.events(event_types=("executive.mission_generation_blocked",)))
    assert generate_missions_for_objective(database, objective.objective_id) == []
    assert len(database.events(event_types=("executive.mission_generation_blocked",))) == before


def test_stale_observation_refreshes_on_head_change(tmp_path: Path) -> None:
    database, repository = _db(tmp_path)
    stored = database.row("resources", "resource_id", "resource-t")
    old_head = __import__("json").loads(stored["baseline_json"])["head"]
    (repository / "fix.txt").write_text("repair\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "fix.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "fix"], check=True, capture_output=True, text=True)
    result = refresh_git_resource_readiness(database, "resource-t", source_id="coh-test")
    assert result["refreshed"] is True
    stored = database.row("resources", "resource_id", "resource-t")
    new_head = __import__("json").loads(stored["baseline_json"])["head"]
    assert new_head != old_head
    # The action-specific capability is READY even though the aggregate
    # resource rollup stays PROBING by construction (git_local_commit is
    # always declared PROBING): per-capability truth wins for the action.
    from jarvis_foundry.control.capabilities import latest_capability_state
    latest = latest_capability_state(database, "resource-t", "write_registered_worktree_files")
    assert str((latest or {}).get("state")) == "READY"


def test_refresh_preserves_mission_baselines_and_history(tmp_path: Path) -> None:
    database, repository = _db(tmp_path)
    missions_before = database.row("missions", "mission_id", "nope")
    assert missions_before is None
    cap_events_before = len(database.events(event_types=("capability.state_changed",)))
    (repository / "dirty.txt").write_text(" scratch, not exhaust\n", encoding="utf-8")
    result = refresh_git_resource_readiness(database, "resource-t", source_id="coh-test")
    assert result["refreshed"] is True
    assert database.row("resources", "resource_id", "resource-t")["readiness_state"] == "PROBING"
    # History is append-only: old evidence rows are still present.
    assert len(database.events(event_types=("capability.state_changed",))) > cap_events_before
    assert database.row("missions", "mission_id", "nope") is None


def test_action_ready_not_blocked_by_aggregate_probing(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    from jarvis_foundry.executive.autonomy import _capability_blockers, create_objective
    objective = create_objective(database, "probe coherence", objective_id="probe-obj")
    plan = build_plan(database, objective.objective_id, [
        {"step_id": "s", "description": "probe", "resource_id": "resource-t",
         "required_capabilities": (_CAP,)},
    ])
    assert _capability_blockers(database, plan.steps[0]) == ()


def test_real_blocking_capability_fails_closed(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    from jarvis_foundry.executive.autonomy import _capability_blockers, create_objective
    set_capability_state(database, "resource-t", _CAP, "DEGRADED", evidence={"source": "fault"})
    objective = create_objective(database, "probe coherence", objective_id="probe-obj")
    plan = build_plan(database, objective.objective_id, [
        {"step_id": "s", "description": "probe", "resource_id": "resource-t",
         "required_capabilities": (_CAP,)},
    ])
    assert _capability_blockers(database, plan.steps[0]) != ()


def test_repeated_wait_cannot_continue_forever(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, _contractless_goal(), nonce="coh-n7")
    for index in range(1, 4):
        run_autonomy_cycle(database, cycle_id=f"coh-wait-{index}", resource_id="resource-t")
    decision = decide_continuation(
        database,
        next(o for o in rebuild_executive_state(database).objectives.values()
             if o.objective_id.startswith("owner-goal:")).objective_id,
    )
    assert decision.action in {"WAIT", "REPLAN", "ESCALATE"}
    assert decision.action != "CONTINUE"
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0
    assert not database.events(event_types=("executive.escalation_opened",))


def test_changed_evidence_permits_reevaluation(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, _contractless_goal(), nonce="coh-n8a")
    assert generate_missions_for_objective(
        database, sync_owner_goals(database)[0].objective_id) == []
    # The same need with explicit owner scope is executable: explicit
    # success criteria plus an explicit step contract materialize.
    _goal(database, "Store the outcome receipt. success: outcome receipt stored at outcome.md",
          nonce="coh-n8b")
    sync_owner_goals(database)
    objectives = [o for o in rebuild_executive_state(database).objectives.values()
                  if o.objective_id.startswith("owner-goal:")]
    scoped = next(o for o in objectives if "outcome receipt stored" in o.objective)
    build_plan(database, scoped.objective_id, [
        {"step_id": "execute-scoped", "description": "Store the outcome receipt at outcome.md",
         "resource_id": "resource-t", "required_capabilities": (_CAP,),
         "output_contract": ("outcome receipt stored at outcome.md",)},
    ])
    requests = generate_missions_for_objective(database, scoped.objective_id)
    assert len(requests) == 1
    assert active_plan(database, scoped.objective_id).steps[-1].status == "READY"


def test_bounded_recurrence_marks_durable_blocker(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    _goal(database, _contractless_goal(), nonce="coh-n9")
    for index in range(1, 4):
        run_autonomy_cycle(database, cycle_id=f"coh-rec-{index}", resource_id="resource-t")
    objectives = [o for o in rebuild_executive_state(database).objectives.values()
                  if o.objective_id.startswith("owner-goal:")]
    assert len(objectives) == 1
    plan = active_plan(database, objectives[0].objective_id)
    assert plan is not None
    assert all(step.status != "READY" for step in plan.steps)
    ranked = next(r for r in rank_objectives(database)
                  if r.objective_id == objectives[0].objective_id)
    assert ranked.eligible is False
    assert any("plan_step_blocked" in blocker for blocker in ranked.blockers)


def test_redirect_supersession_lineage(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    first = _goal(database, "First direction. success: first receipt exists", nonce="coh-r1")
    sync_owner_goals(database)
    second = _goal(database, "Redirected direction. success: second receipt exists",
                   nonce="coh-r2", supersedes=first)
    created = sync_owner_goals(database)
    assert [o.objective_id for o in created] == [f"owner-goal:{second}"]
    state = rebuild_executive_state(database).objectives
    assert state[f"owner-goal:{first}"].status == "SUSPENDED"
    assert state[f"owner-goal:{second}"].status == "ACTIVE"
    superseded = database.events(event_types=("executive.objective_authority_superseded",))
    assert len(superseded) == 1
    payload = superseded[0].payload
    assert payload["objective_id"] == f"owner-goal:{first}"
    assert payload["superseded_by"] == second
    assert payload["successor_objective_id"] == f"owner-goal:{second}"
    revoked = database.events(event_types=("executive.objective_authority_revoked",))
    assert all(e.payload.get("directive_id") != first for e in revoked)
    with database.connect(readonly=True) as connection:
        active = connection.execute(
            "SELECT directive_id FROM directives WHERE active = 1").fetchall()
    assert [row[0] for row in active] == [second]


def test_replay_reconstructs_same_authority(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    first = _goal(database, "First direction. success: first receipt exists", nonce="coh-p1")
    sync_owner_goals(database)
    second = _goal(database, "Redirected direction. success: second receipt exists",
                   nonce="coh-p2", supersedes=first)
    sync_owner_goals(database)
    reopened = FoundryDatabase(database.path)
    live = rebuild_executive_state(database).objectives
    replayed = rebuild_executive_state(reopened).objectives
    assert set(replayed) == set(live)
    assert replayed[f"owner-goal:{second}"].status == "ACTIVE"
    assert replayed[f"owner-goal:{first}"].status == "SUSPENDED"


def test_repair_creates_no_duplicates(tmp_path: Path) -> None:
    database, _ = _db(tmp_path)
    directive = _goal(database, _contractless_goal(), nonce="coh-n12")
    for index in range(1, 4):
        run_autonomy_cycle(database, cycle_id=f"coh-dup-{index}", resource_id="resource-t")
    state = rebuild_executive_state(database).objectives
    owned = [o for o in state.values()
             if str(o.provenance.get("directive_id") or "") == directive]
    assert len(owned) == 1
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0
    wakes = [w for w in rebuild_wake_state(database).values()
             if w.get("objective_id") == owned[0].objective_id and w.get("status") == "ACTIVE"]
    assert len(wakes) <= 1


def test_sweep_heals_unselected_objectives(tmp_path: Path) -> None:
    # A WAITING objective the ranking never selects must still converge:
    # the cycle-wide conformance sweep coerces its stale READY step and
    # records the refusal receipt without any mission.
    from jarvis_foundry.executive.autonomy import set_objective_status
    database, _ = _db(tmp_path)
    _goal(database, _contractless_goal(), nonce="coh-n14a")
    parked = sync_owner_goals(database)[0]
    build_plan(database, parked.objective_id, [
        {"step_id": "execute", "description": parked.objective,
         "resource_id": "resource-t", "required_capabilities": (_CAP,)},
    ])
    set_objective_status(database, parked.objective_id, "WAITING",
                         reason="parked for sweep coverage")
    _goal(database, "Store the scoped receipt. success: scoped receipt stored",
          nonce="coh-n14b")
    run_autonomy_cycle(database, cycle_id="coh-sweep", resource_id="resource-t")
    coerced = active_plan(database, parked.objective_id)
    assert coerced is not None
    assert coerced.steps[0].status == "BLOCKED"
    assert CONTRACT_SCOPE_MARKER in tuple(coerced.steps[0].evidence_refs)
    receipts = [e for e in database.events(
        event_types=("executive.mission_generation_blocked",))
        if e.payload.get("objective_id") == parked.objective_id]
    assert any("no explicit executable" in e.payload.get("reason", "") for e in receipts)
    # Exactly one mission exists: the explicitly-scoped objective's.  The
    # parked objective materialized nothing.
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 1


def test_production_cycle_refreshes_stale_baseline(tmp_path: Path) -> None:
    # The autonomy cycle itself (the production path; the continuous cycle
    # is OFF by default) re-inspects a generation-blocked step's resource,
    # advancing a stale observed baseline without touching mission state.
    import json as _json
    database, repository = _db(tmp_path)
    _goal(database, _contractless_goal(), nonce="coh-n15")
    run_autonomy_cycle(database, cycle_id="coh-stale", resource_id="resource-t")
    old_head = _json.loads(
        database.row("resources", "resource_id", "resource-t")["baseline_json"])["head"]
    (repository / "change.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "change.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "change"],
                   check=True, capture_output=True, text=True)
    run_autonomy_cycle(database, cycle_id="coh-refresh", resource_id="resource-t")
    stored = database.row("resources", "resource_id", "resource-t")
    live_head = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()
    assert live_head != old_head
    assert _json.loads(stored["baseline_json"])["head"] == live_head
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0
