from __future__ import annotations

import pytest

from jarvis_foundry.capabilities_live.ledger_events import r9_event_registry
from jarvis_foundry.capabilities_live.policy import FinancialPolicy
from jarvis_foundry.kernel.errors import FinancialActionDeniedError


def test_no_financial_event_kind_is_registered() -> None:
    registry = r9_event_registry()
    assert FinancialPolicy.financial_kinds(registry) == []


def test_no_financial_capability_exists() -> None:
    assert FinancialPolicy.has_capability() is False
    assert FinancialPolicy.capability_ids() == ()


@pytest.mark.parametrize("request_type", ["transfer", "payment", "charge", "payout", "invoice"])
def test_every_financial_request_is_denied(request_type) -> None:
    with pytest.raises(FinancialActionDeniedError, match="always denied"):
        FinancialPolicy.assert_denied(request_type)
