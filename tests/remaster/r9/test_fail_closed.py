from __future__ import annotations

import pytest
from r9_helpers import EXPIRED_UTC, FIXED_UTC, LATER_UTC, make_broker, register_canary_credential

from jarvis_foundry.capabilities_live.adapters.base import Outcome
from jarvis_foundry.capabilities_live.adapters.fakes import FailClosedFakeAdapter
from jarvis_foundry.capabilities_live.broker import AuthorizationDeniedError


@pytest.mark.parametrize(
    ("mode", "expected_outcome"),
    [
        ("missing_credential", Outcome.REFUSED),
        ("expired", Outcome.REFUSED),
        ("revoked", Outcome.REFUSED),
        ("quota", Outcome.DEFERRED),
        ("rate", Outcome.DEFERRED),
        ("malformed_receipt", Outcome.NOT_ACCEPTED),
        ("unknown_target", Outcome.REFUSED),
        ("financial", Outcome.DENIED),
    ],
)
def test_fail_closed_modes_never_produce_effect(tmp_path, mode, expected_outcome) -> None:
    proving = tmp_path / "proving"
    broker = make_broker(tmp_path)
    register_canary_credential(broker, credential_id="cred-fake", expires_at=EXPIRED_UTC)
    adapter = FailClosedFakeAdapter(proving, mode)
    before = adapter._dir_hash(adapter.root)
    credential = None
    if mode != "missing_credential":
        register_canary_credential(
            broker,
            credential_id="cred-live",
            secret_value="r9-local-canary-credential-live",
            capabilities=(adapter.capability_id,),
            expires_at=EXPIRED_UTC if mode in {"expired", "revoked"} else LATER_UTC,
        )
        try:
            credential = broker.resolve(
                "cred-live",
                capability=adapter.capability_id,
                requested_target="canary/fake/target",
                at=FIXED_UTC,
            )
        except AuthorizationDeniedError:
            credential = None
    result = adapter.execute(
        "act",
        {"idempotency_key": "fake-1", "mode": mode, "request_type": "payment"},
        credential,
    )
    assert result.outcome == expected_outcome
    assert result.no_partial_effect is True
    assert adapter._dir_hash(adapter.root) == before
    if mode in {"quota", "rate"}:
        assert "retry_after_seconds" in result.reconciliation


def test_financial_mode_is_denied_with_no_capability(tmp_path) -> None:
    proving = tmp_path / "proving"
    broker = make_broker(tmp_path)
    adapter = FailClosedFakeAdapter(proving, "financial")
    register_canary_credential(
        broker,
        credential_id="cred-live",
        capabilities=(adapter.capability_id,),
    )
    credential = broker.resolve(
        "cred-live",
        capability=adapter.capability_id,
        requested_target="canary/fake/target",
        at=FIXED_UTC,
    )
    result = adapter.execute(
        "transfer",
        {"idempotency_key": "fin-1", "mode": "financial", "request_type": "payment"},
        credential,
    )
    assert result.outcome == Outcome.DENIED
    assert result.no_partial_effect is True


def test_broker_refuses_expired_before_adapter_can_act(tmp_path) -> None:
    broker = make_broker(tmp_path)
    register_canary_credential(broker, credential_id="cred-exp", expires_at=EXPIRED_UTC)
    with pytest.raises(AuthorizationDeniedError, match="expired"):
        broker.resolve(
            "cred-exp",
            capability="capability.files",
            requested_target="canary/fake/target",
            at=FIXED_UTC,
        )
