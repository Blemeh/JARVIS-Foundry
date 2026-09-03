"""Canonical adapter-artifact trial, verification, adoption, and rollback.

The Adapter Factory is the sole producer of ``jarvis.capability.adapter.v1``
artifacts.  This module consumes that exact artifact; it never generates a
second wrapper or interprets a second solution descriptor.  Trial state is an
append-only event stream and only the Fabric Catalog can adopt or disable a
capability.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from ..clock import isoformat
from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..control.redaction import redact_text
from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash
from ..platform.windows_subprocess import headless_run_kwargs, sanitized_env
from .catalog import (
    capability_registration,
    catalog_truth_snapshot,
    disable_capability,
    observed_readiness,
    record_outcome,
    register_capability,
)
from .contracts import CapabilityMetadata
from .model import CapabilitySpec, CapabilityTrust, FabricError, Readiness, VerifyVerdict

if TYPE_CHECKING:  # pragma: no cover - imports are deferred to avoid cycles
    from ..adapter_factory.model import AdapterBuildArtifact


TRIAL_PROTOCOL = "jarvis.capability.trial.v1"
ADAPTER_PROTOCOL = "jarvis.capability.adapter.v1"
SOURCE_ID = "v12-canonical-artifact-trial"
EVENT_PREFIX = "fabric.capability.artifact_trial."

TRIAL_SELECTED = EVENT_PREFIX + "selected"
TRIAL_ENVIRONMENT_READY = EVENT_PREFIX + "environment_ready"
TRIAL_PROBE_PASSED = EVENT_PREFIX + "probe_passed"
TRIAL_CONTRACTS_PASSED = EVENT_PREFIX + "contracts_passed"
TRIAL_MISSION_COMPLETED = EVENT_PREFIX + "mission_completed"
TRIAL_VERIFIED = EVENT_PREFIX + "verified"
TRIAL_ADOPTION_DECIDED = EVENT_PREFIX + "adoption_decided"
TRIAL_ADOPTED = EVENT_PREFIX + "adopted"
TRIAL_REJECTED = EVENT_PREFIX + "rejected"
TRIAL_DISABLED = EVENT_PREFIX + "disabled"


class ArtifactTrialError(FabricError):
    """The canonical artifact is unsafe, malformed, or failed its trial."""


class ArtifactTrialStopped(RuntimeError):
    """Owner STOP/veto interrupted a resumable trial before adoption."""


class ArtifactTrialStatus(StrEnum):
    SELECTED = "SELECTED"
    ENVIRONMENT_READY = "ENVIRONMENT_READY"
    PROBE_PASSED = "PROBE_PASSED"
    CONTRACTS_PASSED = "CONTRACTS_PASSED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    VERIFIED = "VERIFIED"
    PROBATION = "PROBATION"
    REJECTED = "REJECTED"
    DISABLED = "DISABLED"
    RESTORED = "RESTORED"


@dataclass(frozen=True, slots=True)
class TrialMission:
    """One realistic read-only mission independently repeated by the verifier."""

    case_id: str
    capability_id: str
    payload: Mapping[str, Any]
    expected: Mapping[str, Any]
    fixtures: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("case_id", "capability_id"):
            value = str(getattr(self, name) or "").strip()
            if not value or len(value) > 256 or any(char.isspace() for char in value):
                raise ArtifactTrialError(f"{name} must be a bounded identity")
            object.__setattr__(self, name, value)
        for name in ("payload", "expected"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ArtifactTrialError(f"mission {name} must be an object")
            try:
                clean = json.loads(canonical_json(dict(value)))
            except (TypeError, ValueError) as error:
                raise ArtifactTrialError(f"mission {name} must be JSON-safe") from error
            object.__setattr__(self, name, clean)
        fixtures: dict[str, str] = {}
        if not isinstance(self.fixtures, Mapping):
            raise ArtifactTrialError("mission fixtures must be an object")
        for name, content in self.fixtures.items():
            relative = Path(str(name))
            if (
                relative.is_absolute()
                or not relative.parts
                or ".." in relative.parts
                or len(relative.parts) > 4
            ):
                raise ArtifactTrialError("mission fixture path must stay relative and bounded")
            text = str(content)
            if len(text.encode("utf-8")) > 1024 * 1024:
                raise ArtifactTrialError("mission fixture exceeds the 1 MiB trial limit")
            fixtures[relative.as_posix()] = text
        object.__setattr__(self, "fixtures", fixtures)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "capability_id": self.capability_id,
            "payload": dict(self.payload),
            "expected": dict(self.expected),
            "fixtures": dict(self.fixtures),
        }


@dataclass(frozen=True, slots=True)
class ArtifactTrialResult:
    trial_id: str
    status: str
    artifact_id: str
    capability_id: str
    registration_id: str
    workspace: str
    verification: Mapping[str, Any] | None = None
    rejection: Mapping[str, Any] | None = None
    catalog: Mapping[str, Any] | None = None
    disable: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol": TRIAL_PROTOCOL,
            "trial_id": self.trial_id,
            "status": self.status,
            "artifact_id": self.artifact_id,
            "capability_id": self.capability_id,
            "registration_id": self.registration_id,
            "workspace": self.workspace,
            "verification": dict(self.verification) if self.verification else None,
            "rejection": dict(self.rejection) if self.rejection else None,
            "catalog": dict(self.catalog) if self.catalog else None,
            "disable": dict(self.disable) if self.disable else None,
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _sha256(path)


def _append(
    database: FoundryDatabase,
    event_type: str,
    trial_id: str,
    payload: Mapping[str, Any],
) -> None:
    body = {"trial_id": trial_id, **dict(payload)}
    database.append(Event.create(
        event_type,
        body,
        source_type="supervisor",
        source_id=SOURCE_ID,
        correlation_id=trial_id,
        idempotency_key=f"{event_type}:{trial_id}",
    ))


def artifact_trial_record(database: FoundryDatabase, trial_id: str) -> dict[str, Any] | None:
    """Rebuild one trial only from durable events."""

    statuses = {
        TRIAL_SELECTED: ArtifactTrialStatus.SELECTED.value,
        TRIAL_ENVIRONMENT_READY: ArtifactTrialStatus.ENVIRONMENT_READY.value,
        TRIAL_PROBE_PASSED: ArtifactTrialStatus.PROBE_PASSED.value,
        TRIAL_CONTRACTS_PASSED: ArtifactTrialStatus.CONTRACTS_PASSED.value,
        TRIAL_MISSION_COMPLETED: ArtifactTrialStatus.MISSION_COMPLETED.value,
        TRIAL_VERIFIED: ArtifactTrialStatus.VERIFIED.value,
        TRIAL_ADOPTED: ArtifactTrialStatus.PROBATION.value,
        TRIAL_REJECTED: ArtifactTrialStatus.REJECTED.value,
        TRIAL_DISABLED: ArtifactTrialStatus.DISABLED.value,
    }
    record: dict[str, Any] | None = None
    for event in database.events(event_type_prefixes=(EVENT_PREFIX,), readonly=True):
        if str(event.payload.get("trial_id") or "") != trial_id:
            continue
        if record is None:
            record = {"trial_id": trial_id, "events": []}
        payload = dict(event.payload)
        record["events"].append({"event_type": event.event_type, "event_id": event.event_id})
        record.update(payload)
        record["status"] = statuses.get(event.event_type, record.get("status"))
        if event.event_type == TRIAL_ADOPTED:
            record["verification"] = payload.get("verification")
        elif event.event_type == TRIAL_REJECTED:
            record["rejection"] = payload.get("rejection")
        elif event.event_type == TRIAL_DISABLED:
            record["disable"] = payload
            if payload.get("restored"):
                record["status"] = ArtifactTrialStatus.RESTORED.value
    return record


def _subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _subset(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            _subset(left, right) for left, right in zip(expected, actual)
        )
    return expected == actual


def _materialize(value: Any, *, trial_root: Path, artifact_root: Path) -> Any:
    if isinstance(value, str):
        return value.replace("$trial_root", str(trial_root)).replace(
            "$artifact_root", str(artifact_root)
        )
    if isinstance(value, list):
        return [_materialize(item, trial_root=trial_root, artifact_root=artifact_root) for item in value]
    if isinstance(value, Mapping):
        return {
            str(key): _materialize(item, trial_root=trial_root, artifact_root=artifact_root)
            for key, item in value.items()
        }
    return value


class CanonicalArtifactTrialPipeline:
    """Trial and adopt one Adapter Factory artifact without reinterpretation."""

    def __init__(
        self,
        database: FoundryDatabase,
        trial_root: str | Path,
        *,
        project_root: str | Path,
    ) -> None:
        self.database = database
        self.trial_root = Path(trial_root).expanduser().resolve()
        self.project_root = Path(project_root).expanduser().resolve()

    @staticmethod
    def _identities(artifact_id: str, descriptor_hash: str, capability_id: str) -> tuple[str, str]:
        identity = {
            "adapter_protocol": ADAPTER_PROTOCOL,
            "artifact_id": artifact_id,
            "descriptor_hash": descriptor_hash,
            "capability_id": capability_id,
        }
        digest = payload_hash(identity)[:24]
        return f"trial:{digest}", f"fabric-registration:artifact-trial:{digest}"

    def _workspace(self, trial_id: str) -> Path:
        return self.trial_root / trial_id.replace(":", "-")

    def _gate(self, *targets: str) -> None:
        stop = emergency_stop_reason(
            self.database,
            Path(self.database.path).resolve().parent / "EMERGENCY_STOP",
        )
        if stop:
            raise ArtifactTrialStopped(f"STOP active: {stop}")
        veto = matching_veto_reason(
            self.database,
            {"GLOBAL", "capability-expansion", "capability-trial", *targets},
        )
        if veto:
            raise ArtifactTrialStopped(f"owner veto active: {veto}")

    def _ensure_environment(
        self,
        artifact: "AdapterBuildArtifact",
        *,
        workspace: Path,
        trial_id: str,
        manifest_sha256: str,
    ) -> tuple[Path, dict[str, Any]]:
        from ..adapter_factory import install_isolated_runtime

        marker = workspace / "trial-marker.json"
        workspace.mkdir(parents=True, exist_ok=True)
        marker_payload = {
            "protocol": TRIAL_PROTOCOL,
            "trial_id": trial_id,
            "artifact_id": artifact.artifact_id,
            "descriptor_hash": artifact.descriptor_hash,
        }
        if marker.exists():
            try:
                observed = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise ArtifactTrialError("trial workspace marker is unreadable") from error
            if observed != marker_payload:
                raise ArtifactTrialError("trial workspace identity conflicts with artifact")
        else:
            _write_json(marker, marker_payload)
        environment = workspace / "runtime"
        install_record_path = environment / "adapter-runtime-install.json"
        if install_record_path.exists():
            try:
                install = json.loads(install_record_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                install = {}
            python = Path(str(install.get("python") or "")).resolve()
            if (
                install.get("artifact_id") == artifact.artifact_id
                and install.get("adapter_manifest_sha256") == manifest_sha256
                and python.is_file()
            ):
                return python, install
            # A hard kill may leave only a partial venv or install record.
            # This exact directory is identity-bound by the trial marker and
            # contained beneath its disposable workspace, so rebuilding it is
            # the safe restart path rather than consuming another attempt.
            shutil.rmtree(environment)
        if environment.exists():
            shutil.rmtree(environment)
        install = install_isolated_runtime(
            artifact,
            environment_root=environment,
            project_root=self.project_root,
        )
        python = Path(str(install["python"])).resolve()
        return python, install

    def _verify_independently(self, bundle_path: Path, python: Path) -> dict[str, Any]:
        verifier = Path(__file__).with_name("artifact_trial_verifier.py").resolve()
        process = subprocess.run(
            [str(python), "-I", str(verifier), str(bundle_path)],
            cwd=str(bundle_path.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
            env=sanitized_env(),
            **headless_run_kwargs(),
        )
        try:
            value = json.loads((process.stdout or "").strip().splitlines()[-1])
        except (IndexError, ValueError):
            value = {
                "verdict": "FAIL",
                "verifier_id": "jarvis.v12.canonical-artifact-verifier",
                "findings": ["independent verifier returned no JSON"],
            }
        if not isinstance(value, dict):
            raise ArtifactTrialError("independent verifier returned a non-object")
        return value

    def _result(
        self,
        *,
        trial_id: str,
        artifact_id: str,
        capability_id: str,
        registration_id: str,
        workspace: Path,
        record: Mapping[str, Any],
    ) -> ArtifactTrialResult:
        return ArtifactTrialResult(
            trial_id=trial_id,
            status=str(record.get("status") or ArtifactTrialStatus.SELECTED.value),
            artifact_id=artifact_id,
            capability_id=capability_id,
            registration_id=registration_id,
            workspace=str(workspace),
            verification=record.get("verification"),
            rejection=record.get("rejection"),
            catalog=catalog_truth_snapshot(self.database),
            disable=record.get("disable"),
        )

    def run(
        self,
        artifact: "AdapterBuildArtifact",
        mission: TrialMission,
    ) -> ArtifactTrialResult:
        from ..adapter_factory import (
            AdapterBuildArtifact,
            disable_artifact,
            invoke_artifact,
            load_verified_artifact,
            probe_artifact,
        )

        if not isinstance(artifact, AdapterBuildArtifact):
            raise ArtifactTrialError("trial requires the canonical AdapterBuildArtifact")
        root, manifest, descriptor = load_verified_artifact(artifact.root)
        if manifest.get("protocol") != ADAPTER_PROTOCOL:
            raise ArtifactTrialError("artifact does not use jarvis.capability.adapter.v1")
        if mission.capability_id not in {item.capability_id for item in descriptor.capabilities}:
            raise ArtifactTrialError("trial mission capability is absent from artifact")
        capability = next(
            item for item in descriptor.capabilities
            if item.capability_id == mission.capability_id
        )
        if capability.effect_class.value != "READ_ONLY":
            raise ArtifactTrialError("probation adoption trial must be read-only")
        trial_id, registration_id = self._identities(
            artifact.artifact_id,
            artifact.descriptor_hash,
            mission.capability_id,
        )
        workspace = self._workspace(trial_id)
        prior = artifact_trial_record(self.database, trial_id)
        if prior is not None and prior.get("mission") != mission.as_dict():
            raise ArtifactTrialError("stable trial identity is bound to a different mission")
        if prior is not None and prior.get("status") in {
            ArtifactTrialStatus.PROBATION.value,
            ArtifactTrialStatus.REJECTED.value,
            ArtifactTrialStatus.DISABLED.value,
            ArtifactTrialStatus.RESTORED.value,
        }:
            return self._result(
                trial_id=trial_id,
                artifact_id=artifact.artifact_id,
                capability_id=mission.capability_id,
                registration_id=registration_id,
                workspace=workspace,
                record=prior,
            )
        self._gate(trial_id, mission.capability_id)
        for relative, content in mission.fixtures.items():
            target = (workspace / relative).resolve()
            try:
                target.relative_to(workspace)
            except ValueError as error:
                raise ArtifactTrialError("trial fixture escapes its workspace") from error
            target.parent.mkdir(parents=True, exist_ok=True)
            expected_bytes = content.encode("utf-8")
            if target.exists() and target.read_bytes() != expected_bytes:
                raise ArtifactTrialError("trial fixture identity conflicts with existing content")
            if not target.exists():
                target.write_bytes(expected_bytes)
        before = (
            dict(prior.get("catalog_before"))
            if prior is not None and isinstance(prior.get("catalog_before"), Mapping)
            else catalog_truth_snapshot(self.database)
        )
        _append(self.database, TRIAL_SELECTED, trial_id, {
            "artifact_id": artifact.artifact_id,
            "adapter_id": artifact.adapter_id,
            "descriptor_hash": artifact.descriptor_hash,
            "capability_id": mission.capability_id,
            "registration_id": registration_id,
            "mission": mission.as_dict(),
            "catalog_before": before,
            "catalog_before_sha256": before["sha256"],
        })
        registered = False
        stage = "environment"
        verification: dict[str, Any] | None = None
        try:
            self._gate(trial_id, mission.capability_id)
            manifest_sha256 = _sha256(artifact.manifest_path)
            python, install = self._ensure_environment(
                artifact,
                workspace=workspace,
                trial_id=trial_id,
                manifest_sha256=manifest_sha256,
            )
            _append(self.database, TRIAL_ENVIRONMENT_READY, trial_id, {
                "workspace": str(workspace),
                "python": str(python),
                "artifact_manifest": str(artifact.manifest_path),
                "artifact_manifest_sha256": manifest_sha256,
                "dependency_manifest_sha256": _sha256(root / "dependency-manifest.json"),
                "install_record_sha256": _sha256(workspace / "runtime" / "adapter-runtime-install.json"),
                "installed_packages": list(install.get("installed_packages") or ()),
            })

            stage = "readiness"
            self._gate(trial_id, mission.capability_id)
            probe = probe_artifact(root, python_executable=python)
            probe_path = workspace / "evidence" / "readiness.json"
            probe_hash = _write_json(probe_path, probe)
            if probe.get("ready") is not True or probe.get("protocol") != ADAPTER_PROTOCOL:
                raise ArtifactTrialError("canonical artifact readiness probe failed")
            _append(self.database, TRIAL_PROBE_PASSED, trial_id, {
                "probe_path": str(probe_path),
                "probe_sha256": probe_hash,
            })

            stage = "contracts"
            contract_reports: list[dict[str, Any]] = []
            if not capability.contract_cases:
                raise ArtifactTrialError("artifact has no frozen contract cases")
            for case in capability.contract_cases:
                self._gate(trial_id, mission.capability_id)
                payload = _materialize(case.payload, trial_root=workspace, artifact_root=root)
                expected = _materialize(case.expected, trial_root=workspace, artifact_root=root)
                result = invoke_artifact(
                    root,
                    mission.capability_id,
                    payload,
                    python_executable=python,
                    allowed_root=workspace,
                    cancel_check=lambda: bool(emergency_stop_reason(
                        self.database,
                        Path(self.database.path).resolve().parent / "EMERGENCY_STOP",
                    )),
                )
                report = {
                    "case_id": case.case_id,
                    "payload": payload,
                    "expected": expected,
                    "result": result,
                    "result_sha256": payload_hash(result),
                }
                contract_reports.append(report)
                if result.get("claimed") != "COMPLETED" or not _subset(expected, result):
                    raise ArtifactTrialError(f"contract case failed: {case.case_id}")
            contracts_path = workspace / "evidence" / "contracts.json"
            contracts_hash = _write_json(contracts_path, {
                "protocol": TRIAL_PROTOCOL,
                "cases": contract_reports,
            })
            _append(self.database, TRIAL_CONTRACTS_PASSED, trial_id, {
                "contracts_path": str(contracts_path),
                "contracts_sha256": contracts_hash,
                "case_count": len(contract_reports),
            })

            stage = "mission"
            self._gate(trial_id, mission.capability_id)
            mission_payload = _materialize(
                mission.payload, trial_root=workspace, artifact_root=root
            )
            mission_expected = _materialize(
                mission.expected, trial_root=workspace, artifact_root=root
            )
            mission_result = invoke_artifact(
                root,
                mission.capability_id,
                mission_payload,
                python_executable=python,
                allowed_root=workspace,
                cancel_check=lambda: bool(emergency_stop_reason(
                    self.database,
                    Path(self.database.path).resolve().parent / "EMERGENCY_STOP",
                )),
            )
            mission_record = {
                "case_id": mission.case_id,
                "payload": mission_payload,
                "expected": mission_expected,
                "result": mission_result,
                "result_sha256": payload_hash(mission_result),
            }
            mission_path = workspace / "evidence" / "mission.json"
            mission_hash = _write_json(mission_path, mission_record)
            if mission_result.get("claimed") != "COMPLETED" or not _subset(
                mission_expected, mission_result
            ):
                raise ArtifactTrialError("realistic trial mission failed")
            _append(self.database, TRIAL_MISSION_COMPLETED, trial_id, {
                "mission_path": str(mission_path),
                "mission_sha256": mission_hash,
            })

            stage = "independent_verifier"
            during = catalog_truth_snapshot(self.database)
            if during["sha256"] != before["sha256"]:
                raise ArtifactTrialError("Catalog changed before independent verification")
            bundle = {
                "protocol": "jarvis.capability.artifact-trial.bundle.v1",
                "trial_id": trial_id,
                "artifact_id": artifact.artifact_id,
                "descriptor_hash": artifact.descriptor_hash,
                "artifact_root": str(root),
                "manifest_path": str(artifact.manifest_path),
                "manifest_sha256": manifest_sha256,
                "python": str(python),
                "workspace": str(workspace),
                "capability_id": mission.capability_id,
                "registration_id": registration_id,
                "catalog_before_sha256": before["sha256"],
                "catalog_during_sha256": during["sha256"],
                "contract_cases": contract_reports,
                "mission": mission_record,
                "builder_pid": os.getpid(),
            }
            bundle_path = workspace / "evidence" / "bundle.json"
            _write_json(bundle_path, bundle)
            raw_verification = self._verify_independently(bundle_path, python)
            verification_path = workspace / "evidence" / "verification.json"
            if (
                raw_verification.get("verdict") != "PASS"
                or int(raw_verification.get("verifier_pid") or 0) == os.getpid()
            ):
                raise ArtifactTrialError("independent verifier rejected artifact trial")
            # Process ids prove separation to this orchestrator but are not
            # semantic trial identity.  Persist a normalized verdict so a
            # crash after verification can replay under a new process without
            # creating a conflicting verification event.
            verification = {
                key: value for key, value in raw_verification.items()
                if key not in {"verifier_pid", "builder_pid"}
            }
            verification["process_independent"] = True
            verification_hash = _write_json(verification_path, verification)
            logical_bundle_hash = payload_hash({
                key: value for key, value in bundle.items()
                if key != "builder_pid"
            })
            _append(self.database, TRIAL_VERIFIED, trial_id, {
                "verification_id": f"verification:{trial_id}",
                "verification": verification,
                "verification_path": str(verification_path),
                "verification_sha256": verification_hash,
                "bundle_sha256": logical_bundle_hash,
            })

            stage = "adoption"
            self._gate(trial_id, mission.capability_id)
            records = json.loads((root / "fabric-capabilities.json").read_text(encoding="utf-8"))
            row = next(
                (
                    item for item in records.get("capabilities", ())
                    if (item.get("spec") or {}).get("capability_id") == mission.capability_id
                ),
                None,
            )
            if not isinstance(row, Mapping):
                raise ArtifactTrialError("artifact has no matching Fabric capability record")
            spec = CapabilitySpec.from_mapping(row["spec"])
            spec = CapabilitySpec(
                capability_id=spec.capability_id,
                version=spec.version,
                kind=spec.kind,
                action_kind=spec.action_kind,
                semantic=spec.semantic,
                executor=str(artifact.manifest_path),
                requirements=spec.requirements,
                unattended=spec.unattended,
                limitations=spec.limitations,
                supersedes=spec.supersedes,
                provider_deps=spec.provider_deps,
            )
            metadata = CapabilityMetadata.from_mapping(row["metadata"])
            metadata = replace(
                metadata,
                execute={
                    **dict(metadata.execute),
                    "executor": str(artifact.manifest_path),
                    "python_executable": str(python),
                    "artifact_id": artifact.artifact_id,
                },
            )
            _append(self.database, TRIAL_ADOPTION_DECIDED, trial_id, {
                "decision": "ADOPT_PROBATION",
                "verification_id": f"verification:{trial_id}",
                "registration_id": registration_id,
                "catalog_before": before,
                "catalog_before_sha256": before["sha256"],
            })
            register_capability(
                self.database,
                spec,
                note="canonical adapter artifact independently verified",
                metadata=metadata,
                trust=CapabilityTrust.PROBATION,
                registration_id=registration_id,
                adoption={
                    "trial_id": trial_id,
                    "artifact_id": artifact.artifact_id,
                    "descriptor_hash": artifact.descriptor_hash,
                    "verification_id": f"verification:{trial_id}",
                    "verification_sha256": verification_hash,
                    "adapter_manifest_sha256": manifest_sha256,
                },
            )
            registered = True
            if observed_readiness(self.database, spec.capability_id).state is not Readiness.READY:
                record_outcome(
                    self.database,
                    spec.capability_id,
                    success=True,
                    verified=VerifyVerdict.MATCH,
                    note="V12 canonical artifact trial PASS",
                )
            after = catalog_truth_snapshot(self.database)
            adopted = {
                "artifact_id": artifact.artifact_id,
                "capability_id": spec.capability_id,
                "registration_id": registration_id,
                "workspace": str(workspace),
                "trust": CapabilityTrust.PROBATION.value,
                "verification": verification,
                "catalog_before_sha256": before["sha256"],
                "catalog_after_sha256": after["sha256"],
            }
            _append(self.database, TRIAL_ADOPTED, trial_id, adopted)
            return ArtifactTrialResult(
                trial_id=trial_id,
                status=ArtifactTrialStatus.PROBATION.value,
                artifact_id=artifact.artifact_id,
                capability_id=spec.capability_id,
                registration_id=registration_id,
                workspace=str(workspace),
                verification=verification,
                catalog=after,
            )
        except ArtifactTrialStopped:
            raise
        except Exception as error:
            current = capability_registration(self.database, mission.capability_id)
            if registered or (
                isinstance(current, Mapping)
                and current.get("registration_id") == registration_id
            ):
                try:
                    disable_capability(
                        self.database,
                        mission.capability_id,
                        reason="artifact trial finalization failed",
                        registration_id=registration_id,
                    )
                except Exception:
                    pass
            try:
                disable_artifact(root, reason=f"trial rejected at {stage}")
            except Exception:
                pass
            after_failure = catalog_truth_snapshot(self.database)
            rejection = {
                "stage": stage,
                "reason": redact_text(str(error))[:600],
                "artifact_id": artifact.artifact_id,
                "catalog_unchanged": after_failure["sha256"] == before["sha256"],
                "catalog_before_sha256": before["sha256"],
                "catalog_after_sha256": after_failure["sha256"],
            }
            if verification is not None:
                rejection["verification"] = verification
            _append(self.database, TRIAL_REJECTED, trial_id, {
                "artifact_id": artifact.artifact_id,
                "capability_id": mission.capability_id,
                "registration_id": registration_id,
                "workspace": str(workspace),
                "rejection": rejection,
            })
            return ArtifactTrialResult(
                trial_id=trial_id,
                status=ArtifactTrialStatus.REJECTED.value,
                artifact_id=artifact.artifact_id,
                capability_id=mission.capability_id,
                registration_id=registration_id,
                workspace=str(workspace),
                verification=verification,
                rejection=rejection,
                catalog=after_failure,
            )

    def disable(
        self,
        artifact: "AdapterBuildArtifact",
        *,
        capability_id: str,
        reason: str = "self-expanded probation capability rolled back",
    ) -> ArtifactTrialResult:
        from ..adapter_factory import AdapterBuildArtifact, disable_artifact, load_verified_artifact

        if not isinstance(artifact, AdapterBuildArtifact):
            raise ArtifactTrialError("disable requires the canonical AdapterBuildArtifact")
        root, _manifest, _descriptor = load_verified_artifact(artifact.root)
        trial_id, registration_id = self._identities(
            artifact.artifact_id, artifact.descriptor_hash, capability_id
        )
        record = artifact_trial_record(self.database, trial_id)
        if record is None:
            raise ArtifactTrialError(f"unknown artifact trial: {trial_id}")
        if record.get("status") in {
            ArtifactTrialStatus.DISABLED.value,
            ArtifactTrialStatus.RESTORED.value,
        }:
            return self._result(
                trial_id=trial_id,
                artifact_id=artifact.artifact_id,
                capability_id=capability_id,
                registration_id=registration_id,
                workspace=self._workspace(trial_id),
                record=record,
            )
        if record.get("status") != ArtifactTrialStatus.PROBATION.value:
            raise ArtifactTrialError("only an adopted PROBATION trial can be disabled")
        self._gate(trial_id, capability_id)
        disable_catalog = disable_capability(
            self.database,
            capability_id,
            reason=reason,
            registration_id=registration_id,
        )
        disable_adapter = disable_artifact(root, reason=reason)
        after = catalog_truth_snapshot(self.database)
        before_hash = str(record.get("catalog_before_sha256") or "")
        restored = after["sha256"] == before_hash
        payload = {
            "artifact_id": artifact.artifact_id,
            "capability_id": capability_id,
            "registration_id": registration_id,
            "workspace": str(self._workspace(trial_id)),
            "reason": str(reason)[:600],
            "catalog_before_sha256": before_hash,
            "catalog_after_sha256": after["sha256"],
            "catalog_disabled": bool(
                disable_catalog.get("disabled") or disable_catalog.get("already_disabled")
            ),
            "artifact_disabled": bool(disable_adapter.get("disabled")),
            "restored": restored,
        }
        _append(self.database, TRIAL_DISABLED, trial_id, payload)
        return ArtifactTrialResult(
            trial_id=trial_id,
            status=(
                ArtifactTrialStatus.RESTORED.value
                if restored else ArtifactTrialStatus.DISABLED.value
            ),
            artifact_id=artifact.artifact_id,
            capability_id=capability_id,
            registration_id=registration_id,
            workspace=str(self._workspace(trial_id)),
            verification=record.get("verification"),
            catalog=after,
            disable=payload,
        )


__all__ = [
    "ADAPTER_PROTOCOL",
    "ArtifactTrialError",
    "ArtifactTrialResult",
    "ArtifactTrialStatus",
    "ArtifactTrialStopped",
    "CanonicalArtifactTrialPipeline",
    "TRIAL_PROTOCOL",
    "TrialMission",
    "artifact_trial_record",
]
