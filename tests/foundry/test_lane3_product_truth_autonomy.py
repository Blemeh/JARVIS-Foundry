"""Lane 3 product-truth proof over a disposable Foundry runtime."""

from __future__ import annotations

from scripts.lane3_autonomy_fixture_server import prepare_fixture


def test_source_backed_loop_reaches_memory_and_owner_briefing() -> None:
    server, proof, _root = prepare_fixture(0, None)
    try:
        assert proof["source"] == "disposable_foundry_database"
        assert proof["mission_state"] in {"COMPLETE", "COMPLETED"}
        assert proof["verification_statuses"]
        assert "FAIL" in proof["verification_statuses"]
        assert "PASS" in proof["verification_statuses"]
        assert proof["evidence_bundles"]
        assert proof["memory_ids"]
        assert proof["briefing"]["briefing_id"]
        assert proof["briefing"]["created"] is True
        event_types = set(proof["event_types"])
        assert {"owner_directive.recorded", "mission.created", "agent_run.started", "evidence.created"} <= event_types
        assert {"verification.failed", "verification.passed", "mission.completed", "memory.observed", "briefing.planned"} <= event_types
        adversarial = proof["adversarial_coverage"]
        assert adversarial["worker_false_claim"] == "PASS"
        assert adversarial["failed_verifier"] == "PASS"
        assert adversarial["repair_retry"] == "PASS"
        assert all(value in {"PASS", "COVERED_BY_EXISTING_CANONICAL_TEST"} for value in adversarial.values())
    finally:
        server.close()
