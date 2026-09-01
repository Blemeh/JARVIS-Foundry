"""Focused V10 EXIT authority and truth-boundary regressions."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from jarvis_foundry.capabilities import registry as legacy_registry
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive import autonomy
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    VerifyVerdict,
    execute_action,
    record_outcome,
    seed_builtins,
)
from jarvis_foundry.control.broker import emergency_stop_reason
from jarvis_foundry.briefing_items import event_to_item
from jarvis_foundry.executive.calibration import outcome_from_mission
from jarvis_foundry.live_ingestion import run_ingestion_cycle
from jarvis_foundry.mission.verification import (
    record_verification_acceptance,
    record_verification_outcome,
    start_verification,
)
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.world_model.procedures import detect_procedures

from worker_test_helpers import mission_database


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _append_raw_pass(database: FoundryDatabase, verdict_id: str) -> Event:
    event = Event.create(
        "verification.passed",
        {"verdict_id": verdict_id, "mission_id": "mission-a"},
        source_type="test",
        source_id="v10-exit-audit",
        idempotency_key=f"verification.passed:{verdict_id}",
    )
    database.append(event)
    return event


def test_executive_fabric_ids_do_not_consult_legacy_registry(
    tmp_path: Path, monkeypatch,
) -> None:
    database = _db(tmp_path)
    seed_builtins(database)
    record_outcome(
        database,
        "cap.file.write",
        success=True,
        verified=VerifyVerdict.MATCH,
        note="audit proof",
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("legacy registry must not govern Fabric ids")

    monkeypatch.setattr(legacy_registry, "discover_capabilities", forbidden)
    assert autonomy._operational_capability_blockers(
        database, "cap.file.write",
    ) == ()


def test_fabric_mutations_use_one_effect_identity_and_independent_github_readback(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)
    owned = tmp_path / "owned"
    context = ExecutorContext(database=database, owned_root=owned)
    intent = TaskIntent(task_id="mission-v10-audit", required_actions=("file.write",))
    seed_builtins(database)

    first = execute_action(
        database, context, intent, "file.write",
        {"target": "once.txt", "content": "one"},
    )
    second = execute_action(
        database, context, intent, "file.write",
        {"target": "once.txt", "content": "one"},
    )
    effect_id = first.receipt["effect_id"]
    journal = [
        event for event in database.events()
        if event.payload.get("action_key") == effect_id
        and event.event_type in {"system.action_started", "system.action_completed"}
    ]
    assert first.success is True
    assert second.success is True
    assert second.claimed_effect["replayed"] is True
    assert [event.event_type for event in journal] == [
        "system.action_started", "system.action_completed",
    ]
    assert (owned / "once.txt").read_text(encoding="utf-8") == "one"

    repository = owned / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "audit@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "V10 Audit"], cwd=repository, check=True)
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    git_payload = {
        "worktree": str(repository), "existing": True,
        "paths": ["tracked.txt"], "message": "audit commit",
    }
    git_first = execute_action(
        database, context, intent, "git.commit", git_payload,
    )
    git_second = execute_action(
        database, context, intent, "git.commit", git_payload,
    )
    git_effect_id = git_first.receipt["effect_id"]
    git_journal = [
        event for event in database.events()
        if event.payload.get("action_key") == git_effect_id
        and event.event_type in {"system.action_started", "system.action_completed"}
    ]
    assert git_first.success is True
    assert git_second.success is True
    assert git_second.claimed_effect["replayed"] is True
    assert len(git_journal) == 2

    calls: list[str] = []

    def github_write(endpoint: str, _payload: dict) -> tuple[int, bytes]:
        calls.append(endpoint)
        return 201, json.dumps({
            "number": 9,
            "html_url": "https://github.test/owner/repo/issues/9",
            "title": "no independent readback",
            "body": "audit",
        }).encode("utf-8")

    github_context = ExecutorContext(
        database=database,
        owned_root=owned,
        github_write=github_write,
    )
    github_payload = {
        "owner": "owner", "repo": "repo",
        "title": "no independent readback", "body": "audit",
    }
    github_first = execute_action(
        database, github_context, intent,
        "github.issue.create", github_payload,
    )
    github_second = execute_action(
        database, github_context, intent,
        "github.issue.create", github_payload,
    )
    assert github_first.claimed.value == "COMPLETED"
    assert github_first.verified is VerifyVerdict.MISMATCH
    assert github_first.success is False
    assert github_first.receipt["state"] == "FAILED"
    assert github_second.success is False
    assert len(calls) == 1


def test_stop_sentinel_clear_cannot_override_durable_owner_stop(tmp_path: Path) -> None:
    database = _db(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    directive = Event.create(
        "owner_directive.recorded",
        {
            "directive_id": "owner-stop-audit",
            "command_type": "STOP_EVERYTHING",
            "target": "GLOBAL",
            "exact_text": "audit stop",
        },
        source_type="owner",
        source_id="v10-exit-audit",
        idempotency_key="owner_directive.recorded:owner-stop-audit",
    )
    database.append(directive)
    EmergencyStop(stop_path).activate("local mirror", source="hotkey")
    assert emergency_stop_reason(database, stop_path, identities={"mission-audit"})
    assert EmergencyStop(stop_path).clear() is True
    reason = emergency_stop_reason(database, stop_path, identities={"mission-audit"})
    assert reason is not None and "STOP_EVERYTHING" in reason

    database.append(Event.create(
        "owner_directive.revoked",
        {"directive_id": "owner-stop-audit"},
        source_type="owner",
        source_id="v10-exit-audit",
        idempotency_key="owner_directive.revoked:owner-stop-audit",
    ))
    assert emergency_stop_reason(database, stop_path, identities={"mission-audit"}) is None


def test_raw_verifier_pass_cannot_be_final_truth_or_learning_input(tmp_path: Path) -> None:
    database, _repository = mission_database(tmp_path)
    raw = _append_raw_pass(database, "raw-a")
    _append_raw_pass(database, "raw-b")
    _append_raw_pass(database, "raw-c")

    assert event_to_item(raw, 1) is None
    ingestion = run_ingestion_cycle(database, sources=["system"])
    assert ingestion["system"]["claims_created"] == 0
    assert detect_procedures(database) == []

    start_verification(
        database,
        verdict_id="accepted-a",
        mission_id="mission-1",
        step_id="step-a",
        builder_run_id="builder-a",
        verifier_run_id="verifier-a",
        independence={"session_independent": True},
        criteria=["audit"],
    )
    record_verification_outcome(
        database,
        verdict_id="accepted-a",
        outcome="PASS",
        findings=[],
        evidence={"audit": True},
    )
    pending = outcome_from_mission(database, "mission-1")
    assert pending["outcome"] != "PASSED"

    record_verification_acceptance(
        database,
        verdict_id="accepted-a",
        mission_id="mission-1",
        step_id="step-a",
        builder_run_id="builder-a",
        verifier_run_id="verifier-a",
        checks={"audit": "ok"},
    )
    # A newer preliminary PASS cannot inherit an older acceptance receipt.
    start_verification(
        database,
        verdict_id="raw-latest",
        mission_id="mission-1",
        step_id="step-a",
        builder_run_id="builder-latest",
        verifier_run_id="verifier-latest",
        independence={"session_independent": True},
        criteria=["audit"],
    )
    record_verification_outcome(
        database,
        verdict_id="raw-latest",
        outcome="PASS",
        findings=[],
        evidence={"audit": True},
    )
    for verdict_id in ("raw-b", "raw-c"):
        record_verification_acceptance(
            database,
            verdict_id=verdict_id,
            mission_id="mission-a",
            step_id="audit",
            builder_run_id=f"builder-{verdict_id}",
            verifier_run_id=f"verifier-{verdict_id}",
            checks={"audit": "ok"},
        )
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-1", "state": "COMPLETE"},
        source_type="supervisor", source_id="v10-exit-audit",
        idempotency_key="mission.state_changed:mission-1:COMPLETE:audit",
    ))
    database.append(Event.create(
        "mission.completed",
        {"mission_id": "mission-1", "outcome": "PASSED"},
        source_type="supervisor", source_id="v10-exit-audit",
        idempotency_key="mission.completed:mission-1:audit",
    ))
    final = outcome_from_mission(database, "mission-1")
    assert final["outcome"] != "PASSED"
    record_verification_acceptance(
        database,
        verdict_id="raw-latest",
        mission_id="mission-1",
        step_id="step-a",
        builder_run_id="builder-latest",
        verifier_run_id="verifier-latest",
        checks={"audit": "ok"},
    )
    final = outcome_from_mission(database, "mission-1")
    assert final["outcome"] == "PASSED"
    assert len(detect_procedures(database)) == 1
