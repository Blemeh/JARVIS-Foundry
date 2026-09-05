from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.workforce import WorkPackage, WorkerSession, create_work_package
from jarvis_foundry.workforce.observation import WorkforceObserver

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "worktree"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "observer@example.invalid")
    _git(root, "config", "user.name", "Workforce Observer")
    (root / "src").mkdir()
    (root / "src" / "base.py").write_text("BASE = True\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "state" / "foundry.db")
    database.initialize()
    return database


def _fixture(
    tmp_path: Path,
    *,
    session_status: str = "HEALTHY",
    result: dict | None = None,
    worktree_path: Path | None = None,
) -> tuple[FoundryDatabase, WorkPackage, WorkerSession, Path]:
    root, source_sha = _repository(tmp_path)
    database = _database(tmp_path)
    package = WorkPackage(
        package_id="package-observation",
        parent_objective_id="objective-observation",
        mission_id="mission-observation",
        step_id="step-observation",
        role="builder",
        capability="worker.code",
        bounded_goal="Implement the bounded observer.",
        acceptance_criteria=("focused observer tests pass",),
        source_sha=source_sha,
        repository_id="fixture-repository",
        branch_name="master",
        worktree_path=str(worktree_path or root),
        allowed_paths=("src",),
        forbidden_paths=("src/private",),
        required_tests=("pytest -q tests/foundry/test_v13_workforce_observation.py",),
    )
    create_work_package(database, package)
    session = WorkerSession(
        session_id="session-observation",
        package_id=package.package_id,
        run_id="run-observation",
        attempt=1,
        framework="hermes",
        provider="opencode_go",
        model="deepseek-v4-flash",
        status=session_status,
        started_at="2026-09-04T11:59:00Z",
        heartbeat_at="2026-09-04T11:59:30Z",
        progress_fingerprint="progress-one",
        result=result or {},
    )
    _append(database, "workforce.session_started", session.to_payload())
    _append(database, "agent_run.started", {
        "run_id": session.run_id,
        "mission_id": package.mission_id,
        "step_id": package.step_id,
        "role": "builder",
        "provider": session.provider,
        "model": session.model,
        "context_manifest_hash": "a" * 64,
        "tool_policy": {},
    })
    return database, package, session, root


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: dict,
    *,
    occurred_at: str = "2026-09-04T12:00:00Z",
) -> None:
    database.append(Event.create(
        event_type,
        payload,
        source_type="test",
        source_id="v13-workforce-observation",
        idempotency_key=f"{event_type}:{database.event_count()}",
        occurred_at=occurred_at,
    ))


def _verification_failed(
    database: FoundryDatabase,
    package: WorkPackage,
    session: WorkerSession,
) -> None:
    verdict_id = f"verdict-{database.event_count()}"
    _append(database, "verification.started", {
        "verdict_id": verdict_id,
        "mission_id": package.mission_id,
        "subject_type": "MISSION",
        "subject_id": package.mission_id,
        "builder_run_id": session.run_id,
        "independence": {},
        "criteria": [],
    })
    _append(database, "verification.failed", {
        "verdict_id": verdict_id,
        "mission_id": package.mission_id,
        "builder_run_id": session.run_id,
    })


@pytest.mark.parametrize(
    ("expected", "session_status", "result", "prepare"),
    [
        ("HEALTHY_PROGRESS", "HEALTHY", {}, lambda db, package, session, root: None),
        ("NO_PROGRESS_STALL", "STALLED", {}, lambda db, package, session, root: None),
        ("PROVIDER_OUTAGE", "FAILED", {}, lambda db, package, session, root: _append(
            db, "provider.dispatch_failed", {
                "run_id": session.run_id, "mission_id": package.mission_id,
                "failure_class": "OUTAGE",
            },
        )),
        ("WRONG_BRANCH", "HEALTHY", {}, lambda db, package, session, root: _git(
            root, "checkout", "-qb", "other-branch",
        )),
        ("SCOPE_ESCAPE", "HEALTHY", {}, lambda db, package, session, root: (
            root / "outside.py"
        ).write_text("outside = True\n", encoding="utf-8")),
        ("GOAL_DRIFT", "HEALTHY", {"goal_drift": True}, lambda db, package, session, root: None),
        ("ENDLESS_ANALYSIS", "HEALTHY", {"analysis_rounds": 3}, lambda db, package, session, root: None),
        ("MANDATORY_TESTS_SKIPPED", "COMPLETED", {"tests_skipped": True}, lambda db, package, session, root: None),
        ("FALSE_COMPLETION", "COMPLETED", {}, lambda db, package, session, root: _verification_failed(
            db, package, session,
        )),
        ("SESSION_DEAD", "FAILED", {}, lambda db, package, session, root: _append(
            db, "agent_run.failed", {
                "run_id": session.run_id, "failure_class": "worker_runtime_error",
            },
        )),
        ("PARTIAL_MUTATION", "FAILED", {}, lambda db, package, session, root: _append(
            db, "worker.crashed", {
                "run_id": session.run_id, "partial_state": {"changed_files": ["src/base.py"]},
            },
        )),
        ("REPEATED_FAILURE", "FAILED", {}, lambda db, package, session, root: (
            _append(db, "agent_run.failed", {
                "run_id": session.run_id, "failure_class": "worker_runtime_error",
            }),
            _verification_failed(db, package, session),
        )),
        ("EXTERNAL_BLOCKER", "BLOCKED", {"external_blocker": "upstream approval pending"}, lambda db, package, session, root: None),
    ],
)
def test_observer_classifies_durable_workforce_evidence(
    tmp_path: Path,
    expected: str,
    session_status: str,
    result: dict,
    prepare,
) -> None:
    database, package, session, root = _fixture(
        tmp_path,
        session_status=session_status,
        result=result,
    )
    prepare(database, package, session, root)

    report = WorkforceObserver(database).observe(now=NOW)

    assert report.scanned == 1
    assert [item.kind for item in report.recorded] == [expected]
    assert report.snapshots[0].kind == expected
    assert report.snapshots[0].fingerprint == report.recorded[0].progress_fingerprint
    assert database.event_count(event_type="workforce.observation_recorded") == 1


def test_observer_detects_a_wrong_declared_repository(tmp_path: Path) -> None:
    root, _source_sha = _repository(tmp_path)
    database, _package, _session, _ = _fixture(
        tmp_path / "fixture",
        worktree_path=root / "not-a-worktree",
    )

    report = WorkforceObserver(database).observe(now=NOW)

    assert report.recorded[0].kind == "WRONG_REPOSITORY"


def test_observer_coalesces_unchanged_snapshots_and_records_progress_delta(
    tmp_path: Path,
) -> None:
    database, _package, session, _root = _fixture(tmp_path)
    observer = WorkforceObserver(database)

    first = observer.observe(now=NOW)
    second = observer.observe(now=NOW)
    changed = replace(session, progress_fingerprint="progress-two")
    _append(database, "workforce.session_state_changed", changed.to_payload())
    third = observer.observe(now=NOW)

    assert len(first.recorded) == 1
    assert second.recorded == ()
    assert second.suppressed == 1
    assert len(third.recorded) == 1
    assert third.recorded[0].progress_fingerprint != first.recorded[0].progress_fingerprint
    assert database.event_count(event_type="workforce.observation_recorded") == 2


def test_durable_heartbeats_drive_stall_and_recovery_without_healthy_noise(
    tmp_path: Path,
) -> None:
    database, _package, session, _root = _fixture(tmp_path)
    observer = WorkforceObserver(database, stall_after_seconds=60)

    first = observer.observe(now="2026-09-04T12:00:00Z")
    _append(
        database,
        "agent_run.heartbeat",
        {"run_id": session.run_id},
        occurred_at="2026-09-04T12:00:30Z",
    )
    heartbeat_only = observer.observe(now="2026-09-04T12:01:00Z")
    stalled = observer.observe(now="2026-09-04T12:02:00Z")
    _append(
        database,
        "agent_run.heartbeat",
        {"run_id": session.run_id},
        occurred_at="2026-09-04T12:02:01Z",
    )
    recovered = observer.observe(now="2026-09-04T12:02:10Z")

    assert [item.kind for item in first.recorded] == ["HEALTHY_PROGRESS"]
    assert heartbeat_only.recorded == ()
    assert [item.kind for item in stalled.recorded] == ["NO_PROGRESS_STALL"]
    assert [item.kind for item in recovered.recorded] == ["HEALTHY_PROGRESS"]
    assert database.event_count(event_type="workforce.observation_recorded") == 3


def test_same_path_content_changes_and_correct_branch_commit_are_progress(
    tmp_path: Path,
) -> None:
    database, _package, _session, root = _fixture(tmp_path)
    observer = WorkforceObserver(database)
    first = observer.observe(now=NOW)

    tracked = root / "src" / "base.py"
    tracked.write_text("BASE = False\n", encoding="utf-8")
    second = observer.observe(now=NOW)
    tracked.write_text("BASE = 'changed-again'\n", encoding="utf-8")
    third = observer.observe(now=NOW)
    _git(root, "add", "src/base.py")
    _git(root, "commit", "-qm", "bounded progress")
    fourth = observer.observe(now=NOW)

    reports = (first, second, third, fourth)
    assert all(report.recorded[0].kind == "HEALTHY_PROGRESS" for report in reports)
    assert len({
        report.recorded[0].progress_fingerprint
        for report in reports
    }) == 4
    assert second.snapshots[0].semantic_state["worktree"]["changed_files"] == [
        "src/base.py"
    ]
    assert fourth.snapshots[0].semantic_state["worktree"]["source_is_ancestor"] is True


def test_policy_route_is_external_blocker_and_fallback_success_clears_outage(
    tmp_path: Path,
) -> None:
    blocked_db, package, session, _root = _fixture(tmp_path / "blocked")
    _append(blocked_db, "provider.route_decided", {
        "run_id": session.run_id,
        "mission_id": package.mission_id,
        "status": "BLOCKED",
        "failure_class": "BLOCKED_POLICY",
        "selected": None,
        "rejected": [{
            "provider": session.provider,
            "failure_class": "BLOCKED_POLICY",
        }],
    })

    blocked = WorkforceObserver(blocked_db).observe(now=NOW)

    healthy_db, package, session, _root = _fixture(tmp_path / "fallback")
    _append(healthy_db, "provider.route_decided", {
        "run_id": session.run_id,
        "mission_id": package.mission_id,
        "status": "FALLBACK",
        "failure_class": None,
        "selected": {"provider": "provider-b", "model": "model-b"},
        "rejected": [{
            "provider": session.provider,
            "failure_class": "OUTAGE",
        }],
    })
    _append(healthy_db, "provider.dispatch_succeeded", {
        "run_id": session.run_id,
        "provider": "provider-b",
        "model": "model-b",
    })

    healthy = WorkforceObserver(healthy_db).observe(now=NOW)

    assert blocked.recorded[0].kind == "EXTERNAL_BLOCKER"
    assert healthy.recorded[0].kind == "HEALTHY_PROGRESS"
    assert healthy.snapshots[0].semantic_state["provider"]["selected_provider"] == (
        "provider-b"
    )


def test_agent_completion_with_not_tested_verdict_marks_required_tests_skipped(
    tmp_path: Path,
) -> None:
    database, _package, session, _root = _fixture(tmp_path)
    _append(database, "agent_run.completed", {
        "run_id": session.run_id,
        "result": {
            "run_id": session.run_id,
            "role": "builder",
            "status": "COMPLETED",
            "verdict": "NOT_TESTED",
            "provider": session.provider,
            "model": session.model,
        },
        "cost": {"usd": 0.0},
    })

    report = WorkforceObserver(database).observe(now=NOW)

    assert report.recorded[0].kind == "MANDATORY_TESTS_SKIPPED"


def test_failed_run_with_observed_git_delta_is_partial_mutation(
    tmp_path: Path,
) -> None:
    database, _package, session, root = _fixture(tmp_path)
    (root / "src" / "partial.py").write_text(
        "PARTIAL = True\n",
        encoding="utf-8",
    )
    _append(database, "agent_run.failed", {
        "run_id": session.run_id,
        "failure_class": "heartbeat_timeout",
    })

    before = _git(root, "status", "--porcelain=v1")
    report = WorkforceObserver(database).observe(now=NOW)
    after = _git(root, "status", "--porcelain=v1")

    assert report.recorded[0].kind == "PARTIAL_MUTATION"
    assert before == after


def test_goal_drift_requires_structured_evidence_not_summary_wording(
    tmp_path: Path,
) -> None:
    database, _package, session, _root = _fixture(
        tmp_path,
        result={"summary": "Discussion mentioned goal drift as a rejected idea."},
    )
    observer = WorkforceObserver(database)

    first = observer.observe(now=NOW)
    changed = replace(session, result={"goal_alignment": False})
    _append(database, "workforce.session_state_changed", changed.to_payload())
    second = observer.observe(now=NOW)

    assert first.recorded[0].kind == "HEALTHY_PROGRESS"
    assert second.recorded[0].kind == "GOAL_DRIFT"


def test_durable_result_evidence_changes_the_semantic_fingerprint(
    tmp_path: Path,
) -> None:
    database, _package, session, _root = _fixture(tmp_path)
    observer = WorkforceObserver(database)

    first = observer.observe(now=NOW)
    changed = replace(session, result={"evidence": ["artifact:first"]})
    _append(database, "workforce.session_state_changed", changed.to_payload())
    second = observer.observe(now=NOW)

    assert second.recorded[0].kind == "HEALTHY_PROGRESS"
    assert (
        first.recorded[0].progress_fingerprint
        != second.recorded[0].progress_fingerprint
    )


def test_other_run_verification_does_not_contaminate_same_mission_session(
    tmp_path: Path,
) -> None:
    database, package, _session, _root = _fixture(
        tmp_path,
        session_status="COMPLETED",
    )
    _append(database, "verification.failed", {
        "verdict_id": "verdict-another-run",
        "mission_id": package.mission_id,
        "builder_run_id": "another-run",
    })

    report = WorkforceObserver(database).observe(now=NOW)

    assert report.recorded[0].kind == "HEALTHY_PROGRESS"
    assert report.snapshots[0].semantic_state["verification"] == "NONE"
