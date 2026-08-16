"""R9-G02: credential safety, fingerprints only, expiry/revocation/scoping."""

from __future__ import annotations

import sys
import time

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    TEMP_BASE,
    read_json,
    safe_clear,
    write_json,
)

from jarvis_foundry.capabilities_live.broker import AuthorizationDeniedError, CredentialBroker
from jarvis_foundry.kernel.clock import FixedClock


def main() -> int:
    started = time.monotonic()
    credentials = read_json(EVIDENCE_DIR / "R9_CREDENTIALS.json")
    store = TEMP_BASE / "stores" / "credentials.db"
    broker = CredentialBroker(store, clock=FixedClock(EVIDENCE_UTC))
    records = broker.records_public()
    secrets = broker.secrets()

    evidence_text = ""
    scanned_files = 0
    for path in sorted(EVIDENCE_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {
            "R9_PRIVACY.json",
            "R9_LIVE_GUARD_BEFORE.json",
            "R9_LIVE_GUARD_AFTER.json",
        }:
            continue
        scanned_files += 1
        evidence_text += path.read_text(encoding="utf-8", errors="replace")
    leaks = [secret for secret in secrets if secret and secret in evidence_text]
    fingerprints_only = bool(records) and all(
        item["secret_value"] == "[REDACTED]" for item in records
    )
    ledger_fingerprints = True
    for event in read_json(EVIDENCE_DIR / "R9_LIFECYCLE.json")["credential_states"].values():
        if "fingerprint" in event and len(str(event["fingerprint"])) != 64:
            ledger_fingerprints = False

    probe_root = TEMP_BASE / "gates" / "g02"
    safe_clear(probe_root)
    probe_root.mkdir(parents=True, exist_ok=True)
    probe_broker = CredentialBroker(probe_root / "credentials.db", clock=FixedClock(EVIDENCE_UTC))
    probe_broker.register(
        identity="gate-owner",
        provider="gate",
        secret_value="r9-local-canary-gate-scope-token",
        capabilities=("capability.files",),
        scope={"targets": ["canary/gate/only.txt"]},
        expires_at="2026-08-17T06:00:00Z",
        credential_id="cred-gate-scoped",
    )
    scope_ok = True
    try:
        probe_broker.resolve(
            "cred-gate-scoped",
            capability="capability.files",
            requested_target="canary/gate/only.txt",
            at=EVIDENCE_UTC,
        )
    except AuthorizationDeniedError:
        scope_ok = False
    scope_refused = False
    try:
        probe_broker.resolve(
            "cred-gate-scoped",
            capability="capability.files",
            requested_target="canary/other/only.txt",
            at=EVIDENCE_UTC,
        )
    except AuthorizationDeniedError:
        scope_refused = True
    probe_broker.register(
        identity="gate-owner",
        provider="gate",
        secret_value="r9-local-canary-gate-expired-token",
        capabilities=("capability.files",),
        scope={"targets": ["canary/gate/*"]},
        expires_at="2026-08-16T05:00:00Z",
        credential_id="cred-gate-expired",
    )
    expired_refused = False
    try:
        probe_broker.resolve(
            "cred-gate-expired",
            capability="capability.files",
            requested_target="canary/gate/x.txt",
            at=EVIDENCE_UTC,
        )
    except AuthorizationDeniedError:
        expired_refused = True
    probe_broker.register(
        identity="gate-owner",
        provider="gate",
        secret_value="r9-local-canary-gate-revoke-token",
        capabilities=("capability.files",),
        scope={"targets": ["canary/gate/*"]},
        expires_at="2026-08-17T06:00:00Z",
        credential_id="cred-gate-revoke",
    )
    probe_broker.resolve(
        "cred-gate-revoke",
        capability="capability.files",
        requested_target="canary/gate/x.txt",
        at=EVIDENCE_UTC,
    )
    probe_broker.revoke("cred-gate-revoke", "gate probe")
    revoked_refused = False
    try:
        probe_broker.resolve(
            "cred-gate-revoke",
            capability="capability.files",
            requested_target="canary/gate/x.txt",
            at=EVIDENCE_UTC,
        )
    except AuthorizationDeniedError:
        revoked_refused = True
    unknown_refused = False
    try:
        probe_broker.resolve_for_capability(
            "no-such-owner",
            capability="capability.files",
            requested_target="canary/gate/x.txt",
            at=EVIDENCE_UTC,
        )
    except AuthorizationDeniedError:
        unknown_refused = True

    fresh_probe_ok = bool(
        scope_ok and scope_refused and expired_refused and revoked_refused and unknown_refused
    )
    passed = bool(not leaks and fingerprints_only and ledger_fingerprints and fresh_probe_ok)
    payload = {
        "schema": "jarvis-r9-g02-credentials-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "store": {
            "path": str(store),
            "sha256": credentials["store_sha256"],
            "records_public": records,
        },
        "secret_scan_over_evidence": {
            "evidence_files_scanned": scanned_files,
            "secret_leaks": leaks,
            "no_leaks": not leaks,
        },
        "fingerprints_only": fingerprints_only,
        "ledger_fingerprints_are_64_hex": ledger_fingerprints,
        "fresh_probes": {
            "scope_allowed": scope_ok,
            "scope_substitution_refused": scope_refused,
            "expired_refused": expired_refused,
            "revoked_refused": revoked_refused,
            "unknown_account_refused": unknown_refused,
        },
    }
    write_json(EVIDENCE_DIR / "R9_G02_CREDENTIALS.json", payload)
    print(f"g02 credentials: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
