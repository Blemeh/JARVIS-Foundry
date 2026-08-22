"""V3 WP4 tests: bounded teams, fresh verifier binding, repair budget 3."""
from __future__ import annotations

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.verification import (
    record_verification_outcome,
    start_verification,
)
from jarvis_foundry.v3_teams import (
    DEFAULT_REPAIR_BUDGET,
    MAX_DELEGATION_DEPTH,
    MAX_TEAM_SIZE,
    ROLE_MANIFEST,
    fresh_verifier_identity,
    repair_budget_exhausted,
    validate_role,
    validate_team,
)


def test_bounded_manifest_matches_spec_table():
    assert set(ROLE_MANIFEST) == {
        "scout", "researcher", "architect", "builder", "tester", "critic",
        "verifier", "repairer", "documenter", "knowledge_curator"}
    # Only the verifier may verify; nobody accepts (acceptance is the
    # deterministic loop's, not a role's).
    assert [r for r, m in ROLE_MANIFEST.items() if m["verify"]] == ["verifier"]
    assert not any(m["accept"] for m in ROLE_MANIFEST.values())
    assert MAX_TEAM_SIZE == 6 and MAX_DELEGATION_DEPTH == 2


def test_unknown_role_fails_closed():
    with pytest.raises(ValueError, match="unknown team role"):
        validate_role("supreme_overlord")


def test_team_size_cap_and_single_verifier():
    ok = ["builder", "tester", "critic", "verifier", "repairer",
          "documenter"]
    assert validate_team(ok)["size"] == 6
    with pytest.raises(ValueError, match="team size"):
        validate_team([*ok, "scout"])
    with pytest.raises(ValueError, match="one verifier"):
        validate_team(["verifier", "verifier"])


def test_fresh_verifier_identity_independent_and_reduced():
    identity = fresh_verifier_identity(
        verifier_provider="opencode_go", verifier_session_id="sess-b",
        builder_provider="other_provider", builder_run_id="run-a")
    assert identity["fresh_session"] is True
    assert identity["independence"] == "INDEPENDENT"

    same_provider = fresh_verifier_identity(
        verifier_provider="opencode_go", verifier_session_id="sess-c",
        builder_provider="opencode_go", builder_run_id="run-a")
    assert same_provider["independence"] == "REDUCED"


def test_verifier_reusing_builder_run_is_self_certification_rejected():
    with pytest.raises(ValueError, match="FRESH"):
        fresh_verifier_identity(
            verifier_provider="p", verifier_session_id="run-a",
            builder_provider="p", builder_run_id="run-a")


def test_missing_session_identity_rejected():
    with pytest.raises(ValueError, match="fresh session"):
        fresh_verifier_identity(verifier_provider="p",
                                verifier_session_id="   ",
                                builder_provider=None)


def _verdict(database: FoundryDatabase, verdict_id: str, mission_id: str,
             step_id: str, outcome: str) -> None:
    start_verification(
        database, verdict_id=verdict_id, mission_id=mission_id,
        step_id=step_id, builder_run_id=f"builder-{verdict_id}",
        verifier_run_id=f"verifier-{verdict_id}",
        independence={"independence": "INDEPENDENT"},
        criteria=["artifact exists"])
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome=outcome,
        findings=[], evidence={"source": "v3-team-test"})


def test_repair_budget_three_then_exhausted(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    for index in range(DEFAULT_REPAIR_BUDGET):
        check = repair_budget_exhausted(
            database, mission_id="mission-m", step_id="executive-step")
        assert check["exhausted"] is False
        assert check["remaining"] == DEFAULT_REPAIR_BUDGET - index
        _verdict(database, f"v-{index}", "mission-m", "executive-step",
                 "FAIL")
    exhausted = repair_budget_exhausted(
        database, mission_id="mission-m", step_id="executive-step")
    assert exhausted["exhausted"] is True
    assert exhausted["remaining"] == 0


def test_pass_verdicts_do_not_consume_repair_budget(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    _verdict(database, "v-pass-1", "mission-p", "step-x", "PASS")
    check = repair_budget_exhausted(database, mission_id="mission-p",
                                    step_id="step-x")
    assert check["failed_verdicts"] == 0 and check["exhausted"] is False


def test_budget_is_per_step_not_global(tmp_path):
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    for index in range(DEFAULT_REPAIR_BUDGET):
        _verdict(database, f"va-{index}", "mission-q", "step-a", "FAIL")
    other = repair_budget_exhausted(database, mission_id="mission-q",
                                    step_id="step-b")
    assert other["exhausted"] is False
