from __future__ import annotations

import urllib.error
import urllib.request

import pytest
from r9_helpers import FIXED_UTC, make_broker, register_canary_credential

from jarvis_foundry.capabilities_live.adapters.base import Outcome
from jarvis_foundry.capabilities_live.adapters.files import FilesAdapter
from jarvis_foundry.capabilities_live.adapters.git import GitAdapter
from jarvis_foundry.capabilities_live.adapters.services import ServicesAdapter


def _credential(broker, tmp_path, target: str, capability: str):
    register_canary_credential(
        broker,
        credential_id="cred-real",
        capabilities=(capability,),
        scope={"targets": ["canary"]},
    )
    return broker.resolve(
        "cred-real",
        capability=capability,
        requested_target=target,
        at=FIXED_UTC,
    )


def test_files_adapter_real_effect_idempotent_rollback(tmp_path) -> None:
    proving = tmp_path / "proving"
    broker = make_broker(tmp_path)
    credential = _credential(broker, proving, "canary/files/one.txt", "capability.files")
    adapter = FilesAdapter(proving)
    before = adapter._dir_hash(adapter.root)
    first = adapter.execute(
        "write",
        {
            "idempotency_key": "files-write-1",
            "relative_path": "one.txt",
            "content": "hello r9\n",
        },
        credential,
    )
    assert first.outcome == Outcome.EXECUTED
    assert first.before.state_hash == before
    assert first.after is not None and first.after.state_hash != before
    assert first.receipt is not None and first.receipt.effect_hash
    assert first.reconciliation["reconciled"] is True

    second = adapter.execute(
        "write",
        {
            "idempotency_key": "files-write-1",
            "relative_path": "one.txt",
            "content": "hello r9\n",
        },
        credential,
    )
    assert second.outcome == Outcome.REPLAYED
    assert second.receipt is not None and second.receipt == first.receipt
    assert adapter._dir_hash(adapter.root) == first.after.state_hash

    rollback = adapter.execute(
        "rollback",
        {
            "idempotency_key": "files-write-1",
            "relative_path": "one.txt",
        },
        credential,
    )
    assert rollback.outcome == Outcome.EXECUTED
    assert rollback.after is not None and rollback.after.state_hash == before


def test_files_adapter_stop_and_fault_leave_no_effect(tmp_path) -> None:
    proving = tmp_path / "proving"
    broker = make_broker(tmp_path)
    credential = _credential(broker, proving, "canary/files/one.txt", "capability.files")
    adapter = FilesAdapter(proving)
    before = adapter._dir_hash(adapter.root)
    adapter.request_stop()
    stopped = adapter.execute(
        "write",
        {"idempotency_key": "stop-1", "relative_path": "one.txt", "content": "x\n"},
        credential,
    )
    assert stopped.outcome == Outcome.STOPPED
    assert stopped.no_partial_effect is True
    assert adapter._dir_hash(adapter.root) == before

    fresh = FilesAdapter(proving)
    fresh.inject_fault()
    failed = fresh.execute(
        "write",
        {"idempotency_key": "fault-1", "relative_path": "one.txt", "content": "x\n"},
        credential,
    )
    assert failed.outcome == Outcome.FAILED
    assert failed.no_partial_effect is True
    assert fresh._dir_hash(fresh.root) == before


def test_git_adapter_real_commit_and_revert(tmp_path) -> None:
    proving = tmp_path / "proving"
    broker = make_broker(tmp_path)
    credential = _credential(broker, proving, "canary/repo/note.md", "capability.git")
    adapter = GitAdapter(proving)
    repo = adapter.canary_repo()
    baseline_head = adapter._git(["rev-parse", "HEAD"], repo).stdout.strip()
    baseline_tree = adapter._git(["rev-parse", "HEAD^{tree}"], repo).stdout.strip()

    committed = adapter.execute(
        "commit",
        {
            "idempotency_key": "git-commit-1",
            "relative_path": "note.md",
            "content": "# canary note\n",
            "message": "add canary note",
        },
        credential,
    )
    assert committed.outcome == Outcome.EXECUTED
    assert committed.before.state_hash == baseline_head
    assert committed.after is not None and committed.after.state_hash != baseline_head
    assert committed.receipt is not None
    assert committed.reconciliation["reconciled"] is True

    replayed = adapter.execute(
        "commit",
        {
            "idempotency_key": "git-commit-1",
            "relative_path": "note.md",
            "content": "# canary note\n",
            "message": "add canary note",
        },
        credential,
    )
    assert replayed.outcome == Outcome.REPLAYED
    assert replayed.receipt == committed.receipt
    assert adapter._git(["rev-parse", "HEAD"], repo).stdout.strip() == committed.after.state_hash

    commit_sha = committed.receipt.detail["commit_sha"]
    reverted = adapter.execute(
        "revert",
        {
            "idempotency_key": "git-revert-1",
            "commit_sha": commit_sha,
            "expected_tree": baseline_tree,
        },
        credential,
    )
    assert reverted.outcome == Outcome.EXECUTED
    assert reverted.after is not None
    after_tree = adapter._git(["rev-parse", "HEAD^{tree}"], repo).stdout.strip()
    assert after_tree == baseline_tree
    assert reverted.reconciliation["reconciled"] is True


def test_git_adapter_stop_and_fault(tmp_path) -> None:
    proving = tmp_path / "proving"
    broker = make_broker(tmp_path)
    credential = _credential(broker, proving, "canary/repo/note.md", "capability.git")
    adapter = GitAdapter(proving)
    repo = adapter.canary_repo()
    before_head = adapter._git(["rev-parse", "HEAD"], repo).stdout.strip()
    adapter.request_stop()
    stopped = adapter.execute(
        "commit",
        {"idempotency_key": "stop-1", "relative_path": "note.md", "content": "x\n", "message": "x"},
        credential,
    )
    assert stopped.outcome == Outcome.STOPPED
    assert adapter._git(["rev-parse", "HEAD"], repo).stdout.strip() == before_head

    fresh = GitAdapter(proving)
    fresh.inject_fault()
    failed = fresh.execute(
        "commit",
        {
            "idempotency_key": "fault-1",
            "relative_path": "note.md",
            "content": "x\n",
            "message": "x",
        },
        credential,
    )
    assert failed.outcome == Outcome.FAILED
    assert failed.no_partial_effect is True
    assert fresh._git(["rev-parse", "HEAD"], repo).stdout.strip() == before_head


def test_services_adapter_real_lifecycle_and_stop(tmp_path) -> None:
    proving = tmp_path / "proving"
    broker = make_broker(tmp_path)
    credential = _credential(broker, proving, "canary/service", "capability.services")
    adapter = ServicesAdapter(proving)
    started = adapter.execute(
        "lifecycle",
        {"idempotency_key": "service-1", "port": 0},
        credential,
    )
    assert started.outcome == Outcome.EXECUTED
    assert started.before.state_hash == "stopped"
    assert started.after is not None and started.after.state_hash == "running"
    assert started.receipt is not None
    assert started.reconciliation["reconciled"] is True
    port = int(started.receipt.detail["port"])

    replayed = adapter.execute(
        "lifecycle",
        {"idempotency_key": "service-1", "port": port},
        credential,
    )
    assert replayed.outcome == Outcome.REPLAYED
    assert replayed.receipt == started.receipt

    wrong_fingerprint = "deadbeef"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/health",
        headers={"X-Jarvis-Credential-Fingerprint": wrong_fingerprint},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(request, timeout=5)
    assert exc_info.value.code == 403

    stop = adapter.stop()
    assert stop["port_released"] is True
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)


def test_services_adapter_refuses_after_stop(tmp_path) -> None:
    proving = tmp_path / "proving"
    broker = make_broker(tmp_path)
    credential = _credential(broker, proving, "canary/service", "capability.services")
    adapter = ServicesAdapter(proving)
    adapter.request_stop()
    result = adapter.execute(
        "lifecycle",
        {"idempotency_key": "stop-1", "port": 0},
        credential,
    )
    assert result.outcome == Outcome.STOPPED
    assert result.no_partial_effect is True
