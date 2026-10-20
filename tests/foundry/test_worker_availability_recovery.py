"""Worker-availability recovery: BLOCKED no_eligible_worker missions resume.

A mission BLOCKED on an empty worker route decision re-enters action
selection once the shared eligibility authority reports an eligible
framework. Exactly once per blocking decision, with lineage; missions
blocked for other reasons are never reopened.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.events import Event  # noqa: E402
from jarvis_foundry.runtime._stack import (  # noqa: E402
    worker_framework_eligibility,
)
from jarvis_foundry.workers.mission_bridge import (  # noqa: E402
    WORKER_AVAILABILITY_RESUMED_EVENT,
    resume_worker_blocked_missions,
    worker_blocked_mission_ids,
)


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _block_on_no_worker(database: FoundryDatabase,
                         mission_id: str = "mission-1") -> None:
    database.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": "o",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["worker step verified and accepted"],
         "next_action": "execute: fix CI"},
        source_type="test", source_id="recovery-test",
        idempotency_key=f"recovery-test:{mission_id}:created",
    ))
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": mission_id, "state": "ORIENTING",
         "run_id": "run-9", "next_action": "execute: fix CI"},
        source_type="test", source_id="recovery-test",
        idempotency_key=f"recovery-test:{mission_id}:orient",
    ))
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": mission_id, "state": "ACTION_SELECTION",
         "run_id": "run-9", "next_action": "execute: fix CI"},
        source_type="test", source_id="recovery-test",
        idempotency_key=f"recovery-test:{mission_id}:select",
    ))
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": mission_id, "state": "READY",
         "run_id": "run-9", "next_action": "execute: fix CI"},
        source_type="test", source_id="recovery-test",
        idempotency_key=f"recovery-test:{mission_id}:ready",
    ))
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": mission_id, "state": "EXECUTING",
         "run_id": "run-9", "next_action": "execute: fix CI"},
        source_type="test", source_id="recovery-test",
        idempotency_key=f"recovery-test:{mission_id}:executing",
    ))
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": mission_id, "state": "BLOCKED",
         "run_id": "run-9", "next_action": "execute: fix CI"},
        source_type="test", source_id="recovery-test",
        idempotency_key=f"recovery-test:{mission_id}:blocked",
    ))
    database.append(Event.create(
        "worker.route_decided",
        {"mission_id": mission_id, "run_id": "run-9", "role": "builder",
         "eligible_workers": [], "selected_worker": "",
         "reason": "no eligible worker (all unavailable or vetoed)"},
        source_type="supervisor", source_id="hybrid-worker-bridge",
        idempotency_key=f"worker.route_decided:run-9:{mission_id}",
    ))


# 17. BLOCKED no_eligible_worker mission resumes when workers are READY.
def test_worker_blocked_mission_resumes(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _block_on_no_worker(database)
    resumed = resume_worker_blocked_missions(
        database, eligible_frameworks={"hermes": True, "openhands": False},
        source_id="recovery-test")
    assert resumed == ("mission-1",)
    row = database.row("missions", "mission_id", "mission-1")
    assert row is not None and row["state"] == "ACTION_SELECTION"
    assert row["next_action"] == "execute: fix CI"
    assert database.event_count(
        event_type=WORKER_AVAILABILITY_RESUMED_EVENT) == 1


# 18. Recovery is exactly once per blocking decision.
def test_recovery_exactly_once(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _block_on_no_worker(database)
    first = resume_worker_blocked_missions(
        database, eligible_frameworks={"hermes": True},
        source_id="recovery-test")
    assert first == ("mission-1",)
    # The mission left BLOCKED; a replay that re-blocks on the SAME
    # decision must not duplicate the resume.
    for state in ("READY", "EXECUTING", "BLOCKED"):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": "mission-1", "state": state,
             "run_id": "run-9", "next_action": "execute: fix CI"},
            source_type="test", source_id="recovery-test",
            idempotency_key=f"recovery-test:mission-1:re-{state}",
        ))
    second = resume_worker_blocked_missions(
        database, eligible_frameworks={"hermes": True},
        source_id="recovery-test")
    assert second == ()
    assert database.event_count(
        event_type=WORKER_AVAILABILITY_RESUMED_EVENT) == 1


# 19. Lineage links the blocked decision to the resumed work.
def test_recovery_lineage(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _block_on_no_worker(database)
    resume_worker_blocked_missions(
        database, eligible_frameworks={"hermes": True},
        source_id="recovery-test")
    payloads = [
        event.payload for event in database.events(
            event_types=(WORKER_AVAILABILITY_RESUMED_EVENT,))]
    assert payloads[0]["mission_id"] == "mission-1"
    assert payloads[0]["blocked_run_id"] == "run-9"
    assert "no eligible worker" in payloads[0]["blocked_reason"]
    assert payloads[0]["eligible_frameworks"] == ["hermes"]


# 20. Restart/replay does not duplicate recovery (fresh handle, same DB).
def test_replay_no_duplicate(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _block_on_no_worker(database)
    resume_worker_blocked_missions(
        database, eligible_frameworks={"hermes": True},
        source_id="recovery-test")
    reopened = FoundryDatabase(database.path)
    assert resume_worker_blocked_missions(
        reopened, eligible_frameworks={"hermes": True},
        source_id="recovery-test") == ()


# 21. Unrelated BLOCKED reasons are NOT reopened.
def test_unrelated_block_untouched(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.append(Event.create(
        "mission.created",
        {"mission_id": "mission-stop", "objective": "o",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["worker step verified and accepted"]},
        source_type="test", source_id="recovery-test",
        idempotency_key="recovery-test:mission-stop:created",
    ))
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-stop", "state": "ORIENTING",
         "run_id": "run-1"},
        source_type="test", source_id="recovery-test",
        idempotency_key="recovery-test:mission-stop:orient",
    ))
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-stop", "state": "ACTION_SELECTION",
         "run_id": "run-1"},
        source_type="test", source_id="recovery-test",
        idempotency_key="recovery-test:mission-stop:select",
    ))
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-stop", "state": "READY",
         "run_id": "run-1"},
        source_type="test", source_id="recovery-test",
        idempotency_key="recovery-test:mission-stop:ready",
    ))
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-stop", "state": "EXECUTING",
         "run_id": "run-1"},
        source_type="test", source_id="recovery-test",
        idempotency_key="recovery-test:mission-stop:executing",
    ))
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-stop", "state": "BLOCKED",
         "run_id": "run-1"},
        source_type="test", source_id="recovery-test",
        idempotency_key="recovery-test:mission-stop:blocked",
    ))
    assert worker_blocked_mission_ids(database) == ()
    assert resume_worker_blocked_missions(
        database, eligible_frameworks={"hermes": True},
        source_id="recovery-test") == ()
    row = database.row("missions", "mission_id", "mission-stop")
    assert row is not None and row["state"] == "BLOCKED"


# 22. No eligible framework means truthful WAIT (silent no-op).
def test_no_eligible_framework_is_noop(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _block_on_no_worker(database)
    assert resume_worker_blocked_missions(
        database, eligible_frameworks={"hermes": False},
        source_id="recovery-test") == ()
    row = database.row("missions", "mission_id", "mission-1")
    assert row is not None and row["state"] == "BLOCKED"


# Eligibility authority parity with the stack build rule.
def test_eligibility_requires_evidence(tmp_path: Path) -> None:
    from types import SimpleNamespace

    assert worker_framework_eligibility(SimpleNamespace(
        lab_scripted_hermes=False,
        hermes={"enabled": True},
        openhands={"enabled": False},
        readiness={"hermes": {"state": "READY",
                              "evidence": ["probe"]}})) == {
        "hermes": True, "openhands": False}
    assert worker_framework_eligibility(SimpleNamespace(
        lab_scripted_hermes=False,
        hermes={"enabled": True},
        openhands={"enabled": False},
        readiness={"hermes": {"state": "READY", "evidence": []}})) == {
        "hermes": False, "openhands": False}
    assert worker_framework_eligibility(SimpleNamespace(
        lab_scripted_hermes=False,
        hermes={"enabled": False},
        openhands={"enabled": False},
        readiness={"hermes": {"state": "READY",
                              "evidence": ["probe"]}})) == {
        "hermes": False, "openhands": False}


# 23. Resume commits both writes atomically: the single-event append
#     path must never be used (a crash between two separate appends
#     would burn the exactly-once key while the mission stays BLOCKED).
def test_resume_uses_atomic_batch(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _block_on_no_worker(database)
    calls: list = []
    original_append = database.append

    def guarded_append(event):
        calls.append(event.event_type)
        return original_append(event)

    database.append = guarded_append  # type: ignore[method-assign]
    try:
        resumed = resume_worker_blocked_missions(
            database, eligible_frameworks={"hermes": True},
            source_id="recovery-test")
    finally:
        database.append = original_append  # type: ignore[method-assign]
    assert resumed == ("mission-1",)
    assert calls == [], calls
    assert database.event_count(
        event_type=WORKER_AVAILABILITY_RESUMED_EVENT) == 1
    row = database.row("missions", "mission_id", "mission-1")
    assert row is not None and row["state"] == "ACTION_SELECTION"


# 24. The Hermes adapter prompt carries the contract's mandatory
#     deliverables (live 2026-09-30: three runs failed verification on
#     an artifact the model was never told to write).
def test_adapter_prompt_lists_expected_artifacts() -> None:
    from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
    from jarvis_foundry.workers.contracts import WorkerStepContract

    contract = WorkerStepContract(
        run_id="run-x", mission_id="mission-x", step_id="discovery-execute",
        attempt=1, role="builder", objective="fix CI",
        instructions="fix the workflow",
        working_root=None, allowed_paths=(), forbidden_paths=(),
        provider="opencode_go", model="muse-spark-1.3-contributor",
        duration_seconds=60, budget_usd=0.0, task_signals=(),
        expected_artifacts=(".jarvis/discovery/investigation-abc.md",),
        verification_criteria=("workflow parses",),
        required_tests=("pytest -q",),
        package_id="p", package_hash="h", attempt_id="a",
    )
    block = HermesWorkerAdapter.contract_requirements_block(contract)
    assert ".jarvis/discovery/investigation-abc.md" in block
    assert "MANDATORY" in block
    assert "workflow parses" in block
    assert "pytest -q" in block

    empty = WorkerStepContract(
        run_id="run-y", mission_id="mission-y", step_id="s",
        attempt=1, role="builder", objective="o",
        instructions="do it",
        working_root=None, allowed_paths=(), forbidden_paths=(),
        provider="opencode_go", model="muse-spark-1.3-contributor",
        duration_seconds=60, budget_usd=0.0, task_signals=(),
        expected_artifacts=(), verification_criteria=(),
        required_tests=(),
        package_id="p", package_hash="h", attempt_id="a",
    )
    assert HermesWorkerAdapter.contract_requirements_block(empty) == ""


# 25. Out-of-scope artifact entries are never rendered as CREATE
#     directives (review 2026-09-30: absolute/escape entries must be
#     report-don't-write lines, never write instructions).
def test_adapter_block_scopes_artifact_paths() -> None:
    from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
    from jarvis_foundry.workers.contracts import WorkerStepContract

    def _contract(*artifacts: str) -> WorkerStepContract:
        return WorkerStepContract(
            run_id="run-s", mission_id="mission-s", step_id="s",
            attempt=1, role="builder", objective="o",
            instructions="do it",
            working_root=None, allowed_paths=(), forbidden_paths=(),
            provider="opencode_go", model="muse-spark-1.3-contributor",
            duration_seconds=60, budget_usd=0.0, task_signals=(),
            expected_artifacts=tuple(artifacts),
            verification_criteria=(), required_tests=(),
            package_id="p", package_hash="h", attempt_id="a",
        )

    block = HermesWorkerAdapter.contract_requirements_block(
        _contract(".jarvis/discovery/ok.md", "/tmp/stale.md",
                 "C:/abs/win.md", "../escape.md",
                 ".. /win-strip.md", ".. ./win-strip2.md",
                 "~/evil.md", "~user/evil.md",
                 "$HOME/evil.md", "%USERPROFILE%/evil.md"))
    assert "- .jarvis/discovery/ok.md" in block
    assert "MANDATORY DELIVERABLES" in block
    assert "OUT-OF-SCOPE" in block
    # Absolute/escape entries appear ONLY in the report section, never
    # adjacent to a CREATE directive.
    create_idx = block.index("CREATE it inside")
    for bad in ("/tmp/stale.md", "C:/abs/win.md", "../escape.md",
                ".. /win-strip.md", ".. ./win-strip2.md",
                "~/evil.md", "~user/evil.md",
                "$HOME/evil.md", "%USERPROFILE%/evil.md"):
        assert block.index(bad) > create_idx
