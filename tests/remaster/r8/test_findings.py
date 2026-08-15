from __future__ import annotations

from pathlib import Path

from jarvis_foundry.orchestration.findings import Finding, FindingsRegistry


def _registry(tmp_path: Path) -> FindingsRegistry:
    return FindingsRegistry(tmp_path / "findings.jsonl")


def test_findings_are_durable_and_hash_bound(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    finding = Finding(
        finding_id="F-001",
        severity="S2",
        title="verifier trusts claims",
        detail="detail",
        evidence=("worker_result.json",),
        reproduced_command="pytest -q",
        mission_id="mission-1",
    )
    registry.record(finding)
    reloaded = FindingsRegistry(registry.path)
    assert reloaded.all()[0].finding_id == "F-001"


def test_findings_reject_tampering(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.record(
        Finding(
            finding_id="F-002",
            severity="S3",
            title="title",
            detail="detail",
            evidence=(),
            reproduced_command="",
            mission_id="mission-1",
        )
    )
    raw = registry.path.read_text(encoding="utf-8")
    registry.path.write_text(raw.replace("S3", "S2"), encoding="utf-8")
    try:
        registry.all()
    except ValueError:
        return
    raise AssertionError("tampered findings line was accepted")


def test_findings_mark_repaired(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.record(
        Finding(
            finding_id="F-003",
            severity="S2",
            title="title",
            detail="detail",
            evidence=(),
            reproduced_command="",
            mission_id="mission-1",
        )
    )
    registry.mark_repaired("F-003", "mission-repair")
    updated = registry.all()[0]
    assert updated.status == "REPAIRED"
    assert updated.repair_mission_id == "mission-repair"
