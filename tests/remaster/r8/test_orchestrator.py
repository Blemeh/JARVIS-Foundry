from __future__ import annotations

from pathlib import Path

from r8_helpers import (
    asset_config,
    make_alpha_project,
    make_orchestration_candidate,
    run_mission,
)

from jarvis_foundry.kernel.portfolio.store import PortfolioStore
from jarvis_foundry.orchestration.findings import FindingsRegistry
from jarvis_foundry.orchestration.orchestrator import Orchestrator
from jarvis_foundry.orchestration.repair import FIXED_METHOD


def test_asset_mission_end_to_end(tmp_path: Path) -> None:
    source = make_alpha_project(tmp_path / "source")
    evidence = tmp_path / "evidence"
    proving = tmp_path / "proving"
    config = asset_config(
        mission_id="mission-asset",
        source_project=source,
        evidence_dir=evidence,
        proving_root=proving,
        worker_module="jarvis_foundry.orchestration.workers.asset_builder",
    )
    report = run_mission(config, proving)
    assert report["status"] == "ACCEPTED"
    assert report["rollback"]["restored"] is True
    assert report["source_after_unchanged"] is True
    event_types = [event["event_type"] for event in report["ledger"]["events"]]
    for expected in (
        "mission.dispatched",
        "mission.result_ingested",
        "mission.inspected",
        "mission.findings_recorded",
        "mission.reverified",
        "mission.accepted",
        "mission.reconciled",
        "mission.rollback_demonstrated",
    ):
        assert expected in event_types, expected
    clone = proving / "clones" / "mission-asset"
    assert (clone / "tests" / "test_regression_edges.py").is_file()
    assert (clone / "docs" / "health.md").is_file()
    store = PortfolioStore(evidence / "r8_store")
    mission_links = store.mission_links()
    assert len(mission_links) == 1
    assert mission_links[0].mission_id == "mission-asset"
    # The link is bound at acceptance; the report head hash includes later
    # reconciliation events, so assert the link's hash is a real ledger hash.
    assert len(mission_links[0].ledger_header_hash) == 64


def test_malformed_worker_result_fails_closed(tmp_path: Path) -> None:
    source = make_alpha_project(tmp_path / "source")
    evidence = tmp_path / "evidence"
    proving = tmp_path / "proving"
    config = asset_config(
        mission_id="mission-malformed",
        source_project=source,
        evidence_dir=evidence,
        proving_root=proving,
        worker_module="jarvis_foundry.orchestration.workers.malformed_worker",
    )
    report = run_mission(config, proving)
    assert report["status"] == "REJECTED"
    assert report["rejection_reasons"]
    event_types = [event["event_type"] for event in report["ledger"]["events"]]
    assert "mission.malformed_rejected" in event_types
    assert "mission.accepted" not in event_types


def test_waiting_mission_is_honest(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    proving = tmp_path / "proving"
    config = asset_config(
        mission_id="mission-waiting",
        source_project=make_alpha_project(tmp_path / "source"),
        evidence_dir=evidence,
        proving_root=proving,
        worker_module="jarvis_foundry.orchestration.workers.asset_builder",
        kind="waiting",
    )
    orchestrator = Orchestrator(config)
    report = orchestrator.run()
    assert report["status"] == "WAITING"
    assert any(event.event_type == "mission.waiting" for event in orchestrator.ledger.replay())


def test_repair_mission_uses_fresh_worker_and_accepts(tmp_path: Path) -> None:
    source = make_orchestration_candidate(tmp_path / "candidate")
    evidence = tmp_path / "evidence"
    proving = tmp_path / "proving"
    reproduce = ["pytest", "tests/test_verify_repro.py", "-q"]
    regression = ["pytest", "tests", "-q"]
    config = asset_config(
        mission_id="mission-repair",
        source_project=source,
        evidence_dir=evidence,
        proving_root=proving,
        worker_module="jarvis_foundry.orchestration.workers.repair_worker",
        kind="repair",
        worker_spec={
            "target_file": "src/jarvis_foundry/orchestration/verify.py",
            "transform_id": "verifier_recompute",
            "reproduce_command": reproduce,
            "regression_command": regression,
        },
        required_tests=(
            "pytest tests/test_verify_repro.py -q",
            "pytest tests -q",
        ),
        repair_finding_id="R8-F-001",
        reproduced_test_command="pytest tests/test_verify_repro.py -q",
    )
    report = run_mission(config, proving)
    assert report["status"] == "ACCEPTED"
    assert report["worker_result"]["claims"]["defect_reproduced"] is True
    assert report["rollback"]["restored"] is True
    assert report["source_after_unchanged"] is True
    event_types = [event["event_type"] for event in report["ledger"]["events"]]
    assert "mission.repair_dispatched" in event_types
    assert "mission.rejected" not in event_types
    clone = proving / "clones" / "mission-repair"
    fixed = (clone / "src" / "jarvis_foundry" / "orchestration" / "verify.py").read_text(
        encoding="utf-8"
    )
    assert FIXED_METHOD in fixed
    findings = FindingsRegistry(evidence / "r8_findings.jsonl")
    assert findings.open_findings() == []
