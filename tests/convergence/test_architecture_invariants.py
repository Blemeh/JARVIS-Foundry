"""Convergence invariants that must survive semantic forward-porting."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "jarvis_foundry"


def test_newer_canonical_memory_worker_and_runtime_surfaces_remain() -> None:
    required = (
        PACKAGE / "hybrid_memory",
        PACKAGE / "workers",
        PACKAGE / "runtime",
        PACKAGE / "mission" / "loop.py",
        PACKAGE / "supervisor" / "evolution_control.py",
        PACKAGE / "control" / "capability_broker.py",
        PACKAGE / "integrated_rollback.py",
    )
    missing = [str(path.relative_to(ROOT)) for path in required
               if not path.exists()]
    assert not missing, missing


def test_superseded_remaster_architectures_are_not_resurrected() -> None:
    # These were additive remaster packages, not current-main architecture.
    for name in ("kernel", "orchestration", "capabilities_live",
                 "evolution_live", "fault_campaign"):
        assert not (PACKAGE / name).exists(), name
    assert (PACKAGE / "command_center.py").exists()
    assert not (PACKAGE / "command_center").is_dir()
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in PACKAGE.rglob("*.py")
        if "convergence" not in path.parts
    )
    for forbidden in (
        "jarvis_foundry.kernel",
        "jarvis_foundry.orchestration",
        "jarvis_foundry.capabilities_live",
        "jarvis_foundry.evolution_live",
        "jarvis_foundry.fault_campaign",
    ):
        assert forbidden not in source_text, forbidden


def test_one_durable_stop_and_worker_runtime_truth() -> None:
    assert (PACKAGE / "supervisor" / "estop.py").exists()
    assert (PACKAGE / "owner_controls.py").exists()
    assert (PACKAGE / "control" / "action_envelope.py").exists()
    assert (ROOT / "tests" / "foundry" / "test_runtime_worker_loop.py").exists()
    assert (ROOT / "tests" / "foundry" / "test_worker_mission_bridge.py").exists()
