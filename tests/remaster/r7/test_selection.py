from __future__ import annotations

from pathlib import Path

import pytest
from r7_helpers import (
    make_budget,
    make_candidate,
    make_project,
)

from jarvis_foundry.kernel.portfolio.control import PortfolioControl
from jarvis_foundry.kernel.portfolio.missions import TargetMismatchError
from jarvis_foundry.kernel.portfolio.records import CandidateAction
from jarvis_foundry.kernel.portfolio.selection import SelectionService
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def _loaded_store(
    tmp_path: Path,
    *,
    candidates: list[object] | None = None,
    projects: list[object] | None = None,
) -> PortfolioStore:
    store = PortfolioStore(tmp_path / "portfolio")
    for project in projects or [make_project(), make_project(project_id="operators")]:
        store.append_project(project)
    for candidate in candidates or [
        make_candidate(
            "leadgen-repair",
            project_id="leadgen",
            expected_value=0.8,
            uncertainty=0.1,
        ),
        make_candidate(
            "operators-repair",
            project_id="operators",
            expected_value=0.7,
            uncertainty=0.2,
        ),
    ]:
        store.append_candidate(candidate)
    return store


def _service(store: PortfolioStore, budget=None) -> SelectionService:
    return SelectionService(store, budget=budget or make_budget())


def test_selection_picks_one_action_bound_to_canonical_target(
    tmp_path: Path,
) -> None:
    store = _loaded_store(tmp_path)
    decision = _service(store).run()
    assert decision.status == "SELECTED"
    assert decision.selected_candidate_id == "leadgen-repair"
    assert decision.project_id == "leadgen"
    assert decision.canonical_target == "proving/leadgen"
    assert len(decision.alternatives) == 2


def test_selection_records_every_eligible_alternative_and_tradeoff(
    tmp_path: Path,
) -> None:
    store = _loaded_store(tmp_path)
    decision = _service(store).run()
    candidate_ids = {alt.candidate_id for alt in decision.alternatives}
    assert candidate_ids == {"leadgen-repair", "operators-repair"}
    assert all(alt.tradeoffs for alt in decision.alternatives)
    assert decision.uncertainty


def test_selection_records_zero_value_alternatives_but_never_selects_them(
    tmp_path: Path,
) -> None:
    store = _loaded_store(tmp_path)
    zero = CandidateAction(
        candidate_id="zero-value",
        project_id="leadgen",
        objective="zero expected value probe",
        status="READY",
        expected_value=0.0,
        canonical_target="proving/leadgen",
        action_class="bounded_repair",
        evidence_refs=(),
        uncertainty=0.1,
        urgency=0.1,
        information_gain=0.1,
        reversibility=0.1,
        opportunity_cost=0.1,
        risk=0.1,
        cost_usd=0.0,
        budget_effects=1,
    )
    store.append_candidate(zero)
    decision = _service(store).run()
    assert decision.status == "SELECTED"
    assert decision.selected_candidate_id != "zero-value"
    ids = {alt.candidate_id for alt in decision.alternatives}
    assert "zero-value" in ids


def test_selection_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    service = _service(store)
    first = service.run()
    second = service.run()
    assert first.decision_id == second.decision_id
    assert first.selected_candidate_id == second.selected_candidate_id
    assert len(store.decisions()) == 1


def test_restart_reproduces_same_decision(tmp_path: Path) -> None:
    root = tmp_path / "portfolio"
    store = _loaded_store(tmp_path)
    first = _service(store).run()
    reopened = PortfolioStore(root)
    decision = _service(reopened).run()
    assert decision.decision_id == first.decision_id
    assert decision.selected_candidate_id == first.selected_candidate_id
    assert len(reopened.decisions()) == 1


def test_honest_waiting_when_nothing_valuable(tmp_path: Path) -> None:
    store = _loaded_store(
        tmp_path,
        candidates=[
            make_candidate("weak", expected_value=0.0, uncertainty=1.0),
        ],
    )
    decision = _service(store).run()
    assert decision.status == "WAITING"
    assert decision.selected_candidate_id is None
    assert "wait" in decision.reason.lower() or "valuable" in decision.reason.lower()
    # J04-S01/J04-U02: the WAITING decision still records every eligible
    # alternative and its uncertainty instead of hiding them.
    assert len(decision.alternatives) == 1
    assert decision.alternatives[0].candidate_id == "weak"
    assert decision.uncertainty == {"weak": 1.0}


def test_no_eligible_candidates_emits_waiting(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path, candidates=[make_candidate(status="BLOCKED")])
    decision = _service(store).run()
    assert decision.status == "WAITING"
    assert decision.selected_candidate_id is None


def test_paused_project_is_excluded(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    control = PortfolioControl(store)
    control.pause("operators", nonce="pause-1")
    decision = _service(store).run()
    assert decision.selected_candidate_id == "leadgen-repair"
    assert all(alt.candidate_id != "operators-repair" for alt in decision.alternatives)
    control.resume("operators", nonce="resume-1")
    decision_after = _service(store).run()
    assert len(decision_after.alternatives) == 2


def test_stop_blocks_selection_fail_closed(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    control = PortfolioControl(store)
    control.stop(nonce="stop-1", reason="owner STOP")
    decision = _service(store).run()
    assert decision.status == "WAITING"
    assert decision.selected_candidate_id is None


def test_reprioritize_changes_winner_deterministically(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    control = PortfolioControl(store)
    control.reprioritize("operators-repair", priority_boost=10, nonce="boost-1")
    decision = _service(store).run()
    assert decision.selected_candidate_id == "operators-repair"
    assert decision.canonical_target == "proving/operators"


def test_budget_effect_limit_refuses_oversized_action(tmp_path: Path) -> None:
    store = _loaded_store(
        tmp_path,
        candidates=[
            make_candidate("oversized", budget_effects=5),
            make_candidate("small", budget_effects=1, expected_value=0.2),
        ],
    )
    decision = _service(store, make_budget(max_calls=2)).run()
    assert decision.selected_candidate_id == "small"
    assert decision.status == "SELECTED"


def test_budget_exhausted_emits_waiting_or_refuses(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    budget = make_budget(max_calls=0)
    decision = _service(store, budget).run()
    assert decision.status == "WAITING"
    assert "budget" in decision.reason.lower()


def test_dispatch_target_mismatch_is_refused(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    service = _service(store)
    decision = service.run()
    with pytest.raises(TargetMismatchError):
        service.validate_dispatch(decision, "proving/operators")


def test_vetoed_candidate_is_excluded(tmp_path: Path) -> None:
    store = _loaded_store(tmp_path)
    control = PortfolioControl(store)
    control.veto("leadgen-repair", nonce="veto-1", reason="owner veto")
    decision = _service(store).run()
    assert decision.selected_candidate_id == "operators-repair"
