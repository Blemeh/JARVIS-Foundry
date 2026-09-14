"""Regression: each fault-campaign child process carries its own loop identity.

The durable cycle identity (request/response ``cycle_id``) is shared across a
fault injection and its resume, but the worker-loop identity must not be: the
runtime records ``worker_loop.cycle_started:<loop_id>`` with the dispatching
process identity in the payload, so replaying one loop id from a second
process raises an ``EventConflict`` instead of resuming.  Production never
replays a loop id -- the worker-loop CLI derives a fresh ``<iteration>-<hex>``
id for every bounded cycle -- so the proving driver must match that contract.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DRIVER_SCRIPT = ROOT / "scripts" / "v11_a3a4_driver.py"
WORKER_LOOP = ROOT / "src" / "jarvis_foundry" / "runtime" / "worker_loop.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fault_children_never_replay_the_durable_cycle_loop_id(tmp_path, monkeypatch) -> None:
    """Injection and resume must use distinct, non-colliding loop ids."""
    driver = _load("v11_a3a4_driver_loop_identity", DRIVER_SCRIPT)
    cycle_id = "cycle-abc123"
    observed: list[tuple[str, str | None]] = []

    def fake_run_child(args, child_cycle_id, *, fault_hook=None, expect_hard_exit=False, mid_build_kill=False):
        observed.append((child_cycle_id, fault_hook))
        return {"outcome": "FROZEN" if fault_hook is None else "RUNNING"}, {
            "child_cycle_id": child_cycle_id
        }

    monkeypatch.setattr(driver, "_run_child", fake_run_child)
    monkeypatch.setattr(driver, "_settle", lambda args, cid: ("WAIT", [{"outcome": "WAITING"}]))
    monkeypatch.setattr(
        driver,
        "_snapshot",
        lambda args, request, **kwargs: {
            "schema": driver.DRIVER_SCHEMA,
            "cycle_id": request["cycle_id"],
            "status": kwargs.get("status"),
            "records": {},
            "metadata": {"agent_attempts": []},
        },
    )

    # Every fault point, in both the injection and the resume phase.
    for point in (
        "post-candidate",
        "post-selection-objective",
        "mid-build",
        "post-effect-pre-acceptance",
        "process-restart",
    ):
        for resume in (False, True):
            driver._fault_cycle(
                _Args(tmp_path),
                {
                    "schema": driver.DRIVER_SCHEMA,
                    "action": "run_cycle",
                    "run_id": "regression-run",
                    "cycle_id": cycle_id,
                    "expected_sha": "f" * 40,
                    "fault_point": point,
                    "resume": resume,
                },
            )

    used = [child_cycle_id for child_cycle_id, _hook in observed]
    assert used, "no child cycles were dispatched"
    assert len(set(used)) == len(used), f"loop identity replay: {used}"
    assert all(item != cycle_id for item in used), (
        "child processes must not reuse the durable cycle id as their loop id"
    )


class _Args:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.source_repo = ROOT
        self.expected_sha = "f" * 40
        self.hermes_root = state_root
        self.hermes_python = state_root / "python.exe"
        self.uv_exe = state_root / "uv.exe"
