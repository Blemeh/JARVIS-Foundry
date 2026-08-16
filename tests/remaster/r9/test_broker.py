from __future__ import annotations

import pytest
from r9_helpers import (
    EXPIRED_UTC,
    FIXED_UTC,
    LATER_UTC,
    make_broker,
    make_ledger,
    register_canary_credential,
    resolve_ok,
)

from jarvis_foundry.capabilities_live.broker import AuthorizationDeniedError, CredentialBroker
from jarvis_foundry.kernel.errors import CapabilityError


def test_public_records_expose_only_fingerprints(tmp_path) -> None:
    ledger = make_ledger(tmp_path)
    broker = make_broker(tmp_path, ledger)
    record = register_canary_credential(broker)
    public = record.public()
    assert public["secret_value"] == "[REDACTED]"
    assert public["fingerprint"] == CredentialBroker.fingerprint("r9-local-canary-credential-a")
    assert all("r9-local-canary-credential-a" not in str(item) for item in broker.records_public())
    resolved = resolve_ok(broker)
    assert "r9-local-canary-credential-a" not in repr(resolved)
    assert resolved.fingerprint == public["fingerprint"]
    assert "credential.registered" in [e.event_type for e in ledger.read()]
    registered = next(e for e in ledger.read() if e.event_type == "credential.registered")
    assert "r9-local-canary-credential-a" not in str(registered.payload)


def test_scope_and_capability_bindings_are_enforced(tmp_path) -> None:
    broker = make_broker(tmp_path)
    register_canary_credential(
        broker,
        capabilities=("capability.files",),
        scope={"targets": ["canary/files"]},
    )
    resolve_ok(broker, target="canary/files/one.txt")
    with pytest.raises(AuthorizationDeniedError, match="target outside granted scope"):
        resolve_ok(broker, target="canary/other/one.txt")
    with pytest.raises(AuthorizationDeniedError, match="not bound"):
        resolve_ok(broker, capability="capability.git", target="canary/files/one.txt")


def test_expired_and_revoked_credentials_refuse(tmp_path) -> None:
    broker = make_broker(tmp_path)
    register_canary_credential(broker, expires_at=EXPIRED_UTC)
    with pytest.raises(AuthorizationDeniedError, match="expired"):
        resolve_ok(broker)
    with pytest.raises(AuthorizationDeniedError, match="expired"):
        resolve_ok(broker, at=EXPIRED_UTC)

    ledger = make_ledger(tmp_path)
    live_broker = make_broker(tmp_path / "live", ledger)
    register_canary_credential(live_broker, credential_id="cred-live")
    live_broker.resolve(
        "cred-live",
        capability="capability.files",
        requested_target="canary/files/one.txt",
        at=FIXED_UTC,
    )
    live_broker.revoke("cred-live", "owner revocation")
    with pytest.raises(AuthorizationDeniedError, match="revoked"):
        live_broker.resolve(
            "cred-live",
            capability="capability.files",
            requested_target="canary/files/one.txt",
            at=LATER_UTC,
        )
    assert "credential.revoked" in [e.event_type for e in ledger.read()]


def test_deterministic_authorization_chooses_first_binding(tmp_path) -> None:
    broker = make_broker(tmp_path)
    register_canary_credential(
        broker,
        credential_id="cred-a",
        secret_value="r9-local-canary-credential-a",
    )
    register_canary_credential(
        broker,
        credential_id="cred-b",
        secret_value="r9-local-canary-credential-b",
    )
    first = broker.resolve_for_capability(
        "canary-owner",
        capability="capability.files",
        requested_target="canary/files/one.txt",
        at=FIXED_UTC,
    )
    second = broker.resolve_for_capability(
        "canary-owner",
        capability="capability.files",
        requested_target="canary/files/one.txt",
        at=FIXED_UTC,
    )
    assert first.credential_id == second.credential_id == "cred-a"
    assert first.fingerprint == second.fingerprint


def test_unknown_identity_and_credential_refuse(tmp_path) -> None:
    broker = make_broker(tmp_path)
    with pytest.raises(CapabilityError, match="unknown credential"):
        broker.resolve(
            "cred-missing",
            capability="capability.files",
            requested_target="canary/files/one.txt",
            at=FIXED_UTC,
        )
    with pytest.raises(AuthorizationDeniedError, match="no authorized credential"):
        broker.resolve_for_capability(
            "unknown-owner",
            capability="capability.files",
            requested_target="canary/files/one.txt",
            at=FIXED_UTC,
        )


def test_canonical_identity_and_target_normalization(tmp_path) -> None:
    broker = make_broker(tmp_path)
    register_canary_credential(broker, identity="  Canary-Owner  ")
    assert broker.canonical_identity("Canary-Owner") == "canary-owner"
    assert broker.canonical_target("canary\\files\\ONE.txt") == "canary/files/one.txt"
