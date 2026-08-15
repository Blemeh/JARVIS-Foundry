from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.effects import EffectEngine, PreparedEffect, RecordingSystemAdapter
from jarvis_foundry.kernel.errors import EffectError
from jarvis_foundry.kernel.events import EventKindRegistry
from jarvis_foundry.kernel.ids import DeterministicIdGenerator
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.records import EffectIntent


def effect_registry() -> EventKindRegistry:
    return (
        EventKindRegistry()
        .register("effect.intent", required_keys=("intent_id", "idempotency_key"))
        .register("effect.receipt", required_keys=("receipt_id", "intent_id", "status"))
    )


class CrashOnExecuteAdapter(RecordingSystemAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.prepared: list[str] = []
        self.fail_operations = {"crash"}

    def prepare(self, intent: EffectIntent) -> PreparedEffect:
        self.prepared.append(intent.intent_id)
        return PreparedEffect(
            effect_id=f"prepared-{intent.intent_id}",
            intent_id=intent.intent_id,
            system=intent.system,
            operation=intent.operation,
            params=intent.params,
            prepared_at="2026-08-15T00:00:00Z",
        )

    def execute(self, prepared: PreparedEffect) -> dict[str, Any]:
        if prepared.operation in self.fail_operations:
            raise EffectError("injected failure")
        return {"status": "ok"}


def fresh_engine(
    tmp_path: Path,
    adapter: RecordingSystemAdapter | None = None,
) -> tuple[EffectEngine, Ledger]:
    ledger = Ledger(tmp_path / "kernel.db", registry=effect_registry())
    engine = EffectEngine(
        store_path=tmp_path / "effects.db",
        adapter=adapter or RecordingSystemAdapter(),
        ledger=ledger,
        clock=FixedClock("2026-08-15T00:00:00Z"),
        ids=DeterministicIdGenerator(seed=1),
    )
    return engine, ledger


def intent(index: int = 1, operation: str = "write") -> EffectIntent:
    return EffectIntent(
        intent_id=f"intent-{index}",
        idempotency_key=f"effect:{operation}:{index}",
        action_envelope_id=f"action-{index}",
        system="fs",
        operation=operation,
        params={"path": f"C:/x/{index}"},
    )


def test_submit_execute_and_idempotent_duplicate(tmp_path: Path) -> None:
    engine, _ledger = fresh_engine(tmp_path)
    engine.submit(intent(1))
    first = engine.execute("intent-1")
    assert first.status == "SUCCEEDED"
    second = engine.execute("intent-1")
    assert second.receipt_id == first.receipt_id
    engine.submit(intent(1))
    assert len(engine.inspect("intent-1")) == 1


def test_inspect_and_missing_intent_errors(tmp_path: Path) -> None:
    engine, _ledger = fresh_engine(tmp_path)
    with pytest.raises(EffectError):
        engine.execute("missing")
    assert engine.inspect("missing") == []


def test_partial_success_reconciles_and_compensates(tmp_path: Path) -> None:
    adapter = CrashOnExecuteAdapter()
    engine, _ledger = fresh_engine(tmp_path, adapter)
    engine.submit(intent(1, "write"))
    engine.submit(intent(2, "crash"))
    assert engine.execute("intent-1").status == "SUCCEEDED"
    assert engine.execute("intent-2").status == "FAILED"
    result = engine.reconcile(["intent-1", "intent-2"])
    assert result.reconciliation_status == "PARTIAL_SUCCESS"
    assert result.matched is True
    assert "intent-1" in result.compensated_intent_ids
    compensated = engine.inspect("intent-1")
    assert any(receipt.status == "COMPENSATED" for receipt in compensated)


def test_crash_between_prepare_and_execute_recovers(tmp_path: Path) -> None:
    adapter = CrashOnExecuteAdapter()
    engine, _ledger = fresh_engine(tmp_path, adapter)
    engine.submit(intent(1, "crash"))
    receipt = engine.execute("intent-1")
    assert receipt.status == "FAILED"
    recovered = engine.recover()
    assert list(recovered.recovered_intents) == ["intent-1"]
    assert any(item.status == "FAILED" for item in engine.inspect("intent-1"))


def test_restart_safety_and_recovery_are_durable(tmp_path: Path) -> None:
    adapter = CrashOnExecuteAdapter()
    engine, ledger = fresh_engine(tmp_path, adapter)
    engine.submit(intent(1, "crash"))
    engine.execute("intent-1")
    restarted = EffectEngine(
        store_path=tmp_path / "effects.db",
        adapter=adapter,
        ledger=Ledger(tmp_path / "kernel.db", registry=effect_registry()),
        clock=FixedClock("2026-08-15T00:00:00Z"),
        ids=DeterministicIdGenerator(seed=1),
    )
    assert len(restarted.inspect("intent-1")) == 1
    result = restarted.reconcile(["intent-1"])
    assert result.reconciliation_status in {"FAILED", "PARTIAL_SUCCESS"}
    assert ledger.verify_chain().valid is True
