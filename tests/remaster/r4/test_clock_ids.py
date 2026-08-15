from __future__ import annotations

from jarvis_foundry.kernel.clock import FixedClock, SystemClock
from jarvis_foundry.kernel.ids import DeterministicIdGenerator, IdGenerator


def test_fixed_clock_is_injectable_and_deterministic() -> None:
    clock = FixedClock("2026-08-15T00:00:00Z")
    assert clock.now_utc() == "2026-08-15T00:00:00Z"
    assert clock.now_utc() == clock.now_utc()
    clock.advance(seconds=60)
    assert clock.now_utc() == "2026-08-15T00:01:00Z"


def test_system_clock_returns_utc_zulu() -> None:
    value = SystemClock().now_utc()
    assert value.endswith("Z")
    assert "T" in value


def test_deterministic_ids_are_injectable_and_stable() -> None:
    generator = DeterministicIdGenerator(seed=7)
    assert generator.new_id("goal") == "goal-00000007"
    assert generator.new_id("goal") == "goal-00000008"
    second = DeterministicIdGenerator(seed=7)
    assert second.new_id("goal") == "goal-00000007"


def test_id_generator_protocol_satisfied() -> None:
    def make() -> IdGenerator:
        return DeterministicIdGenerator(seed=1)

    assert make().new_id("x") == "x-00000001"
