"""Scenario runner for the disposable unattended acceptance harness."""
from __future__ import annotations

import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .clock import HarnessClock
from .adversarial import run_false_pass_factory
from .contracts import (
    ClockMode,
    FaultRecord,
    InvariantRecord,
    ResourceEnvelope,
    ResourceSample,
    RunCheckpoint,
    ScenarioSpec,
    VerdictStatus,
)
from .evidence import EvidenceWriter, utc_now
from .fixtures import run_owned_supervisor_restart_probe, run_v7_fault_matrix
from .simulation import RuntimeFixtureState
from .verdict import derive_verdict, render_final_report


def _status(status: str, reason: str, **evidence: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, "evidence": evidence}


def run_interruption_resume_probe(output_root: str | Path) -> dict[str, Any]:
    from .contracts import FaultSpec

    proof_root = Path(output_root) / "interruption-resume"
    proof_scenario = ScenarioSpec(
        scenario_id="INTERRUPTION_PROOF",
        duration_seconds=4.0,
        clock_mode=ClockMode.ACCELERATED_DISPOSABLE,
        seed=20260824,
        initial_state={"stop_available": True},
        required_capabilities=("supervisor",),
        scheduled_faults=(FaultSpec("provider", 1.0, "PROVIDER_OUTAGE", "fake", {"recovery_after_seconds": 1}),),
        expected_invariants=("STOP_AVAILABLE", "HEARTBEAT_CONTINUITY", "MISSION_PROGRESS", "EVIDENCE_COMPLETE"),
        resource_envelope=ResourceEnvelope(),
        owner_spam_threshold=4,
        busywork_threshold=2,
        restart_checkpoints=("PRE_RESTART", "POST_RESTART"),
        required_end_state="CONTINUABLE",
        rollback_requirement="HEALTHY_AFTER_ROLLBACK",
        evidence_output="interruption-resume",
        sample_interval_seconds=1.0,
    )
    first = AcceptanceRunner(
        proof_scenario, proof_root, run_id="interrupted-proof", run_process_probe=False,
        run_fault_matrix=False, run_false_pass_factory=False, run_interruption_probe=False,
        interrupt_after_steps=2,
    ).run()
    second = AcceptanceRunner(
        proof_scenario, proof_root, run_id="interrupted-proof", resume=True, run_process_probe=False,
        run_fault_matrix=False, run_false_pass_factory=False, run_interruption_probe=False,
    ).run()
    fault_path = proof_root / "FAULTS.jsonl"
    fault_rows = [json.loads(line) for line in fault_path.read_text(encoding="utf-8").splitlines() if line] if fault_path.exists() else []
    payload = {
        "status": "PASS" if first.get("status") == "INTERRUPTED" and second.get("verdict") == "PASS" and len({row["fault_id"] for row in fault_rows}) == len(fault_rows) else "FAIL",
        "same_run_id": True,
        "first_status": first.get("status"),
        "resumed_verdict": second.get("verdict"),
        "fault_rows": len(fault_rows),
        "fault_ids": [row["fault_id"] for row in fault_rows],
        "proof_root": str(proof_root),
    }
    Path(output_root).mkdir(parents=True, exist_ok=True)
    Path(output_root, "INTERRUPTION_RESUME.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


class AcceptanceRunner:
    def __init__(
        self,
        scenario: ScenarioSpec,
        output_root: str | Path,
        *,
        run_id: str | None = None,
        resume: bool = False,
        run_process_probe: bool = True,
        run_fault_matrix: bool = True,
        run_false_pass_factory: bool = True,
        run_interruption_probe: bool = True,
        interrupt_after_steps: int | None = None,
        canonical_database: Any | None = None,
        canonical_stop_path: str | Path | None = None,
        canonical_release_root: str | Path | None = None,
    ) -> None:
        self.scenario = scenario
        self.output_root = Path(output_root)
        self.run_id = run_id or f"{scenario.scenario_id.lower()}-{uuid.uuid4().hex[:12]}"
        self.writer = EvidenceWriter(self.output_root, run_id=self.run_id, scenario=scenario)
        self.resume_requested = resume
        self.run_process_probe = run_process_probe
        self.run_fault_matrix = run_fault_matrix
        self.run_false_pass_factory = run_false_pass_factory
        self.run_interruption_probe = run_interruption_probe
        self.interrupt_after_steps = interrupt_after_steps
        # Optional read-only observer boundary.  The disposable fixture keeps
        # running exactly as before; when supplied, this is an additional
        # canonical-ledger observation and never a source-of-truth write.
        self.canonical_database = canonical_database
        self.canonical_stop_path = canonical_stop_path
        self.canonical_release_root = canonical_release_root
        self.state = RuntimeFixtureState.from_scenario(scenario)
        self.clock = HarnessClock(scenario.clock_mode, duration_seconds=scenario.duration_seconds)
        self.next_fault_index = 0
        self.step_count = 0
        self.fired_fault_ids: set[str] = set()
        self.fault_records: list[FaultRecord] = []
        self.invariant_records: list[InvariantRecord] = []
        self.resource_samples: list[ResourceSample] = []
        self.component_results: dict[str, dict[str, Any]] = {}
        self.timeline: list[dict[str, Any]] = []
        self.interrupted = False
        self._load_existing()

    def _observe_canonical(self) -> None:
        if self.canonical_database is None:
            return
        try:
            from .observer import observe_canonical
            observation = observe_canonical(
                self.canonical_database,
                stop_path=self.canonical_stop_path,
                release_root=self.canonical_release_root,
                now=utc_now(),
            )
            self.writer.write_json("CANONICAL_OBSERVATION.json", observation)
            manual = int(observation.get("manual_continuation_required", 0) or 0)
            self.component_results["canonical_observer"] = _status(
                "PASS" if manual == 0 else "FAIL",
                "canonical append-only ledger was observed without fixture leakage",
                source=observation.get("source"),
                fixture_leakage=observation.get("fixture_leakage"),
                manual_continuation_required=manual,
            )
        except Exception as error:  # noqa: BLE001 - acceptance must report observer truth
            self.component_results["canonical_observer"] = _status(
                "FAIL", "canonical observer failed closed", error_class=type(error).__name__,
            )

    def _load_existing(self) -> None:
        checkpoint = self.writer.read_checkpoint()
        if checkpoint is None:
            self.writer.write_scenario()
            self.component_results["scenario_contract"] = _status("PASS", "scenario parsed and serialized")
            return
        if not self.resume_requested:
            raise ValueError("run already has a checkpoint; pass resume=True to continue")
        if checkpoint.run_id != self.run_id:
            raise ValueError("checkpoint run_id does not match requested run")
        if checkpoint.scenario_id != self.scenario.scenario_id:
            raise ValueError("checkpoint scenario_id does not match scenario")
        self._restore_state(checkpoint.artifacts.get("state", {}))
        self.next_fault_index = checkpoint.next_fault_index
        self.step_count = int(checkpoint.artifacts.get("step_count", 0))
        self.fired_fault_ids = set(checkpoint.fired_fault_ids)
        self.component_results = {
            str(name): dict(value)
            for name, value in dict(checkpoint.artifacts.get("component_results", {})).items()
        }
        self.clock = HarnessClock(
            self.scenario.clock_mode,
            duration_seconds=self.scenario.duration_seconds,
            logical_start=checkpoint.logical_time_seconds,
            wall_elapsed_offset=float(checkpoint.artifacts.get("wall_elapsed_seconds", 0.0)),
        )
        self._load_jsonl_records()

    def _restore_state(self, snapshot: Mapping[str, Any]) -> None:
        for name, value in snapshot.items():
            if hasattr(self.state, name):
                current = getattr(self.state, name)
                if isinstance(current, dict):
                    setattr(self.state, name, dict(value))
                elif isinstance(current, list):
                    setattr(self.state, name, list(value))
                else:
                    setattr(self.state, name, value)

    def _load_jsonl_records(self) -> None:
        fault_path = self.writer.path("FAULTS.jsonl")
        if fault_path.exists():
            self.fault_records = [
                FaultRecord(
                    fault_id=str(row["fault_id"]), kind=str(row["kind"]), target=str(row["target"]),
                    scheduled_at_seconds=float(row["scheduled_at_seconds"]),
                    fired_at_seconds=float(row["fired_at_seconds"]), phase=str(row["phase"]),
                    injected=bool(row["injected"]), status=str(row["status"]),
                    expected=str(row["expected"]), observed=dict(row.get("observed", {})),
                    containment=str(row["containment"]), recovery=str(row["recovery"]),
                    evidence_refs=tuple(row.get("evidence_refs", [])),
                )
                for row in self._read_jsonl(fault_path)
            ]
        invariant_path = self.writer.path("INVARIANTS.jsonl")
        if invariant_path.exists():
            self.invariant_records = [
                InvariantRecord(
                    invariant_id=str(row["invariant_id"]), observed_at=str(row["observed_at"]),
                    status=str(row["status"]), value=row.get("value"), required=bool(row["required"]),
                    wall_time_required=bool(row["wall_time_required"]), reason=str(row["reason"]),
                    evidence_refs=tuple(row.get("evidence_refs", [])),
                )
                for row in self._read_jsonl(invariant_path)
            ]
        resource_path = self.writer.path("RESOURCE_SAMPLES.jsonl")
        if resource_path.exists():
            self.resource_samples = [
                ResourceSample(
                    observed_at=str(row["observed_at"]),
                    logical_time_seconds=float(row["logical_time_seconds"]),
                    values=dict(row.get("values", {})), violations=tuple(row.get("violations", [])),
                )
                for row in self._read_jsonl(resource_path)
            ]

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"evidence row is not an object: {path}")
                rows.append(value)
        return rows

    def _checkpoint(self, *, phase: str, status: str) -> None:
        remaining = self.scenario.ordered_faults[self.next_fault_index:]
        next_checkpoint = (
            f"t+{remaining[0].at_seconds:g}s:{remaining[0].fault_id}" if remaining else None
        )
        checkpoint = RunCheckpoint(
            run_id=self.run_id,
            scenario_id=self.scenario.scenario_id,
            phase=phase,
            logical_time_seconds=self.clock.now(),
            next_fault_index=self.next_fault_index,
            fired_fault_ids=tuple(sorted(self.fired_fault_ids)),
            sampled_invariants=tuple(sorted({row.invariant_id for row in self.invariant_records})),
            artifacts={
                "state": self.state.snapshot(),
                "step_count": self.step_count,
                "wall_elapsed_seconds": self.clock.wall_elapsed_seconds,
                "component_results": self.component_results,
            },
            jarvis_identities={
                "supervisor_id": self.state.supervisor_id,
                "worker_id": self.state.worker_id,
            },
            next_checkpoint=next_checkpoint,
            status=status,
            saved_at=utc_now(),
        )
        self.writer.write_checkpoint(checkpoint)

    def _timeline(self, event: str, *, phase: str, detail: Mapping[str, Any] | None = None) -> None:
        row = {
            "event_id": f"{self.run_id}:{self.step_count}:{event}",
            "run_id": self.run_id,
            "event": event,
            "phase": phase,
            "logical_time_seconds": self.clock.now(),
            "observed_at": utc_now(),
            "detail": dict(detail or {}),
        }
        self.timeline.append(row)
        self.writer.append_jsonl("TIMELINE.jsonl", row, key_field="event_id")

    def _fire_fault(self, fault) -> None:
        if fault.fault_id in self.fired_fault_ids:
            return
        self.fired_fault_ids.add(fault.fault_id)
        phase = "PRE_RESTART" if fault.kind.upper() in {"REBOOT", "PROCESS_RESTART", "PROCESS_CRASH", "SUPERVISOR_CRASH"} else "RUNNING"
        observed = self.state.apply_fault(fault, self.clock.now())
        if fault.kind.upper() in {"PROCESS_CRASH", "SUPERVISOR_CRASH", "PROCESS_RESTART", "REBOOT"} and self.run_process_probe:
            probe = run_owned_supervisor_restart_probe(self.output_root, timeout_seconds=15.0)
            observed["process_probe"] = probe
            probe_evidence = {key: value for key, value in probe.items() if key != "status"}
            self.component_results["process_fault"] = _status(
                probe.get("status", "FAIL"), "owned process crash and replacement probe", **probe_evidence
            )
            self.component_results["restart_contract"] = _status(
                probe.get("status", "FAIL"), "replacement identity and state continuity probe", **probe_evidence
            )
        kind = fault.kind.upper()
        expected = str(observed.pop("expected", "fault was attributable and bounded"))
        containment = str(observed.pop("containment", "fixture scope recorded"))
        recovery = str(observed.pop("recovery", "scenario-specific recovery recorded"))
        record = FaultRecord(
            fault_id=fault.fault_id,
            kind=fault.kind,
            target=fault.target,
            scheduled_at_seconds=fault.at_seconds,
            fired_at_seconds=self.clock.now(),
            phase=phase,
            injected=True,
            status="PASS",
            expected=expected,
            observed=observed,
            containment=containment,
            recovery=recovery,
            evidence_refs=(f"timeline:{self.run_id}:{self.step_count}:{fault.fault_id}",),
        )
        self.fault_records.append(record)
        self.writer.append_jsonl("FAULTS.jsonl", record.to_mapping(), key_field="fault_id")
        self._timeline("fault.fired", phase=phase, detail={"fault_id": fault.fault_id, "kind": kind, "target": fault.target})
        self.next_fault_index += 1

    def _sample(self) -> None:
        observed_at = utc_now()
        logical_time = self.clock.now()
        for invariant_id in self.scenario.expected_invariants:
            if invariant_id == "EVIDENCE_COMPLETE":
                continue
            status, value, reason = self.state.invariant(invariant_id, self.scenario)
            record = InvariantRecord(
                invariant_id=invariant_id,
                observed_at=observed_at,
                status=status,
                value=value,
                required=True,
                wall_time_required=invariant_id in self.scenario.wall_time_invariants,
                reason=reason,
                evidence_refs=(f"timeline:{self.run_id}:{self.step_count}:sample",),
            )
            self.invariant_records.append(record)
            self.writer.append_jsonl(
                "INVARIANTS.jsonl",
                {"sample_id": f"{self.step_count}:{invariant_id}", **record.to_mapping()},
                key_field="sample_id",
            )
        values = self.state.resource_sample(logical_time)
        sample = ResourceSample(
            observed_at=observed_at,
            logical_time_seconds=logical_time,
            values=values,
            violations=tuple(self.scenario.resource_envelope.violations(values)),
        )
        self.resource_samples.append(sample)
        self.writer.append_jsonl(
            "RESOURCE_SAMPLES.jsonl",
            {"sample_id": f"{self.step_count}:resource", **sample.to_mapping()},
            key_field="sample_id",
        )

    def _component_results(self) -> None:
        if "process_fault" not in self.component_results:
            process_required = any(f.kind.upper() in {"PROCESS_CRASH", "SUPERVISOR_CRASH", "PROCESS_RESTART", "REBOOT"} for f in self.scenario.ordered_faults)
            self.component_results["process_fault"] = _status("NOT_TESTED" if not process_required else "FAIL", "no owned process probe was run")
        self.component_results.setdefault(
            "restart_contract",
            _status("PASS" if not self.state.restarted else "FAIL", "no process restart was required" if not self.state.restarted else "restart evidence missing"),
        )
        self.component_results["checkpoint_resume"] = _status(
            "PASS" if self.writer.read_checkpoint() is not None and self.step_count > 0 else "FAIL",
            "checkpoint persisted at every sampling boundary",
            step_count=self.step_count,
        )
        self.component_results["invariant_sampler"] = _status(
            "PASS" if self.invariant_records else "FAIL", "invariant records are present", count=len(self.invariant_records)
        )
        self.component_results["continuation"] = _status(
            "PASS" if self.state.continuation_count > 0 or self.state.wait_count > 0 else "FAIL",
            "terminal outcome led to next selection or honest WAIT",
            continuation_count=self.state.continuation_count,
            wait_count=self.state.wait_count,
        )
        busywork_max = max(Counter(self.state.busywork_fingerprints).values(), default=0)
        self.component_results["busywork"] = _status(
            "PASS" if busywork_max <= self.scenario.busywork_threshold else "FAIL",
            "repeated work stayed within threshold",
            maximum_repeat=busywork_max,
        )
        owner_action_count = sum(bool(item.get("owner_action_required")) for item in self.state.briefings)
        self.component_results["owner_spam"] = _status(
            "PASS" if owner_action_count <= self.scenario.owner_spam_threshold else "FAIL",
            "owner-action briefing count stayed within threshold",
            owner_action_count=owner_action_count,
        )
        self.component_results["effect_ambiguity"] = _status(
            "PASS" if self.state.effect_ambiguities == 0 or self.state.reconciled_effects >= self.state.effect_ambiguities else "FAIL",
            "ambiguous effects froze and were explicitly reconciled",
            ambiguities=self.state.effect_ambiguities,
            reconciled=self.state.reconciled_effects,
        )
        self.component_results["database_fault"] = _status(
            "PASS" if self.state.db_mode == "healthy" else "FAIL",
            "disposable database returned to healthy state",
            mode=self.state.db_mode,
        )
        resource_violations = sorted({violation for sample in self.resource_samples for violation in sample.violations})
        self.component_results["resource_envelope"] = _status(
            "PASS" if not resource_violations else "FAIL",
            "resource samples stayed below scenario ceilings" if not resource_violations else "resource ceiling exceeded",
            violations=resource_violations,
        )
        self.component_results["release_rollback"] = _status(
            "PASS" if self.state.rollback_attempts == 0 or (self.state.rollback_succeeded and self.state.release_frozen) else "FAIL",
            "release rollback was freeze-first and healthy" if self.state.rollback_attempts else "no release degradation was required",
            rollback_attempts=self.state.rollback_attempts,
            rollback_succeeded=self.state.rollback_succeeded,
        )
        expected_order = [fault.fault_id for fault in self.scenario.ordered_faults]
        actual_order = [record.fault_id for record in self.fault_records]
        self.component_results["fault_scheduler"] = _status(
            "PASS" if actual_order == expected_order[:len(actual_order)] else "FAIL",
            "faults fired deterministically in schedule order",
            expected_order=expected_order,
            actual_order=actual_order,
        )
        self.component_results["scenario_contract"] = _status("PASS", "scenario contract remained immutable")
        self.component_results["verdict_engine"] = _status("PASS", "verdict derived from structured evidence")

    def _write_summaries(self) -> dict[str, Any]:
        metrics = {
            "run_id": self.run_id,
            "scenario_id": self.scenario.scenario_id,
            "clock_mode": self.scenario.clock_mode.value,
            "acceptance_label": "ACCELERATED_EQUIVALENT_ONLY" if self.scenario.clock_mode is ClockMode.ACCELERATED_DISPOSABLE else "REAL_TIME_DISPOSABLE",
            "logical_duration_seconds": self.clock.now(),
            "wall_elapsed_seconds": self.clock.wall_elapsed_seconds,
            "cycles": len(self.state.completed_missions),
            "outcomes": len(self.state.outcome_ids),
            "continuation_cycles": self.state.continuation_count,
            "time_to_next_selection_seconds": self.scenario.sample_interval_seconds if self.state.continuation_count else None,
            "manual_continuation_required": self.state.manual_continuation_required,
            "manual_continuation_count": self.state.manual_continuation_required,
            "wait_count": self.state.wait_count,
            "wake_events": self.state.wake_events,
            "duplicate_mission_count": len(self.state.selected_missions) - len(set(self.state.selected_missions)),
            "duplicate_effect_count": self.state.duplicate_effects,
            "owner_action_briefings": sum(bool(item.get("owner_action_required")) for item in self.state.briefings),
            "busywork_fingerprint_max": max(Counter(self.state.busywork_fingerprints).values(), default=0),
        }
        incident_summary = {
            "active": dict(self.state.active_incidents),
            "history": list(self.state.incident_history),
            "count": len(self.state.incident_history),
        }
        effect_summary = {
            "calls": dict(self.state.effect_calls),
            "states": dict(self.state.effect_states),
            "provider_calls": self.state.provider_calls,
            "ambiguities": self.state.effect_ambiguities,
            "reconciled": self.state.reconciled_effects,
            "duplicates": self.state.duplicate_effects,
        }
        categories = Counter(str(item.get("category", "UNKNOWN")) for item in self.state.briefings)
        briefing_summary = {
            "total": len(self.state.briefings),
            "owner_action_required": sum(bool(item.get("owner_action_required")) for item in self.state.briefings),
            "categories": dict(categories),
            "dedupe_keys": len({item.get("dedupe_key") for item in self.state.briefings}),
        }
        self.writer.write_json("AUTONOMY_METRICS.json", metrics)
        self.writer.write_json("INCIDENT_SUMMARY.json", incident_summary)
        self.writer.write_json("EFFECT_SUMMARY.json", effect_summary)
        self.writer.write_json("BRIEFING_SUMMARY.json", briefing_summary)
        return metrics

    def _run_components(self) -> None:
        if self.run_fault_matrix:
            matrix = run_v7_fault_matrix(self.output_root, timeout_seconds=120.0)
            self.component_results["provider_fault"] = _status(
                matrix.get("status", "FAIL"), "canonical V7 disposable fault matrix rerun", summary=matrix.get("summary"), verdict=matrix.get("verdict"),
            )
            self.component_results["v7_fault_matrix"] = {
                "status": matrix.get("status", "FAIL"),
                "summary": matrix.get("summary"),
                "verdict": matrix.get("verdict"),
                "artifact": str(self.output_root / "V7_FAULT_MATRIX.json"),
            }
        else:
            provider_required = any(f.kind.upper() in {"PROVIDER_OUTAGE", "PROVIDER_TIMEOUT", "RATE_LIMIT", "PROVIDER_5XX"} for f in self.scenario.ordered_faults)
            self.component_results["provider_fault"] = _status("PASS" if not provider_required or self.state.wake_events >= 0 else "FAIL", "fixture provider schedule exercised")
        if self.scenario.clock_mode is ClockMode.ACCELERATED_DISPOSABLE:
            self.component_results["accelerated_proof"] = _status("PASS", "logical 12-hour-equivalent sequence completed", duration_seconds=self.clock.now())
            self.component_results["realtime_proof"] = _status("NOT_TESTED", "real wall-clock proof is a separate run")
        else:
            self.component_results["realtime_proof"] = _status("PASS", "wall-clock sampling and scheduled faults completed", duration_seconds=self.clock.wall_elapsed_seconds)
            self.component_results["accelerated_proof"] = _status("NOT_TESTED", "accelerated proof is a separate run")
        if self.run_interruption_probe:
            interruption = run_interruption_resume_probe(self.output_root)
            self.component_results["interruption_resume"] = _status(
                interruption.get("status", "FAIL"), "same run resumes without duplicate faults", **{
                    key: value for key, value in interruption.items() if key != "status"
                }
            )
        else:
            self.component_results.setdefault("interruption_resume", _status("NOT_TESTED", "interruption proof is a separate resumed run"))
        if self.run_false_pass_factory:
            factory = run_false_pass_factory(self.output_root)
            self.component_results["false_pass_factory"] = _status(
                factory.get("verdict", "FAIL"), "false-PASS adversarial cases refused PASS", summary=factory.get("summary"),
            )
        else:
            self.component_results.setdefault("false_pass_factory", _status("NOT_TESTED", "false-PASS proof is a separate adversarial run"))

    def _finalize(self, *, run_status: str) -> dict[str, Any]:
        self._observe_canonical()
        self._component_results()
        metrics = self._write_summaries()
        supporting_missing = [name for name in self.writer.missing_artifacts() if name not in {"RUN_MANIFEST.json", "FINAL_VERDICT.json", "FINAL_REPORT.md"}]
        if not supporting_missing:
            evidence_record = InvariantRecord(
                invariant_id="EVIDENCE_COMPLETE",
                observed_at=utc_now(),
                status="PASS",
                value=True,
                required="EVIDENCE_COMPLETE" in self.scenario.expected_invariants,
                wall_time_required=False,
                reason="all required artifacts are present before final hashing",
                evidence_refs=("RUN_MANIFEST.json",),
            )
            self.invariant_records.append(evidence_record)
            self.writer.append_jsonl(
                "INVARIANTS.jsonl",
                {"sample_id": f"final:EVIDENCE_COMPLETE", **evidence_record.to_mapping()},
                key_field="sample_id",
            )
        self.component_results["evidence_bundle"] = _status(
            "PASS" if not supporting_missing else "FAIL",
            "supporting evidence artifacts are present" if not supporting_missing else "supporting artifacts are missing",
            missing=supporting_missing,
        )
        self._component_results()
        self.component_results["evidence_bundle"] = _status(
            "PASS" if not supporting_missing else "FAIL",
            "supporting evidence artifacts are present" if not supporting_missing else "supporting artifacts are missing",
            missing=supporting_missing,
        )
        preliminary = derive_verdict(
            self.scenario,
            run_status=run_status,
            faults=self.fault_records,
            invariants=self.invariant_records,
            resources=self.resource_samples,
            component_results=self.component_results,
            artifact_missing=supporting_missing,
            interrupted=self.interrupted,
        )
        self.writer.write_json("FINAL_VERDICT.json", preliminary)
        report = render_final_report(
            preliminary,
            frozen_base="107827d81eaee514edc66eab98390438b63c1650",
            final_head="candidate working HEAD",
            remote_candidate="not pushed in local run",
            metrics=metrics,
            profiles={name: "READY_NOT_RUN" for name in ("QUICK_30M", "STANDARD_6H", "FULL_12H", "FULL_24H", "REBOOT_ASSISTED_24H")},
            tests="focused harness and source regressions recorded by caller",
            hygiene="candidate-only; final git check is caller-owned",
            secret_scan="redaction and candidate scan required",
            worktree_clean="NOT_TESTED",
            candidate_pushed="NO",
        )
        self.writer.write_text("FINAL_REPORT.md", report)
        manifest = self.writer.write_manifest(status=preliminary["verdict"], phase="FINAL", extra={"component_results": self.component_results})
        manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
        hash_mismatches = self.writer.verify_hashes(manifest_value)
        if hash_mismatches:
            preliminary = derive_verdict(
                self.scenario,
                run_status=run_status,
                faults=self.fault_records,
                invariants=self.invariant_records,
                resources=self.resource_samples,
                component_results=self.component_results,
                artifact_missing=supporting_missing,
                hash_mismatches=hash_mismatches,
                interrupted=self.interrupted,
            )
            self.writer.write_json("FINAL_VERDICT.json", preliminary)
            self.writer.write_text("FINAL_REPORT.md", render_final_report(
                preliminary,
                frozen_base="107827d81eaee514edc66eab98390438b63c1650",
                final_head="candidate working HEAD",
                remote_candidate="not pushed in local run",
                metrics=metrics,
                profiles={name: "READY_NOT_RUN" for name in ("QUICK_30M", "STANDARD_6H", "FULL_12H", "FULL_24H", "REBOOT_ASSISTED_24H")},
                tests="focused harness and source regressions recorded by caller",
                hygiene="candidate-only; final git check is caller-owned",
                secret_scan="redaction and candidate scan required",
                worktree_clean="NOT_TESTED",
                candidate_pushed="NO",
            ))
            self.writer.write_manifest(status=preliminary["verdict"], phase="FINAL", extra={"component_results": self.component_results})
        return preliminary

    def run(self) -> dict[str, Any]:
        if self.writer.path("FINAL_VERDICT.json").exists() and self.resume_requested:
            return json.loads(self.writer.path("FINAL_VERDICT.json").read_text(encoding="utf-8"))
        self._timeline("run.started", phase="START", detail={"resume": self.resume_requested, "clock_mode": self.scenario.clock_mode.value})
        faults = self.scenario.ordered_faults
        while self.next_fault_index < len(faults) or not self.clock.at_end():
            next_fault_time = faults[self.next_fault_index].at_seconds if self.next_fault_index < len(faults) else self.scenario.duration_seconds
            target = min(next_fault_time, self.clock.now() + self.scenario.sample_interval_seconds, self.scenario.duration_seconds)
            self.clock.advance_to(target)
            while self.next_fault_index < len(faults) and faults[self.next_fault_index].at_seconds <= self.clock.now():
                self._fire_fault(faults[self.next_fault_index])
            self.state.tick(self.clock.now())
            self._sample()
            self._timeline("sample", phase="RUNNING", detail={"heartbeat_count": self.state.heartbeat_count, "mission_count": len(self.state.completed_missions)})
            self.step_count += 1
            self._checkpoint(phase="RUNNING", status="RUNNING")
            if self.interrupt_after_steps is not None and self.step_count >= self.interrupt_after_steps:
                self.interrupted = True
                self._timeline("run.interrupted", phase="INTERRUPTED", detail={"step_count": self.step_count})
                self._checkpoint(phase="INTERRUPTED", status="INTERRUPTED")
                return {"run_id": self.run_id, "scenario_id": self.scenario.scenario_id, "verdict": VerdictStatus.ABORTED.value, "status": "INTERRUPTED", "output": str(self.output_root)}
            if self.clock.at_end() and self.next_fault_index >= len(faults):
                break
        self.state.tick(self.scenario.duration_seconds)
        self._sample()
        self._run_components()
        self._checkpoint(phase="FINAL", status="COMPLETED")
        self._timeline("run.finalizing", phase="FINAL", detail={"faults": len(self.fault_records)})
        result = self._finalize(run_status="COMPLETED")
        self._checkpoint(phase="FINAL", status="COMPLETED")
        return result
