"""V7B-6: disposable immutable deployment with health and rollback."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.operations.deployment import (
    DeploymentError,
    DisposableDeploymentSlot,
)


def test_disposable_deployment_switches_and_rolls_back(tmp_path: Path) -> None:
    source = tmp_path / "disposable-source"
    source.mkdir()
    (source / "health.txt").write_text("ok", encoding="utf-8")
    slot = DisposableDeploymentSlot(tmp_path / "disposable-slot")
    first = slot.build(source, artifact_id="release-v1", source_ref="git:one")
    slot.stage(first)
    assert slot.health_check(first, lambda path: (path / "health.txt").read_text() == "ok")
    receipt = slot.switch(first)
    assert receipt.current == "release-v1"

    (source / "health.txt").write_text("broken", encoding="utf-8")
    second = slot.build(source, artifact_id="release-v2", source_ref="git:two")
    slot.stage(second)
    slot.set_health("release-v2", False)
    with pytest.raises(DeploymentError, match="health"):
        slot.switch(second)
    assert slot.reconcile()["current"] == "release-v1"
    slot.set_health("release-v2", True)
    slot.switch(second)
    assert slot.reconcile()["previous"] == "release-v1"
    rollback = slot.rollback()
    assert rollback.current == "release-v1"
    assert slot.reconcile()["consistent"]
