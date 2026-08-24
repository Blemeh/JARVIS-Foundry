"""V7B-15: one disposable multi-capability digital-office mission."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.operations.lab import DigitalOfficeLab


def test_digital_office_lab_passes_without_live_accounts(tmp_path: Path) -> None:
    report = DigitalOfficeLab(tmp_path / "disposable-digital-office").run()
    assert report.status == "PASS"
    assert report.procedure_outcome == "COMPLETED"
    assert len(report.capabilities) >= 5
    assert report.project_commit == report.remote_commit
    assert report.deployment_current == "release-v1"
    assert report.timeout_reconciled
    assert report.recoverable_failure
    assert report.compensation_applied
    assert report.stop_veto_checked
    assert report.restart_resumed
    assert report.hostile_content_contained
    assert report.project_clean
    assert report.no_real_accounts
