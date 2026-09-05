from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Callable

import pytest

import jarvis_foundry.workforce.proving as proving_module
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import STARTED, effect_state
from jarvis_foundry.events import Event, payload_hash
from jarvis_foundry.mission.contracts import MissionStepContract
from jarvis_foundry.workforce.convergence import (
    PackageConvergenceEvidence,
    analyze_convergence,
    record_convergence_decision,
)
from jarvis_foundry.workforce.contracts import WorkPackage, WorkerSession
from jarvis_foundry.workforce.proving import (
    PROOF_DATE,
    PROOF_SCHEMA,
    WorkforceLongRunReport,
    _build_integration_verification_package,
    _commit_branch,
    _create_integration_candidate,
    _git,
    _objective_state,
    _record_integration_candidate,
    _record_objective_completion,
    run_long_run_proof,
)
from jarvis_foundry.workforce.state import (
    create_work_package,
    transition_work_package,
)

_COMPLETION_COMMAND_EVENTS = frozenset({
    "workforce.objective_completion_recorded",
})


def test_recovery_evidence_is_scoped_to_the_requested_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    package = WorkPackage(
        package_id="proof-target",
        parent_objective_id="objective-proof",
        role="builder",
        capability="write_registered_worktree_files",
        bounded_goal="Prove package-scoped recovery evidence.",
        acceptance_criteria=("the scoped recovery is recorded",),
        source_sha="a" * 40,
        repository_id="proof-repository",
        branch_name="proof/target",
        worktree_path=str(tmp_path / "worktree"),
        allowed_paths=("src/proof.txt",),
        mission_id="mission-proof-target",
        step_id="step-proof-target",
    )
    monkeypatch.setattr(
        proving_module,
        "reap_stale_role_runs",
        lambda _database: [
            "workforce:proof-target:a1",
            "workforce:proof-other:a1",
        ],
    )
    monkeypatch.setattr(
        proving_module,
        "recover_stale_mission_state",
        lambda _database: [
            "mission-proof-other",
            "mission-proof-target",
        ],
    )

    assert proving_module._recover_dead_role(
        database,
        package,
        "workforce:proof-target:a1",
    ) == (
        ["workforce:proof-target:a1"],
        ["mission-proof-target"],
    )


def test_integration_verification_contract_uses_bounded_exclusion_criteria(
    tmp_path: Path,
) -> None:
    excluded = tuple(
        {
            "package_id": f"excluded-{index}",
            "classification": "CONFLICTING",
            "base_sha": "a" * 40,
            "result_sha": str(index) * 40,
            "changed_paths": [f"src/excluded-{index}.txt"],
        }
        for index in range(1, 4)
    )

    package, contract = _build_integration_verification_package(
        tmp_path,
        integration_sha="f" * 40,
        integrated_artifacts=("src/integrated.txt",),
        excluded_deltas=excluded,
    )

    exclusion_criteria = tuple(
        criterion
        for criterion in contract.verification_criteria
        if criterion.startswith("classified excluded delta remains absent:")
    )
    assert package.acceptance_criteria == contract.verification_criteria
    assert len(exclusion_criteria) == len(excluded)
    assert all(len(criterion) <= 512 for criterion in exclusion_criteria)
    assert all(
        f"package_id=excluded-{index}" in exclusion_criteria[index - 1]
        for index in range(1, 4)
    )


@pytest.fixture(scope="module")
def long_run_report(
    tmp_path_factory: pytest.TempPathFactory,
) -> WorkforceLongRunReport:
    return run_long_run_proof(
        tmp_path_factory.mktemp("v13-long-run") / "proof"
    )


def _replay_database(
    report: WorkforceLongRunReport,
    path: Path,
    *,
    transform: Callable[[Event], Event | None] | None = None,
) -> FoundryDatabase:
    source = FoundryDatabase(report.database_path)
    events: list[Event] = []
    for event in source.events(readonly=True):
        replayed = transform(event) if transform is not None else event
        if replayed is not None:
            events.append(replayed)
    database = FoundryDatabase(path)
    database.initialize()
    database.append_events(events)
    return database


def test_v13_workforce_long_run_proof(
    long_run_report: WorkforceLongRunReport,
) -> None:
    report = long_run_report
    payload = json.loads(
        Path(report.report_path).read_text(encoding="utf-8")
    )

    assert payload == report.to_payload()
    assert (
        report.schema
        == PROOF_SCHEMA
        == "jarvis.workforce.long-run-proof.v2"
    )
    assert report.proof_date == PROOF_DATE == "2026-09-04"
    assert report.duration_seconds < 240

    assert report.overlap["peak_concurrency"] >= 2
    assert len(report.overlap["live_run_ids"]) == 2
    assert {
        item["launch_kind"] for item in report.overlap["processes"]
    } == {"SEALED_PACKAGE_RUNNER"}
    assert all(item["alive"] for item in report.overlap["processes"])

    assert report.recovery["before_category"] == "DEAD"
    assert report.recovery["reconciliation_actions"] == ["RECONCILED"]
    assert report.recovery["after_category"] == "TERMINAL"
    assert report.recovery["killed_pids"]
    assert report.recovery["role_runs_reaped"] == [
        "workforce:proof-repair-runner:a1"
    ]
    assert report.recovery["missions_recovered"] == [
        "mission-proof-repair-runner"
    ]

    incidents = {
        item.expected_kind: item.observed_kind
        for item in report.incidents
    }
    assert incidents == {
        "ENDLESS_ANALYSIS": "ENDLESS_ANALYSIS",
        "FALSE_COMPLETION": "FALSE_COMPLETION",
        "GOAL_DRIFT": "GOAL_DRIFT",
        "MANDATORY_TESTS_SKIPPED": "MANDATORY_TESTS_SKIPPED",
        "PARTIAL_MUTATION": "PARTIAL_MUTATION",
        "PROVIDER_OUTAGE": "PROVIDER_OUTAGE",
        "SCOPE_ESCAPE": "SCOPE_ESCAPE",
    }

    interventions = {
        item.action: item for item in report.interventions
    }
    assert set(interventions) == {"REASSIGN", "REPAIR"}
    assert all(
        item.outcome == "READY_FOR_RELAUNCH"
        and item.sealed_relaunch
        and item.replayed_without_duplicate
        and item.request_events == 1
        and item.completion_events == 1
        for item in interventions.values()
    )

    assert report.stop["blocked_new_mutation"] is True
    assert report.stop["event_count_unchanged"] is True
    assert report.stop["termination_outcome"] == "TERMINATED"
    assert report.stop["terminated_owned_process"] is True
    assert report.stop["package_status"] == "CANCELLED"

    classifications = report.convergence["classifications"]
    assert classifications["proof-overlap-a"] == "CONFLICTING"
    assert classifications["proof-overlap-b"] == "CONFLICTING"
    assert classifications["proof-weak"] == "FAILED_REJECTED"
    assert classifications["proof-repair-runner"] == (
        "UNIQUE_ACCEPTED_DELTA"
    )
    assert classifications["proof-reassign-runner"] == (
        "UNIQUE_ACCEPTED_DELTA"
    )

    integration = report.integration
    assert integration["target"] == "proof/integration"
    assert integration["base_sha"] != integration["integration_sha"]
    assert integration["commit_count"] == 1
    assert integration["target_repository_clean"] is True
    assert set(integration["included_packages"]) == {
        "proof-reassign-runner",
        "proof-repair-runner",
    }
    assert {
        item["package_id"] for item in integration["included_deltas"]
    } == set(integration["included_packages"])
    assert set(integration["excluded_packages"]) == {
        "proof-overlap-a",
        "proof-overlap-b",
        "proof-weak",
    }
    assert set(integration["conflicting_packages"]) == {
        "proof-overlap-a",
        "proof-overlap-b",
    }
    assert integration["rejected_packages"] == ["proof-weak"]
    assert set(integration["changed_paths"]) == {
        "src/proof-reassign-runner.txt",
        "src/proof-repair-runner.txt",
    }
    assert {
        path
        for item in integration["excluded_deltas"]
        for path in item["changed_paths"]
    }.isdisjoint(integration["changed_paths"])

    verifier = integration["verifier"]
    assert integration["independently_verified"] is True
    assert verifier["package_id"] == "proof-integration-verifier"
    assert verifier["source_type"] == "supervisor"
    assert verifier["source_id"] == "mission-loop"
    assert verifier["started_source_type"] == "supervisor"
    assert verifier["started_source_id"] == "mission-loop"
    assert verifier["decision"] == "ACCEPT"
    assert verifier["verifier_run_id"] != verifier["builder_run_id"]
    assert verifier["binding_head"] == integration["integration_sha"]
    assert verifier["worktree_head"] == integration["integration_sha"]
    assert verifier["binding_hash"]
    assert verifier["context_manifest_hash"]
    assert verifier["worktree_clean"] is False
    assert verifier["verification_delta"] == [
        ".jarvis-integration-verification.txt"
    ]
    assert (
        integration["convergence_sequence"]
        < integration["candidate_sequence"]
        < verifier["started_sequence"]
        < verifier["receipt_sequence"]
    )
    expected_completion_order = [
        "PACKAGE_RESULTS_ACCEPTED",
        "CONVERGENCE_RECORDED",
        "INTEGRATION_COMMIT_CREATED",
        "INTEGRATION_CANDIDATE_RECORDED",
        "INTEGRATION_VERIFICATION_STARTED",
        "INTEGRATION_ACCEPTED",
        "OBJECTIVE_COMPLETE",
    ]
    assert integration["completion_order"] == expected_completion_order

    assert report.restart["package_count"] == (
        report.restart["package_creation_events"]
    )
    assert report.restart["package_count"] == (
        report.restart["worktree_preparation_events"]
    )
    assert report.restart["duplicate_effect_events"] == []
    assert report.restart["duplicate_issue_codes"] == []
    assert len(report.restart["completed_effect_actions"]) >= 2

    assert report.objective.before_status == "INCOMPLETE"
    assert report.objective.status == "COMPLETE"
    assert report.objective.independently_verified is True
    assert set(report.objective.accepted_packages) == {
        "proof-reassign-runner",
        "proof-repair-runner",
    }
    assert all(run_id.endswith(":a2") for run_id in report.objective.stronger_run_ids)
    assert len(set(report.objective.verifier_run_ids)) == 2
    assert (
        report.objective.integration_sha
        == integration["integration_sha"]
    )
    assert (
        report.objective.integration_verifier_run_id
        == verifier["verifier_run_id"]
    )
    assert (
        report.objective.integration_receipt_id
        == verifier["receipt_id"]
    )
    assert (
        report.objective.completion_event_id
        == integration["completion_event_id"]
    )
    assert (
        report.objective.completion_sequence
        == integration["completion_sequence"]
    )
    assert (
        verifier["receipt_sequence"]
        < report.objective.completion_sequence
    )
    assert list(report.objective.completion_order) == (
        expected_completion_order
    )
    assert report.objective.blockers == ()

    assert report.event_counts["verification.accepted"] >= 3
    assert report.event_counts["workforce.convergence_recorded"] == 1
    assert (
        report.event_counts["workforce.integration_candidate_created"]
        == 1
    )
    assert (
        report.event_counts["workforce.objective_completion_recorded"]
        == 1
    )
    assert (
        report.event_counts.get(
            "workforce.objective_completion_authorized",
            0,
        )
        == 0
    )
    assert report.limitations


def test_objective_completion_replays_one_durable_event(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(long_run_report.database_path)
    requested = ("proof-repair-runner", "proof-reassign-runner")
    fabricated_mapping = {
        "integration_sha": "f" * 40,
        "included_packages": list(requested),
        "independently_verified": True,
        "verifier": {
            "source_type": "supervisor",
            "source_id": "mission-loop",
            "decision": "ACCEPT",
            "builder_run_id": "fabricated-builder",
            "verifier_run_id": "fabricated-verifier",
            "binding_head": "f" * 40,
        },
        "completion_order": ["OBJECTIVE_COMPLETE"],
    }

    first = _objective_state(
        database,
        requested,
        integration=fabricated_mapping,
    )
    replayed = _replay_database(
        long_run_report,
        tmp_path / "portable-objective-replay.db",
    )
    second = _objective_state(replayed, requested)

    assert first["status"] == second["status"] == "COMPLETE"
    assert first["independently_verified"] is True
    assert second["independently_verified"] is True
    assert first["integration_sha"] == second["integration_sha"]
    assert first["integration_sha"] != fabricated_mapping["integration_sha"]
    assert (
        first["completion_event_id"]
        == second["completion_event_id"]
        == long_run_report.objective.completion_event_id
    )
    assert (
        replayed.event_count(
            event_type="workforce.objective_completion_recorded",
            readonly=True,
        )
        == 1
    )
    completion = next(
        event
        for event in replayed.events(
            event_types=("workforce.objective_completion_recorded",),
            readonly=True,
        )
    )
    receipt = next(
        event
        for event in replayed.events(
            event_types=("verification.accepted",),
            readonly=True,
        )
        if event.event_id == long_run_report.objective.integration_receipt_id
    )
    assert completion.causation_id == receipt.event_id
    assert receipt.source_type == "supervisor"
    assert receipt.source_id == "mission-loop"
    assert receipt.payload["decision"] == "ACCEPT"
    assert (
        completion.payload["package_binding_heads"]
        == completion.payload["package_result_shas"]
    )
    assert (
        completion.payload["convergence_event_id"]
        == long_run_report.integration["convergence_event_id"]
    )


def test_builder_self_asserted_completion_is_rejected(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
) -> None:
    source = FoundryDatabase(long_run_report.database_path)
    completion = next(
        event
        for event in source.events(
            event_types=("workforce.objective_completion_recorded",),
            readonly=True,
        )
    )
    database = _replay_database(
        long_run_report,
        tmp_path / "builder-self-asserted-completion.db",
        transform=lambda event: (
            None
            if event.event_type in _COMPLETION_COMMAND_EVENTS
            else event
        ),
    )
    database.append(replace(
        completion,
        source_type="worker",
        source_id="builder-self-assertion",
    ))

    result = _objective_state(
        database,
        ("proof-repair-runner", "proof-reassign-runner"),
    )

    assert result["packages_verified"] is True
    assert result["integration_verified"] is True
    assert result["status"] == "INCOMPLETE"
    assert result["completion_event_id"] is None
    assert result["blockers"] == [
        "objective:completion-verifier-binding-invalid"
    ]


def test_completion_without_exact_verifier_causation_is_rejected(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
) -> None:
    source = FoundryDatabase(long_run_report.database_path)
    completion = next(
        event
        for event in source.events(
            event_types=("workforce.objective_completion_recorded",),
            readonly=True,
        )
    )
    database = _replay_database(
        long_run_report,
        tmp_path / "completion-with-wrong-verifier-causation.db",
        transform=lambda event: (
            None
            if event.event_type in _COMPLETION_COMMAND_EVENTS
            else event
        ),
    )
    database.append(replace(
        completion,
        causation_id="forged-verifier-receipt",
    ))

    result = _objective_state(
        database,
        ("proof-repair-runner", "proof-reassign-runner"),
    )

    assert result["packages_verified"] is True
    assert result["integration_verified"] is True
    assert result["status"] == "INCOMPLETE"
    assert result["completion_event_id"] is None
    assert result["blockers"] == [
        "objective:completion-verifier-binding-invalid"
    ]


def test_fabricated_mapping_without_durable_integration_cannot_complete(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
) -> None:
    source = FoundryDatabase(long_run_report.database_path)
    before_candidate = int(
        long_run_report.integration["candidate_sequence"]
    )
    with source.connect(readonly=True) as connection:
        prefix_ids = {
            str(row["event_id"])
            for row in connection.execute(
                "SELECT event_id FROM events WHERE sequence < ?",
                (before_candidate,),
            ).fetchall()
        }
    prefix = [
        event
        for event in source.events(readonly=True)
        if event.event_id in prefix_ids
    ]
    adversarial = FoundryDatabase(tmp_path / "accepted-only.db")
    adversarial.initialize()
    adversarial.append_events(prefix)

    requested = ("proof-repair-runner", "proof-reassign-runner")
    fabricated_mapping = {
        "integration_sha": "f" * 40,
        "included_packages": list(requested),
        "independently_verified": True,
        "verifier": {
            "source_type": "supervisor",
            "source_id": "mission-loop",
            "decision": "ACCEPT",
            "builder_run_id": (
                long_run_report.objective.stronger_run_ids[0]
            ),
            "verifier_run_id": "fabricated-verifier",
            "binding_head": "f" * 40,
        },
        "completion_order": ["OBJECTIVE_COMPLETE"],
    }

    result = _objective_state(
        adversarial,
        requested,
        integration=fabricated_mapping,
    )

    assert result["packages_verified"] is True
    assert set(result["accepted_packages"]) == set(requested)
    assert result["status"] == "INCOMPLETE"
    assert result["integration_verified"] is False
    assert result["independently_verified"] is False
    assert result["completion_event_id"] is None
    assert "integration:canonical-candidate-missing" in result["blockers"]
    assert (
        adversarial.event_count(
            event_type="workforce.integration_candidate_created",
            readonly=True,
        )
        == 0
    )
    assert (
        adversarial.event_count(
            event_type="workforce.objective_completion_recorded",
            readonly=True,
        )
        == 0
    )


def test_forged_same_base_delta_cannot_replace_accepted_package(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
) -> None:
    source = FoundryDatabase(long_run_report.database_path)
    source_events = source.events(readonly=True)
    candidate_event = next(
        event
        for event in source_events
        if event.event_type == "workforce.integration_candidate_created"
    )
    package_id = "proof-repair-runner"
    original_delta = next(
        delta
        for delta in candidate_event.payload["included_deltas"]
        if delta["package_id"] == package_id
    )
    base_sha = str(original_delta["base_sha"])
    original_result_sha = str(original_delta["result_sha"])

    forged_repository = tmp_path / "forged-source"
    clone = subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-local",
            long_run_report.source_repository,
            str(forged_repository),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert clone.returncode == 0, clone.stderr
    _git(
        forged_repository,
        "config",
        "user.email",
        "v13-forged-proof@example.invalid",
    )
    _git(
        forged_repository,
        "config",
        "user.name",
        "JARVIS V13 Forged Proof",
    )
    changes: dict[str, str] = {}
    for relative in original_delta["changed_paths"]:
        shown = subprocess.run(
            [
                "git",
                "-C",
                str(forged_repository),
                "show",
                f"{original_result_sha}:{relative}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
            check=False,
        )
        assert shown.returncode == 0, shown.stderr
        changes[str(relative)] = shown.stdout
    forged_result_sha = _commit_branch(
        forged_repository,
        branch="candidate/forged-proof-repair",
        parent=base_sha,
        changes=changes,
    )
    assert forged_result_sha != original_result_sha
    assert _git(forged_repository, "rev-parse", f"{forged_result_sha}^") == (
        base_sha
    )
    assert _git(
        forged_repository,
        "rev-parse",
        f"{forged_result_sha}^{{tree}}",
    ) == _git(
        forged_repository,
        "rev-parse",
        f"{original_result_sha}^{{tree}}",
    )

    def candidate_payload() -> dict[str, object]:
        payload = json.loads(json.dumps(candidate_event.payload))
        payload["repository_path"] = str(forged_repository.resolve())
        for delta in payload["included_deltas"]:
            if delta["package_id"] == package_id:
                delta["result_sha"] = forged_result_sha
        return payload

    convergence_event_id = str(
        candidate_event.payload["convergence_event_id"]
    )

    def convergence_payload(event: Event) -> dict[str, object]:
        payload = json.loads(json.dumps(event.payload))
        replaced = 0
        for delta in payload["package_deltas"]:
            if delta["package_id"] == package_id:
                assert delta["result_sha"] == original_result_sha
                delta["result_sha"] = forged_result_sha
                replaced += 1
        assert replaced == 1
        return payload

    def replay(
        name: str,
        *,
        forge_convergence: bool,
    ) -> FoundryDatabase:
        database = FoundryDatabase(tmp_path / f"{name}.db")
        database.initialize()
        events: list[Event] = []
        for event in source_events:
            if event.event_type in _COMPLETION_COMMAND_EVENTS:
                continue
            replacement_payload: dict[str, object] | None = None
            if event.event_id == candidate_event.event_id:
                replacement_payload = candidate_payload()
            elif (
                forge_convergence
                and event.event_id == convergence_event_id
            ):
                replacement_payload = convergence_payload(event)
            events.append(
                replace(
                    event,
                    payload=replacement_payload,
                    payload_hash=payload_hash(replacement_payload),
                )
                if replacement_payload is not None
                else event
            )
        database.append_events(events)
        return database

    requested = ("proof-repair-runner", "proof-reassign-runner")
    convergence_mismatch = replay(
        "forged-convergence-mismatch",
        forge_convergence=False,
    )
    convergence_result = _objective_state(
        convergence_mismatch,
        requested,
    )
    assert convergence_result["status"] == "INCOMPLETE"
    assert convergence_result["integration_verified"] is False
    assert convergence_result["independently_verified"] is False
    assert convergence_result["blockers"] == [
        "integration:effect-authority-invalid"
    ]

    receipt_mismatch = replay(
        "forged-receipt-mismatch",
        forge_convergence=True,
    )
    receipt_result = _objective_state(receipt_mismatch, requested)
    assert receipt_result["packages_verified"] is True
    assert receipt_result["status"] == "INCOMPLETE"
    assert receipt_result["integration_verified"] is False
    assert receipt_result["independently_verified"] is False
    assert receipt_result["completion_event_id"] is None
    assert receipt_result["blockers"] == [
        "integration:convergence-evidence-invalid"
    ]
    assert (
        receipt_mismatch.event_count(
            event_type="workforce.objective_completion_recorded",
            readonly=True,
        )
        == 0
    )


def test_contradictory_integration_effect_terminal_blocks_completion(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
) -> None:
    database = _replay_database(
        long_run_report,
        tmp_path / "contradictory-integration-effect.db",
        transform=lambda event: (
            None
            if event.event_type in _COMPLETION_COMMAND_EVENTS
            else event
        ),
    )
    action_key = str(
        long_run_report.integration["integration_effect_action_key"]
    )
    database.append(Event.create(
        "system.action_failed",
        {
            "action_key": action_key,
            "reason": "forged contradictory terminal",
        },
        source_type="supervisor",
        source_id="v5-effect-driver",
        idempotency_key=f"{action_key}.failed.forged",
    ))

    result = _objective_state(
        database,
        ("proof-repair-runner", "proof-reassign-runner"),
    )

    assert result["packages_verified"] is True
    assert result["integration_verified"] is False
    assert result["status"] == "INCOMPLETE"
    assert result["blockers"] == [
        "integration:effect-authority-invalid"
    ]


def test_omitted_excluded_convergence_candidate_blocks_completion(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
) -> None:
    convergence_event_id = str(
        long_run_report.integration["convergence_event_id"]
    )

    def omit_weak_candidate(event: Event) -> Event | None:
        if event.event_type in _COMPLETION_COMMAND_EVENTS:
            return None
        if event.event_id != convergence_event_id:
            return event
        payload = json.loads(json.dumps(event.payload))
        payload["classifications"].pop("proof-weak")
        payload["accepted_packages"] = [
            package_id
            for package_id in payload["accepted_packages"]
            if package_id != "proof-weak"
        ]
        payload["package_deltas"] = [
            delta
            for delta in payload["package_deltas"]
            if delta["package_id"] != "proof-weak"
        ]
        return replace(
            event,
            payload=payload,
            payload_hash=payload_hash(payload),
        )

    database = _replay_database(
        long_run_report,
        tmp_path / "omitted-excluded-convergence-candidate.db",
        transform=omit_weak_candidate,
    )
    result = _objective_state(
        database,
        ("proof-repair-runner", "proof-reassign-runner"),
    )

    assert result["packages_verified"] is True
    assert result["integration_verified"] is False
    assert result["status"] == "INCOMPLETE"
    assert result["blockers"] == [
        "integration:convergence-evidence-invalid"
    ]


@pytest.mark.parametrize(
    "identity_kind",
    ("payload", "correlation", "idempotency"),
)
def test_duplicate_candidate_identity_blocks_completion(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
    identity_kind: str,
) -> None:
    canonical_key = (
        "workforce.integration_candidate_created:"
        "objective-v13-long-run"
    )

    def transform(event: Event) -> Event:
        if (
            identity_kind == "idempotency"
            and event.event_type
            == "workforce.integration_candidate_created"
        ):
            return replace(
                event,
                idempotency_key=canonical_key + ":original",
            )
        return event

    database = _replay_database(
        long_run_report,
        tmp_path / f"duplicate-candidate-{identity_kind}.db",
        transform=transform,
    )
    payload: dict[str, object] = {
        "schema": "jarvis.workforce.integration-candidate.v1",
        "included_packages": ["proof-repair-runner"],
    }
    if identity_kind == "payload":
        payload["objective_id"] = "objective-v13-long-run"
    database.append(Event.create(
        "workforce.integration_candidate_created",
        payload,
        source_type="supervisor",
        source_id="adversarial-proof",
        correlation_id=(
            "objective-v13-long-run"
            if identity_kind == "correlation"
            else "adversarial-correlation"
        ),
        idempotency_key=(
            canonical_key
            if identity_kind == "idempotency"
            else f"adversarial-candidate:{identity_kind}"
        ),
    ))

    result = _objective_state(
        database,
        ("proof-repair-runner", "proof-reassign-runner"),
    )

    assert result["status"] == "INCOMPLETE"
    assert result["integration_verified"] is False
    assert "integration:multiple-canonical-candidates" in result["blockers"]


@pytest.mark.parametrize(
    "identity_kind",
    ("payload", "correlation", "idempotency"),
)
def test_duplicate_completion_identity_blocks_completion(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
    identity_kind: str,
) -> None:
    canonical_key = (
        "workforce.objective_completion_recorded:"
        "objective-v13-long-run"
    )

    def transform(event: Event) -> Event:
        if (
            identity_kind == "idempotency"
            and event.event_type
            == "workforce.objective_completion_recorded"
        ):
            return replace(
                event,
                idempotency_key=canonical_key + ":original",
            )
        return event

    database = _replay_database(
        long_run_report,
        tmp_path / f"duplicate-completion-{identity_kind}.db",
        transform=transform,
    )
    payload: dict[str, object] = {
        "schema": "jarvis.workforce.objective-completion.v1",
        "package_ids": ["proof-repair-runner"],
    }
    if identity_kind == "payload":
        payload["objective_id"] = "objective-v13-long-run"
    database.append(Event.create(
        "workforce.objective_completion_recorded",
        payload,
        source_type="supervisor",
        source_id="adversarial-proof",
        correlation_id=(
            "objective-v13-long-run"
            if identity_kind == "correlation"
            else "adversarial-correlation"
        ),
        idempotency_key=(
            canonical_key
            if identity_kind == "idempotency"
            else f"adversarial-completion:{identity_kind}"
        ),
    ))

    result = _objective_state(
        database,
        ("proof-repair-runner", "proof-reassign-runner"),
    )

    assert result["status"] == "INCOMPLETE"
    assert result["integration_verified"] is True
    assert "objective:completion-event-count-invalid" in result["blockers"]


def test_omitted_unique_delta_cannot_complete_objective(
    long_run_report: WorkforceLongRunReport,
) -> None:
    database = FoundryDatabase(long_run_report.database_path)

    result = _objective_state(database, ("proof-repair-runner",))

    assert result["packages_verified"] is False
    assert result["status"] == "INCOMPLETE"
    assert result["integration_verified"] is False
    assert result["blockers"] == [
        "objective:accepted-package-universe-mismatch"
    ]


def test_forged_excluded_delta_is_not_trusted_from_candidate_payload(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
) -> None:
    source = FoundryDatabase(long_run_report.database_path)
    source_events = source.events(readonly=True)
    candidate_event = next(
        event
        for event in source_events
        if event.event_type == "workforce.integration_candidate_created"
    )
    package_id = "proof-weak"
    original_delta = next(
        delta
        for delta in candidate_event.payload["excluded_deltas"]
        if delta["package_id"] == package_id
    )
    base_sha = str(original_delta["base_sha"])
    original_result_sha = str(original_delta["result_sha"])
    repository = Path(long_run_report.source_repository)
    integration_ref = _git(
        repository,
        "rev-parse",
        "refs/heads/proof/integration",
    )
    forged_result_sha = _git(
        repository,
        "commit-tree",
        f"{original_result_sha}^{{tree}}",
        "-p",
        base_sha,
        "-m",
        "forged excluded same-tree identity",
    )
    assert forged_result_sha != original_result_sha
    assert _git(
        repository,
        "rev-parse",
        f"{forged_result_sha}^{{tree}}",
    ) == _git(
        repository,
        "rev-parse",
        f"{original_result_sha}^{{tree}}",
    )
    assert _git(
        repository,
        "rev-parse",
        "refs/heads/proof/integration",
    ) == integration_ref
    convergence_event_id = str(
        candidate_event.payload["convergence_event_id"]
    )

    def replay(
        name: str,
        *,
        forge_convergence: bool,
    ) -> FoundryDatabase:
        def transform(event: Event) -> Event | None:
            if event.event_type in _COMPLETION_COMMAND_EVENTS:
                return None
            if event.event_id not in {
                candidate_event.event_id,
                convergence_event_id,
            }:
                return event
            if (
                event.event_id == convergence_event_id
                and not forge_convergence
            ):
                return event
            payload = json.loads(json.dumps(event.payload))
            if event.event_id == candidate_event.event_id:
                deltas = payload["excluded_deltas"]
            else:
                deltas = payload["package_deltas"]
            changed = 0
            for delta in deltas:
                if delta["package_id"] == package_id:
                    delta["result_sha"] = forged_result_sha
                    changed += 1
            assert changed == 1
            return replace(
                event,
                payload=payload,
                payload_hash=payload_hash(payload),
            )

        return _replay_database(
            long_run_report,
            tmp_path / f"{name}.db",
            transform=transform,
        )

    payload_only = replay(
        "forged-excluded-payload-only",
        forge_convergence=False,
    )
    result = _objective_state(
        payload_only,
        ("proof-repair-runner", "proof-reassign-runner"),
    )

    assert result["status"] == "INCOMPLETE"
    assert result["integration_verified"] is False
    assert result["blockers"] == [
        f"integration:convergence-delta-mismatch:{package_id}"
    ]

    coordinated = replay(
        "forged-excluded-coordinated",
        forge_convergence=True,
    )
    coordinated_result = _objective_state(
        coordinated,
        ("proof-repair-runner", "proof-reassign-runner"),
    )

    assert coordinated_result["status"] == "INCOMPLETE"
    assert coordinated_result["integration_verified"] is False
    assert coordinated_result["blockers"] == [
        "integration:convergence-evidence-invalid"
    ]


def test_weak_integration_verification_contract_cannot_complete(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
) -> None:
    source = FoundryDatabase(long_run_report.database_path)
    source_events = source.events(readonly=True)
    package_event = next(
        event
        for event in source_events
        if event.event_type == "workforce.package_created"
        and event.payload["package_id"] == "proof-integration-verifier"
    )
    contract_event = next(
        event
        for event in source_events
        if event.event_type == "mission.step_contract_recorded"
        and event.payload["mission_id"]
        == "mission-proof-integration-verifier"
    )
    original_package = WorkPackage.from_payload(package_event.payload)
    original_contract = MissionStepContract.from_payload(
        contract_event.payload
    )
    weak_contract = replace(
        original_contract,
        verification_criteria=("the integration HEAD exists",),
        forbidden_paths=("live",),
    )
    weak_package = replace(
        original_package,
        acceptance_criteria=weak_contract.verification_criteria,
        forbidden_paths=weak_contract.forbidden_paths,
        required_tests=("git diff --check",),
        mission_step_contract_hash=weak_contract.content_hash(),
    )

    def transform(event: Event) -> Event | None:
        if event.event_type in _COMPLETION_COMMAND_EVENTS:
            return None
        if event.event_id == package_event.event_id:
            payload = {
                **event.payload,
                **weak_package.to_payload(),
            }
            return replace(
                event,
                payload=payload,
                payload_hash=payload_hash(payload),
            )
        if event.event_id == contract_event.event_id:
            payload = weak_contract.to_payload()
            return replace(
                event,
                payload=payload,
                payload_hash=payload_hash(payload),
            )
        return event

    database = _replay_database(
        long_run_report,
        tmp_path / "weak-integration-contract.db",
        transform=transform,
    )
    result = _objective_state(
        database,
        ("proof-repair-runner", "proof-reassign-runner"),
    )

    assert result["packages_verified"] is True
    assert result["status"] == "INCOMPLETE"
    assert result["integration_verified"] is False
    assert result["blockers"] == [
        "integration:verification-contract-mismatch"
    ]


def test_objective_reader_is_pure_and_completion_command_is_gated(
    long_run_report: WorkforceLongRunReport,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _replay_database(
        long_run_report,
        tmp_path / "precompletion.db",
        transform=lambda event: (
            None
            if event.event_type in _COMPLETION_COMMAND_EVENTS
            else event
        ),
    )
    requested = ("proof-repair-runner", "proof-reassign-runner")
    before = database.event_count(readonly=True)

    reconstructed = _objective_state(database, requested)

    assert database.event_count(readonly=True) == before
    assert reconstructed["status"] == "INCOMPLETE"
    assert reconstructed["integration_verified"] is True
    assert reconstructed["blockers"] == [
        "objective:completion-event-missing"
    ]

    stop_path = tmp_path / "EMERGENCY_STOP"
    stop_path.write_text("owner stop\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="workforce stopped"):
        _record_objective_completion(
            database,
            requested,
            stop_path=stop_path,
        )
    assert database.event_count(readonly=True) == before

    stop_path.unlink()
    original_append_on_connection = database.append_events_on_connection
    append_observations: list[bool] = []

    def append_on_guarded_connection(connection, events):
        append_observations.append(bool(connection.in_transaction))
        return original_append_on_connection(connection, events)

    def reject_unguarded_append(event):
        raise AssertionError("objective completion used an unguarded append")

    monkeypatch.setattr(
        database,
        "append_events_on_connection",
        append_on_guarded_connection,
    )
    monkeypatch.setattr(database, "append", reject_unguarded_append)
    first = _record_objective_completion(
        database,
        requested,
        stop_path=stop_path,
    )
    replayed = _record_objective_completion(
        database,
        requested,
        stop_path=stop_path,
    )
    completed = _objective_state(database, requested)

    assert replayed.event_id == first.event_id
    assert append_observations == [True]
    assert completed["status"] == "COMPLETE"
    assert completed["completion_event_id"] == first.event_id
    assert (
        completed["receipt_sequences"][-1]
        < completed["completion_sequence"]
    )
    assert (
        database.event_count(
            event_type="workforce.objective_completion_recorded",
            readonly=True,
        )
        == 1
    )
    completion = next(
        event
        for event in database.events(
            event_types=("workforce.objective_completion_recorded",),
            readonly=True,
        )
    )
    assert completion.causation_id == completed["integration_receipt_id"]


def _disjoint_integration_fixture(
    tmp_path: Path,
) -> tuple[
    Path,
    str,
    tuple[PackageConvergenceEvidence, ...],
    dict[str, str],
    FoundryDatabase,
    str,
]:
    repository = tmp_path / "source"
    repository.mkdir(parents=True)
    _git(repository, "init", "-q")
    _git(
        repository,
        "config",
        "user.email",
        "v13-integration-test@example.invalid",
    )
    _git(
        repository,
        "config",
        "user.name",
        "JARVIS V13 Integration Test",
    )
    (repository / "src").mkdir()
    (repository / "src" / "shared.txt").write_text(
        "one\ntwo\nthree\nfour\n",
        encoding="utf-8",
    )
    (repository / "src" / "overlap.txt").write_text(
        "base\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "base")
    base_sha = _git(repository, "rev-parse", "HEAD")
    results = {
        "proof-repair-runner": _commit_branch(
            repository,
            branch="candidate/proof-repair-runner",
            parent=base_sha,
            changes={
                "src/shared.txt": "ONE\ntwo\nthree\nfour\n",
            },
        ),
        "proof-reassign-runner": _commit_branch(
            repository,
            branch="candidate/proof-reassign-runner",
            parent=base_sha,
            changes={
                "src/shared.txt": "one\ntwo\nthree\nFOUR\n",
            },
        ),
        "proof-overlap-a": _commit_branch(
            repository,
            branch="candidate/proof-overlap-a",
            parent=base_sha,
            changes={"src/overlap.txt": "overlap-a\n"},
        ),
        "proof-overlap-b": _commit_branch(
            repository,
            branch="candidate/proof-overlap-b",
            parent=base_sha,
            changes={"src/overlap.txt": "overlap-b\n"},
        ),
        "proof-weak": _commit_branch(
            repository,
            branch="candidate/proof-weak",
            parent=base_sha,
            changes={"src/weak.txt": "weak\n"},
        ),
    }
    candidates = tuple(
        PackageConvergenceEvidence(
            package_id=package_id,
            base_sha=base_sha,
            result_sha=result_sha,
            status=(
                "REJECTED"
                if package_id == "proof-weak"
                else "ACCEPTED"
            ),
            evidence=(f"test:{package_id}",),
        )
        for package_id, result_sha in results.items()
    )
    decision = analyze_convergence(
        repository,
        decision_id="decision:v13-long-run-proof",
        integration_target="proof/integration",
        target_sha=base_sha,
        packages=candidates,
    )
    database = FoundryDatabase(tmp_path / "home" / "foundry.db")
    database.initialize()
    for package_id in ("proof-repair-runner", "proof-reassign-runner"):
        _record_accepted_integration_package(
            database,
            repository=repository,
            package_id=package_id,
            base_sha=base_sha,
            result_sha=results[package_id],
        )
    record_convergence_decision(
        database,
        decision,
        source_id="v13-long-run-restart",
    )
    convergence_event = next(
        event
        for event in database.events(
            event_types=("workforce.convergence_recorded",),
            readonly=True,
        )
    )
    return (
        repository,
        base_sha,
        candidates,
        dict(decision.classifications),
        database,
        convergence_event.event_id,
    )


def _record_accepted_integration_package(
    database: FoundryDatabase,
    *,
    repository: Path,
    package_id: str,
    base_sha: str,
    result_sha: str,
) -> None:
    mission_id = f"mission-{package_id}"
    step_id = f"step-{package_id}"
    builder_run_id = f"workforce:{package_id}:a1"
    verifier_run_id = f"verifier:{package_id}:a1"
    verdict_id = f"verdict:{package_id}:a1"
    package = WorkPackage(
        package_id=package_id,
        parent_objective_id="objective-v13-long-run",
        role="builder",
        capability="write",
        bounded_goal=f"Produce the accepted {package_id} delta.",
        acceptance_criteria=("Exact result SHA is independently verified.",),
        source_sha=base_sha,
        repository_id="proof-source",
        branch_name=f"candidate/{package_id}",
        worktree_path=str(repository),
        allowed_paths=("src/shared.txt",),
        expected_artifacts=("src/shared.txt",),
        mission_id=mission_id,
        step_id=step_id,
    )
    create_work_package(database, package)
    transition_work_package(
        database,
        package_id,
        "READY",
        reason="integration fixture ready",
    )
    session = WorkerSession(
        session_id=builder_run_id,
        package_id=package_id,
        run_id=builder_run_id,
        attempt=1,
        framework="codex",
        provider="local",
        model="fixture",
        status="COMPLETED",
        result={"package_hash": package.content_hash()},
    )
    binding_material = {
        "builder_run_id": builder_run_id,
        "verifier_run_id": verifier_run_id,
        "head": result_sha,
        "target": str((repository / "src" / "shared.txt").resolve()),
        "context_manifest_hash": payload_hash({
            "package_id": package_id,
            "result_sha": result_sha,
        }),
    }
    binding = {
        **binding_material,
        "binding_hash": payload_hash(binding_material),
    }
    database.append_events([
        Event.create(
            "workforce.session_bound",
            {
                **session.to_payload(),
                "expected_session_version": 0,
                "session_version": 1,
            },
            source_type="supervisor",
            source_id="workforce-gateway",
            correlation_id=package_id,
            idempotency_key=f"workforce.session_bound:{builder_run_id}",
        ),
        Event.create(
            "verification.started",
            {
                "verdict_id": verdict_id,
                "mission_id": mission_id,
                "subject_type": "mission_step",
                "subject_id": step_id,
                "builder_run_id": builder_run_id,
                "verifier_run_id": verifier_run_id,
                "independence": {"fresh_session": True},
                "criteria": list(package.acceptance_criteria),
                "binding": binding,
            },
            source_type="supervisor",
            source_id="mission-loop",
            idempotency_key=f"verification.started:{verdict_id}",
        ),
        Event.create(
            "verification.passed",
            {
                "verdict_id": verdict_id,
                "findings": [],
                "evidence": {
                    "builder_run_id": builder_run_id,
                    "verifier_run_id": verifier_run_id,
                    "binding_hash": binding["binding_hash"],
                    "fabrication_review": "performed",
                },
            },
            source_type="supervisor",
            source_id="mission-loop",
            idempotency_key=f"verification.passed:{verdict_id}",
        ),
        Event.create(
            "verification.accepted",
            {
                "decision": "ACCEPT",
                "verdict_id": verdict_id,
                "mission_id": mission_id,
                "subject_type": "mission_step",
                "subject_id": step_id,
                "builder_run_id": builder_run_id,
                "verifier_run_id": verifier_run_id,
                "checks": {
                    "stop_veto": "clear",
                    "verdict_status": "PASS",
                    "binding_present": "yes",
                    "ordering": "ok",
                    "binding_integrity": "ok",
                    "identity": "ok",
                    "isolation": "fresh-session",
                    "evidence_complete": "yes",
                    "post_verdict_mutation": "none",
                },
            },
            source_type="supervisor",
            source_id="mission-loop",
            idempotency_key=f"verification.accepted:{verdict_id}",
        ),
    ])
    transition_work_package(
        database,
        package_id,
        "VERIFYING",
        reason="integration fixture verification passed",
        acceptance_builder_run_id=builder_run_id,
    )
    transition_work_package(
        database,
        package_id,
        "ACCEPTED",
        reason="integration fixture accepted",
    )


def test_integration_preserves_disjoint_same_file_hunks(
    tmp_path: Path,
) -> None:
    (
        repository,
        base_sha,
        candidates,
        classifications,
        database,
        convergence_event_id,
    ) = _disjoint_integration_fixture(tmp_path)

    integration = _create_integration_candidate(
        database,
        repository,
        target="proof/integration",
        base_sha=base_sha,
        candidates=candidates,
        classifications=classifications,
        convergence_event_id=convergence_event_id,
        stop_path=tmp_path / "home" / "EMERGENCY_STOP",
    )

    assert _git(
        repository,
        "show",
        f"{integration['integration_sha']}:src/shared.txt",
    ).splitlines() == ["ONE", "two", "three", "FOUR"]
    assert integration["changed_paths"] == ["src/shared.txt"]
    assert {
        tuple(delta["changed_paths"])
        for delta in integration["included_deltas"]
    } == {("src/shared.txt",)}
    assert effect_state(
        database,
        integration["integration_effect_action_key"],
    )["status"] == "COMPLETED"


def test_integration_candidate_record_is_stop_gated_and_transactional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        repository,
        base_sha,
        candidates,
        classifications,
        database,
        convergence_event_id,
    ) = _disjoint_integration_fixture(tmp_path)
    stop_path = tmp_path / "home" / "EMERGENCY_STOP"
    integration = _create_integration_candidate(
        database,
        repository,
        target="proof/integration",
        base_sha=base_sha,
        candidates=candidates,
        classifications=classifications,
        convergence_event_id=convergence_event_id,
        stop_path=stop_path,
    )

    stop_path.parent.mkdir(parents=True, exist_ok=True)
    stop_path.write_text("owner stop\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="workforce stopped"):
        _record_integration_candidate(
            database,
            integration,
            convergence_event_id=convergence_event_id,
            stop_path=stop_path,
        )
    assert database.event_count(
        event_type="workforce.integration_candidate_created",
        readonly=True,
    ) == 0

    stop_path.unlink()
    original_append_on_connection = database.append_events_on_connection
    append_observations: list[bool] = []

    def append_on_guarded_connection(connection, events):
        append_observations.append(bool(connection.in_transaction))
        return original_append_on_connection(connection, events)

    def reject_unguarded_append(event):
        raise AssertionError("integration candidate used an unguarded append")

    monkeypatch.setattr(
        database,
        "append_events_on_connection",
        append_on_guarded_connection,
    )
    monkeypatch.setattr(database, "append", reject_unguarded_append)
    recorded = _record_integration_candidate(
        database,
        integration,
        convergence_event_id=convergence_event_id,
        stop_path=stop_path,
    )
    replayed = _record_integration_candidate(
        database,
        integration,
        convergence_event_id=convergence_event_id,
        stop_path=stop_path,
    )

    assert replayed.event_id == recorded.event_id
    assert append_observations == [True, True]
    assert database.event_count(
        event_type="workforce.integration_candidate_created",
        readonly=True,
    ) == 1


def test_integration_reconciles_crash_after_exact_ref_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        repository,
        base_sha,
        candidates,
        classifications,
        database,
        convergence_event_id,
    ) = _disjoint_integration_fixture(tmp_path)
    original_append = database.append
    crashed = False

    def crash_before_effect_completion(event: Event):
        nonlocal crashed
        if event.event_type == "system.action_completed" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash after integration ref update")
        return original_append(event)

    monkeypatch.setattr(database, "append", crash_before_effect_completion)
    with pytest.raises(
        RuntimeError,
        match="simulated crash after integration ref update",
    ):
        _create_integration_candidate(
            database,
            repository,
            target="proof/integration",
            base_sha=base_sha,
            candidates=candidates,
            classifications=classifications,
            convergence_event_id=convergence_event_id,
            stop_path=tmp_path / "home" / "EMERGENCY_STOP",
        )
    landed_sha = _git(
        repository,
        "rev-parse",
        "refs/heads/proof/integration",
    )
    started = next(
        event
        for event in database.events(
            event_types=("system.action_started",),
            readonly=True,
        )
    )
    assert effect_state(database, started.payload["action_key"])[
        "status"
    ] == STARTED

    monkeypatch.setattr(database, "append", original_append)
    replayed = _create_integration_candidate(
        database,
        repository,
        target="proof/integration",
        base_sha=base_sha,
        candidates=candidates,
        classifications=classifications,
        convergence_event_id=convergence_event_id,
        stop_path=tmp_path / "home" / "EMERGENCY_STOP",
    )
    state = effect_state(
        database,
        replayed["integration_effect_action_key"],
    )

    assert replayed["integration_sha"] == landed_sha
    assert _git(
        repository,
        "rev-list",
        "--count",
        f"{base_sha}..{landed_sha}",
    ) == "1"
    assert state["status"] == "FROZEN"
    assert state["accepted"] is True
    assert state["reconciled"] is True
    assert state["terminal_outcome"] == "COMPLETED"
    assert (
        database.event_count(
            event_type="system.action_started",
            readonly=True,
        )
        == 1
    )
    assert (
        database.event_count(
            event_type="effect.terminal_truth",
            readonly=True,
        )
        == 1
    )


def test_stop_blocks_integration_before_effect_or_ref(
    tmp_path: Path,
) -> None:
    (
        repository,
        base_sha,
        candidates,
        classifications,
        database,
        convergence_event_id,
    ) = _disjoint_integration_fixture(tmp_path)
    stop_path = tmp_path / "home" / "EMERGENCY_STOP"
    stop_path.write_text("owner stop\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="workforce stopped"):
        _create_integration_candidate(
            database,
            repository,
            target="proof/integration",
            base_sha=base_sha,
            candidates=candidates,
            classifications=classifications,
            convergence_event_id=convergence_event_id,
            stop_path=stop_path,
        )

    assert _git(
        repository,
        "branch",
        "--list",
        "proof/integration",
    ) == ""
    assert (
        database.event_count(
            event_type="system.action_started",
            readonly=True,
        )
        == 0
    )
