from __future__ import annotations

import json

from jarvis_foundry.orchestration.mission_context import (
    build_mission_context,
)

REQUIRED = (
    "mission_id",
    "MISSION",
    "CURRENT STEP",
    "ROLE",
    "OBJECTIVE",
    "KNOWN VERIFIED STATE",
    "RELEVANT PREVIOUS FAILURES",
    "ALLOWED MUTATION",
    "FORBIDDEN MUTATION",
    "ACCEPTANCE CRITERIA",
    "REQUIRED TESTS",
    "REQUIRED EVIDENCE",
    "ROLLBACK EXPECTATION",
    "RESULT CONTRACT",
)


def _context() -> object:
    return build_mission_context(
        mission_id="mission-test",
        mission="prove R8",
        current_step="ASSET",
        role="builder",
        objective="create regression suite",
        known_verified_state={"source": "clean"},
        relevant_previous_failures=["prior empty report"],
        allowed_mutation=["tests/"],
        forbidden_mutation=["protected/"],
        acceptance_criteria=["tests green"],
        required_tests=["pytest tests -q"],
        required_evidence=["worker_result.json"],
        rollback_expectation="restore demo",
        result_contract={"fields": ["status"], "prose_is_not_truth": True},
    )


def test_mission_context_has_all_bounded_fields() -> None:
    payload = _context().to_dict()
    for key in REQUIRED:
        assert key in payload, key


def test_mission_context_hash_is_deterministic() -> None:
    first = _context().content_hash()
    second = _context().content_hash()
    assert first == second
    assert len(first) == 64


def test_mission_context_json_roundtrip(tmp_path) -> None:
    context = _context()
    path = tmp_path / "context.json"
    context.write(path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["MISSION"] == context.mission
