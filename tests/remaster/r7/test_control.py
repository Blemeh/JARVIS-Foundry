from __future__ import annotations

from pathlib import Path

from r7_helpers import make_store

from jarvis_foundry.kernel.portfolio.control import PortfolioControl


def test_stop_pause_veto_are_durable(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    control = PortfolioControl(store)
    control.stop(nonce="stop-1", reason="owner STOP")
    control.pause("leadgen", nonce="pause-1")
    control.veto("candidate-1", nonce="veto-1", reason="owner veto")
    state = control.state()
    assert state.stop_active is True
    assert state.paused_projects == ("leadgen",)
    assert state.vetoed_candidates == ("candidate-1",)
    assert len(store.control_directives()) == 3


def test_resume_and_reprioritize_clear_or_persist(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    control = PortfolioControl(store)
    control.pause("leadgen", nonce="pause-1")
    control.resume("leadgen", nonce="resume-1")
    control.reprioritize("candidate-1", priority_boost=3, nonce="boost-1")
    state = control.state()
    assert state.paused_projects == ()
    assert control.priority_boost("candidate-1") == 3


def test_control_hash_changes_only_on_owner_actions(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    control = PortfolioControl(store)
    before = control.state().control_hash
    assert before
    # A no-op re-read must not change the durable control state.
    assert control.state().control_hash == before
