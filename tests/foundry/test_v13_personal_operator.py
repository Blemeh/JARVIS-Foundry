"""Focused proof for the V13 bounded local operator connector."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis_foundry.connectors.personal_operator import (
    InjectedOperatorFailure,
    PersonalOperatorConnector,
    PersonalOperatorLimits,
    RECEIPT_SCHEMA,
)
from jarvis_foundry.platform.windows_subprocess import headless_popen_kwargs, pid_alive


def _hash(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _connector(
    root: Path,
    *,
    fault=None,
    command_seconds: float = 2.0,
    output_bytes: int = 32_768,
) -> PersonalOperatorConnector:
    return PersonalOperatorConnector(
        root,
        executable_allowlist=(Path(sys.executable).resolve(),),
        environment_allowlist=("V13_OPERATOR_TEST_VALUE",),
        limits=PersonalOperatorLimits(
            max_file_bytes=1_000_000,
            max_search_files=50,
            max_search_results=10,
            max_search_file_bytes=100_000,
            max_command_seconds=command_seconds,
            max_command_output_bytes=output_bytes,
            max_command_args=16,
        ),
        fault_injector=fault,
    )


def _assert_json_receipt(receipt: dict[str, object]) -> None:
    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["status"] in {"COMPLETED", "FAILED", "UNKNOWN"}
    encoded = json.dumps(receipt, ensure_ascii=True, sort_keys=True)
    assert json.loads(encoded) == receipt
    assert {
        "before",
        "target_identity",
        "source_identity",
        "result",
        "readback",
        "error",
    }.issubset(receipt)


def _terminate_popen(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def test_file_lifecycle_has_hashes_readback_and_no_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    connector = _connector(root)

    created = connector.create_file(
        "inbox/source.txt", "alpha\n", idempotency_key="create-source"
    )
    _assert_json_receipt(created)
    assert created["status"] == "COMPLETED"
    assert created["before"]["exists"] is False
    assert created["target_identity"]["sha256"] == _hash(b"alpha\n")
    assert created["readback"] == created["target_identity"]

    replayed = _connector(root).create_file(
        "inbox/source.txt", "alpha\n", idempotency_key="create-source"
    )
    assert replayed["status"] == "COMPLETED"
    assert replayed["replayed"] is True
    assert replayed["reconciled"] is True

    overwrite = connector.create_file(
        "inbox/source.txt", "changed", idempotency_key="create-overwrite"
    )
    assert overwrite["status"] == "FAILED"
    assert overwrite["error"]["classification"] == "target_exists"
    assert (root / "inbox" / "source.txt").read_bytes() == b"alpha\n"

    copied = connector.copy_file(
        "inbox/source.txt", "work/copied.txt", idempotency_key="copy-source"
    )
    assert copied["status"] == "COMPLETED"
    assert copied["source_identity"]["sha256"] == copied["target_identity"]["sha256"]
    assert copied["readback"]["source"]["exists"] is True

    moved = connector.move_file(
        "work/copied.txt", "work/moved.txt", idempotency_key="move-copy"
    )
    assert moved["status"] == "COMPLETED"
    assert moved["readback"]["source"]["exists"] is False
    assert moved["readback"]["target"]["sha256"] == _hash(b"alpha\n")

    renamed = connector.rename_file(
        "work/moved.txt", "archive/final.txt", idempotency_key="rename-moved"
    )
    assert renamed["status"] == "COMPLETED"
    assert renamed["operation"] == "file.rename"
    assert renamed["readback"]["source"]["exists"] is False
    assert (root / "archive" / "final.txt").read_bytes() == b"alpha\n"


def test_traversal_reserved_paths_invalid_input_and_search_bounds(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    connector = _connector(root)
    connector.create_file("notes/a.txt", "needle one", idempotency_key="a")
    connector.create_file("notes/b.log", "needle two", idempotency_key="b")
    connector.create_file("notes/c.txt", "other", idempotency_key="c")

    traversal = connector.inspect_file("../outside.txt")
    assert traversal["status"] == "FAILED"
    assert traversal["error"]["classification"] == "path_outside_root"

    absolute = connector.inspect_file(tmp_path / "outside.txt")
    assert absolute["error"]["classification"] == "path_outside_root"

    reserved = connector.inspect_file(".jarvis-personal-operator/operations.json")
    assert reserved["error"]["classification"] == "reserved_path"

    invalid = connector.search()
    assert invalid["error"]["classification"] == "invalid_input"

    search = connector.search(
        name_pattern="*.txt",
        content_query="needle",
        max_results=1,
        max_files=50,
    )
    _assert_json_receipt(search)
    assert search["status"] == "COMPLETED"
    assert search["readback"]["match_count"] == 1
    assert search["readback"]["limit_reached"] is True
    assert search["result"]["matches"][0]["identity"]["ref"] == "notes/a.txt"
    assert ".jarvis-personal-operator" not in json.dumps(search)


def test_safe_edit_preserves_original_and_replays_after_restart(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    target = root / "drafts" / "memo.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"before\n")
    source_hash = _hash(b"before\n")
    connector = _connector(root)

    edited = connector.safe_text_edit(
        "drafts/memo.txt",
        "after\n",
        expected_source_hash=source_hash,
        idempotency_key="edit-memo",
    )
    _assert_json_receipt(edited)
    assert edited["status"] == "COMPLETED"
    assert edited["before"]["sha256"] == source_hash
    assert edited["target_identity"]["sha256"] == _hash(b"after\n")
    assert edited["source_identity"]["sha256"] == source_hash
    backup = root / edited["result"]["backup_ref"]
    assert backup.read_bytes() == b"before\n"
    assert target.read_bytes() == b"after\n"

    replay = _connector(root).safe_text_edit(
        "drafts/memo.txt",
        "after\n",
        expected_source_hash=source_hash,
        idempotency_key="edit-memo",
    )
    assert replay["status"] == "COMPLETED"
    assert replay["replayed"] is True
    assert replay["reconciled"] is True
    backups = list((root / ".jarvis-personal-operator" / "backups").rglob("*.original"))
    assert backups == [backup]

    stale = _connector(root).safe_text_edit(
        "drafts/memo.txt",
        "third\n",
        expected_source_hash=source_hash,
        idempotency_key="edit-with-stale-hash",
    )
    assert stale["status"] == "FAILED"
    assert stale["error"]["classification"] == "source_hash_mismatch"
    assert target.read_bytes() == b"after\n"


def test_safe_edit_reconciles_injected_failure_after_atomic_replace(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned"
    target = root / "state.txt"
    root.mkdir()
    target.write_text("old", encoding="utf-8")
    source_hash = _hash(b"old")

    def fail_after_replace(stage: str) -> None:
        if stage == "safe_edit.after_replace":
            raise InjectedOperatorFailure("simulated crash after target replacement")

    interrupted = _connector(root, fault=fail_after_replace).safe_text_edit(
        "state.txt",
        "new",
        expected_source_hash=source_hash,
        idempotency_key="crash-safe-edit",
    )
    assert interrupted["status"] == "UNKNOWN"
    assert interrupted["error"]["classification"] == "partial_failure"
    assert target.read_bytes() == b"new"

    reconciled = _connector(root).safe_text_edit(
        "state.txt",
        "new",
        expected_source_hash=source_hash,
        idempotency_key="crash-safe-edit",
    )
    assert reconciled["status"] == "COMPLETED"
    assert reconciled["replayed"] is True
    assert reconciled["reconciled"] is True
    assert reconciled["readback"]["backup"]["sha256"] == source_hash
    assert len(
        list((root / ".jarvis-personal-operator" / "backups").rglob("*.original"))
    ) == 1


def test_system_environment_and_no_shell_bounded_command(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    connector = _connector(root, command_seconds=1.0, output_bytes=2_048)
    os.environ["V13_OPERATOR_TEST_VALUE"] = "visible"
    try:
        system = connector.system_info()
        assert system["status"] == "COMPLETED"
        assert system["result"]["owned_root"] == str(root.resolve())

        processes = connector.inspect_processes((os.getpid(),))
        assert processes["status"] == "COMPLETED"
        assert processes["result"]["processes"][0]["pid"] == os.getpid()
        assert processes["result"]["processes"][0]["alive"] is True

        applications = connector.inspect_applications(
            (Path(sys.executable).resolve(),)
        )
        assert applications["status"] == "COMPLETED"
        assert applications["result"]["applications"][0]["path"] == str(
            Path(sys.executable).resolve()
        )
        assert applications["result"]["applications"][0]["size_bytes"] > 0

        invalid_processes = connector.inspect_processes((0,))
        assert invalid_processes["error"]["classification"] == "invalid_input"

        environment = connector.environment_info(("V13_OPERATOR_TEST_VALUE",))
        assert environment["result"]["values"] == {
            "V13_OPERATOR_TEST_VALUE": "visible"
        }
        refused = connector.environment_info(("PATH",))
        assert refused["error"]["classification"] == "environment_not_allowed"

        command = connector.run_command(
            Path(sys.executable).resolve(),
            ("-c", "print('bounded-output')"),
            timeout_seconds=1.0,
            output_limit_bytes=2_048,
        )
        _assert_json_receipt(command)
        assert command["status"] == "COMPLETED"
        assert command["result"]["stdout"].strip() == "bounded-output"
        assert command["result"]["exit_code"] == 0
        assert command["readback"]["alive"] is False

        relative_executable = connector.run_command(
            Path(sys.executable).name, ("-c", "print('no')")
        )
        assert relative_executable["error"]["classification"] == "executable_not_allowed"

        output_limited = connector.run_command(
            Path(sys.executable).resolve(),
            ("-c", "print('x' * 100000)"),
            timeout_seconds=1.0,
            output_limit_bytes=128,
        )
        assert output_limited["status"] == "FAILED"
        assert output_limited["error"]["classification"] == "output_limit_exceeded"
        assert output_limited["readback"]["alive"] is False
    finally:
        os.environ.pop("V13_OPERATOR_TEST_VALUE", None)


def test_bounded_command_timeout_terminates_exact_process(tmp_path: Path) -> None:
    connector = _connector(
        tmp_path / "owned", command_seconds=1.0, output_bytes=2_048
    )
    timed_out = connector.run_command(
        Path(sys.executable).resolve(),
        ("-c", "import time; time.sleep(10)"),
        timeout_seconds=0.2,
        output_limit_bytes=2_048,
    )
    assert timed_out["status"] == "FAILED"
    assert timed_out["error"]["classification"] == "command_timeout"
    assert timed_out["readback"]["alive"] is False


@pytest.mark.skipif(os.name != "nt", reason="exact creation identity proof is Windows-only")
def test_restart_reconcile_and_close_leave_same_executable_process_alive(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    executable = Path(sys.executable).resolve()
    unrelated = subprocess.Popen(
        [str(executable), "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **headless_popen_kwargs(),
    )
    owned_pid = None
    try:
        connector = _connector(root)
        launched = connector.launch_process(
            "owned-sleeper",
            executable,
            ("-c", "import time; time.sleep(60)"),
            idempotency_key="launch-owned-sleeper",
        )
        _assert_json_receipt(launched)
        assert launched["status"] == "COMPLETED"
        owned_pid = launched["result"]["pid"]
        assert owned_pid != unrelated.pid
        assert pid_alive(owned_pid)
        assert pid_alive(unrelated.pid)
        assert launched["target_identity"]["creation_identity"]["create_time"] is not None
        assert launched["target_identity"]["command_identity"]

        restarted = _connector(root)
        registry_path = (
            root / ".jarvis-personal-operator" / "processes.json"
        )
        registry_before = registry_path.read_bytes()
        inspected = restarted.inspect_process("owned-sleeper")
        assert inspected["status"] == "COMPLETED"
        assert inspected["result"] == {
            "process_id": "owned-sleeper",
            "state": "running",
            "matches": True,
        }
        assert registry_path.read_bytes() == registry_before

        replay = restarted.launch_process(
            "owned-sleeper",
            executable,
            ("-c", "import time; time.sleep(60)"),
            idempotency_key="launch-owned-sleeper",
        )
        assert replay["status"] == "COMPLETED"
        assert replay["replayed"] is True
        assert replay["result"]["pid"] == owned_pid

        focus = restarted.focus_process("owned-sleeper")
        assert focus["status"] == "FAILED"
        assert focus["error"]["classification"] in {
            "window_not_found",
            "focus_refused",
        }

        closed = restarted.close_process(
            "owned-sleeper", idempotency_key="close-owned-sleeper"
        )
        assert closed["status"] == "COMPLETED"
        assert closed["result"]["state"] == "exited"
        assert not pid_alive(owned_pid)
        assert pid_alive(unrelated.pid)

        close_replay = _connector(root).close_process(
            "owned-sleeper", idempotency_key="close-owned-sleeper"
        )
        assert close_replay["status"] == "COMPLETED"
        assert close_replay["replayed"] is True
        assert pid_alive(unrelated.pid)
    finally:
        if owned_pid is not None and pid_alive(owned_pid):
            _connector(root).close_process(
                "owned-sleeper",
                idempotency_key="close-owned-sleeper",
            )
        _terminate_popen(unrelated)


@pytest.mark.skipif(os.name != "nt", reason="desktop launch identity proof is Windows-only")
def test_desktop_open_is_explicit_app_launch_for_owned_target(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    script = root / "open_target.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    connector = _connector(root)

    opened = connector.desktop_open(
        "owned-open",
        Path(sys.executable).resolve(),
        "open_target.py",
        idempotency_key="desktop-open-owned-script",
        show_window=False,
    )
    assert opened["status"] == "COMPLETED"
    assert opened["operation"] == "desktop.open"
    assert opened["source_identity"]["ref"] == "open_target.py"
    assert opened["target_identity"]["opened_identity"]["sha256"] == _hash(
        script.read_bytes()
    )
    closed = connector.close_process(
        "owned-open", idempotency_key="desktop-close-owned-script"
    )
    assert closed["status"] == "COMPLETED"
