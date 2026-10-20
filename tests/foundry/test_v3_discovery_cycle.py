"""V3 WP3 tests: unified discovery cycle behind discovery.cycle_enabled.

Flag OFF: v3_loop WAIT + executive selection unchanged (regression).
Flag ON (lab): one observe -> score -> select -> compile cycle record.
Duplicate prevention: one candidate fingerprint can never produce two
mission.created events.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import generate_candidate
from jarvis_foundry.events import Event
from jarvis_foundry.executive.eligibility import (
    current_activation_epoch,
    enable_admission_authority,
)
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.v3_cycle import run_discovery_cycle
from jarvis_foundry.v3_scoring import register_policy

from v3_fixtures import load_policy
from worker_test_helpers import mission_database

EPOCH = "v3-cycle-epoch"

FLAG_OFF_CONFIG = WorkerRuntimeConfig(enabled=True)


def _register_resource(database, repository, resource_id="resource-1") -> None:
    from jarvis_foundry.control.capabilities import set_capability_state
    from jarvis_foundry.control.registration import (
        inspect_git_resource,
        registration_event,
    )
    database.append(registration_event(
        inspect_git_resource(repository, resource_id=resource_id)))
    set_capability_state(
        database, resource_id, "write_registered_worktree_files", "READY",
        evidence={"source": "v3-cycle-test"})


def _activate_epoch(database, epoch=EPOCH) -> None:
    database.append(Event.create(
        "executive.activation_recorded",
        {"epoch": epoch, "decision_version": "1.0.0"},
        source_type="supervisor", source_id="v3-test",
        idempotency_key=f"executive.activation_recorded:{epoch}",
    ))
    enable_admission_authority(database, epoch, source_id="v3-test")


def _cycle_env(tmp_path: Path):
    database, repository = mission_database(tmp_path, resource_id="resource-1")
    _register_resource(database, repository)
    _activate_epoch(database)
    register_policy(database, load_policy())
    return database, repository


def _lab_candidate(database, objective="Fix the failing nightly integration",
                   evidence=None):
    result = generate_candidate(
        database, "mission-seed", "maintenance", objective,
        evidence if evidence is not None else [
            {"kind": "ci", "ref": "nightly"},
            {"kind": "log", "ref": "run-1"},
            {"kind": "issue", "ref": "i-7"}],
        typed={"expected_outcome": f"verified: {objective}",
               "candidate_type": "repair"})
    return result["candidate_id"]


def _observe_stub(observed=4, created=1):
    def _observe(_database):
        return {"observed": {"vault": observed}, "candidates_created": created,
                "research_items": 0, "monitors": {}, "maintenance": {},
                "maintenance_candidate_created": 0}
    return _observe


# ---------------------------------------------------------------- flag OFF

def test_flag_defaults_off_and_config_roundtrip(tmp_path):
    config = WorkerRuntimeConfig(enabled=True)
    assert config.discovery_cycle_enabled is False
    yaml_path = tmp_path / "worker.yaml"
    yaml_path.write_text(
        "enabled: true\n"
        "hermes:\n  allow_data_training_tiers_noninteractive: true\n"
        "discovery:\n"
        "  cycle_enabled: true\n",
        encoding="utf-8")
    parsed = WorkerRuntimeConfig.from_yaml(yaml_path)
    assert parsed.discovery_cycle_enabled is True
    default_yaml = tmp_path / "default.yaml"
    default_yaml.write_text(
        "enabled: true\nhermes:\n  allow_data_training_tiers_noninteractive: true\n", encoding="utf-8")
    assert WorkerRuntimeConfig.from_yaml(default_yaml).discovery_cycle_enabled is False


def test_flag_off_v3_loop_wait_regression(tmp_path):
    """The legacy path must be byte-for-byte behaviorally unchanged."""
    from jarvis_foundry.v3_loop import run_one_loop

    class _Router:
        def enforce_directive_routing(self):
            return None

    class _Adapter:
        pass

    database, _ = _cycle_env(tmp_path)
    report = run_one_loop(
        database, loop_id="legacy-wait", adapter=_Adapter(), router=_Router(),
        vault_root=tmp_path / "vault", handoff_dir=tmp_path / "handoffs")
    assert report["outcome"] == "WAITING"
    assert "test-only" in report["reason"]
    cycle_events = [e for e in database.events()
                    if e.event_type == "discovery.cycle_completed"]
    assert not cycle_events  # the unified cycle never ran


# ----------------------------------------------------------------- flag ON

def test_flag_on_lab_loop_observe_to_mission(tmp_path):
    database, repository = _cycle_env(tmp_path)
    candidate_id = _lab_candidate(database)

    report = run_discovery_cycle(
        database, loop_id="v3-lab-1", resource_id="resource-1",
        observe=_observe_stub())

    # UNCONDITIONAL: the flagged lab loop must reach mission creation.
    # (MVP review FAIL round 1: this used to accept WAITING vacuously.)
    assert report.outcome == "COMPILED", report.to_payload()
    completed = [e for e in database.events()
                 if e.event_type == "discovery.cycle_completed"]
    assert len(completed) == 1
    payload = completed[0].payload
    assert payload["loop_id"] == "v3-lab-1"
    assert payload["observed"] == 4
    assert payload["outcome"] == "COMPILED"
    portfolio_row = database.row("discovery_portfolio", "loop_id", "v3-lab-1")
    assert portfolio_row is not None
    assert portfolio_row["policy_version"] == "v3-policy-2026-08-21"
    missions = [e for e in database.events()
                if e.event_type == "mission.created"
                and str(e.payload.get("created_from") or "")
                .startswith("candidate:")]
    assert len(missions) == 1
    assert report.mission_id == missions[0].payload["mission_id"]
    assert report.lineage["candidate_id"] == candidate_id
    assert report.lineage["decision_id"] == report.decision_id
    lineage_events = [e for e in database.events()
                      if e.event_type == "discovery.cycle_lineage_recorded"]
    assert len(lineage_events) == 1
    assert lineage_events[0].payload["mission_id"] == report.mission_id


def test_duplicate_fingerprint_cannot_create_second_mission(tmp_path):
    database, _ = _cycle_env(tmp_path)
    first = _lab_candidate(database, name_dup_check := None) \
        if False else _lab_candidate(database)
    duplicate = generate_candidate(
        database, "mission-seed", "maintenance",
        "FIX the failing NIGHTLY integration!",  # case/punctuation variant
        [{"kind": "ci", "ref": "nightly"}])
    assert duplicate["created"] is False
    assert duplicate["duplicate_of"]

    run_discovery_cycle(database, loop_id="dedup-1",
                        resource_id="resource-1", observe=_observe_stub())
    run_discovery_cycle(database, loop_id="dedup-2",
                        resource_id="resource-1", observe=_observe_stub())
    run_discovery_cycle(database, loop_id="dedup-3",
                        resource_id="resource-1", observe=_observe_stub())

    created_from = [
        e for e in database.events()
        if e.event_type == "mission.created"
        and str(e.payload.get("created_from") or "").startswith("candidate:")
    ]
    objectives = [str(e.payload.get("objective") or "") for e in created_from]
    assert len(objectives) == len(set(objectives)), objectives


def test_stop_blocks_cycle_before_observation(tmp_path):
    database, _ = _cycle_env(tmp_path)
    secret = b"test-secret-32-bytes-minimum!!"
    svc = OwnerControlService(database, secret=secret, owner_id="owner")
    nonce = "stop-nonce-v3cyc"
    sig = sign_command(secret, "STOP_EVERYTHING", "GLOBAL", "halt", nonce)
    svc.apply("STOP_EVERYTHING", target="GLOBAL", exact_text="halt",
              nonce=nonce, signature=sig)

    def _must_not_run(_database):
        raise AssertionError("observation must not run under STOP")

    report = run_discovery_cycle(
        database, loop_id="stopped", resource_id="resource-1",
        observe=_must_not_run)
    assert report.outcome == "STOPPED"
    events = [e.event_type for e in database.events()]
    assert "portfolio.evaluated" not in events


def test_stop_race_after_observation_blocks_scoring_and_materialization(tmp_path):
    database, _ = _cycle_env(tmp_path)
    secret = b"test-secret-32-bytes-minimum!!"
    service = OwnerControlService(database, secret=secret, owner_id="owner")

    def _observe_then_stop(_database):
        nonce = "stop-after-observe"
        signature = sign_command(secret, "STOP_EVERYTHING", "GLOBAL",
                                 "race", nonce)
        service.apply("STOP_EVERYTHING", target="GLOBAL", exact_text="race",
                      nonce=nonce, signature=signature)
        return {"observed": {"vault": 4}, "candidates_created": 1}

    report = run_discovery_cycle(
        database, loop_id="stop-after-observe", resource_id="resource-1",
        observe=_observe_then_stop)
    assert report.outcome == "STOPPED"
    events = [e.event_type for e in database.events()]
    assert "portfolio.evaluated" not in events
    assert "executive.decision_recorded" not in events
    assert not [e for e in database.events()
                if e.event_type == "mission.created"
                and str(e.payload.get("created_from") or "").startswith("candidate:")]


def test_worker_loop_dual_path_flag_on_compiles(tmp_path):
    database, repository = _cycle_env(tmp_path)
    _lab_candidate(database)
    cfg = WorkerRuntimeConfig(
        enabled=True,
        resource_id="resource-1",
        discovery_cycle_enabled=True,
        executive_activation_epoch=EPOCH,
        runtime_state_root=str(tmp_path / "state"),
        stop_sentinel_path=str(tmp_path / "EMERGENCY_STOP"),
        lab_scripted_hermes=True,
    )
    report = run_cycle(
        database, FoundryPaths(tmp_path), cfg,
        loop_id="dual-on", instance_id="inst-on",
        vault_root=tmp_path / "vault", handoff_dir=tmp_path / "handoffs")
    assert report["outcome"] == "PASSED", report
    compiled = [e for e in database.events()
                if e.event_type == "discovery.cycle_completed"
                and e.payload.get("outcome") == "COMPILED"]
    assert len(compiled) == 1 and compiled[0].payload["loop_id"] == "dual-on"
    outcome_rows = database.all("candidate_outcomes")
    assert len(outcome_rows) == 1
    assert outcome_rows[0]["status"] in ("RECORDED", "UNKNOWN")
    assert any(e.event_type == "discovery.cycle_lineage_completed"
               and e.payload["loop_id"] == "dual-on"
               for e in database.events())


def test_worker_loop_flag_off_unchanged_no_cycle_event(tmp_path):
    database, repository = _cycle_env(tmp_path)
    _lab_candidate(database)
    cfg = WorkerRuntimeConfig(
        enabled=True,
        resource_id="resource-1",
        executive_selection_enabled=True,
        executive_activation_epoch=EPOCH,
        executive_min_action_score=0.30,
        runtime_state_root=str(tmp_path / "state"),
        stop_sentinel_path=str(tmp_path / "EMERGENCY_STOP"),
        lab_scripted_hermes=True,
    )
    report = run_cycle(
        database, FoundryPaths(tmp_path), cfg,
        loop_id="dual-off", instance_id="inst-off",
        vault_root=tmp_path / "vault", handoff_dir=tmp_path / "handoffs")
    assert report["outcome"] in ("PASSED", "WAITING")
    assert not [e for e in database.events()
                if e.event_type == "discovery.cycle_completed"]


def test_disable_flag_requires_no_code_revert(tmp_path):
    """Rollback contract: flip the config value; the split path returns."""
    database, repository = _cycle_env(tmp_path)
    cfg_on = WorkerRuntimeConfig(enabled=True, resource_id="resource-1",
                                 discovery_cycle_enabled=True)
    cfg_off = WorkerRuntimeConfig(enabled=True, resource_id="resource-1",
                                  discovery_cycle_enabled=False)
    assert cfg_on.discovery_cycle_enabled and not cfg_off.discovery_cycle_enabled
    report_off = run_cycle(
        database, FoundryPaths(tmp_path), cfg_off,
        loop_id="rollback", instance_id="i")
    assert report_off["outcome"] in ("WAITING", "DISABLED")
