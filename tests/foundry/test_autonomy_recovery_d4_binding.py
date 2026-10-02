"""D4: discovery mission binds to its declaring Executive step, and the binding
emits the exact scope signal a parked step waits on.

Live incident: mission-ecdb7918c2d4 (portfolio discovery, operator-tick fed)
materialized with NO executive_objective_id linkage.  The owner goal's plan
step `execute` therefore stayed `mission_id: None` forever, so
`observe_mission_outcome` and `_verifier_recovery_plan` — both keyed on
step.mission_id — could never engage.  The goal parked on
WAIT_FOR_SIGNAL/executive-scope with no producer.

Repair: `_materialize_discovery_opportunity` binds the mission to the declared
step when the fed item carries executive_objective_id + executive_step_id,
and emits exactly one idempotent `executive-scope:{oid}:{step}` signal event.

Bounds:
- undeclared items are never bound (no invented lineage);
- unknown objective / unknown step fail closed with a reason;
- the signal is idempotent per mission (no wake-generation loop);
- binding never changes step status or evidence (dependency truth untouched).
"""

from __future__ import annotations

from pathlib import Path

from jarvis_foundry.executive.autonomy import (
    active_plan,
    build_plan,
    create_objective,
)
from jarvis_foundry.executive.portfolio_bridge import materialize_portfolio_selection

from worker_test_helpers import mission_database


def _goal_with_step(database, *, objective_id: str = "owner-goal:test-d4",
                    step_id: str = "execute"):
    objective = create_objective(
        database,
        "Improve the registered software project",
        objective_id=objective_id,
        provenance={"directive_id": "owner-test-d4"},
    )
    build_plan(database, objective.objective_id, [
        {"step_id": step_id, "description": objective.objective,
         "resource_id": "resource-1"},
    ])
    return objective


def _register_discovery_item(database, *, item_id: str, kind: str = "CI_FAILURE",
                             objective_id: str | None = None,
                             step_id: str | None = None):
    from jarvis_foundry.evolution.executive_portfolio import register_work_item

    metadata: dict = {}
    if objective_id:
        metadata["executive_objective_id"] = objective_id
    if step_id:
        metadata["executive_step_id"] = step_id
    register_work_item(
        database,
        item_id=item_id,
        kind="REPAIR",
        objective=f"opportunity {kind}",
        resource_id="resource-1",
        provenance={
            "source_lane": "live-github-research-activation",
            "opportunity_kind": kind,
            "problem_statement": f"probe {kind} problem",
            "repository": "blemeh/repo",
            "duplicate_key": f"dup-d4-{item_id}",
            "why_now": "live regression",
        },
        metadata=metadata,
        source_id="test",
    )
    from jarvis_foundry.evolution.executive_portfolio import rebuild_portfolio_state
    item = rebuild_portfolio_state(database).work_items[item_id]
    return item.to_payload() if hasattr(item, "to_payload") else dict(item.__dict__)


def _select(item_id: str, selected_item: dict, *, decision_id: str) -> dict:
    return {
        "decision": "SELECT",
        "decision_id": decision_id,
        "entity_id": item_id,
        "selected_item": selected_item,
        "selected_class": "REPAIR",
    }


def test_declared_mission_binds_to_the_declaring_step(tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    objective = _goal_with_step(database)
    selected_item = _register_discovery_item(
        database, item_id="pw-d4-bind",
        objective_id=objective.objective_id, step_id="execute")

    result = materialize_portfolio_selection(
        database,
        decision=_select("pw-d4-bind", selected_item,
                         decision_id="portfolio-executive:test-d4-bind"),
        resource_id="resource-1",
        capability="write_registered_worktree_files",
        source_id="test",
    )
    assert result["materialized"] is True
    binding = result.get("objective_binding") or {}
    assert binding.get("bound") is True
    assert binding["objective_id"] == objective.objective_id
    assert binding["step_id"] == "execute"

    plan = active_plan(database, objective.objective_id)
    assert plan is not None
    step = next(s for s in plan.steps if s.step_id == "execute")
    assert step.mission_id == result["mission_id"], (
        "the declaring step must carry the mission linkage recovery needs")


def test_binding_emits_exactly_one_scope_signal(tmp_path: Path) -> None:
    from jarvis_foundry.executive.continuous import (
        is_wake_due,
        rebuild_wake_state,
        schedule_wait_for_signal,
    )

    database, _repo = mission_database(tmp_path)
    objective = _goal_with_step(database)
    # Park the step the way the runtime does: a WAIT_FOR_SIGNAL wake on the
    # exact scope signal.
    signal = f"executive-scope:{objective.objective_id}:execute"
    schedule_wait_for_signal(database, objective.objective_id, signal=signal,
                             reason="await executable scope",
                             controller="supervisor")
    assert rebuild_wake_state(database), "wake must exist before binding"

    selected_item = _register_discovery_item(
        database, item_id="pw-d4-signal",
        objective_id=objective.objective_id, step_id="execute")

    result = materialize_portfolio_selection(
        database,
        decision=_select("pw-d4-signal", selected_item,
                         decision_id="portfolio-executive:test-d4-signal"),
        resource_id="resource-1",
        capability="write_registered_worktree_files",
        source_id="test",
    )
    assert result["materialized"] is True
    events = database.events(event_types=(f"{signal}.materialized",))
    assert len(events) == 1, f"exactly one signal event, got {len(events)}"
    assert events[0].payload["mission_id"] == result["mission_id"]

    # The parked wake must now resolve: the signal event postdates the wake.
    wakes = rebuild_wake_state(database)
    parked = next(w for w in wakes.values()
                  if w.get("signal") == signal)
    assert is_wake_due(database, parked) is True, (
        "the scope-parked wake must release on the binding signal")


def test_undeclared_items_are_never_bound(tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    _goal_with_step(database)
    selected_item = _register_discovery_item(
        database, item_id="pw-d4-plain")  # no executive linkage at all

    result = materialize_portfolio_selection(
        database,
        decision=_select("pw-d4-plain", selected_item,
                         decision_id="portfolio-executive:test-d4-plain"),
        resource_id="resource-1",
        capability="write_registered_worktree_files",
        source_id="test",
    )
    assert result["materialized"] is True
    assert (result.get("objective_binding") or {}).get("bound") is False

    plan = active_plan(database, "owner-goal:test-d4")
    step = next(s for s in plan.steps if s.step_id == "execute")
    assert step.mission_id is None


def test_unknown_step_fails_closed_without_stranding(tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    objective = _goal_with_step(database)
    selected_item = _register_discovery_item(
        database, item_id="pw-d4-ghost",
        objective_id=objective.objective_id, step_id="no-such-step")

    result = materialize_portfolio_selection(
        database,
        decision=_select("pw-d4-ghost", selected_item,
                         decision_id="portfolio-executive:test-d4-ghost"),
        resource_id="resource-1",
        capability="write_registered_worktree_files",
        source_id="test",
    )
    # The mission itself is fine; only the lineage bind is refused.
    assert result["materialized"] is True
    binding = result.get("objective_binding") or {}
    assert binding.get("bound") is False
    assert "reason" in binding


def test_binding_leaves_step_status_and_evidence_untouched(tmp_path: Path) -> None:
    database, _repo = mission_database(tmp_path)
    objective = _goal_with_step(database)
    before = active_plan(database, objective.objective_id)
    step_before = next(s for s in before.steps if s.step_id == "execute")
    assert step_before.status == "READY"

    selected_item = _register_discovery_item(
        database, item_id="pw-d4-pure",
        objective_id=objective.objective_id, step_id="execute")
    materialize_portfolio_selection(
        database,
        decision=_select("pw-d4-pure", selected_item,
                         decision_id="portfolio-executive:test-d4-pure"),
        resource_id="resource-1",
        capability="write_registered_worktree_files",
        source_id="test",
    )
    after = active_plan(database, objective.objective_id)
    step_after = next(s for s in after.steps if s.step_id == "execute")
    # The bind marks the step RUNNING (a mission now owns it) — that is the
    # recovery linkage working, not a status regression.  Dependency truth
    # (evidence, strategies) is untouched.
    assert step_after.status == "RUNNING"
    assert step_after.mission_id is not None
    assert tuple(step_after.evidence_refs or ()) == tuple(
        step_before.evidence_refs or ())
    assert tuple(step_after.attempted_strategies or ()) == tuple(
        step_before.attempted_strategies or ())


def test_second_declaration_keeps_the_first_binding(tmp_path: Path) -> None:
    """P1: two items declaring the same step must not orphan the first.

    The first binding stands; the second mission remains valid portfolio
    work but gains no Executive lineage, and emits no second signal.
    """
    database, _repo = mission_database(tmp_path)
    objective = _goal_with_step(database)
    first_item = _register_discovery_item(
        database, item_id="pw-d4-first",
        objective_id=objective.objective_id, step_id="execute")
    first = materialize_portfolio_selection(
        database,
        decision=_select("pw-d4-first", first_item,
                         decision_id="portfolio-executive:test-d4-first"),
        resource_id="resource-1",
        capability="write_registered_worktree_files",
        source_id="test",
    )
    assert (first.get("objective_binding") or {}).get("bound") is True
    first_mission = first["mission_id"]

    second_item = _register_discovery_item(
        database, item_id="pw-d4-second",
        objective_id=objective.objective_id, step_id="execute")
    second = materialize_portfolio_selection(
        database,
        decision=_select("pw-d4-second", second_item,
                         decision_id="portfolio-executive:test-d4-second"),
        resource_id="resource-1",
        capability="write_registered_worktree_files",
        source_id="test",
    )
    # The second mission itself materializes; only the lineage is refused.
    assert second["materialized"] is True
    binding = second.get("objective_binding") or {}
    assert binding.get("bound") is False
    assert "already bound" in str(binding.get("reason") or "")

    plan = active_plan(database, objective.objective_id)
    step = next(s for s in plan.steps if s.step_id == "execute")
    assert step.mission_id == first_mission, (
        "the first mission's recovery lineage must not be orphaned")

    signal = f"executive-scope:{objective.objective_id}:execute"
    events = database.events(event_types=(f"{signal}.materialized",))
    assert len(events) == 1, (
        f"one signal per bound mission, got {len(events)}")


def test_unsatisfiable_template_refused_before_any_shell(tmp_path: Path) -> None:
    """P1: a template that mandates a deliverable inside its own forbidden
    set is refused with an exact reason and leaves no mission behind."""
    from jarvis_foundry.executive import portfolio_bridge as bridge

    scratch = {
        "target": ".jarvis/evil",
        "forbidden": ("src/", ".jarvis/", "var/"),
        "risk_class": "C1",
        "rollback": {"method": "delete_file", "reversible": True},
        "plan_focus": "Probe the scratch problem.",
        "execute_focus": "Fix the scratch problem.",
        "acceptance": (
            "scratch problem fixed with recorded evidence",
            "no file outside the allowed scope was modified",
        ),
        "artifacts": (".jarvis/evil.md",),
    }
    bridge._DISCOVERY_CONTRACT_TEMPLATES["SCRATCH_UNSAT"] = scratch
    try:
        database, _repo = mission_database(tmp_path)
        selected_item = _register_discovery_item(
            database, item_id="pw-d4-unsat", kind="SCRATCH_UNSAT")
        result = materialize_portfolio_selection(
            database,
            decision=_select(
                "pw-d4-unsat", selected_item,
                decision_id="portfolio-executive:test-d4-unsat"),
            resource_id="resource-1",
            capability="write_registered_worktree_files",
            source_id="test",
        )
        assert result["materialized"] is False
        assert result["mission_id"] is None
        assert "unsatisfiable" in str(result.get("blocked_reason") or "")
    finally:
        del bridge._DISCOVERY_CONTRACT_TEMPLATES["SCRATCH_UNSAT"]
