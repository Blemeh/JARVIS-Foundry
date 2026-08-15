from __future__ import annotations

import pytest

from jarvis_foundry.kernel.errors import EffectError
from jarvis_foundry.kernel.ports import SystemAdapterPort
from jarvis_foundry.kernel.records import EffectIntent
from jarvis_foundry.kernel.system_adapter import RejectingSystemAdapter


def test_system_adapter_port_surface() -> None:
    assert {"prepare", "execute", "inspect", "reconcile", "compensate"} <= set(
        SystemAdapterPort.__dict__
    )


def test_rejecting_adapter_fails_closed() -> None:
    adapter = RejectingSystemAdapter()
    intent = EffectIntent(
        intent_id="i-1",
        idempotency_key="effect:1",
        action_envelope_id="a-1",
        system="fs",
        operation="write",
        params={},
    )
    with pytest.raises(EffectError):
        adapter.prepare(intent)
    with pytest.raises(EffectError):
        adapter.execute(intent)
    with pytest.raises(EffectError):
        adapter.inspect("i-1")
    with pytest.raises(EffectError):
        adapter.reconcile("i-1")
    with pytest.raises(EffectError):
        adapter.compensate("i-1")
