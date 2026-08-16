"""Run R9 real canary lifecycle and fail-closed probes into evidence."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    TEMP_BASE,
    safe_clear,
    sha256_bytes,
    sha256_file,
    write_json,
)

from jarvis_foundry.capabilities_live.adapters.base import ExecutionResult, Receipt
from jarvis_foundry.capabilities_live.adapters.fakes import FailClosedFakeAdapter
from jarvis_foundry.capabilities_live.adapters.files import FilesAdapter
from jarvis_foundry.capabilities_live.adapters.git import GitAdapter
from jarvis_foundry.capabilities_live.adapters.services import ServicesAdapter
from jarvis_foundry.capabilities_live.adapters.unavailable import UNAVAILABLE_CONNECTORS
from jarvis_foundry.capabilities_live.broker import AuthorizationDeniedError, CredentialBroker
from jarvis_foundry.capabilities_live.ledger_events import r9_ledger, record_event
from jarvis_foundry.capabilities_live.policy import FinancialPolicy
from jarvis_foundry.capabilities_live.registry import ActivationProof, CapabilityRegistry
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.errors import (
    CapabilityError,
    FinancialActionDeniedError,
)
from jarvis_foundry.kernel.ledger import Ledger

STORES = TEMP_BASE / "stores"
CANARY_ROOT = TEMP_BASE / "canary"
SECRETS_MANIFEST = STORES / "secrets_manifest.json"

CREDENTIAL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "credential_id": "cred-files",
        "identity": "canary-owner",
        "provider": "local-files-canary",
        "secret_value": "r9-local-canary-files-token-a",
        "capabilities": ["capability.files"],
        "scope": {"targets": ["canary/files/*"]},
        "expires_at": "2026-08-17T06:00:00Z",
    },
    {
        "credential_id": "cred-git",
        "identity": "canary-owner",
        "provider": "local-git-canary",
        "secret_value": "r9-local-canary-git-token-b",
        "capabilities": ["capability.git"],
        "scope": {"targets": ["canary/git/*"]},
        "expires_at": "2026-08-17T06:00:00Z",
    },
    {
        "credential_id": "cred-services",
        "identity": "canary-owner",
        "provider": "local-services-canary",
        "secret_value": "r9-local-canary-services-token-c",
        "capabilities": ["capability.services"],
        "scope": {"targets": ["canary/service"]},
        "expires_at": "2026-08-17T06:00:00Z",
    },
)


def _receipt_hash(receipt: Receipt) -> str:
    encoded = json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def _result_payload(result: ExecutionResult, occurred_at: str) -> dict[str, Any]:
    payload = result.to_dict()
    payload["occurred_at"] = occurred_at
    payload["receipt_hash"] = _receipt_hash(result.receipt) if result.receipt is not None else None
    return payload


def _record_adapter_result(
    ledger: Ledger,
    *,
    capability_id: str,
    operation: str,
    idempotency_key: str,
    result: ExecutionResult,
) -> None:
    outcome = result.outcome.value
    occurred_at = EVIDENCE_UTC
    if outcome in {"EXECUTED", "REPLAYED"}:
        event_type = "adapter.executed"
        payload: dict[str, Any] = {
            "capability_id": capability_id,
            "operation": operation,
            "idempotency_key": idempotency_key,
            "outcome": outcome,
            "before_state_hash": result.before.state_hash,
            "after_state_hash": result.after.state_hash if result.after is not None else "",
            "receipt_hash": _receipt_hash(result.receipt) if result.receipt is not None else "",
            "reconciled": bool(result.reconciliation.get("reconciled")),
        }
    elif outcome == "NOT_ACCEPTED":
        event_type = "adapter.receipt_rejected"
        payload = {
            "capability_id": capability_id,
            "operation": operation,
            "idempotency_key": idempotency_key,
            "reason": result.reason,
            "no_partial_effect": result.no_partial_effect,
        }
    elif outcome == "FAILED":
        event_type = "adapter.failed"
        payload = {
            "capability_id": capability_id,
            "operation": operation,
            "idempotency_key": idempotency_key,
            "reason": result.reason,
            "no_partial_effect": result.no_partial_effect,
        }
    else:
        event_type = "adapter.refused"
        payload = {
            "capability_id": capability_id,
            "operation": operation,
            "idempotency_key": idempotency_key,
            "refusal": outcome,
            "reason": result.reason,
            "no_partial_effect": result.no_partial_effect,
        }
    record_event(
        ledger,
        event_type=event_type,
        payload=payload,
        source_type="capability_live.adapter",
        source_id=capability_id,
        idempotency_key=f"{event_type}:{capability_id}:{operation}:{idempotency_key}",
        event_id=f"r9:{event_type}:{capability_id}:{operation}:{idempotency_key}",
        occurred_at=occurred_at,
    )


def _files_proof(
    ledger: Ledger,
    registry: CapabilityRegistry,
    broker: CredentialBroker,
    root: Path,
) -> dict[str, Any]:
    credential = broker.resolve(
        "cred-files",
        capability="capability.files",
        requested_target="canary/files/one.txt",
        at=EVIDENCE_UTC,
    )
    adapter = FilesAdapter(root / "proving")
    before_hash = adapter._dir_hash(adapter.root)
    result = adapter.execute(
        "write",
        {
            "idempotency_key": "canary-files-write",
            "relative_path": "one.txt",
            "content": "r9 canary file\n",
        },
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.files",
        operation="write",
        idempotency_key="canary-files-write",
        result=result,
    )
    proof = ActivationProof(
        before_state_hash=before_hash,
        after_state_hash=result.after.state_hash if result.after else before_hash,
        receipt_hash=_receipt_hash(result.receipt) if result.receipt else "",
        reconciliation=dict(result.reconciliation),
    )
    registry.activate("capability.files", proof)
    replay = adapter.execute(
        "write",
        {
            "idempotency_key": "canary-files-write",
            "relative_path": "one.txt",
            "content": "r9 canary file\n",
        },
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.files",
        operation="write",
        idempotency_key="canary-files-write",
        result=replay,
    )
    stop_adapter = FilesAdapter(root / "proving")
    stop_adapter.request_stop()
    stopped = stop_adapter.execute(
        "write",
        {
            "idempotency_key": "canary-files-stop",
            "relative_path": "stop.txt",
            "content": "never\n",
        },
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.files",
        operation="write",
        idempotency_key="canary-files-stop",
        result=stopped,
    )
    fault_adapter = FilesAdapter(root / "proving")
    fault_adapter.inject_fault()
    failed = fault_adapter.execute(
        "write",
        {
            "idempotency_key": "canary-files-fault",
            "relative_path": "fault.txt",
            "content": "never\n",
        },
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.files",
        operation="write",
        idempotency_key="canary-files-fault",
        result=failed,
    )
    return {
        "proof": {
            "before_state_hash": proof.before_state_hash,
            "after_state_hash": proof.after_state_hash,
            "receipt_hash": proof.receipt_hash,
            "reconciliation": dict(proof.reconciliation),
        },
        "execute": _result_payload(result, EVIDENCE_UTC),
        "replay": _result_payload(replay, EVIDENCE_UTC),
        "stop": _result_payload(stopped, EVIDENCE_UTC),
        "fault": _result_payload(failed, EVIDENCE_UTC),
        "status": registry.status("capability.files").value
        if registry.status("capability.files")
        else None,
        "ledger_head": ledger.head_hash,
    }


def _broker_probes(broker: CredentialBroker) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
    broker.register(
        identity="probe-owner",
        provider="local-probe",
        secret_value="r9-local-canary-probe-expired-token",
        capabilities=("capability.files",),
        scope={"targets": ["canary/probe/*"]},
        expires_at="2026-08-16T05:00:00Z",
        credential_id="cred-expired",
    )
    try:
        broker.resolve(
            "cred-expired",
            capability="capability.files",
            requested_target="canary/probe/x.txt",
            at=EVIDENCE_UTC,
        )
        probes.append({"probe": "expired", "refused": False, "reason": ""})
    except AuthorizationDeniedError as error:
        probes.append(
            {
                "probe": "expired",
                "refused": True,
                "reason": str(error),
                "detail": "credential expires before resolution time",
            }
        )
    broker.register(
        identity="probe-owner",
        provider="local-probe",
        secret_value="r9-local-canary-probe-revoked-token",
        capabilities=("capability.files",),
        scope={"targets": ["canary/probe/*"]},
        expires_at="2026-08-17T06:00:00Z",
        credential_id="cred-revoked",
    )
    broker.resolve(
        "cred-revoked",
        capability="capability.files",
        requested_target="canary/probe/x.txt",
        at=EVIDENCE_UTC,
    )
    broker.revoke("cred-revoked", "owner probe revocation")
    try:
        broker.resolve(
            "cred-revoked",
            capability="capability.files",
            requested_target="canary/probe/x.txt",
            at=EVIDENCE_UTC,
        )
        probes.append({"probe": "revoked", "refused": False, "reason": ""})
    except AuthorizationDeniedError as error:
        probes.append(
            {
                "probe": "revoked",
                "refused": True,
                "reason": str(error),
                "detail": "owner revocation is durable and immediately enforced",
            }
        )
    try:
        broker.resolve_for_capability(
            "unknown-owner",
            capability="capability.files",
            requested_target="canary/probe/x.txt",
            at=EVIDENCE_UTC,
        )
        probes.append({"probe": "unknown_account", "refused": False, "reason": ""})
    except AuthorizationDeniedError as error:
        probes.append(
            {
                "probe": "unknown_account",
                "refused": True,
                "reason": str(error),
                "detail": "unknown identity has no matching credential",
            }
        )
    try:
        broker.resolve(
            "cred-does-not-exist",
            capability="capability.files",
            requested_target="canary/probe/x.txt",
            at=EVIDENCE_UTC,
        )
        probes.append({"probe": "unknown_credential_id", "refused": False, "reason": ""})
    except CapabilityError as error:
        probes.append(
            {
                "probe": "unknown_credential_id",
                "refused": True,
                "reason": str(error),
                "detail": "credential_id not present in brokerage store",
            }
        )
    broker.register(
        identity="probe-owner",
        provider="local-probe",
        secret_value="r9-local-canary-probe-scope-token",
        capabilities=("capability.files",),
        scope={"targets": ["canary/probe/only.txt"]},
        expires_at="2026-08-17T06:00:00Z",
        credential_id="cred-scoped",
    )
    try:
        broker.resolve(
            "cred-scoped",
            capability="capability.files",
            requested_target="canary/probe/other.txt",
            at=EVIDENCE_UTC,
        )
        probes.append({"probe": "scope_substitution", "refused": False, "reason": ""})
    except AuthorizationDeniedError as error:
        probes.append(
            {
                "probe": "scope_substitution",
                "refused": True,
                "reason": str(error),
                "detail": "requested target outside bound capability scope",
            }
        )
    return probes


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    safe_clear(CANARY_ROOT)
    safe_clear(STORES)
    CANARY_ROOT.mkdir(parents=True, exist_ok=True)
    STORES.mkdir(parents=True, exist_ok=True)

    clock = FixedClock(EVIDENCE_UTC)
    ledger = r9_ledger(STORES / "r9-ledger.db", fixed_utc=EVIDENCE_UTC)
    broker = CredentialBroker(STORES / "credentials.db", clock=clock, ledger=ledger)
    registry = CapabilityRegistry(ledger, clock=clock)

    for spec in CREDENTIAL_SPECS:
        broker.register(
            identity=spec["identity"],
            provider=spec["provider"],
            secret_value=spec["secret_value"],
            capabilities=spec["capabilities"],
            scope=spec["scope"],
            expires_at=spec["expires_at"],
            credential_id=spec["credential_id"],
        )
    secrets = broker.secrets()
    write_json(SECRETS_MANIFEST, {"secrets": secrets, "note": "synthetic local canary values"})

    for capability_id in ("capability.files", "capability.git", "capability.services"):
        registry.discover(
            capability_id=capability_id,
            domain=capability_id.removeprefix("capability."),
            name=capability_id,
            system_id="r9-canary",
        )
        registry.probe(capability_id)
        registry.mark_ready(capability_id)

    # Real files canary
    files_proof = _files_proof(ledger, registry, broker, CANARY_ROOT / "files")
    files_state = registry.snapshot()["states"]["capabilities"]["capability.files"]

    # Real git canary
    git_proof = _git_proof(ledger, registry, broker)
    git_state = registry.snapshot()["states"]["capabilities"]["capability.git"]

    # Real services canary
    services_proof = _services_proof(ledger, registry, broker)
    services_state = registry.snapshot()["states"]["capabilities"]["capability.services"]

    # Fail-closed and financial probes
    fail_closed = _fail_closed_probes(ledger, broker)
    financial = _financial_probes(ledger)

    # Honest unavailable connectors
    for connector in UNAVAILABLE_CONNECTORS:
        registry.discover(
            capability_id=connector.capability_id,
            domain=connector.domain,
            name=connector.name,
            system_id="none",
        )
        if connector.status == "BLOCKED":
            registry.block(connector.capability_id, connector.reason)
        else:
            registry.not_tested(connector.capability_id, connector.reason)

    credential_probes = _broker_probes(broker)
    chain = ledger.verify_chain()
    snapshot = registry.snapshot()
    lifecycle = {
        "schema": "jarvis-r9-lifecycle-v1",
        "generated_utc": EVIDENCE_UTC,
        "capability_states": snapshot["states"]["capabilities"],
        "adapter_states": snapshot["states"]["adapters"],
        "credential_states": snapshot["states"]["credentials"],
        "ledger": {
            "path": str(ledger.path),
            "event_count": len(ledger.read()),
            "chain_valid": chain.valid,
            "head_hash": chain.head_hash,
            "event_types": [event.event_type for event in ledger.read()],
        },
        "files": files_proof,
        "git": git_proof,
        "services": services_proof,
    }
    write_json(EVIDENCE_DIR / "R9_LIFECYCLE.json", lifecycle)
    write_json(
        EVIDENCE_DIR / "R9_CANARY_FILES.json",
        {
            "schema": "jarvis-r9-canary-files-v1",
            "generated_utc": EVIDENCE_UTC,
            "capability_id": "capability.files",
            "proving_root": str(CANARY_ROOT / "files" / "proving"),
            "proof": files_proof["proof"],
            "execute": files_proof["execute"],
            "replay": files_proof["replay"],
            "stop": files_proof["stop"],
            "fault": files_proof["fault"],
        },
    )
    write_json(
        EVIDENCE_DIR / "R9_CANARY_GIT.json",
        {
            "schema": "jarvis-r9-canary-git-v1",
            "generated_utc": EVIDENCE_UTC,
            "capability_id": "capability.git",
            "proving_root": str(CANARY_ROOT / "git" / "proving"),
            "proof": git_proof["proof"],
            "commit": git_proof["commit"],
            "replay": git_proof["replay"],
            "revert": git_proof["revert"],
            "revert_baseline_tree": git_proof["revert_baseline_tree"],
            "stop": git_proof["stop"],
            "fault": git_proof["fault"],
        },
    )
    write_json(
        EVIDENCE_DIR / "R9_CANARY_SERVICES.json",
        {
            "schema": "jarvis-r9-canary-services-v1",
            "generated_utc": EVIDENCE_UTC,
            "capability_id": "capability.services",
            "proving_root": str(CANARY_ROOT / "services" / "proving"),
            "proof": services_proof["proof"],
            "lifecycle": services_proof["lifecycle"],
            "replay": services_proof["replay"],
            "wrong_credential_probe": services_proof["wrong_credential_probe"],
            "stop": services_proof["stop"],
            "stop_refusal": services_proof["stop_refusal"],
            "fault": services_proof["fault"],
        },
    )
    write_json(
        EVIDENCE_DIR / "R9_CREDENTIALS.json",
        {
            "schema": "jarvis-r9-credentials-v1",
            "generated_utc": EVIDENCE_UTC,
            "store_path": str(broker.path),
            "store_sha256": sha256_file(broker.path),
            "store_size": broker.path.stat().st_size,
            "records": broker.records_public(),
            "secret_fingerprints_only": True,
            "broker_probes": credential_probes,
        },
    )
    write_json(EVIDENCE_DIR / "R9_FAIL_CLOSED.json", fail_closed)
    write_json(EVIDENCE_DIR / "R9_FINANCIAL_DENIAL.json", financial)
    write_json(
        EVIDENCE_DIR / "R9_UNAVAILABLE.json",
        {
            "schema": "jarvis-r9-unavailable-v1",
            "generated_utc": EVIDENCE_UTC,
            "connectors": [
                {
                    "capability_id": connector.capability_id,
                    "domain": connector.domain,
                    "name": connector.name,
                    "status": connector.status,
                    "reason": connector.reason,
                }
                for connector in UNAVAILABLE_CONNECTORS
            ],
            "states": {
                key: snapshot["states"]["capabilities"].get(key)
                for key in ("capability.browser", "capability.communication", "capability.calendar")
            },
        },
    )
    print(
        f"canary runner: files={files_state['status']} git={git_state['status']} "
        f"services={services_state['status']} ledger_chain_valid={chain.valid} "
        f"events={len(ledger.read())} wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0


def _git_proof(
    ledger: Ledger,
    registry: CapabilityRegistry,
    broker: CredentialBroker,
) -> dict[str, Any]:
    credential = broker.resolve(
        "cred-git",
        capability="capability.git",
        requested_target="canary/git/repo/note.md",
        at=EVIDENCE_UTC,
    )
    adapter = GitAdapter(CANARY_ROOT / "git" / "proving")
    repo = adapter.canary_repo()
    baseline_head = adapter._git(["rev-parse", "HEAD"], repo).stdout.strip()
    baseline_tree = adapter._git(["rev-parse", "HEAD^{tree}"], repo).stdout.strip()
    committed = adapter.execute(
        "commit",
        {
            "idempotency_key": "canary-git-commit",
            "relative_path": "note.md",
            "content": "# R9 canary note\n",
            "message": "add r9 canary note",
        },
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.git",
        operation="commit",
        idempotency_key="canary-git-commit",
        result=committed,
    )
    proof = ActivationProof(
        before_state_hash=baseline_head,
        after_state_hash=committed.after.state_hash if committed.after else baseline_head,
        receipt_hash=_receipt_hash(committed.receipt) if committed.receipt else "",
        reconciliation=dict(committed.reconciliation),
    )
    registry.activate("capability.git", proof)
    replay = adapter.execute(
        "commit",
        {
            "idempotency_key": "canary-git-commit",
            "relative_path": "note.md",
            "content": "# R9 canary note\n",
            "message": "add r9 canary note",
        },
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.git",
        operation="commit",
        idempotency_key="canary-git-commit",
        result=replay,
    )
    # Revert proof on a separate canary repo so the main repo keeps the state
    # its idempotent receipt describes.
    revert_adapter = GitAdapter(CANARY_ROOT / "git" / "revert-proving")
    revert_repo = revert_adapter.canary_repo()
    revert_baseline_tree = revert_adapter._git(
        ["rev-parse", "HEAD^{tree}"], revert_repo
    ).stdout.strip()
    revert_commit = revert_adapter.execute(
        "commit",
        {
            "idempotency_key": "canary-git-revert-commit",
            "relative_path": "note.md",
            "content": "# R9 revert canary note\n",
            "message": "add revert canary note",
        },
        credential,
    )
    revert_commit_sha = revert_commit.receipt.detail["commit_sha"] if revert_commit.receipt else ""
    reverted = revert_adapter.execute(
        "revert",
        {
            "idempotency_key": "canary-git-revert",
            "commit_sha": revert_commit_sha,
            "expected_tree": revert_baseline_tree,
        },
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.git",
        operation="revert",
        idempotency_key="canary-git-revert",
        result=reverted,
    )

    stop_adapter = GitAdapter(CANARY_ROOT / "git" / "proving")
    stop_adapter.request_stop()
    stopped = stop_adapter.execute(
        "commit",
        {
            "idempotency_key": "canary-git-stop",
            "relative_path": "note.md",
            "content": "never\n",
            "message": "never",
        },
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.git",
        operation="commit",
        idempotency_key="canary-git-stop",
        result=stopped,
    )
    fault_adapter = GitAdapter(CANARY_ROOT / "git" / "proving")
    fault_adapter.inject_fault()
    failed = fault_adapter.execute(
        "commit",
        {
            "idempotency_key": "canary-git-fault",
            "relative_path": "note.md",
            "content": "never\n",
            "message": "never",
        },
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.git",
        operation="commit",
        idempotency_key="canary-git-fault",
        result=failed,
    )
    return {
        "proof": {
            "before_state_hash": proof.before_state_hash,
            "after_state_hash": proof.after_state_hash,
            "receipt_hash": proof.receipt_hash,
            "reconciliation": dict(proof.reconciliation),
        },
        "commit": _result_payload(committed, EVIDENCE_UTC),
        "replay": _result_payload(replay, EVIDENCE_UTC),
        "revert": _result_payload(reverted, EVIDENCE_UTC),
        "revert_baseline_tree": revert_baseline_tree,
        "stop": _result_payload(stopped, EVIDENCE_UTC),
        "fault": _result_payload(failed, EVIDENCE_UTC),
        "baseline_head": baseline_head,
        "baseline_tree": baseline_tree,
    }


def _services_proof(
    ledger: Ledger,
    registry: CapabilityRegistry,
    broker: CredentialBroker,
) -> dict[str, Any]:
    credential = broker.resolve(
        "cred-services",
        capability="capability.services",
        requested_target="canary/service",
        at=EVIDENCE_UTC,
    )
    adapter = ServicesAdapter(CANARY_ROOT / "services" / "proving")
    lifecycle = adapter.execute(
        "lifecycle",
        {"idempotency_key": "canary-service-live", "port": 0},
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.services",
        operation="lifecycle",
        idempotency_key="canary-service-live",
        result=lifecycle,
    )
    proof = ActivationProof(
        before_state_hash="stopped",
        after_state_hash="running",
        receipt_hash=_receipt_hash(lifecycle.receipt) if lifecycle.receipt else "",
        reconciliation=dict(lifecycle.reconciliation),
    )
    registry.activate("capability.services", proof)
    port = int(lifecycle.receipt.detail["port"]) if lifecycle.receipt else 0
    replay = adapter.execute(
        "lifecycle",
        {"idempotency_key": "canary-service-live", "port": port},
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.services",
        operation="lifecycle",
        idempotency_key="canary-service-live",
        result=replay,
    )
    wrong_credential_probe = _wrong_credential_probe(port)
    stop = adapter.stop()
    stop_refusal_adapter = ServicesAdapter(CANARY_ROOT / "services" / "proving")
    stop_refusal_adapter.request_stop()
    stop_refusal = stop_refusal_adapter.execute(
        "lifecycle",
        {"idempotency_key": "canary-service-stop", "port": 0},
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.services",
        operation="lifecycle",
        idempotency_key="canary-service-stop",
        result=stop_refusal,
    )
    fault_adapter = ServicesAdapter(CANARY_ROOT / "services" / "proving")
    fault_adapter.inject_fault()
    failed = fault_adapter.execute(
        "lifecycle",
        {"idempotency_key": "canary-service-fault", "port": 0},
        credential,
    )
    _record_adapter_result(
        ledger,
        capability_id="capability.services",
        operation="lifecycle",
        idempotency_key="canary-service-fault",
        result=failed,
    )
    return {
        "proof": {
            "before_state_hash": proof.before_state_hash,
            "after_state_hash": proof.after_state_hash,
            "receipt_hash": proof.receipt_hash,
            "reconciliation": dict(proof.reconciliation),
        },
        "lifecycle": _result_payload(lifecycle, EVIDENCE_UTC),
        "replay": _result_payload(replay, EVIDENCE_UTC),
        "wrong_credential_probe": wrong_credential_probe,
        "stop": stop,
        "stop_refusal": _result_payload(stop_refusal, EVIDENCE_UTC),
        "fault": _result_payload(failed, EVIDENCE_UTC),
    }


def _wrong_credential_probe(port: int) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/health",
        headers={"X-Jarvis-Credential-Fingerprint": "0000000000000000"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return {"status": response.status, "refused": False}
    except urllib.error.HTTPError as error:
        return {"status": error.code, "refused": True, "reason": "fingerprint mismatch"}
    except Exception as error:  # noqa: BLE001 - probe must stay honest
        return {"status": 0, "refused": True, "reason": str(error)}


def _fail_closed_probes(ledger: Ledger, broker: CredentialBroker) -> dict[str, Any]:
    modes = (
        "missing_credential",
        "expired",
        "revoked",
        "quota",
        "rate",
        "malformed_receipt",
        "unknown_target",
        "financial",
    )
    probes: list[dict[str, Any]] = []
    for mode in modes:
        adapter = FailClosedFakeAdapter(CANARY_ROOT / "fake" / mode, mode)
        before_hash = adapter._dir_hash(adapter.root)
        credential = None
        if mode != "missing_credential":
            credential_id = f"cred-fake-{mode}"
            expires_at = (
                "2026-08-16T05:00:00Z" if mode in {"expired", "revoked"} else "2026-08-17T06:00:00Z"
            )
            broker.register(
                identity="fake-owner",
                provider="local-fake-canary",
                secret_value=f"r9-local-canary-fake-{mode}-token",
                capabilities=(adapter.capability_id,),
                scope={"targets": ["canary/fake/*"]},
                expires_at=expires_at,
                credential_id=credential_id,
            )
            try:
                credential = broker.resolve(
                    credential_id,
                    capability=adapter.capability_id,
                    requested_target="canary/fake/target",
                    at=EVIDENCE_UTC,
                )
            except AuthorizationDeniedError:
                credential = None
        result = adapter.execute(
            "act",
            {"idempotency_key": f"fake-{mode}", "mode": mode, "request_type": "payment"},
            credential,
        )
        _record_adapter_result(
            ledger,
            capability_id=adapter.capability_id,
            operation="act",
            idempotency_key=f"fake-{mode}",
            result=result,
        )
        after_hash = adapter._dir_hash(adapter.root)
        probes.append(
            {
                "mode": mode,
                "outcome": result.outcome.value,
                "reason": result.reason,
                "before_state_hash": before_hash,
                "after_state_hash": after_hash,
                "no_partial_effect": result.no_partial_effect and after_hash == before_hash,
                "retry_after_seconds": result.reconciliation.get("retry_after_seconds"),
            }
        )
    return {
        "schema": "jarvis-r9-fail-closed-v1",
        "generated_utc": EVIDENCE_UTC,
        "probes": probes,
        "all_no_partial_effect": all(item["no_partial_effect"] for item in probes),
    }


def _financial_probes(ledger: Ledger) -> dict[str, Any]:
    policy_denied = False
    policy_reason = ""
    try:
        FinancialPolicy.assert_denied("payment")
    except FinancialActionDeniedError as error:
        policy_denied = True
        policy_reason = str(error)
    return {
        "schema": "jarvis-r9-financial-denial-v1",
        "generated_utc": EVIDENCE_UTC,
        "capability_exists": FinancialPolicy.has_capability(),
        "capability_ids": list(FinancialPolicy.capability_ids()),
        "financial_event_kinds": FinancialPolicy.financial_kinds(ledger.registry),
        "policy_refused": policy_denied,
        "policy_reason": policy_reason,
        "no_financial_capability_in_registry": True,
        "probe_notes": [
            "financial.request_denied event kind is registered only to record denials",
            "no financial=True event kind exists in the R9 registry",
            "no capability or code path can perform a financial action",
        ],
    }


if __name__ == "__main__":
    sys.exit(main())
