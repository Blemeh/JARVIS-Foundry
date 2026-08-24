from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.acceptance_harness.adversarial import run_false_pass_factory
from jarvis_foundry.acceptance_harness.clock import HarnessClock
from jarvis_foundry.acceptance_harness.contracts import ClockMode, FaultSpec, ResourceEnvelope, ScenarioSpec
from jarvis_foundry.acceptance_harness.fixtures import run_owned_supervisor_restart_probe
from jarvis_foundry.acceptance_harness.runner import AcceptanceRunner


def scenario(*, duration: float = 4.0, faults: tuple[FaultSpec, ...] = ()) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id="TEST_UNATTENDED",
        duration_seconds=duration,
        clock_mode=ClockMode.ACCELERATED_DISPOSABLE,
        seed=7,
        initial_state={"stop_available": True, "provider_mode": "fake-local"},
        required_capabilities=("supervisor", "portfolio", "effects", "database"),
        scheduled_faults=faults,
        expected_invariants=(
            "STOP_AVAILABLE", "SINGLE_SUPERVISOR", "HEARTBEAT_CONTINUITY",
            "MISSION_PROGRESS", "PORTFOLIO_CONTINUATION", "MANUAL_CONTINUATION_REQUIRED",
            "WAIT_IS_HONEST", "NO_DUPLICATE_MISSION", "NO_DUPLICATE_EFFECT",
            "EFFECT_UNKNOWN_FROZEN", "INCIDENT_CONTAINED", "DB_DURABLE",
            "RESOURCE_ENVELOPE", "OWNER_BRIEFING_BOUND", "BUSYWORK_BOUND",
            "RELEASE_ROLLBACK_READY", "RESTART_RESUMABLE", "EVIDENCE_COMPLETE",
        ),
        resource_envelope=ResourceEnvelope(max_memory_bytes=64 * 1024 * 1024),
        owner_spam_threshold=4,
        busywork_threshold=2,
        restart_checkpoints=("PRE_RESTART", "POST_RESTART"),
        required_end_state="CONTINUABLE",
        rollback_requirement="HEALTHY_AFTER_ROLLBACK",
        evidence_output="evidence/runs/test",
        sample_interval_seconds=1.0,
    )


def test_scenario_orders_faults_and_rejects_duplicate_ids() -> None:
    first = FaultSpec("b", 2.0, "PROVIDER_OUTAGE", "fake")
    second = FaultSpec("a", 1.0, "DATABASE_LOCK", "db")
    built = scenario(faults=(first, second))
    assert [item.fault_id for item in built.ordered_faults] == ["a", "b"]
    with pytest.raises(ValueError, match="unique"):
        scenario(faults=(first, FaultSpec("b", 3.0, "STOP", "stop")))


def test_accelerated_run_writes_complete_pass_bundle(tmp_path: Path) -> None:
    built = scenario(faults=(
        FaultSpec("provider", 1.0, "PROVIDER_OUTAGE", "fake", {"recovery_after_seconds": 1}),
        FaultSpec("ambiguous", 2.0, "RESPONSE_LOST_AFTER_EFFECT", "fake", {"recovery_after_seconds": 1}),
        FaultSpec("db", 3.0, "DATABASE_LOCK", "db", {"recovery_after_seconds": 1}),
    ))
    result = AcceptanceRunner(
        built, tmp_path / "run", run_process_probe=False, run_fault_matrix=False, run_false_pass_factory=False, run_interruption_probe=False,
    ).run()
    assert result["verdict"] == "PASS"
    output = tmp_path / "run"
    required = {
        "RUN_MANIFEST.json", "SCENARIO.json", "TIMELINE.jsonl", "INVARIANTS.jsonl",
        "FAULTS.jsonl", "RESOURCE_SAMPLES.jsonl", "AUTONOMY_METRICS.json",
        "INCIDENT_SUMMARY.json", "EFFECT_SUMMARY.json", "BRIEFING_SUMMARY.json",
        "FINAL_VERDICT.json", "FINAL_REPORT.md",
    }
    assert required <= {path.name for path in output.iterdir()}
    manifest = json.loads((output / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["missing_artifacts"] == []
    assert all(len(value) == 64 for value in manifest["artifact_hashes"].values())


def test_interrupted_run_resumes_without_refiring_faults(tmp_path: Path) -> None:
    built = scenario(faults=(FaultSpec("provider", 2.0, "PROVIDER_OUTAGE", "fake", {"recovery_after_seconds": 1}),))
    output = tmp_path / "resume"
    first = AcceptanceRunner(
        built, output, run_id="same-run", run_process_probe=False, run_fault_matrix=False,
        run_false_pass_factory=False, run_interruption_probe=False, interrupt_after_steps=2,
    ).run()
    assert first["status"] == "INTERRUPTED"
    second = AcceptanceRunner(
        built, output, run_id="same-run", resume=True, run_process_probe=False,
        run_fault_matrix=False, run_false_pass_factory=False, run_interruption_probe=False,
    ).run()
    assert second["verdict"] == "PASS"
    fault_rows = [json.loads(line) for line in (output / "FAULTS.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert [row["fault_id"] for row in fault_rows] == ["provider"]
    checkpoint = json.loads((output / "CHECKPOINT.json").read_text(encoding="utf-8"))
    assert checkpoint["run_id"] == "same-run"
    selected = checkpoint["artifacts"]["state"]["selected_missions"]
    assert len(selected) == len(set(selected))
    assert checkpoint["artifacts"]["state"]["mission_sequence"] >= len(selected)


def test_false_pass_factory_rejects_all_attacks(tmp_path: Path) -> None:
    payload = run_false_pass_factory(tmp_path)
    assert payload["verdict"] == "PASS"
    assert payload["summary"] == {"fail": 0, "pass": 8, "total": 8}
    assert all(row["observed_verdict"] != "PASS" for row in payload["rows"])


@pytest.mark.parametrize(
    ("fault", "reason_fragment"),
    [
        (FaultSpec("duplicate", 1.0, "DUPLICATE_EFFECT", "fake"), "duplicate external effect"),
        (FaultSpec("busywork", 1.0, "BUSYWORK", "portfolio"), "exceeds threshold"),
        (FaultSpec("spam", 1.0, "OWNER_SPAM", "briefings", {"count": 20}), "exceeds threshold"),
        (FaultSpec("pressure", 1.0, "RESOURCE_PRESSURE", "fixture", {"memory_bytes": 128 * 1024 * 1024}), "exceeds"),
    ],
)
def test_runtime_negative_controls_refuse_pass(tmp_path: Path, fault: FaultSpec, reason_fragment: str) -> None:
    result = AcceptanceRunner(
        scenario(faults=(fault,)), tmp_path / fault.fault_id,
        run_process_probe=False, run_fault_matrix=False, run_false_pass_factory=False, run_interruption_probe=False,
    ).run()
    assert result["verdict"] == "FAIL"
    assert any(reason_fragment in reason for reason in result["reasons"])


def test_owned_process_restart_probe_is_scoped(tmp_path: Path) -> None:
    result = run_owned_supervisor_restart_probe(tmp_path)
    assert result["status"] == "PASS"
    assert result["distinct_process_identity"] is True


def test_real_clock_reports_wall_elapsed() -> None:
    clock = HarnessClock(ClockMode.REAL_TIME, duration_seconds=0.02)
    clock.advance_to(0.02)
    assert clock.wall_elapsed_seconds >= 0.01
    assert clock.at_end()
