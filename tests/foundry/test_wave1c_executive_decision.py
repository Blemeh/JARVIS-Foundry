"""Wave 1C discriminating pins for research-driven Executive reevaluation."""
from __future__ import annotations

from types import SimpleNamespace

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_sources import SourceSpec
from jarvis_foundry.events import Event
from jarvis_foundry.executive.continuous import record_research_wake_continuation
from jarvis_foundry.executive.decisions import a2_precondition
from jarvis_foundry.executive.scoring import CandidateScore, FactorValue
from jarvis_foundry.initiative_a2 import run_continuous_observation_cycle


def _database(tmp_path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_consumed_research_forces_reevaluation_when_monitor_is_not_due(
    tmp_path, monkeypatch,
) -> None:
    """The pre-fix tree returns WAIT before it ever calls the normal A2 path."""

    import jarvis_foundry.initiative_a2 as a2

    database = _database(tmp_path)
    source = SourceSpec(
        source_id="wave1c-project",
        source_kind="project",
        source_identity=str(tmp_path),
        poll_interval_s=3600,
    )
    calls: list[dict] = []

    monkeypatch.setattr(a2, "due_monitors", lambda *args, **kwargs: [])
    monkeypatch.setattr(a2, "next_monitor_due_at", lambda *args, **kwargs: "2099-01-01T00:00:00+00:00")
    monkeypatch.setattr(a2, "_ensure_observation_wake", lambda *args, **kwargs: None)

    def _normal_a2(*args, **kwargs):
        calls.append(kwargs)
        return {
            "outcome": "WAIT",
            "reason": "fresh decision completed",
            "decision": object(),
            "metrics": {"research_calls": 0},
        }

    monkeypatch.setattr(a2, "run_initiative_a2_cycle", _normal_a2)
    report = run_continuous_observation_cycle(
        database,
        sources=(source,),
        research_wake_consumed=1,
        research_trigger_id="worker-cycle-wave1c",
    )

    assert len(calls) == 1
    assert calls[0]["research_wake_consumed"] == 1
    assert report["cycles_dispatched"] == 1
    assert report["research_triggered_reevaluation"] is True
    assert report["cycle_id"].startswith("research-reevaluation-")


def test_research_continuation_refuses_another_cycles_decision(tmp_path) -> None:
    """A fresh decision is insufficient unless it is the consuming cycle's decision."""

    database = _database(tmp_path)
    database.append(Event.create(
        "executive.context_assembled",
        {"cycle_id": "other-cycle"},
        source_type="supervisor",
        source_id="wave1c-test",
        idempotency_key="wave1c:context:other",
    ))
    database.append(Event.create(
        "executive.decision_recorded",
        {"decision_id": "exec-other", "decision": "SELECT"},
        source_type="supervisor",
        source_id="wave1c-test",
        idempotency_key="executive.decision_recorded:exec-other",
    ))

    written = record_research_wake_continuation(
        database,
        cycle_id="consumer-cycle",
        consumed=[{
            "wake_id": "wake:research:r1",
            "research_id": "r1",
            "objective_id": "initiative-objective:c1",
            "due": True,
        }],
        expected_decision_id="exec-consumer",
        expected_context_cycle_id="consumer-cycle",
    )

    assert len(written) == 1
    assert written[0]["decision_id"] is None
    assert written[0]["fresh_decision"] is False
    assert written[0]["fresh_context"] is False
    assert written[0]["decision_owned_by_consuming_cycle"] is False


def test_research_continuation_binds_exact_consuming_cycle(tmp_path) -> None:
    """Positive control: the exact context and decision identities are credited."""

    database = _database(tmp_path)
    database.append(Event.create(
        "executive.context_assembled",
        {"cycle_id": "consumer-cycle"},
        source_type="supervisor",
        source_id="wave1c-test",
        idempotency_key="wave1c:context:consumer",
    ))
    database.append(Event.create(
        "executive.decision_recorded",
        {"decision_id": "exec-consumer", "decision": "SELECT"},
        source_type="supervisor",
        source_id="wave1c-test",
        idempotency_key="executive.decision_recorded:exec-consumer",
    ))

    written = record_research_wake_continuation(
        database,
        cycle_id="consumer-cycle",
        consumed=[{
            "wake_id": "wake:research:r2",
            "research_id": "r2",
            "objective_id": "initiative-objective:c2",
            "due": True,
        }],
        expected_decision_id="exec-consumer",
        expected_context_cycle_id="consumer-cycle",
    )

    assert len(written) == 1
    assert written[0]["decision_id"] == "exec-consumer"
    assert written[0]["decision_kind"] == "SELECT"
    assert written[0]["fresh_decision"] is True
    assert written[0]["fresh_context"] is True
    assert written[0]["decision_owned_by_consuming_cycle"] is True


def test_research_reevaluation_is_not_structurally_wip_confounded() -> None:
    """Reevaluation may compare actions; unchanged A3 still enforces WIP."""

    score = CandidateScore(
        candidate_id="candidate-better",
        objective="bounded better action",
        source="initiative_observation",
        composite=0.8,
        base_score=0.7,
        goal_fit=0.8,
        calibration_adjustment=0.0,
        owner_priority=0.5,
        factors=(FactorValue("confidence", 0.9, "KNOWN", 0.05, 0.045),),
        compared_goals=("owner-goal",),
        a2_metadata={
            "history": {"completed": [], "active": [], "rejected": [], "superseded": []},
            "expired": False,
            "owner_context": {"goal_relevance": 0.8, "project_relevance": 0.8},
            "capability_states": {},
            "unattended_states": {},
            "capability_requirement_set": {},
            "dependencies": {"states": {}},
            "autonomous_wip": 1,
            "autonomous_wip_cap": 1,
            "research_budget_requested": 0.0,
            "research_budget_per_candidate": 1.0,
            "research": {"supporting": True},
        },
    )

    assert a2_precondition(score) == (
        "DEFERRED", "WIP_LIMIT", "autonomous WIP cap 1 is already occupied",
    )
    assert a2_precondition(score, allow_occupied_wip=True) is None


def test_active_objective_research_wake_can_select_better_action(
    tmp_path, monkeypatch,
) -> None:
    """Removing the active-objective reevaluation/SELECT branch fails this pin."""

    from jarvis_foundry.discovery_sources import SourceSpec
    from jarvis_foundry.executive.intelligence import discover_and_admit_next_work
    import jarvis_foundry.initiative_a2 as a2
    import jarvis_foundry.initiative_a3 as a3
    import jarvis_foundry.executive.autonomy as autonomy

    database = _database(tmp_path)
    selected = SimpleNamespace(
        kind="SELECT",
        decision_id="exec-wave1c-better",
        candidate_id="candidate-better",
    )
    monkeypatch.setattr(a3, "active_initiative_objective", lambda db: "objective-current")
    monkeypatch.setattr(
        a2,
        "run_continuous_observation_cycle",
        lambda *args, **kwargs: {"decision": selected, "reason": "research changed rank"},
    )
    monkeypatch.setattr(
        a3.InitiativeAdmissionAuthority,
        "admit",
        lambda self, decision, **kwargs: (
            admitted_kwargs.update(kwargs)
            or SimpleNamespace(
                admitted=True,
                objective_id="objective-better",
                mission_id="mission-better",
                status="ADMITTED",
                reason="selected by fresh research decision",
            )
        ),
    )
    continued = {"called": False}
    admitted_kwargs: dict = {}
    status_changes: list[tuple] = []

    def _continue(_database, objective_id, **kwargs):
        if objective_id == "objective-current":
            continued["called"] = True
            return SimpleNamespace(mission_id="mission-current", status="CONTINUED", reason="blind")
        return SimpleNamespace(
            mission_id="mission-better", status="ADMITTED", reason="fresh selection"
        )

    monkeypatch.setattr(a3, "continue_initiative_objective", _continue)
    monkeypatch.setattr(
        autonomy,
        "set_objective_status",
        lambda *args, **kwargs: status_changes.append((args, kwargs)),
    )
    result = discover_and_admit_next_work(
        database,
        cycle_id="wave1c-consumer",
        resource_id="resource-alpha",
        sources=(SourceSpec(
            source_id="wave1c-source",
            source_kind="project",
            source_identity=str(tmp_path),
        ),),
        project_roots={"resource-alpha": tmp_path},
        worktree_parent=tmp_path / "worktrees",
        research_wake_consumed=1,
    )

    assert result.status == "MISSION"
    assert result.mission_id == "mission-better"
    assert result.decision_id == "exec-wave1c-better"
    assert continued["called"] is False
    assert admitted_kwargs["replacing_objective_id"] == "objective-current"
    assert status_changes[0][0][1:3] == ("objective-current", "SUSPENDED")


def test_active_objective_research_wait_suspends_without_continuing(
    tmp_path, monkeypatch,
) -> None:
    """A fresh WAIT must not turn the stale active objective into a mission."""

    from jarvis_foundry.discovery_sources import SourceSpec
    from jarvis_foundry.executive.intelligence import discover_and_admit_next_work
    import jarvis_foundry.initiative_a2 as a2
    import jarvis_foundry.initiative_a3 as a3
    import jarvis_foundry.executive.autonomy as autonomy

    database = _database(tmp_path)
    decision = SimpleNamespace(
        kind="WAIT",
        decision_id="exec-wave1c-wait",
        candidate_id=None,
        reason="premises contradicted",
        rejected=(),
        wait=None,
    )
    monkeypatch.setattr(a3, "active_initiative_objective", lambda db: "objective-current")
    monkeypatch.setattr(
        a2,
        "run_continuous_observation_cycle",
        lambda *args, **kwargs: {"decision": decision, "reason": "premises contradicted"},
    )
    monkeypatch.setattr(
        a3,
        "continue_initiative_objective",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("WAIT continued the stale objective")
        ),
    )
    status_changes: list[tuple] = []
    monkeypatch.setattr(
        autonomy,
        "set_objective_status",
        lambda *args, **kwargs: status_changes.append((args, kwargs)),
    )

    result = discover_and_admit_next_work(
        database,
        cycle_id="wave1c-wait-consumer",
        resource_id="resource-alpha",
        sources=(SourceSpec(
            source_id="wave1c-source",
            source_kind="project",
            source_identity=str(tmp_path),
        ),),
        project_roots={"resource-alpha": tmp_path},
        worktree_parent=tmp_path / "worktrees",
        research_wake_consumed=1,
    )

    assert result.status == "JUSTIFIED_WAIT"
    assert result.mission_id is None
    assert result.decision_id == "exec-wave1c-wait"
    assert status_changes[0][0][1:3] == ("objective-current", "SUSPENDED")
