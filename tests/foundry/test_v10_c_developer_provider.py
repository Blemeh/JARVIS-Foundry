"""V10-C developer/provider seam and local Golden Journey 3 proof."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

import pytest

from jarvis_foundry.connectors.account_activation import (
    AccountActivationManager,
    ActivationReadiness,
    FakeCredentialStore,
)
from jarvis_foundry.connectors.gmail_api import GmailApiProvider, GmailResponse
from jarvis_foundry.connectors.github_write import (
    GitHubWriteAdapter,
    GitHubWriteTransportError,
    gh_api_write_transport,
    github_effect_key,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import canonical_json
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    WorkflowStep,
    run_workflow,
)
from jarvis_foundry.operations import DisposableDeploymentSlot, independent_verify_release

from v3_fixtures import make_bare_remote


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _intent(action: str = "github.issue.read") -> TaskIntent:
    return TaskIntent(task_id="v10-c-test", required_actions=(action,), unattended=True)


def _ctx(tmp_path: Path, database: FoundryDatabase, **kwargs: object) -> ExecutorContext:
    owned = tmp_path / "owned"
    owned.mkdir()
    return ExecutorContext(database=database, owned_root=owned, **kwargs)


def _repo(tmp_path: Path, owned_root: Path, *, failing_test: bool = False) -> tuple[Path, Path, str]:
    bare = make_bare_remote(tmp_path / "journey-remote")
    work = owned_root / "repo"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "v10-c@local"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "v10-c-test"], check=True)
    (work / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    test_body = (
        "def test_candidate_file():\n"
        "    assert False, 'forced Journey 3 test failure'\n"
        if failing_test
        else
        "from pathlib import Path\n"
        "\n"
        "def test_candidate_file():\n"
        "    assert Path(__file__).with_name('feature.py').read_text() == 'VALUE = 42\\n'\n"
    )
    (work / "test_feature.py").write_text(test_body, encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", ".gitignore", "test_feature.py"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "journey base"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "HEAD:main"], check=True)
    base_head = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return work, bare, base_head


def _journey_steps(work: Path, bare: Path, *, stop_path: str | None = None) -> tuple[WorkflowStep, ...]:
    commit_payload = {
        "worktree": "$branch.worktree",
        "existing": True,
        "paths": ["feature.py"],
        "message": "implement the bounded Journey 3 change",
    }
    if stop_path is not None:
        commit_payload["stop_path"] = stop_path
    return (
        WorkflowStep(
            "research",
            "github.issue.read",
            {
                "action": "github.issue.read",
                "endpoint": "/repos/microsoft/playwright-mcp/issues/1726",
            },
        ),
        WorkflowStep(
            "inspect",
            "git.inspect",
            {"worktree": str(work), "remote": str(bare), "branch": "main"},
        ),
        WorkflowStep("branch", "git.branch", {"worktree": "$inspect.worktree", "name": "v10-c-journey"}),
        WorkflowStep(
            "worker",
            "file.write",
            {"target": "repo/feature.py", "content": "VALUE = 42\n"},
        ),
        WorkflowStep("tests", "tests.run", {"target": "repo/test_feature.py"}),
        WorkflowStep("commit", "git.commit", commit_payload),
        WorkflowStep("diff", "git.diff", {"worktree": "$commit.worktree"}),
    )


def test_gh_api_write_transport_posts_json_without_private_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        seen["args"] = args
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=b'{"number": 9}', stderr=b"")

    monkeypatch.setattr("jarvis_foundry.connectors.github_write.shutil.which", lambda _name: "gh")
    monkeypatch.setattr("jarvis_foundry.connectors.github_write.subprocess.run", fake_run)
    status, body = gh_api_write_transport(
        "/repos/owner/demo/issues",
        {"title": "bounded", "body": "test", "_jarvis_effect_key": "local-only"},
    )
    assert status == 200
    assert json.loads(body) == {"number": 9}
    args = seen["args"]
    assert isinstance(args, list)
    assert args[0:3] == ["gh", "api", "/repos/owner/demo/issues"]
    assert "--method" in args and "POST" in args and "--input" in args
    assert b"_jarvis_effect_key" not in seen["input"]  # type: ignore[operator]
    assert json.loads(seen["input"].decode("utf-8")) == {"body": "test", "title": "bounded"}  # type: ignore[union-attr]


def test_github_write_adapter_identity_readback_and_duplicate_prevention() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    document = {
        "number": 7,
        "title": "bounded issue",
        "body": "from V10-C",
        "html_url": "https://github.com/owner/demo/issues/7",
    }

    def transport(endpoint: str, payload: dict[str, object]) -> tuple[int, bytes]:
        calls.append((endpoint, dict(payload)))
        return 201, json.dumps(document).encode("utf-8")

    def read(endpoint: str) -> tuple[int, bytes]:
        assert endpoint == "/repos/owner/demo/issues/7"
        return 200, json.dumps(document).encode("utf-8")

    adapter = GitHubWriteAdapter(
        transport=transport,
        read_transport=read,
        dispatch_enabled=True,
        authorized_repositories=("owner/demo",),
    )
    payload = {"title": "bounded issue", "body": "from V10-C"}
    result = adapter.write("github.issue.create", "/repos/owner/demo/issues", payload)
    assert result["claimed"] == "COMPLETED"
    assert result["provider_id"] == "github:issues:owner/demo#7"
    assert result["readback_status"] == "MATCH"
    assert result["reconciliation_required"] is False
    assert result["idempotency_key"] == github_effect_key(
        "github.issue.create", "/repos/owner/demo/issues", payload
    )

    replay = adapter.write("github.issue.create", "/repos/owner/demo/issues", payload)
    assert replay["replayed"] is True
    assert replay["duplicate_prevented"] is True
    assert len(calls) == 1
    assert adapter.reconcile(result["idempotency_key"])["readback_status"] == "MATCH"


def test_github_timeout_stays_unknown_until_collection_reconciliation() -> None:
    calls = 0
    item = {
        "number": 12,
        "title": "ambiguous issue",
        "body": "timeout-after-effect",
        "html_url": "https://github.com/owner/demo/issues/12",
    }

    def timeout(_endpoint: str, _payload: Mapping[str, object]) -> tuple[int, bytes]:
        nonlocal calls
        calls += 1
        raise GitHubWriteTransportError("timeout", "response lost", ambiguous=True)

    def read(endpoint: str) -> tuple[int, bytes]:
        if endpoint == "/repos/owner/demo/issues?state=all&per_page=100":
            return 200, json.dumps([item]).encode("utf-8")
        assert endpoint == "/repos/owner/demo/issues/12"
        return 200, json.dumps(item).encode("utf-8")

    adapter = GitHubWriteAdapter(
        transport=timeout,
        read_transport=read,
        dispatch_enabled=True,
        authorized_repositories=("owner/demo",),
    )
    payload = {"title": "ambiguous issue", "body": "timeout-after-effect"}
    first = adapter.write("github.issue.create", "/repos/owner/demo/issues", payload)
    assert first["claimed"] == "UNKNOWN"
    assert first["error_code"] == "timeout"
    assert first["reconciliation_required"] is True
    second = adapter.write("github.issue.create", "/repos/owner/demo/issues", payload)
    assert second["claimed"] == "UNKNOWN"
    assert second["error_code"] == "pending_reconciliation"
    assert second["duplicate_prevented"] is True
    assert calls == 1

    reconciled = adapter.reconcile(first["idempotency_key"])
    assert reconciled["claimed"] == "COMPLETED"
    assert reconciled["reconciled"] is True
    assert reconciled["provider_id"] == "github:issues:owner/demo#12"
    assert reconciled["readback_status"] == "MATCH"


def test_gmail_activation_is_truthfully_unconfigured_without_credentials(tmp_path: Path) -> None:
    database = _db(tmp_path)
    provider = GmailApiProvider("owner@example.com")
    manager = AccountActivationManager(database, FakeCredentialStore())
    manager.register(
        provider,
        account_identity="owner@example.com",
        credential_reference=None,
        requested_scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        activation_id="activation-gmail-v10c-unconfigured",
    )
    diagnostics = manager.activate("activation-gmail-v10c-unconfigured")
    assert diagnostics.readiness == ActivationReadiness.UNCONFIGURED
    assert diagnostics.enabled is False
    assert provider.spec.connector_id == "conn-gmail-api-v1"


def test_gmail_identity_read_probe_and_dry_run_eligibility_use_opaque_material(tmp_path: Path) -> None:
    database = _db(tmp_path)
    credentials = FakeCredentialStore()
    reference = "cred:gmail:v10c"
    scopes = (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
    )
    credentials.put(
        reference,
        provider="gmail",
        account_identity="owner@example.com",
        scopes=scopes,
        token="synthetic-token-never-in-event",
    )
    calls: list[tuple[str, str]] = []

    def transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> GmailResponse:
        calls.append((method, url))
        assert headers["Authorization"] == "Bearer synthetic-token-never-in-event"
        assert body is None
        if url.endswith("/profile"):
            return GmailResponse(200, {}, b'{"emailAddress":"owner@example.com","messagesTotal":3}')
        return GmailResponse(200, {}, b'{"resultSizeEstimate":1,"threads":[{"id":"thread-001"}]}')

    provider = GmailApiProvider(
        "owner@example.com",
        token_resolver=lambda _handle: "synthetic-token-never-in-event",
        transport=transport,
    )
    manager = AccountActivationManager(database, credentials)
    manager.register(
        provider,
        account_identity="owner@example.com",
        credential_reference=reference,
        requested_scopes=scopes,
        activation_id="activation-gmail-v10c-real-adapter",
    )
    diagnostics = manager.activate("activation-gmail-v10c-real-adapter")
    assert diagnostics.readiness == ActivationReadiness.READY
    assert diagnostics.read_probe is not None
    assert diagnostics.read_probe["resource_kind"] == "thread_collection"
    manager.enable("activation-gmail-v10c-real-adapter")
    eligibility = manager.write_eligibility(
        "activation-gmail-v10c-real-adapter",
        capability="gmail.draft",
        exact_target="draft:v10c-001",
        idempotency_key="gmail-v10c-draft-001",
    )
    assert eligibility.eligible is True
    assert eligibility.dry_run is True
    assert manager.reconcile(
        "activation-gmail-v10c-real-adapter", idempotency_key="gmail-v10c-draft-001"
    )["status"] == "UNKNOWN"
    events = canonical_json([event.payload for event in database.events()])
    assert "synthetic-token-never-in-event" not in events
    assert calls == [
        ("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile"),
        ("GET", "https://gmail.googleapis.com/gmail/v1/users/me/threads?maxResults=1"),
        ("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile"),
    ]


def test_gmail_adapter_without_token_broker_is_blocked(tmp_path: Path) -> None:
    database = _db(tmp_path)
    credentials = FakeCredentialStore()
    credentials.put(
        "cred:gmail:no-broker",
        provider="gmail",
        account_identity="owner@example.com",
        scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        token="synthetic-only",
    )
    manager = AccountActivationManager(database, credentials)
    manager.register(
        GmailApiProvider("owner@example.com"),
        account_identity="owner@example.com",
        credential_reference="cred:gmail:no-broker",
        requested_scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        activation_id="activation-gmail-v10c-no-broker",
    )
    diagnostics = manager.activate("activation-gmail-v10c-no-broker")
    assert diagnostics.readiness == ActivationReadiness.BLOCKED
    assert diagnostics.reason == "Gmail credential material is not available in this runtime"


def test_golden_journey_3_local_release_verification_and_rollback(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(
        tmp_path,
        database,
        github_api=lambda _endpoint: (
            200,
            json.dumps({
                "number": 1726,
                "title": "Option for no-clobber output files and reserved name handling",
                "state": "open",
            }).encode("utf-8"),
        ),
    )
    work, bare, base_head = _repo(tmp_path, ctx.owned_root)
    slot = DisposableDeploymentSlot(tmp_path / "disposable-slot")
    base_artifact = slot.build(work, artifact_id="release-base", source_ref="git-base")
    slot.stage(base_artifact)
    slot.switch(base_artifact)
    workflow = run_workflow(
        database,
        ctx,
        _intent(),
        _journey_steps(work, bare),
    )
    assert workflow["success"] is True
    assert workflow["stopped_at"] is None
    commit_head = workflow["context"]["commit"]["head"]
    assert commit_head != base_head
    assert workflow["context"]["tests"]["returncode"] == 0
    assert workflow["context"]["diff"]["dirty"] is False

    artifact = slot.build(work, artifact_id="release-journey3", source_ref="git-journey3")
    slot.stage(artifact)
    assert slot.health_check(artifact) is True
    switched = slot.switch(artifact)
    assert switched.current == "release-journey3"

    verification = independent_verify_release(
        work,
        expected_head=commit_head,
        artifact=artifact,
        slot=slot,
    )
    assert verification["verdict"] == "VERIFIED"
    assert all(verification["checks"].values())
    repeated = independent_verify_release(
        work,
        expected_head=commit_head,
        artifact=artifact,
        slot=slot,
    )
    assert repeated["runtime_identity"] == verification["runtime_identity"]

    rolled_back = slot.rollback()
    assert rolled_back.current == "release-base"
    assert slot.reconcile()["consistent"] is True


def test_golden_journey_test_failure_has_no_commit_or_release(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(
        tmp_path,
        database,
        github_api=lambda _endpoint: (200, b'{"number":1726,"title":"research","state":"open"}'),
    )
    work, bare, base_head = _repo(tmp_path, ctx.owned_root, failing_test=True)
    result = run_workflow(database, ctx, _intent(), _journey_steps(work, bare))
    current_head = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert result["success"] is False
    assert result["stopped_at"] == "tests"
    assert result["context"]["tests"]["returncode"] != 0
    assert current_head == base_head
    assert (work / "feature.py").is_file()
    assert not (tmp_path / "disposable-slot" / "CURRENT").exists()


def test_golden_journey_stop_before_commit_has_no_commit_or_release(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(
        tmp_path,
        database,
        github_api=lambda _endpoint: (200, b'{"number":1726,"title":"research","state":"open"}'),
    )
    work, bare, base_head = _repo(tmp_path, ctx.owned_root)
    stop = ctx.owned_root / "EMERGENCY_STOP"
    stop.write_text("owner stop\n", encoding="utf-8")
    result = run_workflow(
        database,
        ctx,
        _intent(),
        _journey_steps(work, bare, stop_path="EMERGENCY_STOP"),
    )
    current_head = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert result["success"] is False
    assert result["stopped_at"] == "commit"
    assert result["results"][-1]["claimed"] == "BLOCKED"
    assert current_head == base_head
    assert not (tmp_path / "disposable-slot" / "CURRENT").exists()
