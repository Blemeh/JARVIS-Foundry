"""Fresh R9 verifier subprocess: reproduces evidence without builder context."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from jarvis_foundry.capabilities_live.adapters.base import Outcome
from jarvis_foundry.capabilities_live.adapters.fakes import FailClosedFakeAdapter
from jarvis_foundry.capabilities_live.adapters.files import FilesAdapter
from jarvis_foundry.capabilities_live.adapters.git import GitAdapter
from jarvis_foundry.capabilities_live.adapters.services import ServicesAdapter
from jarvis_foundry.capabilities_live.broker import CredentialBroker
from jarvis_foundry.capabilities_live.ledger_events import r9_ledger
from jarvis_foundry.capabilities_live.policy import FinancialPolicy
from jarvis_foundry.capabilities_live.registry import ActivationProof, CapabilityRegistry
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.errors import FinancialActionDeniedError


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mirror_canary(worktree: Path, mirror_root: Path, fixed_utc: str) -> dict[str, Any]:
    ledger = r9_ledger(mirror_root / "ledger.db", fixed_utc=fixed_utc)
    broker = CredentialBroker(mirror_root / "credentials.db", clock=FixedClock(fixed_utc))
    registry = CapabilityRegistry(ledger, clock=FixedClock(fixed_utc))
    broker.register(
        identity="mirror-owner",
        provider="mirror",
        secret_value="r9-mirror-files-token",
        capabilities=("capability.files", "capability.git", "capability.services"),
        scope={"targets": ["mirror/*"]},
        expires_at="2026-08-17T06:00:00Z",
        credential_id="cred-mirror",
    )
    broker.register(
        identity="mirror-owner",
        provider="mirror",
        secret_value="r9-mirror-fake-token",
        capabilities=("fake.quota",),
        scope={"targets": ["mirror/*"]},
        expires_at="2026-08-17T06:00:00Z",
        credential_id="cred-mirror-fake",
    )
    for capability_id in ("capability.files", "capability.git", "capability.services"):
        registry.discover(
            capability_id=capability_id,
            domain=capability_id.removeprefix("capability."),
            name=capability_id,
            system_id="verifier-mirror",
        )
        registry.probe(capability_id)
        registry.mark_ready(capability_id)

    credential = broker.resolve(
        "cred-mirror",
        capability="capability.files",
        requested_target="mirror/files/one.txt",
        at=fixed_utc,
    )
    files = FilesAdapter(mirror_root / "files")
    before = files._dir_hash(files.root)
    executed = files.execute(
        "write",
        {"idempotency_key": "mirror-write", "relative_path": "one.txt", "content": "mirror\n"},
        credential,
    )
    replay = files.execute(
        "write",
        {"idempotency_key": "mirror-write", "relative_path": "one.txt", "content": "mirror\n"},
        credential,
    )
    registry.activate(
        "capability.files",
        ActivationProof(
            before_state_hash=before,
            after_state_hash=executed.after.state_hash if executed.after else before,
            receipt_hash=executed.receipt.to_dict()["effect_hash"] if executed.receipt else "",
            reconciliation=dict(executed.reconciliation),
        ),
    )
    git_credential = broker.resolve(
        "cred-mirror",
        capability="capability.git",
        requested_target="mirror/git/repo/note.md",
        at=fixed_utc,
    )
    git = GitAdapter(mirror_root / "git")
    repo = git.canary_repo()
    baseline_tree = git._git(["rev-parse", "HEAD^{tree}"], repo).stdout.strip()
    commit = git.execute(
        "commit",
        {
            "idempotency_key": "mirror-commit",
            "relative_path": "note.md",
            "content": "# mirror\n",
            "message": "mirror commit",
        },
        git_credential,
    )
    revert = git.execute(
        "revert",
        {
            "idempotency_key": "mirror-revert",
            "commit_sha": commit.receipt.detail["commit_sha"] if commit.receipt else "",
            "expected_tree": baseline_tree,
        },
        git_credential,
    )
    registry.activate(
        "capability.git",
        ActivationProof(
            before_state_hash=commit.before.state_hash,
            after_state_hash=commit.after.state_hash if commit.after else commit.before.state_hash,
            receipt_hash=commit.receipt.to_dict()["effect_hash"] if commit.receipt else "",
            reconciliation=dict(commit.reconciliation),
        ),
    )
    services_credential = broker.resolve(
        "cred-mirror",
        capability="capability.services",
        requested_target="mirror/service",
        at=fixed_utc,
    )
    services = ServicesAdapter(mirror_root / "services")
    lifecycle = services.execute(
        "lifecycle",
        {"idempotency_key": "mirror-service", "port": 0},
        services_credential,
    )
    services.stop()
    registry.activate(
        "capability.services",
        ActivationProof(
            before_state_hash="stopped",
            after_state_hash="running",
            receipt_hash=lifecycle.receipt.to_dict()["effect_hash"] if lifecycle.receipt else "",
            reconciliation=dict(lifecycle.reconciliation),
        ),
    )
    fakes = FailClosedFakeAdapter(mirror_root / "fakes", "quota")
    before_fake = fakes._dir_hash(fakes.root)
    fake_credential = broker.resolve(
        "cred-mirror-fake",
        capability="fake.quota",
        requested_target="mirror/fake/target",
        at=fixed_utc,
    )
    deferred = fakes.execute(
        "act",
        {"idempotency_key": "mirror-fake", "mode": "quota"},
        fake_credential,
    )
    after_fake = fakes._dir_hash(fakes.root)
    financial_denied = False
    try:
        FinancialPolicy.assert_denied("payment")
    except FinancialActionDeniedError:
        financial_denied = True
    states = registry.snapshot()["states"]["capabilities"]
    return {
        "files": {
            "executed": executed.outcome == Outcome.EXECUTED,
            "replayed": replay.outcome == Outcome.REPLAYED,
            "before_after_differ": before
            != (executed.after.state_hash if executed.after else before),
            "active": states["capability.files"]["status"] == "ACTIVE",
        },
        "git": {
            "committed": commit.outcome == Outcome.EXECUTED,
            "revert_restored": revert.outcome == Outcome.EXECUTED
            and revert.after is not None
            and revert.after.detail.get("tree") == baseline_tree,
            "active": states["capability.git"]["status"] == "ACTIVE",
        },
        "services": {
            "started": lifecycle.outcome == Outcome.EXECUTED,
            "stopped": services._server is None,
            "active": states["capability.services"]["status"] == "ACTIVE",
        },
        "fail_closed": {
            "deferred": deferred.outcome == Outcome.DEFERRED,
            "no_partial_effect": before_fake == after_fake,
        },
        "financial_policy_denied": financial_denied,
        "ledger_chain_valid": ledger.verify_chain().valid,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--mirror-root", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--expected-work-package-sha", required=True)
    parser.add_argument("--verifier-id", required=True)
    args = parser.parse_args(argv)
    started = time.monotonic()

    work_package = args.worktree / "docs" / "remaster" / "work_packages" / "R9_WORK_PACKAGE.json"
    work_package_sha = _sha256_file(work_package)
    fixed_utc = "2026-08-16T06:00:00Z"
    mirror = _mirror_canary(args.worktree, args.mirror_root, fixed_utc)

    evidence_files = sorted(args.evidence_dir.rglob("*"))
    evidence_hashes = {
        path.name: _sha256_file(path)
        for path in evidence_files
        if path.is_file() and path.name not in {"R9_ARTIFACT_MANIFEST.json"}
    }
    evidence_bytes = sum(
        path.stat().st_size
        for path in evidence_files
        if path.is_file()
        and path.name not in {"R9_BUILDER_REPORT.json", "R9_ARTIFACT_MANIFEST.json"}
    )
    tests = subprocess.run(
        [args.python, "-m", "pytest", "tests/remaster/r9", "-q"],
        cwd=str(args.worktree),
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    summary = ""
    for line in reversed(tests.stdout.splitlines()):
        if "passed" in line or "failed" in line:
            summary = line.strip()
            break
    mirror_ok = bool(
        all(mirror["files"].values())
        and all(mirror["git"].values())
        and all(mirror["services"].values())
        and mirror["fail_closed"]["deferred"]
        and mirror["fail_closed"]["no_partial_effect"]
        and mirror["financial_policy_denied"]
        and mirror["ledger_chain_valid"]
    )
    passed = bool(
        work_package_sha == args.expected_work_package_sha.lower()
        and evidence_files
        and evidence_bytes < 8_388_608
        and tests.returncode == 0
        and mirror_ok
    )
    payload = {
        "schema": "jarvis-r9-fresh-verifier-reproduction-v1",
        "verifier_id": args.verifier_id,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "passed": passed,
        "work_package_sha256": work_package_sha,
        "expected_work_package_sha256": args.expected_work_package_sha.lower(),
        "evidence_file_count": len(evidence_hashes),
        "evidence_bytes": evidence_bytes,
        "evidence_under_budget": evidence_bytes < 8_388_608,
        "focused_tests": {
            "exit_code": tests.returncode,
            "summary": summary,
        },
        "mirror": mirror,
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    output = args.evidence_dir / "R9_VERIFIER_REPRODUCTION.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"verifier {args.verifier_id}: passed={passed} evidence_bytes={evidence_bytes}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
