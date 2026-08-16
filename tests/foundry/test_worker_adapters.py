"""Adapter safety boundaries (H2, H3) — real CLIs stay DISABLED by default."""
from __future__ import annotations

from pathlib import Path

import pytest
from unittest import mock

from jarvis_foundry.workers.adapters.base import ProviderUnavailable
from jarvis_foundry.workers.adapters.fakes import ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
from jarvis_foundry.workers.adapters.openhands import OpenHandsWorkerAdapter
from jarvis_foundry.workers.contracts import WorkerStepContract


def contract(tmp_path: Path) -> WorkerStepContract:
    return WorkerStepContract(
        run_id="run-1", mission_id="m", step_id="s", attempt=1,
        role="builder", objective="o", instructions="i",
        working_root=tmp_path)


def test_hermes_adapter_disabled_by_default(tmp_path):
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path, hermes_home=tmp_path / "home",
        enabled=False)
    with pytest.raises(ProviderUnavailable):
        adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)


def test_hermes_adapter_refuses_live_installation(tmp_path):
    live = Path(r"C:\Users\b\AppData\Local\hermes\hermes-agent")
    with pytest.raises(Exception):
        HermesWorkerAdapter(
            hermes_root=live, hermes_home=tmp_path / "home", enabled=True)


def test_hermes_adapter_refuses_live_home(tmp_path):
    live_home = Path(r"C:\Users\b\AppData\Local\hermes")
    with pytest.raises(Exception):
        HermesWorkerAdapter(
            hermes_root=tmp_path, hermes_home=live_home / "sub-probe",
            enabled=True,
            python_exe="python")


def test_openhands_adapter_disabled_by_default(tmp_path):
    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    adapter = OpenHandsWorkerAdapter(
        openhands_exe=exe, data_dir=tmp_path / "data", enabled=False)
    with pytest.raises(ProviderUnavailable):
        adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)


def test_openhands_adapter_refuses_live_home(tmp_path):
    live = Path.home() / ".openhands"
    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    with pytest.raises(Exception):
        OpenHandsWorkerAdapter(
            openhands_exe=exe, data_dir=live, enabled=True)


def test_scripted_adapter_records_contracts(tmp_path):
    adapter = ScriptedWorkerAdapter("openhands")
    step = contract(tmp_path)
    envelope = adapter.dispatch(step, heartbeat=lambda: None)
    assert adapter.received == [step]
    assert envelope.framework == "openhands"
    assert envelope.worker_status == "COMPLETED"
    assert envelope.verdict == "PASS"


def test_scripted_adapter_failure_outcome(tmp_path):
    from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome
    adapter = ScriptedWorkerAdapter(
        "hermes",
        outcome=ScriptedOutcome(worker_status="FAILED", verdict="FAIL",
                                error_class="crash"))
    envelope = adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert envelope.worker_status == "FAILED"
    assert envelope.verdict == "FAIL"


def test_openhands_adapter_fails_closed_on_containment_violation(tmp_path):
    """A run that writes into the owner ~/.openhands must be BLOCKED, never
    COMPLETED, and the violation must be recorded in the envelope."""
    import subprocess
    from jarvis_foundry.workers.adapters import openhands as oh_module

    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    adapter = oh_module.OpenHandsWorkerAdapter(
        openhands_exe=exe, data_dir=data_dir, enabled=True,
        timeout_seconds=30)
    adapter._owner_openhands = tmp_path / "owner-openhands"
    adapter._owner_openhands.mkdir()

    fake_result = subprocess.CompletedProcess(
        ["openhands"], 0, '{"event":"agent_state_changed"}\n', "")
    before = {"profiles/owner.json": "a" * 64}
    after = {"profiles/owner.json": "a" * 64,
             "conversations/probe/state.json": "b" * 64}
    with mock.patch.object(oh_module.subprocess, "run",
                           return_value=fake_result), \
         mock.patch.object(oh_module, "snapshot_openhands_tree",
                           side_effect=[before, after]):
        envelope = adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)

    assert envelope.worker_status == "BLOCKED"
    assert envelope.verdict == "NOT_TESTED"
    assert any("new:conversations/probe/state.json" in b
               for b in envelope.blockers)


def test_openhands_structured_verdict_parsing():
    from jarvis_foundry.workers.envelope import VERDICT_NOT_TESTED

    assert OpenHandsWorkerAdapter._structured_verdict(
        "inspect the artifact\nVERDICT: PASS\nGoodbye") == "PASS"
    assert OpenHandsWorkerAdapter._structured_verdict(
        '{"payload": "VERDICT: FAIL"}') == "FAIL"
    assert OpenHandsWorkerAdapter._structured_verdict(
        "no structured verdict here") == VERDICT_NOT_TESTED
    assert OpenHandsWorkerAdapter._structured_verdict(
        "VERDICT: PASSED") == VERDICT_NOT_TESTED  # word boundary
