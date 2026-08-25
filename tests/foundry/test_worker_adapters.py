"""Adapter safety boundaries (H2, H3) — real CLIs stay DISABLED by default."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from unittest import mock

from jarvis_foundry.workers.adapters.base import ProviderUnavailable
from jarvis_foundry.workers.adapters.base import (
    redact_secrets,
)
from jarvis_foundry.workers.adapters.fakes import ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
from jarvis_foundry.workers.adapters.openhands import OpenHandsWorkerAdapter
from jarvis_foundry.workers.contracts import WorkerStepContract
from jarvis_foundry.runtime.provider_config import ProviderSettings


TEST_PROVIDER_SETTINGS = ProviderSettings(
    provider="opencode_go",
    model="muse-spark-1.2-contributor",
    base_url="https://opencode.ai/zen/go/v1",
    api_key="sk-test-adapter-key-1234567890",
    source="test",
)


@pytest.fixture(autouse=True)
def _adapter_provider_settings(monkeypatch):
    """Dispatching adapters need a resolved credential without disk reads."""
    monkeypatch.setattr(
        "jarvis_foundry.runtime.provider_config.default_provider_settings",
        lambda: TEST_PROVIDER_SETTINGS,
    )
    return TEST_PROVIDER_SETTINGS


def _hermes_kwargs(tmp_path: Path, **kwargs: object) -> dict[str, object]:
    home = kwargs.pop("hermes_home", tmp_path / "home")
    Path(home).mkdir(parents=True, exist_ok=True)
    return {
        "hermes_root": tmp_path,
        "hermes_home": home,
        "provider_settings": TEST_PROVIDER_SETTINGS,
        **kwargs,
    }


def _openhands_kwargs(tmp_path: Path, **kwargs: object) -> dict[str, object]:
    data_dir = kwargs.pop("data_dir", tmp_path / "data")
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    exe = kwargs.pop("openhands_exe", tmp_path / "openhands.exe")
    Path(exe).write_text("", encoding="utf-8")
    return {
        "openhands_exe": exe,
        "data_dir": data_dir,
        "provider_settings": TEST_PROVIDER_SETTINGS,
        **kwargs,
    }


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
    live = HermesWorkerAdapter.LIVE_HERMES
    with pytest.raises(Exception):
        HermesWorkerAdapter(
            hermes_root=live, hermes_home=tmp_path / "home", enabled=True)


def test_hermes_adapter_refuses_live_home(tmp_path):
    live_home = HermesWorkerAdapter.LIVE_HERMES_HOME
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
    with mock.patch.object(oh_module, "run_subprocess_with_heartbeat",
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


def test_hermes_structured_verdict_parsing_accepts_late_exact_line():
    transcript = "\n".join(["inspection line"] * 40 + ["VERDICT: FAIL"])
    assert HermesWorkerAdapter._structured_verdict(transcript) == "FAIL"


def test_hermes_structured_verdict_parsing_ignores_instruction_echo():
    assert HermesWorkerAdapter._structured_verdict(
        "End with VERDICT: PASS | FAIL | BLOCKED | NOT_TESTED") is None


def test_redact_secrets_removes_token_and_configured_key():
    token = "sk-" + "A" * 24
    key = "sk-cXX" + "B" * 40
    text = f"key={key} token={token} plain"
    redacted = redact_secrets(text, configured_key=key)
    assert key not in redacted
    assert token not in redacted
    assert "<REDACTED>" in redacted
    assert "plain" in redacted


def test_hermes_adapter_redacts_captured_secrets(tmp_path, monkeypatch):
    """A worker session that dumps the credential into its output must never
    pass it into the durable envelope."""
    from unittest import mock
    from jarvis_foundry.workers.adapters import hermes as hermes_module

    token = "sk-" + "C" * 24
    var_name = "JARVIS_" + "OPENCODE_API_KEY"
    captured = f"VERDICT: PASS\nenv {var_name}={token}\n"
    fake = subprocess.CompletedProcess(
        ["python", "-m", "hermes_cli.main"], 0, captured, "")
    (tmp_path / "home").mkdir()
    _hermes_result_file(tmp_path)
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path, hermes_home=tmp_path / "home",
        enabled=True, timeout_seconds=0, python_exe="python")
    with mock.patch.object(
            hermes_module, "run_subprocess_with_heartbeat",
            return_value=fake), \
         mock.patch.object(
            hermes_module, "configured_api_key", return_value=None):
        envelope = adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert envelope.worker_status == "COMPLETED"
    assert envelope.verdict == "PASS"
    assert token not in envelope.summary
    assert "<REDACTED>" in envelope.summary


def test_openhands_scrubs_disposable_run_state(tmp_path):
    """Retained disposable conversation events must never hold the key."""
    data_dir = tmp_path / "openhands-data"
    (data_dir / "contained-home" / ".openhands" / "conversations" / "c1" / "events"
     ).mkdir(parents=True)
    token = "sk-" + "D" * 24
    var_name = "JARVIS_" + "OPENCODE_API_KEY"
    event = data_dir / "contained-home" / ".openhands" / "conversations" / \
        "c1" / "events" / "event-00001.json"
    event.write_text(f'{{"content": "{var_name}={token}"}}',
                     encoding="utf-8")
    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    adapter = OpenHandsWorkerAdapter(
        openhands_exe=exe, data_dir=data_dir, enabled=False)
    adapter._scrub_disposable_state()
    content = event.read_text(encoding="utf-8")
    assert token not in content
    assert "<REDACTED>" in content


def test_openhands_scrubs_disposable_state_on_nonzero_exit(tmp_path):
    """A failed (non-zero exit) OpenHands run must still scrub retained
    disposable run-state files."""
    from unittest import mock
    from jarvis_foundry.workers.adapters import openhands as oh_module

    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    data_dir = tmp_path / "openhands-data"
    event_dir = data_dir / "contained-home" / ".openhands" / \
        "conversations" / "c1" / "events"
    event_dir.mkdir(parents=True)
    token = "sk-" + "E" * 24
    var_name = "JARVIS_" + "OPENCODE_API_KEY"
    event = event_dir / "event-00001.json"
    event.write_text(f'{{"content": "{var_name}={token}"}}', encoding="utf-8")
    adapter = oh_module.OpenHandsWorkerAdapter(
        openhands_exe=exe, data_dir=data_dir, enabled=True,
        timeout_seconds=30)
    adapter._owner_openhands = tmp_path / "owner-openhands"
    adapter._owner_openhands.mkdir()
    fake_result = subprocess.CompletedProcess(
        ["openhands"], 3, "failed\n", "")
    before = {"profiles/owner.json": "a" * 64}
    after = {"profiles/owner.json": "a" * 64}
    with mock.patch.object(
            oh_module, "run_subprocess_with_heartbeat",
            return_value=fake_result), \
         mock.patch.object(
            oh_module, "snapshot_openhands_tree",
            side_effect=[before, after]):
        envelope = adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert envelope.worker_status == "FAILED"
    content = event.read_text(encoding="utf-8")
    assert token not in content
    assert "<REDACTED>" in content


def test_hermes_scrubs_disposable_home(tmp_path):
    """The disposable HERMES_HOME must be scrubbed after a run."""
    home = tmp_path / "hermes-home"
    (home / "sessions").mkdir(parents=True)
    token = "sk-" + "F" * 24
    var_name = "JARVIS_" + "OPENCODE_API_KEY"
    session = home / "sessions" / "s1.json"
    session.write_text(f'{{"env": "{var_name}={token}"}}', encoding="utf-8")
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path, hermes_home=home,
        enabled=False, timeout_seconds=0, python_exe="python")
    adapter._scrub_disposable_home()
    content = session.read_text(encoding="utf-8")
    assert token not in content
    assert "<REDACTED>" in content


def _hermes_result_file(working_root: Path) -> None:
    (working_root / ".jarvis-result.json").write_text(
        '{"files_changed": ["feature.py"]}', encoding="utf-8")


def _openhands_result_file(working_root: Path) -> None:
    (working_root / ".jarvis-result.json").write_text(
        '{"files_changed": ["rows.py", "tests/public/test_no_header.py"]}',
        encoding="utf-8")


def test_hermes_adapter_consumes_machine_result_file(tmp_path, monkeypatch):
    """Hermes must translate the worker's machine-ingestable claim file
    into files_changed instead of always reporting an empty claim."""
    from unittest import mock
    from jarvis_foundry.workers.adapters import hermes as hermes_module

    fake = subprocess.CompletedProcess(
        ["python", "-m", "hermes_cli.main"], 0, "VERDICT: PASS\nok\n", "")
    (tmp_path / "home").mkdir()
    _hermes_result_file(tmp_path)
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path, hermes_home=tmp_path / "home",
        enabled=True, timeout_seconds=0, python_exe="python")
    with mock.patch.object(
            hermes_module, "run_subprocess_with_heartbeat",
            return_value=fake), \
         mock.patch.object(
            hermes_module, "configured_api_key", return_value=None):
        envelope = adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert envelope.worker_status == "COMPLETED"
    assert envelope.verdict == "PASS"
    assert envelope.files_changed == ("feature.py",)


def test_hermes_adapter_missing_result_file_is_not_testable(
        tmp_path, monkeypatch):
    """A provider session that omits the result contract must never PASS:
    the adapter downgrades the verdict to NOT_TESTED and keeps the claim
    empty (no JARVIS-observed diff is copied into the claim)."""
    from unittest import mock
    from jarvis_foundry.workers.adapters import hermes as hermes_module

    fake = subprocess.CompletedProcess(
        ["python", "-m", "hermes_cli.main"], 0, "VERDICT: PASS\n", "")
    (tmp_path / "home").mkdir()
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path, hermes_home=tmp_path / "home",
        enabled=True, timeout_seconds=0, python_exe="python")
    with mock.patch.object(
            hermes_module, "run_subprocess_with_heartbeat",
            return_value=fake), \
         mock.patch.object(
            hermes_module, "configured_api_key", return_value=None):
        envelope = adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert envelope.worker_status == "COMPLETED"
    assert envelope.verdict == "NOT_TESTED"
    assert envelope.files_changed == ()
    assert ".jarvis-result.json" in envelope.summary


def test_openhands_adapter_consumes_machine_result_file(tmp_path):
    """OpenHands must consume the machine-ingestable claim file written by
    the provider session and carry it into the normalized envelope."""
    from unittest import mock
    from jarvis_foundry.workers.adapters import openhands as oh_module

    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _openhands_result_file(tmp_path)
    fake_result = subprocess.CompletedProcess(
        ["openhands"], 0, '{"event":"agent_state_changed"}\nVERDICT: PASS\n', "")
    before = {"profiles/owner.json": "a" * 64}
    adapter = oh_module.OpenHandsWorkerAdapter(
        openhands_exe=exe, data_dir=data_dir, enabled=True,
        timeout_seconds=30)
    adapter._owner_openhands = tmp_path / "owner-openhands"
    adapter._owner_openhands.mkdir()
    with mock.patch.object(oh_module, "run_subprocess_with_heartbeat",
                           return_value=fake_result), \
         mock.patch.object(oh_module, "snapshot_openhands_tree",
                           side_effect=[before, before]):
        envelope = adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert envelope.worker_status == "COMPLETED"
    assert envelope.verdict == "PASS"
    assert envelope.files_changed == (
        "rows.py", "tests/public/test_no_header.py")


def test_openhands_adapter_missing_result_file_is_not_testable(tmp_path):
    """OpenHands without the result contract is NOT_TESTED, never PASS."""
    from unittest import mock
    from jarvis_foundry.workers.adapters import openhands as oh_module

    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    fake_result = subprocess.CompletedProcess(
        ["openhands"], 0, '{"event":"agent_state_changed"}\nVERDICT: PASS\n', "")
    before = {"profiles/owner.json": "a" * 64}
    adapter = oh_module.OpenHandsWorkerAdapter(
        openhands_exe=exe, data_dir=data_dir, enabled=True,
        timeout_seconds=30)
    adapter._owner_openhands = tmp_path / "owner-openhands"
    adapter._owner_openhands.mkdir()
    with mock.patch.object(oh_module, "run_subprocess_with_heartbeat",
                           return_value=fake_result), \
         mock.patch.object(oh_module, "snapshot_openhands_tree",
                           side_effect=[before, before]):
        envelope = adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert envelope.worker_status == "COMPLETED"
    assert envelope.verdict == "NOT_TESTED"
    assert envelope.files_changed == ()


def test_malformed_result_file_stays_not_testable(tmp_path):
    """A malformed result file (non-list files_changed) is treated exactly
    like a missing one: NOT_TESTED, never a fabricated PASS."""
    from unittest import mock
    from jarvis_foundry.workers.adapters import hermes as hermes_module

    (tmp_path / ".jarvis-result.json").write_text(
        '{"files_changed": "feature.py"}', encoding="utf-8")
    fake = subprocess.CompletedProcess(
        ["python", "-m", "hermes_cli.main"], 0, "VERDICT: PASS\n", "")
    (tmp_path / "home").mkdir()
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path, hermes_home=tmp_path / "home",
        enabled=True, timeout_seconds=0, python_exe="python")
    with mock.patch.object(
            hermes_module, "run_subprocess_with_heartbeat",
            return_value=fake), \
         mock.patch.object(
            hermes_module, "configured_api_key", return_value=None):
        envelope = adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert envelope.verdict == "NOT_TESTED"
    assert envelope.files_changed == ()


def test_hermes_passes_result_path_env(tmp_path):
    """Live Hermes must tell the session the exact control-file path."""
    from jarvis_foundry.workers.adapters import hermes as hermes_module
    from jarvis_foundry.workers.result_contract import RESULT_PATH_ENV, result_path

    captured = {}
    fake = subprocess.CompletedProcess(
        ["python", "-m", "hermes_cli.main"], 0, "VERDICT: NOT_TESTED\n", "")
    (tmp_path / "home").mkdir()

    def fake_run(command, **kwargs):
        captured["env"] = kwargs.get("env") or {}
        return fake

    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path, hermes_home=tmp_path / "home",
        enabled=True, timeout_seconds=0, python_exe="python")
    with mock.patch.object(
            hermes_module, "run_subprocess_with_heartbeat",
            side_effect=fake_run), \
         mock.patch.object(
            hermes_module, "configured_api_key", return_value=None):
        adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert captured["env"][RESULT_PATH_ENV] == str(result_path(tmp_path))


def test_hermes_timeout_ingests_result_file_but_never_passes(tmp_path):
    """A timed-out session that already wrote the marker must not ignore it,
    and must stay FAILED / NOT_TESTED (never PASS)."""
    from jarvis_foundry.workers.adapters import hermes as hermes_module

    _hermes_result_file(tmp_path)
    (tmp_path / "home").mkdir()
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path, hermes_home=tmp_path / "home",
        enabled=True, timeout_seconds=5, python_exe="python")
    with mock.patch.object(
            hermes_module, "run_subprocess_with_heartbeat",
            side_effect=subprocess.TimeoutExpired("hermes", 5)), \
         mock.patch.object(
            hermes_module, "configured_api_key", return_value=None):
        envelope = adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert envelope.worker_status == "FAILED"
    assert envelope.exit_state == "TIMEOUT"
    assert envelope.verdict == "NOT_TESTED"
    assert envelope.files_changed == ("feature.py",)


def test_hermes_timeout_without_result_file_stays_not_tested(tmp_path):
    from jarvis_foundry.workers.adapters import hermes as hermes_module

    (tmp_path / "home").mkdir()
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path, hermes_home=tmp_path / "home",
        enabled=True, timeout_seconds=5, python_exe="python")
    with mock.patch.object(
            hermes_module, "run_subprocess_with_heartbeat",
            side_effect=subprocess.TimeoutExpired("hermes", 5)), \
         mock.patch.object(
            hermes_module, "configured_api_key", return_value=None):
        envelope = adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert envelope.worker_status == "FAILED"
    assert envelope.verdict == "NOT_TESTED"
    assert envelope.files_changed == ()
    assert ".jarvis-result.json" in envelope.summary


def test_hermes_dispatch_passes_stop_path_and_pid_table(tmp_path):
    """P0: production dispatch() must pass stop_path and pid_table, not
    only rely on tests injecting workers= into the heartbeat runner."""
    from jarvis_foundry.workers.adapters import hermes as hermes_module

    fake = subprocess.CompletedProcess(
        ["python", "-m", "hermes_cli.main"], 0, "VERDICT: PASS\nok\n", "")
    (tmp_path / "home").mkdir()
    _hermes_result_file(tmp_path)
    stop = tmp_path / "EMERGENCY_STOP"
    table = tmp_path / "adapter_worker_pids.json"
    adapter = HermesWorkerAdapter(
        hermes_root=tmp_path, hermes_home=tmp_path / "home",
        enabled=True, timeout_seconds=0, python_exe="python",
        stop_path=stop, pid_table=table)
    captured: dict = {}

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return fake

    with mock.patch.object(
            hermes_module, "run_subprocess_with_heartbeat", side_effect=spy), \
         mock.patch.object(
            hermes_module, "configured_api_key", return_value=None):
        adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert captured.get("run_id") == "run-1"
    assert Path(captured["stop_path"]) == stop.resolve()
    assert Path(captured["pid_table"]) == table.resolve()
    assert captured.get("worker_type") == "hermes"
    assert captured.get("mission_id") == "m"
    assert captured.get("step_id") == "s"


def test_openhands_dispatch_passes_stop_path_and_pid_table(tmp_path):
    """P0: OpenHands dispatch() kwargs must match Hermes control parity."""
    from jarvis_foundry.workers.adapters import openhands as oh_module

    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stop = tmp_path / "EMERGENCY_STOP"
    table = tmp_path / "adapter_worker_pids.json"
    fake = subprocess.CompletedProcess(
        ["openhands"], 0, "VERDICT: NOT_TESTED\n", "")
    before = {"profiles/owner.json": "a" * 64}
    adapter = oh_module.OpenHandsWorkerAdapter(
        openhands_exe=exe, data_dir=data_dir, enabled=True,
        timeout_seconds=30, stop_path=stop, pid_table=table)
    adapter._owner_openhands = tmp_path / "owner-openhands"
    adapter._owner_openhands.mkdir()
    captured: dict = {}

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return fake

    with mock.patch.object(oh_module, "run_subprocess_with_heartbeat",
                           side_effect=spy), \
         mock.patch.object(oh_module, "snapshot_openhands_tree",
                           side_effect=[before, before]):
        adapter.dispatch(contract(tmp_path), heartbeat=lambda: None)
    assert captured.get("run_id") == "run-1"
    assert Path(captured["stop_path"]) == stop.resolve()
    assert Path(captured["pid_table"]) == table.resolve()
    assert captured.get("worker_type") == "openhands"
    assert captured.get("mission_id") == "m"
    assert captured.get("step_id") == "s"


def test_stack_wires_cli_stop_and_pid_table_onto_hermes_adapter(tmp_path):
    """build_worker_stack is the production constructor path: adapters
    must receive the CLI sentinel even when yaml still says foundry/."""
    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.paths import FoundryPaths
    from jarvis_foundry.runtime._stack import build_worker_stack
    from jarvis_foundry.runtime.config import WorkerRuntimeConfig

    home = tmp_path / "foundry"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    cfg = WorkerRuntimeConfig(
        hermes={"enabled": True, "root": str(tmp_path),
                "timeout_seconds": 0, "python_exe": sys.executable},
        stop_sentinel_path="foundry/EMERGENCY_STOP",
        runtime_state_root=str(tmp_path / "state"),
    )
    stack = build_worker_stack(
        database, FoundryPaths(home), cfg, run_id="r-stop")
    adapter = stack.registry.get("hermes")
    assert adapter.stop_path == (home / "EMERGENCY_STOP").resolve()
    assert adapter.pid_table == (home / "adapter_worker_pids.json").resolve()
    assert adapter.workers is stack.workers
    assert stack.bridge.stop_path == (home / "EMERGENCY_STOP").resolve()


def test_stack_wires_cli_stop_and_pid_table_onto_openhands_adapter(tmp_path):
    """Production stack must wire OpenHands the same STOP/PID controls."""
    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.paths import FoundryPaths
    from jarvis_foundry.runtime._stack import build_worker_stack
    from jarvis_foundry.runtime.config import WorkerRuntimeConfig

    home = tmp_path / "foundry"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    exe = tmp_path / "openhands.exe"
    exe.write_text("", encoding="utf-8")
    cfg = WorkerRuntimeConfig(
        openhands={"enabled": True, "exe": str(exe), "timeout_seconds": 0},
        stop_sentinel_path="foundry/EMERGENCY_STOP",
        runtime_state_root=str(tmp_path / "state"),
    )
    stack = build_worker_stack(
        database, FoundryPaths(home), cfg, run_id="r-oh")
    adapter = stack.registry.get("openhands")
    assert adapter.stop_path == (home / "EMERGENCY_STOP").resolve()
    assert adapter.pid_table == (home / "adapter_worker_pids.json").resolve()
    assert adapter.workers is stack.workers
    assert stack.bridge.stop_path == (home / "EMERGENCY_STOP").resolve()

