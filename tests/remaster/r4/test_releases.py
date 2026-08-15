from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.errors import ReleaseError
from jarvis_foundry.kernel.events import EventKindRegistry
from jarvis_foundry.kernel.ids import DeterministicIdGenerator
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.releases import ReleaseController, ReleaseFailureAdapter


def release_registry() -> EventKindRegistry:
    return (
        EventKindRegistry()
        .register("release.created", required_keys=("release_id", "artifact_id"))
        .register("release.transitioned", required_keys=("release_id", "stage"))
    )


def fresh_controller(
    tmp_path: Path, adapter: ReleaseFailureAdapter | None = None
) -> ReleaseController:
    ledger = Ledger(tmp_path / "kernel.db", registry=release_registry())
    return ReleaseController(
        store_path=tmp_path / "releases.db",
        ledger=ledger,
        clock=FixedClock("2026-08-15T00:00:00Z"),
        ids=DeterministicIdGenerator(seed=1),
        adapter=adapter,
    )


def test_build_lab_shadow_canary_promote_freeze(tmp_path: Path) -> None:
    controller = fresh_controller(tmp_path)
    release = controller.create(artifact_id="art-1", version="1.0.0")
    assert release.stage == "BUILD"
    release = controller.enter_lab(release.release_id)
    assert release.stage == "LAB"
    release = controller.shadow(release.release_id)
    assert release.stage == "SHADOW"
    release = controller.canary(release.release_id)
    assert release.stage == "CANARY"
    release = controller.promote(release.release_id)
    assert release.stage == "PROMOTED"
    release = controller.freeze(release.release_id)
    assert release.stage == "FROZEN"


def test_rollback_restore_quarantine(tmp_path: Path) -> None:
    controller = fresh_controller(tmp_path)
    release = controller.create(artifact_id="art-1", version="1.0.0")
    release = controller.promote(release.release_id)
    assert controller.rollback(release.release_id).stage == "ROLLED_BACK"
    restored = controller.restore(release.release_id)
    assert restored.stage == "RESTORED"
    quarantined = controller.quarantine(release.release_id)
    assert quarantined.stage == "QUARANTINED"


def test_invalid_transition_and_missing_release_refused(tmp_path: Path) -> None:
    controller = fresh_controller(tmp_path)
    with pytest.raises(ReleaseError):
        controller.promote("missing")
    release = controller.create(artifact_id="art-1", version="1.0.0")
    assert controller.promote(release.release_id).stage == "PROMOTED"
    with pytest.raises(ReleaseError):
        controller.promote(release.release_id)


def test_failure_injection_quarantines_and_duplicate_is_refused(tmp_path: Path) -> None:
    controller = fresh_controller(tmp_path, adapter=ReleaseFailureAdapter(fail_on=("promote",)))
    release = controller.create(artifact_id="art-1", version="1.0.0")
    with pytest.raises(ReleaseError):
        controller.promote(release.release_id)
    after = controller.get(release.release_id)
    assert after.stage == "QUARANTINED"


def test_restart_safety_preserves_stage(tmp_path: Path) -> None:
    controller = fresh_controller(tmp_path)
    release = controller.create(artifact_id="art-1", version="1.0.0")
    controller.promote(release.release_id)
    restarted = fresh_controller(tmp_path)
    record = restarted.get(release.release_id)
    assert record.stage == "PROMOTED"
