from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.kernel.capabilities import CapabilityBroker
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.errors import CapabilityError
from jarvis_foundry.kernel.ids import DeterministicIdGenerator


def broker(tmp_path: Path) -> CapabilityBroker:
    return CapabilityBroker(
        tmp_path / "capabilities.db",
        clock=FixedClock("2026-08-15T00:00:00Z"),
        ids=DeterministicIdGenerator(seed=1),
    )


def test_issue_verify_expiry_and_scope(tmp_path: Path) -> None:
    service = broker(tmp_path)
    grant = service.issue(
        grantee="worker-1",
        capability="file.read",
        target="C:/Work/App",
        scope={"read": True},
        ttl_seconds=60,
    )
    assert grant.status == "ACTIVE"
    assert grant.target_canonical == service.canonical_target("C:/Work/App")
    verified = service.verify(
        grant.grant_id,
        requested_target="c:/work/app",
        at="2026-08-15T00:00:30Z",
    )
    assert verified.status == "ACTIVE"
    with pytest.raises(CapabilityError):
        service.verify(
            grant.grant_id,
            requested_target="C:/Elsewhere",
            at="2026-08-15T00:00:30Z",
        )


def test_expired_grant_is_refused(tmp_path: Path) -> None:
    service = broker(tmp_path)
    grant = service.issue(
        grantee="worker-1",
        capability="file.read",
        target="C:/Work/App",
        scope={},
        ttl_seconds=10,
    )
    with pytest.raises(CapabilityError):
        service.verify(grant.grant_id, requested_target="C:/Work/App", at="2026-08-15T00:00:20Z")


def test_grant_exactly_at_expiry_is_refused(tmp_path: Path) -> None:
    service = broker(tmp_path)
    grant = service.issue(
        grantee="worker-1",
        capability="file.read",
        target="C:/Work/App",
        scope={},
        ttl_seconds=10,
    )
    with pytest.raises(CapabilityError):
        service.verify(grant.grant_id, requested_target="C:/Work/App", at="2026-08-15T00:00:10Z")


def test_revocation_is_durable_and_deterministic(tmp_path: Path) -> None:
    service = broker(tmp_path)
    grant = service.issue(
        grantee="worker-1",
        capability="file.write",
        target="C:/Work/App",
        scope={},
        ttl_seconds=3600,
    )
    revoked = service.revoke(grant.grant_id, reason="owner veto")
    assert revoked.status == "REVOKED"
    with pytest.raises(CapabilityError):
        service.verify(grant.grant_id, requested_target="C:/Work/App", at="2026-08-15T00:00:30Z")
    restarted = broker(tmp_path)
    with pytest.raises(CapabilityError):
        restarted.verify(grant.grant_id, requested_target="C:/Work/App", at="2026-08-15T00:00:40Z")


def test_target_substitution_is_refused_and_canonicalization_is_stable() -> None:
    service = broker(Path("."))
    assert service.canonical_target("C:/Work/App") == service.canonical_target(" c:/work/app ")
    assert service.canonical_target("A//B") == "a/b"
