"""Autonomous production loop: inspect, isolate, dispatch, ingest, verify, accept."""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - bounded fresh-verifier dispatch with explicit paths
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.portfolio.records import MissionLink
from jarvis_foundry.kernel.portfolio.store import PortfolioStore
from jarvis_foundry.orchestration.dispatcher import DispatchRun, dispatch
from jarvis_foundry.orchestration.findings import Finding, FindingsRegistry
from jarvis_foundry.orchestration.git_ops import (
    clone_project,
    inspect_project,
)
from jarvis_foundry.orchestration.ingest import ingest_result
from jarvis_foundry.orchestration.inspector import inspect_mission
from jarvis_foundry.orchestration.ledger_events import r8_ledger, record_event
from jarvis_foundry.orchestration.mission_context import (
    MissionContext,
    build_mission_context,
)
from jarvis_foundry.orchestration.paths import (
    TEMP_BASE,
    clear_within,
    copy_tree,
    tree_hash,
)
from jarvis_foundry.orchestration.reconcile import demonstrate_rollback, reconcile_mission
from jarvis_foundry.orchestration.schemas import WorkerResult
from jarvis_foundry.orchestration.verify import Verifier, VerifierIssue, VerifierVerdict


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True)
class OrchestrationConfig:
    mission_id: str
    kind: str
    source_project: str
    branch: str
    worker_module: str
    worker_id: str
    role: str
    objective: str
    known_verified_state: Mapping[str, Any]
    relevant_previous_failures: list[str] | tuple[str, ...]
    allowed_mutation: list[str] | tuple[str, ...]
    forbidden_mutation: list[str] | tuple[str, ...]
    acceptance_criteria: list[str] | tuple[str, ...]
    required_tests: list[str] | tuple[str, ...]
    required_evidence: list[str] | tuple[str, ...]
    rollback_expectation: str
    worker_spec: Mapping[str, Any] = field(default_factory=dict)
    snapshot_untracked: bool = False
    candidate_id: str = ""
    decision_id: str = ""
    goal_id: str = ""
    repair_finding_id: str = ""
    reproduced_test_command: str = ""
    python: str = str(Path(sys.executable))
    evidence_dir: str = ""
    proving_root: str = str(TEMP_BASE)
    timeout_seconds: int = 1800
    next_action: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> OrchestrationConfig:
        return OrchestrationConfig(
            mission_id=str(payload["mission_id"]),
            kind=str(payload["kind"]),
            source_project=str(payload["source_project"]),
            branch=str(payload["branch"]),
            worker_module=str(payload["worker_module"]),
            worker_id=str(payload["worker_id"]),
            role=str(payload["role"]),
            objective=str(payload["objective"]),
            known_verified_state=dict(payload.get("known_verified_state", {})),
            relevant_previous_failures=tuple(payload.get("relevant_previous_failures", [])),
            allowed_mutation=tuple(payload.get("allowed_mutation", [])),
            forbidden_mutation=tuple(payload.get("forbidden_mutation", [])),
            acceptance_criteria=tuple(payload.get("acceptance_criteria", [])),
            required_tests=tuple(payload.get("required_tests", [])),
            required_evidence=tuple(payload.get("required_evidence", [])),
            rollback_expectation=str(payload.get("rollback_expectation", "")),
            worker_spec=dict(payload.get("worker_spec", {})),
            snapshot_untracked=bool(payload.get("snapshot_untracked", False)),
            candidate_id=str(payload.get("candidate_id", "")),
            decision_id=str(payload.get("decision_id", "")),
            goal_id=str(payload.get("goal_id", "")),
            repair_finding_id=str(payload.get("repair_finding_id", "")),
            reproduced_test_command=str(payload.get("reproduced_test_command", "")),
            python=str(payload.get("python", sys.executable)),
            evidence_dir=str(payload.get("evidence_dir", "")),
            proving_root=str(payload.get("proving_root", TEMP_BASE)),
            timeout_seconds=int(payload.get("timeout_seconds", 1800)),
            next_action=str(payload.get("next_action", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "kind": self.kind,
            "source_project": self.source_project,
            "branch": self.branch,
            "worker_module": self.worker_module,
            "worker_id": self.worker_id,
            "role": self.role,
            "objective": self.objective,
            "known_verified_state": dict(self.known_verified_state),
            "relevant_previous_failures": list(self.relevant_previous_failures),
            "allowed_mutation": list(self.allowed_mutation),
            "forbidden_mutation": list(self.forbidden_mutation),
            "acceptance_criteria": list(self.acceptance_criteria),
            "required_tests": list(self.required_tests),
            "required_evidence": list(self.required_evidence),
            "rollback_expectation": self.rollback_expectation,
            "worker_spec": dict(self.worker_spec),
            "snapshot_untracked": self.snapshot_untracked,
            "candidate_id": self.candidate_id,
            "decision_id": self.decision_id,
            "goal_id": self.goal_id,
            "repair_finding_id": self.repair_finding_id,
            "reproduced_test_command": self.reproduced_test_command,
            "python": self.python,
            "evidence_dir": self.evidence_dir,
            "proving_root": self.proving_root,
            "timeout_seconds": self.timeout_seconds,
            "next_action": self.next_action,
        }


VerifierFactory = Callable[[Path, Path], Verifier]


class Orchestrator:
    def __init__(
        self,
        config: OrchestrationConfig,
        *,
        verifier_factory: VerifierFactory | None = None,
    ) -> None:
        self.config = config
        self.proving_root = Path(config.proving_root)
        self.evidence_dir = Path(config.evidence_dir) if config.evidence_dir else self.proving_root
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = r8_ledger(self.evidence_dir / "r8_ledger.db")
        self.store = PortfolioStore(self.evidence_dir / "r8_store")
        self.findings = FindingsRegistry(self.evidence_dir / "r8_findings.jsonl")
        self._verifier_factory = verifier_factory
        self._event_index = 0
        self._source_before: dict[str, Any] = {}
        self._baseline_hash = ""

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        mission_dir = self.evidence_dir / self.config.mission_id
        mission_dir.mkdir(parents=True, exist_ok=True)
        if self.config.kind == "waiting":
            report = self._run_waiting(mission_dir)
        elif self.config.kind == "repair":
            report = self._run_repair(mission_dir)
        else:
            report = self._run_build(mission_dir)
        report["wall_seconds"] = round(time.monotonic() - started, 3)
        self._write_json(mission_dir / "mission_report.json", report)
        return report

    def _next_event_id(self, suffix: str) -> str:
        self._event_index += 1
        return f"evt-{self.config.mission_id}-{suffix}-{self._event_index:02d}"

    def _dispatch_and_ingest(self, mission_dir: Path, clone: Path) -> dict[str, Any]:
        context_path = mission_dir / "mission_context.json"
        result_path = mission_dir / "worker_result.json"
        stdout_path = mission_dir / "dispatch_stdout.txt"
        context = self._mission_context()
        context.write(context_path)
        event_type = (
            "mission.repair_dispatched" if self.config.kind == "repair" else "mission.dispatched"
        )
        payload: dict[str, Any] = {
            "mission_id": self.config.mission_id,
            "worker_id": self.config.worker_id,
            "clone_path": str(clone),
            "branch": self.config.branch,
            "mission_context_hash": context.content_hash(),
            "source_repo_before_hash": self._source_before["tree_hash"],
            "candidate_id": self.config.candidate_id,
            "decision_id": self.config.decision_id,
            "goal_id": self.config.goal_id,
        }
        if event_type == "mission.repair_dispatched":
            payload.update(
                {
                    "repair_mission_id": self.config.mission_id,
                    "repair_worker_id": self.config.worker_id,
                    "defect_finding_id": self.config.repair_finding_id,
                    "reproduced_test_command": self.config.reproduced_test_command,
                }
            )
        record_event(
            self.ledger,
            event_type=event_type,
            payload=payload,
            source_type="r8-orchestrator",
            source_id=self.config.mission_id,
            idempotency_key=f"{event_type}:{self.config.mission_id}",
            event_id=self._next_event_id("dispatch"),
            occurred_at=utc_now(),
        )

        run = dispatch(
            mission=context,
            worker_module=self.config.worker_module,
            worker_id=self.config.worker_id,
            python=Path(self.config.python),
            clone=clone,
            context_path=context_path,
            result_path=result_path,
            timeout_seconds=self.config.timeout_seconds,
            proving_root=self.proving_root,
        )
        stdout_path.write_text(run.output, encoding="utf-8")
        outcome = ingest_result(result_path)
        if outcome.valid and outcome.result is not None:
            record_event(
                self.ledger,
                event_type="mission.result_ingested",
                payload={
                    "mission_id": self.config.mission_id,
                    "worker_id": self.config.worker_id,
                    "result_status": outcome.result.status,
                    "result_hash": outcome.result_hash,
                    "validated": True,
                },
                source_type="r8-orchestrator",
                source_id=self.config.mission_id,
                idempotency_key=f"mission.result_ingested:{self.config.mission_id}",
                event_id=self._next_event_id("ingested"),
                occurred_at=utc_now(),
            )
        else:
            record_event(
                self.ledger,
                event_type="mission.malformed_rejected",
                payload={
                    "mission_id": self.config.mission_id,
                    "worker_id": self.config.worker_id,
                    "reasons": list(outcome.reasons),
                    "result_hash": outcome.result_hash,
                },
                source_type="r8-orchestrator",
                source_id=self.config.mission_id,
                idempotency_key=f"mission.malformed_rejected:{self.config.mission_id}",
                event_id=self._next_event_id("malformed"),
                occurred_at=utc_now(),
            )
        return {
            "context_path": str(context_path),
            "result_path": str(result_path),
            "stdout_path": str(stdout_path),
            "dispatch": run,
            "outcome": outcome,
        }

    def _mission_context(self) -> MissionContext:
        result_contract = {
            "fields": [
                "status",
                "claims",
                "files_changed",
                "commands_run",
                "tests",
                "evidence",
                "artifacts",
                "external_effects",
                "blockers",
                "uncertainty",
                "recommended_next_action",
            ],
            "validation": "fail-closed; malformed results are REJECTED and recorded",
            "prose_is_not_truth": True,
        }
        return build_mission_context(
            mission_id=self.config.mission_id,
            mission=self.config.objective,
            current_step=self.config.kind.upper(),
            role=self.config.role,
            objective=self.config.objective,
            known_verified_state=self.config.known_verified_state,
            relevant_previous_failures=self.config.relevant_previous_failures,
            allowed_mutation=self.config.allowed_mutation,
            forbidden_mutation=self.config.forbidden_mutation,
            acceptance_criteria=self.config.acceptance_criteria,
            required_tests=self.config.required_tests,
            required_evidence=self.config.required_evidence,
            rollback_expectation=self.config.rollback_expectation,
            result_contract=result_contract,
            dispatch={
                "python": self.config.python,
                "worker_module": self.config.worker_module,
                "worker_id": self.config.worker_id,
                "clone": self.config.source_project,
                "proving_root": str(self.proving_root),
            },
            worker_spec=self.config.worker_spec,
        )

    def _prepare_clone(self, mission_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
        source = Path(self.config.source_project)
        self._source_before = inspect_project(source, captured_at=utc_now())
        clone = self.proving_root / "clones" / self.config.mission_id
        if clone.exists():
            clear_within(clone, self.proving_root)
        clone_info = clone_project(
            source,
            clone,
            branch=self.config.branch,
            snapshot_untracked=self.config.snapshot_untracked,
            proving_root=self.proving_root,
        )
        baseline_snapshot = self.proving_root / "snapshots" / self.config.mission_id / "baseline"
        if baseline_snapshot.exists():
            clear_within(baseline_snapshot, self.proving_root)
        copy_tree(clone, baseline_snapshot, exclude=set())
        baseline_hash = tree_hash(clone)
        return clone, baseline_snapshot, {"clone_info": clone_info, "baseline_hash": baseline_hash}

    def _inspect_and_verify(
        self, mission_dir: Path, clone: Path, result: WorkerResult
    ) -> dict[str, Any]:
        inspection = inspect_mission(clone, result, proving_root=self.proving_root)
        record_event(
            self.ledger,
            event_type="mission.inspected",
            payload={
                "mission_id": self.config.mission_id,
                "diff_summary_hash": inspection["diff_summary_hash"],
                "changed_files": list(inspection["actual_files"].keys()),
                "integrity_clean": inspection["integrity_clean"],
            },
            source_type="r8-orchestrator",
            source_id=self.config.mission_id,
            idempotency_key=f"mission.inspected:{self.config.mission_id}",
            event_id=self._next_event_id("inspected"),
            occurred_at=utc_now(),
        )
        finding_ids: list[str] = []
        if not inspection["integrity_clean"]:
            finding = Finding(
                finding_id=f"{self.config.mission_id}-inspector",
                severity="S2",
                title="Worker result does not match actual diff",
                detail=json.dumps(inspection["integrity_issues"], sort_keys=True),
                evidence=("mission_report.json",),
                reproduced_command=self.config.reproduced_test_command or "inspect_mission",
                mission_id=self.config.mission_id,
                created_at=utc_now(),
            )
            self.findings.record(finding)
            finding_ids.append(finding.finding_id)
        record_event(
            self.ledger,
            event_type="mission.findings_recorded",
            payload={
                "mission_id": self.config.mission_id,
                "finding_ids": finding_ids,
            },
            source_type="r8-orchestrator",
            source_id=self.config.mission_id,
            idempotency_key=f"mission.findings_recorded:{self.config.mission_id}",
            event_id=self._next_event_id("findings"),
            occurred_at=utc_now(),
        )

        verdict = self._fresh_verify(mission_dir, clone, result)
        record_event(
            self.ledger,
            event_type="mission.reverified",
            payload={
                "mission_id": self.config.mission_id,
                "verifier_id": verdict.verifier_id,
                "verdict": verdict.verdict,
                "reason": json.dumps([issue.to_dict() for issue in verdict.issues], sort_keys=True),
            },
            source_type="r8-verifier",
            source_id=self.config.mission_id,
            idempotency_key=f"mission.reverified:{self.config.mission_id}",
            event_id=self._next_event_id("reverified"),
            occurred_at=utc_now(),
        )
        return {"inspection": inspection, "verdict": verdict}

    def _fresh_verify(
        self, mission_dir: Path, clone: Path, result: WorkerResult
    ) -> VerifierVerdict:
        if self._verifier_factory is not None:
            verifier = self._verifier_factory(Path(self.config.python), clone)
            return verifier.verify(clone, result, list(self.config.required_tests))
        verdict_path = mission_dir / "verdict.json"
        command = [
            self.config.python,
            "-m",
            "jarvis_foundry.orchestration.verify_cli",
            "--mission",
            str(mission_dir / "mission_context.json"),
            "--clone",
            str(clone),
            "--result",
            str(mission_dir / "worker_result.json"),
            "--verdict",
            str(verdict_path),
            "--verifier-id",
            f"fresh-verifier-{self.config.mission_id}",
            "--verified-at",
            utc_now(),
        ]
        completed = subprocess.run(  # nosec B603 B607 - no shell, fixed python path
            command,
            capture_output=True,
            text=True,
            timeout=self.config.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0 or not verdict_path.is_file():
            return VerifierVerdict(
                verdict="FAIL",
                issues=(
                    VerifierIssue(
                        "verifier",
                        "fresh verifier subprocess failed: " + completed.stdout[-500:],
                    ),
                ),
                verifier_id=f"fresh-verifier-{self.config.mission_id}",
                verified_at=utc_now(),
                test_evidence={},
            )
        payload = json.loads(verdict_path.read_text(encoding="utf-8"))
        return VerifierVerdict(
            verdict=str(payload["verdict"]),
            issues=tuple(
                VerifierIssue(field=str(item["field"]), detail=str(item["detail"]))
                for item in payload.get("issues", [])
            ),
            verifier_id=str(payload["verifier_id"]),
            verified_at=str(payload["verified_at"]),
            test_evidence=payload.get("test_evidence", {}),
        )

    def _accept_or_reject(
        self,
        mission_dir: Path,
        verdict: VerifierVerdict,
        inspection: dict[str, Any],
    ) -> str:
        accepted = (
            verdict.verdict == "PASS" and inspection["integrity_clean"] and self._source_unchanged()
        )
        event_type = "mission.accepted" if accepted else "mission.rejected"
        payload: dict[str, Any] = {
            "mission_id": self.config.mission_id,
            "accepted_by": "r8-orchestrator",
            "artifact_hashes": {
                path: entry["sha256"] for path, entry in inspection["actual_files"].items()
            },
            "reconciled": False,
        }
        if not accepted:
            payload = {
                "mission_id": self.config.mission_id,
                "rejected_by": "r8-orchestrator",
                "reasons": [
                    f"verdict={verdict.verdict}",
                    f"integrity_clean={inspection['integrity_clean']}",
                    f"source_unchanged={self._source_unchanged()}",
                ],
            }
        record_event(
            self.ledger,
            event_type=event_type,
            payload=payload,
            source_type="r8-orchestrator",
            source_id=self.config.mission_id,
            idempotency_key=f"{event_type}:{self.config.mission_id}",
            event_id=self._next_event_id("decision"),
            occurred_at=utc_now(),
        )
        if accepted:
            self.store.append_mission_link(
                MissionLink(
                    mission_id=self.config.mission_id,
                    goal_id=self.config.goal_id,
                    candidate_id=self.config.candidate_id,
                    decision_id=self.config.decision_id,
                    ledger_event_id=f"{event_type}:{self.config.mission_id}",
                    ledger_header_hash=self.ledger.head_hash,
                    created_at=utc_now(),
                ),
                idempotency_key=f"mission_link:{self.config.mission_id}",
            )
        return "ACCEPTED" if accepted else "REJECTED"

    def _reconcile_and_rollback(
        self,
        clone: Path,
        baseline_snapshot: Path,
        accepted_snapshot: Path,
    ) -> dict[str, Any]:
        reconciliation = reconcile_mission(
            clone=clone,
            ledger=self.ledger,
            mission_id=self.config.mission_id,
            proving_root=self.proving_root,
        )
        record_event(
            self.ledger,
            event_type="mission.reconciled",
            payload={
                "mission_id": self.config.mission_id,
                "reconciliation_status": "RECONCILED" if reconciliation["reconciled"] else "DRIFT",
                "git_tree_hash": reconciliation["git_tree_hash"],
                "ledger_head_hash": reconciliation["ledger_head_hash"],
            },
            source_type="r8-orchestrator",
            source_id=self.config.mission_id,
            idempotency_key=f"mission.reconciled:{self.config.mission_id}",
            event_id=self._next_event_id("reconciled"),
            occurred_at=utc_now(),
        )
        rollback = demonstrate_rollback(
            clone=clone,
            baseline_snapshot=baseline_snapshot,
            accepted_snapshot=accepted_snapshot,
            baseline_tree_hash=self._baseline_hash,
            proving_root=self.proving_root,
        )
        record_event(
            self.ledger,
            event_type="mission.rollback_demonstrated",
            payload={
                "mission_id": self.config.mission_id,
                "baseline_tree_hash": rollback["baseline_tree_hash"],
                "restored_tree_hash": rollback["restored_tree_hash"],
                "restored": rollback["restored"],
            },
            source_type="r8-orchestrator",
            source_id=self.config.mission_id,
            idempotency_key=f"mission.rollback_demonstrated:{self.config.mission_id}",
            event_id=self._next_event_id("rollback"),
            occurred_at=utc_now(),
        )
        return {"reconciliation": reconciliation, "rollback": rollback}

    def _source_unchanged(self) -> bool:
        after = inspect_project(Path(self.config.source_project), captured_at=utc_now())
        tree_unchanged = bool(after["tree_hash"] == self._source_before["tree_hash"])
        head_unchanged = bool(after["head"] == self._source_before["head"])
        porcelain_unchanged = bool(after["porcelain"] == self._source_before["porcelain"])
        return tree_unchanged and head_unchanged and porcelain_unchanged

    def _write_json(self, path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _run_build(self, mission_dir: Path) -> dict[str, Any]:
        clone, baseline_snapshot, prepared = self._prepare_clone(mission_dir)
        self._baseline_hash = str(prepared["baseline_hash"])
        dispatch_state = self._dispatch_and_ingest(mission_dir, clone)
        outcome = dispatch_state["outcome"]
        if not outcome.valid or outcome.result is None:
            return self._rejected_report(mission_dir, dispatch_state, outcome)
        result = outcome.result
        verified_state = self._inspect_and_verify(mission_dir, clone, result)
        verdict = verified_state["verdict"]
        inspection = verified_state["inspection"]
        status = self._accept_or_reject(mission_dir, verdict, inspection)
        accepted_snapshot = self.proving_root / "snapshots" / self.config.mission_id / "accepted"
        reconciled = self._reconcile_and_rollback(clone, baseline_snapshot, accepted_snapshot)
        report = self._mission_report(
            mission_dir,
            dispatch_state=dispatch_state,
            inspection=inspection,
            verdict=verdict,
            status=status,
            reconciled=reconciled,
            source_after=self._source_unchanged(),
        )
        return report

    def _run_repair(self, mission_dir: Path) -> dict[str, Any]:
        return self._run_build(mission_dir)

    def _run_waiting(self, mission_dir: Path) -> dict[str, Any]:
        reason = "no new useful mission exists after accepted R8 missions"
        record_event(
            self.ledger,
            event_type="mission.waiting",
            payload={
                "source": self.config.source_project,
                "reason": reason,
                "next_action": self.config.next_action,
                "mission_id": self.config.mission_id,
            },
            source_type="r8-orchestrator",
            source_id=self.config.mission_id,
            idempotency_key=f"mission.waiting:{self.config.mission_id}",
            event_id=self._next_event_id("waiting"),
            occurred_at=utc_now(),
        )
        self._write_json(mission_dir / "waiting.json", {"status": "WAITING", "reason": reason})
        return {
            "schema": "jarvis-r8-mission-report-v1",
            "mission_id": self.config.mission_id,
            "kind": "waiting",
            "status": "WAITING",
            "reason": reason,
            "next_action": self.config.next_action,
            "ledger_head_hash": self.ledger.head_hash,
            "generated_utc": utc_now(),
        }

    def _rejected_report(
        self,
        mission_dir: Path,
        dispatch_state: dict[str, Any],
        outcome: Any,
    ) -> dict[str, Any]:
        run: DispatchRun = dispatch_state["dispatch"]
        return {
            "schema": "jarvis-r8-mission-report-v1",
            "mission_id": self.config.mission_id,
            "kind": self.config.kind,
            "status": "REJECTED",
            "worker_id": run.worker_id,
            "rejection_reasons": list(outcome.reasons),
            "dispatch_exit_code": run.exit_code,
            "dispatch_wall_seconds": run.wall_seconds,
            "dispatch_output": run.output[-2000:],
            "worker_result_hash": outcome.result_hash,
            "ledger_head_hash": self.ledger.head_hash,
            "ledger": {
                "head_hash": self.ledger.head_hash,
                "chain_valid": self.ledger.verify_chain().valid,
                "events": [
                    {
                        "event_type": event.event_type,
                        "sequence": event.sequence,
                        "event_id": event.event_id,
                    }
                    for event in self.ledger.replay()
                ],
            },
            "source_unchanged": self._source_unchanged(),
            "generated_utc": utc_now(),
        }

    def _mission_report(
        self,
        mission_dir: Path,
        *,
        dispatch_state: dict[str, Any],
        inspection: dict[str, Any],
        verdict: VerifierVerdict,
        status: str,
        reconciled: dict[str, Any],
        source_after: bool,
    ) -> dict[str, Any]:
        run: DispatchRun = dispatch_state["dispatch"]
        outcome = dispatch_state["outcome"]
        result = outcome.result
        result_payload = result.to_dict() if result is not None else {}
        evidence_hashes: dict[str, str] = {}
        for name in mission_dir.glob("*"):
            if name.is_file():
                from jarvis_foundry.orchestration.paths import sha256_file

                evidence_hashes[name.name] = sha256_file(name)
        return {
            "schema": "jarvis-r8-mission-report-v1",
            "mission_id": self.config.mission_id,
            "kind": self.config.kind,
            "status": status,
            "worker_id": run.worker_id,
            "worker_result": result_payload,
            "dispatch": {
                "command": list(run.command),
                "exit_code": run.exit_code,
                "wall_seconds": run.wall_seconds,
                "output_tail": run.output[-4000:],
            },
            "inspection": inspection,
            "verdict": verdict.to_dict(),
            "reconciliation": reconciled["reconciliation"],
            "rollback": reconciled["rollback"],
            "source_before": self._source_before,
            "source_after_unchanged": source_after,
            "ledger": {
                "head_hash": self.ledger.head_hash,
                "chain_valid": self.ledger.verify_chain().valid,
                "events": [
                    {
                        "event_type": event.event_type,
                        "sequence": event.sequence,
                        "event_id": event.event_id,
                    }
                    for event in self.ledger.replay()
                ],
            },
            "evidence_hashes": evidence_hashes,
            "recommended_next_action": self.config.next_action,
            "generated_utc": utc_now(),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    config = OrchestrationConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8")))
    report = Orchestrator(config).run()
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("status") in {"ACCEPTED", "REJECTED", "WAITING"} else 1


if __name__ == "__main__":
    sys.exit(main())
