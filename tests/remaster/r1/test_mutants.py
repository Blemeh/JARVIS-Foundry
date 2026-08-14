from __future__ import annotations

from pathlib import Path

import pytest

from acceptance import run_external_adapter


EVALUATION_TIME = "2030-01-01T00:00:00Z"

MUTANTS = [
    ("missing_evidence", "MISSING_REQUIRED_EVIDENCE"),
    ("fixture_as_live", "FIXTURE_AS_LIVE"),
    ("stale_source", "STALE_OR_UNIDENTIFIABLE_SOURCE"),
    ("target_substitution", "CANONICAL_TARGET_SUBSTITUTED"),
    ("self_verification", "SELF_VERIFICATION"),
    ("budget_overflow", "BUDGET_OVERFLOW"),
    ("duplicate_non_idempotent_effect", "DUPLICATE_NON_IDEMPOTENT_EFFECT"),
    ("absent_after_state", "ABSENT_AFTER_STATE"),
    ("contradiction_erasure", "CONTRADICTION_ERASED"),
    ("failed_rollback", "ROLLBACK_FAILED"),
    ("forged_owner_control", "FORGED_OWNER_CONTROL"),
    ("forbidden_financial_effect", "FORBIDDEN_FINANCIAL_EFFECT"),
]


@pytest.mark.parametrize(("mutant", "expected_code"), MUTANTS, ids=[item[0] for item in MUTANTS])
def test_every_frozen_adversarial_mutant_fails_closed(
    mutant: str,
    expected_code: str,
    purpose_root: Path,
    mutant_command,
    tmp_path: Path,
) -> None:
    result = run_external_adapter(
        mutant_command(mutant),
        purpose_root,
        EVALUATION_TIME,
        run_root=tmp_path / mutant,
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert result["status"] == "FAIL"
    assert expected_code in codes
    assert any(journey["status"] == "FAIL" for journey in result["journeys"])
    assert all(
        evidence_ids
        for journey in result["journeys"]
        for evidence_ids in journey["criterion_evidence"].values()
    )
    assert result["adapter_process"]["exit_code"] == 0


def test_mutant_matrix_is_exactly_the_twelve_frozen_classes() -> None:
    assert len(MUTANTS) == 12
    assert len({item[0] for item in MUTANTS}) == 12
