"""WP3 adversarial path-boundary tests.

Proves the action policy denies every escape class deterministically and
without mutation authority: traversal, absolute-outsider, drive-letter,
case-alias through junctions, and Windows junction escape. Junctions are
creatable without elevation on Windows; the tests skip gracefully when the
host cannot create one (non-Windows or restricted policy).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from jarvis_foundry.control.action_envelope import evaluate_and_record
from jarvis_foundry.control.policy import ActionPolicy
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.domain import ActionDecision

from test_action_policy import envelope, registered_database

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="junction tests are Windows-specific")


def _make_junction(target: Path, junction: Path) -> bool:
    """Create a directory junction without elevation. Returns False if the
    host refuses (restricted policy)."""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def test_traversal_escape_is_denied(tmp_path):
    database, repository = registered_database(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")
    verdict = policy.evaluate(envelope(repository, target=repository / ".." / "outside.txt"))
    assert verdict.decision == ActionDecision.INVALID_TARGET


def test_absolute_outside_target_is_denied(tmp_path):
    database, repository = registered_database(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")
    outside = tmp_path / "outside.txt"
    verdict = policy.evaluate(envelope(repository, target=outside))
    assert verdict.decision == ActionDecision.INVALID_TARGET


def test_drive_root_and_drive_letter_targets_are_denied(tmp_path):
    database, repository = registered_database(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")
    drive = repository.drive or "C:"
    for bad in (Path(f"{drive}\\"), Path(f"{drive}\\windows\\system32\\x"), Path(f"{drive.lower()}\\evil")):
        verdict = policy.evaluate(envelope(repository, target=bad))
        assert verdict.decision == ActionDecision.INVALID_TARGET, bad


def test_junction_escape_is_denied(tmp_path):
    database, repository = registered_database(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = repository / "escape-junction"
    if not _make_junction(outside, junction):
        pytest.skip("junction creation refused by host policy")

    policy = ActionPolicy(database, tmp_path / "STOP")
    verdict = policy.evaluate(envelope(repository, target=junction / "evil.txt"))
    assert verdict.decision == ActionDecision.INVALID_TARGET
    verdict2 = policy.evaluate(envelope(repository, target=junction))
    assert verdict2.decision == ActionDecision.INVALID_TARGET


def test_invalid_target_records_distinct_event_and_decision(tmp_path):
    database, repository = registered_database(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")
    action = envelope(repository, target=repository / ".." / "outside.txt")

    verdict = evaluate_and_record(database, policy, action)

    assert verdict.decision == ActionDecision.INVALID_TARGET
    recorded = database.row("action_decisions", "action_id", action.action_id)
    assert recorded["decision"] == "INVALID_TARGET"
    assert recorded["reason"] == "resolved target escapes registered root"
    assert any(event.event_type == "action.invalid_target" for event in database.events())


def test_malformed_envelope_is_denied_before_any_lookup(tmp_path):
    database, repository = registered_database(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")

    for bad in (
        envelope(repository, action_id="", mission_id="mission-1", resource_id="resource-1"),
        envelope(repository, action_kind=""),
        envelope(repository, expected_effects=()),
        envelope(repository, rollback={}),
        envelope(repository, risk_class="C9"),
    ):
        verdict = policy.evaluate(bad)
        assert verdict.decision == ActionDecision.NOT_READY
        assert "required" in verdict.reason or "risk class" in verdict.reason
