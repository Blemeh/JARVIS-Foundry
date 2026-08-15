from __future__ import annotations

import pytest

from jarvis_foundry.orchestration.repair import (
    BUGGY_METHOD,
    FIXED_METHOD,
    apply_repair_transform,
    revert_repair_transform,
)


def test_apply_repair_transform_replaces_stub() -> None:
    fixed = apply_repair_transform(BUGGY_METHOD + "\n", "verifier_recompute")
    assert FIXED_METHOD in fixed
    assert "unclaimed change" in fixed


def test_repair_transform_roundtrip() -> None:
    source = BUGGY_METHOD + "\n"
    fixed = apply_repair_transform(source, "verifier_recompute")
    reverted = revert_repair_transform(fixed, "verifier_recompute")
    assert reverted == source


def test_unknown_transform_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown repair transform"):
        apply_repair_transform(BUGGY_METHOD, "not-a-transform")


def test_repair_refuses_already_fixed_source() -> None:
    with pytest.raises(ValueError, match="repair target not found"):
        apply_repair_transform(FIXED_METHOD + "\n", "verifier_recompute")
