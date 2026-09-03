"""Acceptance tests for the candidate-neutral V11 A3/A4 proving runner."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "v11_a3a4_proving.py"
SPEC = importlib.util.spec_from_file_location("v11_a3a4_proving", SCRIPT)
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HARNESS
SPEC.loader.exec_module(HARNESS)


def _record(record_id: str, identity: str, **extra):
    return {"id": record_id, "identity_key": identity, **extra}


def _good_snapshot(cycle_id: str, *, fault=None, status="WAIT"):
    candidates = [
        _record(
            f"candidate-{index}", f"candidate-fingerprint-{index}",
            status="REJECTED" if index < 8 else "ELIGIBLE",
        )
        for index in range(12)
    ]
    return {
        "schema": HARNESS.DRIVER_SCHEMA,
        "cycle_id": cycle_id,
        "status": status,
        "stages": list(HARNESS.REQUIRED_FLOW),
        "records": {
            "observations": [
                _record("observation-1", "source:revision:1", real=True, provenance={"source": "git"})
            ],
            "candidates": candidates,
            "objectives": [_record("objective-1", "candidate-10", candidate_id="candidate-10")],
            "missions": [_record("mission-1", "objective-1:mission", state="COMPLETE")],
            "builder_runs": [_record("builder-1", "mission-1:build", mission_id="mission-1")],
            "effects": [_record("effect-1", "mission-1:commit", mission_id="mission-1")],
            "acceptances": [_record(
                "acceptance-1", "mission-1:acceptance", mission_id="mission-1",
                builder_run_id="builder-1", verifier_run_id="verifier-1",
                independent=True, verdict="PASS",
            )],
            "outcomes": [_record(
                "outcome-1", "mission-1:outcome", mission_id="mission-1",
                acceptance_id="acceptance-1", truth_accepted=True, status="SUCCEEDED",
            )],
            "learning": [_record("learning-1", "outcome-1:learning", outcome_id="outcome-1")],
            "repairs": [],
        },
        "fault": fault,
        "stop_new_dispatch_blocked": True,
    }


def test_metrics_are_independently_derived_and_full_gate_can_pass() -> None:
    state = HARNESS.blank_state(run_id="run", expected_sha="f" * 40, duration_hours=48)
    state["started_at"] = (
        datetime.now(UTC) - timedelta(hours=49)
    ).isoformat().replace("+00:00", "Z")
    for point in HARNESS.FAULT_POINTS:
        state["faults"][point] = {"injected": True, "recovered": True}
    for index in range(3):
        HARNESS.record_snapshot(state, _good_snapshot(f"cycle-{index}"))

    verdict = HARNESS.evaluate(state, require_full_gate=True)

    assert verdict["verdict"] == "PASS"
    assert verdict["metrics"]["candidates"] == 12
    assert verdict["metrics"]["rejected"] == 8
    assert verdict["metrics"]["verified_outcomes"] == 1
    assert all(verdict["hard_assertions"].values())


def test_duplicate_identity_false_success_and_unverified_completion_fail_closed() -> None:
    state = HARNESS.blank_state(run_id="run", expected_sha="f" * 40, duration_hours=48)
    first = _good_snapshot("cycle-1")
    HARNESS.record_snapshot(state, first)
    bad = _good_snapshot("cycle-2")
    bad["records"]["candidates"][0]["id"] = "candidate-duplicate-id"
    bad["records"]["builder_runs"][0]["id"] = "builder-duplicate-id"
    bad["records"]["outcomes"][0].update({
        "id": "outcome-fabricated", "identity_key": "fabricated-outcome",
        "acceptance_id": "missing-acceptance",
    })
    bad["unbounded_loop_detected"] = True
    bad["records"]["missions"].append(
        _record("mission-unverified", "unverified-mission", state="COMPLETE")
    )
    HARNESS.record_snapshot(state, bad)

    verdict = HARNESS.evaluate(state, require_full_gate=False)

    assert verdict["verdict"] == "FAIL"
    assert verdict["metrics"]["duplicate_candidates"] == 1
    assert verdict["metrics"]["duplicate_builder_runs"] == 1
    assert verdict["metrics"]["false_successes"] == 1
    assert verdict["metrics"]["unverified_completions"] == 1
    assert verdict["metrics"]["unbounded_loops"] == 1


def test_prepare_bundle_is_not_started_and_refuses_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "prepared"
    result = HARNESS.prepare_bundle(
        output, expected_sha="a" * 40, duration_hours=48, run_id="stable-run",
    )

    assert result["status"] == "PREPARED_NOT_STARTED"
    checkpoint = json.loads((output / "CHECKPOINT.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    assert checkpoint["run_id"] == "stable-run"
    assert checkpoint["started_at"] is None
    assert checkpoint["deadline_at"] is None
    assert checkpoint["fault_cursor"] == 0
    assert manifest["status"] == "PREPARED_NOT_STARTED"
    assert (output / "HARNESS_CONTRACT.json").exists()
    assert (output / "FAULT_PLAN.json").exists()

    try:
        HARNESS.prepare_bundle(output, expected_sha="a" * 40, duration_hours=48)
    except FileExistsError:
        pass
    else:
        raise AssertionError("non-empty evidence output must not be overwritten")


def test_all_fault_requests_resume_same_cycle_and_identity(tmp_path: Path) -> None:
    driver = tmp_path / "driver.py"
    driver.write_text(
        """
import argparse, json
p=argparse.ArgumentParser(); p.add_argument('--request', required=True); a=p.parse_args()
r=json.load(open(a.request, encoding='utf-8'))
point=r.get('fault_point')
fault={'point': point, 'status': 'RECOVERED' if r.get('resume') else 'INJECTED'} if point else None
candidates=[{'id':f'candidate-{i}','identity_key':f'fingerprint-{i}','status':'REJECTED' if i<8 else 'ELIGIBLE'} for i in range(12)]
records={
 'observations':[{'id':'observation-1','identity_key':'source:1','real':True,'provenance':{'source':'git'}}],
 'candidates':candidates,
 'objectives':[{'id':'objective-1','identity_key':'candidate-10'}],
 'missions':[{'id':'mission-1','identity_key':'objective-1:mission','state':'COMPLETE'}],
 'builder_runs':[{'id':'builder-1','identity_key':'mission-1:build'}],
 'effects':[{'id':'effect-1','identity_key':'mission-1:effect'}],
 'acceptances':[{'id':'acceptance-1','identity_key':'mission-1:acceptance','mission_id':'mission-1','builder_run_id':'builder-1','verifier_run_id':'verifier-1','independent':True,'verdict':'PASS'}],
 'outcomes':[{'id':'outcome-1','identity_key':'mission-1:outcome','mission_id':'mission-1','acceptance_id':'acceptance-1','truth_accepted':True,'status':'SUCCEEDED'}],
 'learning':[{'id':'learning-1','identity_key':'outcome-1:learning'}], 'repairs':[]}
print(json.dumps({'schema':'jarvis.v11.a3a4.driver.v1','cycle_id':r['cycle_id'],'status':'WAIT' if r.get('resume') else 'RUNNING','stages':['OBSERVATION','CANDIDATES','SELECTION','OBJECTIVE','MISSION','BUILD','EFFECT','ACCEPTANCE','LEARNING','WAIT'],'records':records,'fault':fault,'stop_new_dispatch_blocked':True}))
""".strip() + "\n",
        encoding="utf-8",
    )
    driver_spec = tmp_path / "driver.json"
    driver_spec.write_text(json.dumps({"argv": [sys.executable, str(driver)]}), encoding="utf-8")
    output = tmp_path / "run"
    HARNESS.prepare_bundle(output, expected_sha="a" * 40, duration_hours=48, run_id="resume-run")
    state = HARNESS._load_state(output)
    argv = HARNESS._driver_argv(driver_spec)

    for _point in HARNESS.FAULT_POINTS:
        HARNESS.run_one_cycle(state, argv, output)

    metrics = HARNESS.derive_metrics(state)
    assert state["fault_cursor"] == len(HARNESS.FAULT_POINTS)
    assert metrics["faults_injected"] == len(HARNESS.FAULT_POINTS)
    assert metrics["faults_recovered"] == len(HARNESS.FAULT_POINTS)
    assert metrics["completed_cycles"] == len(HARNESS.FAULT_POINTS)
    assert metrics["duplicate_candidates"] == 0
    assert metrics["duplicate_objectives"] == 0
    assert metrics["duplicate_missions"] == 0
    assert metrics["duplicate_builder_runs"] == 0
    assert metrics["duplicate_effects"] == 0
    assert metrics["duplicate_learning"] == 0
    checkpoint = HARNESS._load_state(output)
    assert checkpoint["run_id"] == "resume-run"
    assert checkpoint["fault_cursor"] == len(HARNESS.FAULT_POINTS)


def test_immediate_a4_requires_complete_fault_flow_without_starting_clock() -> None:
    state = HARNESS.blank_state(run_id="run", expected_sha="f" * 40, duration_hours=48)
    snapshot = _good_snapshot("cycle-immediate")
    for index, candidate in enumerate(snapshot["records"]["candidates"]):
        candidate["status"] = "SELECTED" if index == 10 else "REJECTED"
    HARNESS.record_snapshot(state, snapshot)
    for point in HARNESS.FAULT_POINTS:
        state["faults"][point] = {"injected": True, "recovered": True}

    verdict = HARNESS.evaluate_immediate_a4(state)

    assert verdict["verdict"] == "PASS"
    assert verdict["checks"]["elapsed_gate_not_started"] is True
    assert state["started_at"] is None


def test_a2_barrier_runs_real_project_observation_with_zero_execution(tmp_path: Path) -> None:
    report = HARNESS.a2_barrier(argparse.Namespace(output=tmp_path / "a2"))

    assert report["verdict"] == "PASS"
    assert report["database_schema"] == 40
    assert report["cycle_outcome"] in {"WAIT", "IDLE"}
    assert report["metrics"]["observations"] >= 1
    assert report["metrics"]["objectives"] == 0
    assert report["metrics"]["missions"] == 0
    assert report["metrics"]["effects"] == 0
    assert report["48h_started"] is False
