"""Focused R12 tests for the integrated fault campaign and guards."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from jarvis_foundry.fault_campaign import REQUIRED_OUTCOME_FIELDS, run_campaign
from jarvis_foundry.fault_campaign.isolation import ProvingWorkspace, seed_portfolio
from jarvis_foundry.fault_campaign.worker_sim import (
    read_heartbeats,
    read_result,
    spawn_worker,
    stop_worker,
)
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.portfolio.selection import SelectionService

FIXED_UTC = "2026-08-16T20:00:00Z"


def _temp_root() -> Path:
    base = Path(tempfile.mkdtemp(prefix="jarvis-r12-test-"))
    os.environ["JARVIS_R12_TEMP"] = str(base)
    return base


def test_all_required_faults_pass() -> None:
    root = _temp_root()
    matrix = run_campaign(root / "campaign", fixed_utc=FIXED_UTC)
    assert matrix["fault_count"] == 44
    assert matrix["failed"] == 0
    assert matrix["incomplete_records"] == 0
    assert matrix["campaign_passed"] is True


def test_every_outcome_has_required_fields() -> None:
    root = _temp_root()
    matrix = run_campaign(root / "campaign", fixed_utc=FIXED_UTC)
    for outcome in matrix["outcomes"]:
        missing = [field for field in REQUIRED_OUTCOME_FIELDS if field not in outcome]
        assert not missing, f"{outcome['fault_id']} missing {missing}"


def test_c06_control_guards_never_bypassed() -> None:
    root = _temp_root()
    matrix = run_campaign(root / "campaign", fixed_utc=FIXED_UTC)
    by_id = {outcome["fault_id"]: outcome for outcome in matrix["outcomes"]}
    assert by_id["f37"]["passed"]  # STOP during queued/in-flight
    assert by_id["f38"]["passed"]  # VETO during work
    assert by_id["f35"]["passed"]  # prompt injection isolated
    assert by_id["f36"]["passed"]  # forged owner directive isolated
    assert by_id["f34"]["passed"]  # malicious content never authority


def test_worker_crash_detected_and_unrelated_continues() -> None:
    root = _temp_root()
    workspace = ProvingWorkspace(root / "worker", fixed_utc=FIXED_UTC)
    crashed_dir = workspace.root / "crashed"
    crashed_dir.mkdir(parents=True, exist_ok=True)
    crashed = spawn_worker(crashed_dir, target_id="w1", mode="crash")
    time.sleep(0.6)
    before = len(read_heartbeats(crashed_dir))
    crashed.wait(timeout=10)
    stop_worker(crashed)
    healthy_dir = workspace.root / "healthy"
    healthy_dir.mkdir(parents=True, exist_ok=True)
    healthy = spawn_worker(healthy_dir, target_id="w2", mode="normal")
    healthy.wait(timeout=10)
    assert before >= 1
    assert read_result(healthy_dir) is not None


def test_portfolio_selection_and_evolution_seed() -> None:
    root = _temp_root()
    workspace = ProvingWorkspace(root / "seed", fixed_utc=FIXED_UTC)
    store = workspace.portfolio("portfolio")
    seed_portfolio(store, fixed_utc=FIXED_UTC)
    decision = SelectionService(store, clock=FixedClock(FIXED_UTC)).run()
    assert decision.status == "SELECTED"
    assert decision.selected_candidate_id == "c-alpha-1"
    assert len(decision.alternatives) == 2


def test_proving_workspace_refuses_outside_temp() -> None:
    root = Path(tempfile.mkdtemp(prefix="jarvis-r12-outside-"))
    os.environ["JARVIS_R12_TEMP"] = str(root / "temp")
    with pytest.raises(RuntimeError):
        ProvingWorkspace(root / "outside", fixed_utc=FIXED_UTC)
