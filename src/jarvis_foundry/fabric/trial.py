"""V12 capability trial and adoption pipeline.

This module wires the existing Fabric catalog, V6-style disposable evaluation
patterns, standard adapter receipts, and the existing ledger into one bounded
state machine:

``selected -> workspace -> installed -> contract-tested -> mission ->
independently verified -> Fabric PROBATION -> disabled/restored``.

The trial never writes the live install or a second capability registry.  The
only catalog mutations are calls to :mod:`jarvis_foundry.fabric.catalog`.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from ..control.redaction import contains_secret_like, redact_text
from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash
from ..platform.windows_subprocess import headless_run_kwargs, sanitized_env
from .catalog import (
    catalog_truth_snapshot,
    capability_registration,
    disable_capability,
    record_outcome,
    register_capability,
)
from .contracts import metadata_for_spec
from .installed_adapter import ADAPTER_PROTOCOL, probe_installed_adapter, run_installed_adapter
from .model import (
    CapabilitySpec,
    CapabilityTrust,
    FabricError,
    Kind,
    VerifyVerdict,
)

TRIAL_PROTOCOL = "jarvis.capability.trial.v1"
TRIAL_EVENT_SOURCE = "v12-capability-trial"
TRIAL_EVENT_PREFIX = "fabric.capability.trial."
TRIAL_SELECTED = TRIAL_EVENT_PREFIX + "selected"
TRIAL_WORKSPACE_READY = TRIAL_EVENT_PREFIX + "workspace_ready"
TRIAL_INSTALLED = TRIAL_EVENT_PREFIX + "installed"
TRIAL_ADAPTER_READY = TRIAL_EVENT_PREFIX + "adapter_ready"
TRIAL_PROBE_PASSED = TRIAL_EVENT_PREFIX + "probe_passed"
TRIAL_CONTRACTS_PASSED = TRIAL_EVENT_PREFIX + "contracts_passed"
TRIAL_MISSION_COMPLETED = TRIAL_EVENT_PREFIX + "mission_completed"
TRIAL_VERIFIED = TRIAL_EVENT_PREFIX + "verified"
TRIAL_ADOPTION_DECIDED = TRIAL_EVENT_PREFIX + "adoption_decided"
TRIAL_ADOPTED = TRIAL_EVENT_PREFIX + "adopted"
TRIAL_REJECTED = TRIAL_EVENT_PREFIX + "rejected"
TRIAL_DISABLED = TRIAL_EVENT_PREFIX + "disabled"


class TrialError(FabricError):
    """A selected solution is invalid or a trial failed closed."""


class TrialStatus(StrEnum):
    SELECTED = "SELECTED"
    WORKSPACE_READY = "WORKSPACE_READY"
    INSTALLED = "INSTALLED"
    ADAPTER_READY = "ADAPTER_READY"
    CONTRACTS_PASSED = "CONTRACTS_PASSED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    VERIFIED = "VERIFIED"
    PROBATION = "PROBATION"
    REJECTED = "REJECTED"
    DISABLED = "DISABLED"
    RESTORED = "RESTORED"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrialError(f"{field_name} must be a non-empty string")
    result = value.strip()
    if any(ord(char) < 32 for char in result):
        raise TrialError(f"{field_name} contains a control character")
    return result


def _identity(value: object, field_name: str) -> str:
    result = _text(value, field_name)
    if any(char.isspace() for char in result) or len(result) > 256:
        raise TrialError(f"{field_name} must be a bounded identity")
    return result


def _segment(value: object, field_name: str) -> str:
    """Validate an identity that will be used as one workspace path segment."""

    result = _identity(value, field_name)
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"COM{index}" for index in range(1, 10)
    } | {f"LPT{index}" for index in range(1, 10)}
    if (
        result in {".", ".."}
        or len(result) > 120
        or any(char in result for char in '<>:"/\\|?*')
        or result.endswith((".", " "))
        or result.split(".", 1)[0].upper() in reserved
    ):
        raise TrialError(f"{field_name} must be a safe workspace segment")
    return result


def _json_safe(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TrialError(f"{field_name} must be an object")
    try:
        encoded = canonical_json(dict(value))
        if contains_secret_like(encoded):
            raise TrialError(f"{field_name} contains secret-like material")
        result = json.loads(encoded)
    except TrialError:
        raise
    except (TypeError, ValueError) as error:
        raise TrialError(f"{field_name} must contain JSON-safe values") from error
    if not isinstance(result, dict):
        raise TrialError(f"{field_name} must be an object")
    return result


@dataclass(frozen=True, slots=True)
class DependencyPin:
    """One reproducible dependency declaration for a selected solution."""

    name: str
    version: str
    source: str = "stdlib"
    sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identity(self.name, "dependency.name"))
        object.__setattr__(self, "version", _text(self.version, "dependency.version"))
        object.__setattr__(self, "source", _text(self.source, "dependency.source"))
        if self.sha256 is not None:
            digest = _text(self.sha256, "dependency.sha256").lower()
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise TrialError("dependency.sha256 must be a 64-character hex digest")
            object.__setattr__(self, "sha256", digest)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "sha256": self.sha256,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DependencyPin":
        if not isinstance(value, Mapping):
            raise TrialError("dependency must be an object")
        return cls(
            name=str(value.get("name") or ""),
            version=str(value.get("version") or ""),
            source=str(value.get("source") or "stdlib"),
            sha256=value.get("sha256"),
        )


@dataclass(frozen=True, slots=True)
class TrialCase:
    """A frozen adapter input and the independently checkable expectation."""

    case_id: str
    payload: Mapping[str, Any]
    expected: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _segment(self.case_id, "case_id"))
        object.__setattr__(self, "payload", _json_safe(self.payload, "case.payload"))
        object.__setattr__(self, "expected", _json_safe(self.expected, "case.expected"))

    def as_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "payload": dict(self.payload), "expected": dict(self.expected)}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TrialCase":
        if not isinstance(value, Mapping):
            raise TrialError("trial case must be an object")
        return cls(
            case_id=str(value.get("case_id") or ""),
            payload=value.get("payload") or {},
            expected=value.get("expected") or {},
        )


@dataclass(frozen=True, slots=True)
class SelectedCapabilitySolution:
    """Input descriptor supplied by a prior solution-selection step."""

    solution_id: str
    spec: CapabilitySpec
    source_path: Path
    dependencies: tuple[DependencyPin, ...]
    contract_cases: tuple[TrialCase, ...]
    mission: TrialCase
    config: Mapping[str, Any] = field(default_factory=dict)
    path_fields: tuple[str, ...] = ("path",)
    selection_reason: str = "selected solution descriptor"
    _source_sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "solution_id", _identity(self.solution_id, "solution_id"))
        if not isinstance(self.spec, CapabilitySpec):
            raise TrialError("spec must be a CapabilitySpec")
        if self.spec.kind is not Kind.ADAPTER:
            raise TrialError("V12 trial solutions must use Kind.ADAPTER")
        source = Path(self.source_path).expanduser().resolve()
        if not source.is_file():
            raise TrialError(f"selected solution source is missing: {source}")
        object.__setattr__(self, "source_path", source)
        object.__setattr__(self, "_source_sha256", hashlib.sha256(source.read_bytes()).hexdigest())
        dependencies = tuple(self.dependencies)
        if not dependencies:
            raise TrialError("selected solution requires a dependency record")
        if any(not isinstance(item, DependencyPin) for item in dependencies):
            raise TrialError("dependencies must be DependencyPin values")
        if len({item.name.lower() for item in dependencies}) != len(dependencies):
            raise TrialError("dependency names must be unique")
        object.__setattr__(self, "dependencies", dependencies)
        cases = tuple(self.contract_cases)
        if not cases:
            raise TrialError("at least one capability contract case is required")
        if any(not isinstance(item, TrialCase) for item in cases):
            raise TrialError("contract_cases must be TrialCase values")
        if not isinstance(self.mission, TrialCase):
            raise TrialError("mission must be a TrialCase")
        if len({item.case_id for item in cases} | {self.mission.case_id}) != len(cases) + 1:
            raise TrialError("contract and mission case ids must be unique")
        object.__setattr__(self, "contract_cases", cases)
        object.__setattr__(self, "config", _json_safe(self.config, "config"))
        fields = tuple(_identity(item, "path_field") for item in self.path_fields)
        object.__setattr__(self, "path_fields", fields)
        object.__setattr__(self, "selection_reason", _text(self.selection_reason, "selection_reason"))

    @property
    def source_sha256(self) -> str:
        return self._source_sha256

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SelectedCapabilitySolution":
        """Parse a selected-solution descriptor without performing a trial."""

        if not isinstance(value, Mapping):
            raise TrialError("selected solution descriptor must be an object")
        declared_protocol = value.get("protocol")
        if declared_protocol is not None and declared_protocol != TRIAL_PROTOCOL:
            raise TrialError("selected solution descriptor protocol is unsupported")
        raw_spec = value.get("spec")
        if not isinstance(raw_spec, Mapping):
            raise TrialError("selected solution descriptor requires a spec object")
        raw_cases = value.get("contract_cases") or ()
        if not isinstance(raw_cases, (list, tuple)):
            raise TrialError("contract_cases must be a list")
        raw_mission = value.get("mission")
        if not isinstance(raw_mission, Mapping):
            raise TrialError("selected solution descriptor requires a mission object")
        selected = cls(
            solution_id=str(value.get("solution_id") or ""),
            spec=CapabilitySpec.from_mapping(raw_spec),
            source_path=Path(str(value.get("source_path") or value.get("source") or "")),
            dependencies=tuple(DependencyPin.from_mapping(item) for item in (value.get("dependencies") or ())),
            contract_cases=tuple(TrialCase.from_mapping(item) for item in raw_cases),
            mission=TrialCase.from_mapping(raw_mission),
            config=value.get("config") or {},
            path_fields=tuple(value.get("path_fields") or ("path",)),
            selection_reason=str(value.get("selection_reason") or "selected solution descriptor"),
        )
        declared_source_hash = value.get("source_sha256")
        if declared_source_hash is not None and declared_source_hash != selected.source_sha256:
            raise TrialError("selected solution source hash does not match the descriptor")
        declared_descriptor_hash = value.get("descriptor_hash")
        if declared_descriptor_hash is not None and declared_descriptor_hash != selected.descriptor_hash:
            raise TrialError("selected solution descriptor hash does not match its contents")
        declared_registration_id = value.get("registration_id")
        if declared_registration_id is not None and declared_registration_id != selected.registration_id:
            raise TrialError("selected solution registration identity does not match its contents")
        return selected

    @classmethod
    def from_file(cls, path: str | Path) -> "SelectedCapabilitySolution":
        """Load a selected-solution descriptor with relative source support."""

        descriptor_path = Path(path).expanduser().resolve()
        try:
            value = json.loads(descriptor_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise TrialError(f"selected solution descriptor is not readable: {descriptor_path}") from error
        if not isinstance(value, Mapping):
            raise TrialError("selected solution descriptor file must contain an object")
        raw = dict(value)
        source_value = raw.get("source_path") or raw.get("source") or raw.get("source_name")
        if isinstance(source_value, str) and not Path(source_value).expanduser().is_absolute():
            raw["source_path"] = str((descriptor_path.parent / source_value).resolve())
        return cls.from_mapping(raw)

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol": TRIAL_PROTOCOL,
            "solution_id": self.solution_id,
            "spec": self.spec.as_dict(),
            "source_path": str(self.source_path),
            "source_name": self.source_path.name,
            "source_sha256": self.source_sha256,
            "dependencies": [item.as_dict() for item in self.dependencies],
            "contract_cases": [item.as_dict() for item in self.contract_cases],
            "mission": self.mission.as_dict(),
            "config": dict(self.config),
            "path_fields": list(self.path_fields),
            "selection_reason": self.selection_reason,
        }

    @property
    def descriptor_hash(self) -> str:
        def scrub(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {str(key): scrub(item) for key, item in value.items()}
            if isinstance(value, list):
                return [scrub(item) for item in value]
            return value

        def scrub_case(case: TrialCase) -> dict[str, Any]:
            payload = scrub(dict(case.payload))
            # Input locations belong to a disposable run, not to the logical
            # selected solution identity.  Expected values and case labels
            # remain frozen so changing the contract creates a new trial.
            if isinstance(payload, dict):
                for field_name in self.path_fields:
                    if field_name in payload:
                        payload[field_name] = "<disposable-input>"
            return {"case_id": case.case_id, "payload": payload, "expected": scrub(dict(case.expected))}

        identity = {
            "protocol": TRIAL_PROTOCOL,
            "solution_id": self.solution_id,
            "spec": {key: value for key, value in self.spec.as_dict().items() if key != "executor"},
            "source_sha256": self.source_sha256,
            "dependencies": [item.as_dict() for item in self.dependencies],
            "contract_cases": [scrub_case(case) for case in self.contract_cases],
            "mission": scrub_case(self.mission),
            "config": scrub(dict(self.config)),
            "path_fields": list(self.path_fields),
        }
        return payload_hash(identity)

    @property
    def registration_id(self) -> str:
        identity = {
            "solution_id": self.solution_id,
            "capability_id": self.spec.capability_id,
            "version": self.spec.version,
            "action_kind": self.spec.action_kind,
            "source_sha256": self.source_sha256,
            "descriptor_hash": self.descriptor_hash,
        }
        return f"fabric-registration:trial:{payload_hash(identity)[:24]}"


@dataclass(frozen=True, slots=True)
class TrialResult:
    trial_id: str
    status: str
    workspace: str
    capability_id: str
    registration_id: str
    descriptor_hash: str
    verification: Mapping[str, Any] | None = None
    catalog: Mapping[str, Any] | None = None
    rejection: Mapping[str, Any] | None = None
    disable: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol": TRIAL_PROTOCOL,
            "trial_id": self.trial_id,
            "status": self.status,
            "workspace": self.workspace,
            "capability_id": self.capability_id,
            "registration_id": self.registration_id,
            "descriptor_hash": self.descriptor_hash,
            "verification": dict(self.verification) if self.verification is not None else None,
            "catalog": dict(self.catalog) if self.catalog is not None else None,
            "rejection": dict(self.rejection) if self.rejection is not None else None,
            "disable": dict(self.disable) if self.disable is not None else None,
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> str:
    encoded = canonical_json(dict(value)) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.is_dir():
        raise TrialError(f"refusing to write unsafe trial file: {path}")
    path.write_text(encoded, encoding="utf-8")
    # Windows text mode may normalize LF to CRLF.  Hash the bytes that were
    # actually installed so every manifest/evidence reference is verifiable
    # on both Windows and POSIX hosts.
    return _sha256(path)


def _subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _subset(value, actual[key]) for key, value in expected.items()
        )
    return expected == actual


ADAPTER_WRAPPER_SOURCE = '''\
"""Generated V12 standard adapter wrapper; do not edit in a trial."""
from __future__ import annotations
import argparse
import importlib.util
import inspect
import json
from pathlib import Path

PROTOCOL = "jarvis.capability.adapter.v1"

def _load(manifest_path: Path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent.resolve()
    solution = (root / manifest["solution_path"]).resolve()
    solution.relative_to(root)
    spec = importlib.util.spec_from_file_location("selected_solution", solution)
    if spec is None or spec.loader is None:
        raise RuntimeError("selected solution cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = {}
    config_path = manifest.get("config_path")
    if config_path:
        config_file = (root / config_path).resolve()
        config_file.relative_to(root)
        config = json.loads(config_file.read_text(encoding="utf-8"))
    return manifest, module, config

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("probe", "execute"))
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    manifest, module, config = _load(args.manifest)
    if args.command == "probe":
        fn = getattr(module, "readiness_probe", None)
        if not callable(fn):
            raise RuntimeError("selected solution has no readiness_probe()")
        try:
            parameters = tuple(inspect.signature(fn).parameters.values())
        except (TypeError, ValueError):
            parameters = ()
        if not parameters:
            result = fn()
        elif all(item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD) for item in parameters if item.kind is not inspect.Parameter.VAR_KEYWORD):
            result = fn(config)
        else:
            result = fn(config=config)
    else:
        fn = getattr(module, "run", None)
        if not callable(fn):
            raise RuntimeError("selected solution has no run(payload)")
        payload = json.loads(__import__("sys").stdin.read() or "{}")
        result = fn(payload)
    if not isinstance(result, dict):
        raise RuntimeError("adapter result must be a JSON object")
    result.setdefault("protocol", PROTOCOL)
    result.setdefault("capability_id", manifest["capability_id"])
    result.setdefault("action_kind", manifest["action_kind"])
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''


def _event_payload(database: FoundryDatabase, event_type: str, trial_id: str) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for event in database.events(readonly=True, event_types=(event_type,)):
        if str(event.payload.get("trial_id")) == trial_id:
            latest = dict(event.payload)
    return latest


def trial_record(database: FoundryDatabase, trial_id: str) -> dict[str, Any] | None:
    """Rebuild one trial's durable state from its event stream."""

    events = database.events(readonly=True, event_type_prefixes=(TRIAL_EVENT_PREFIX,))
    record: dict[str, Any] | None = None
    status_by_event = {
        TRIAL_SELECTED: TrialStatus.SELECTED.value,
        TRIAL_WORKSPACE_READY: TrialStatus.WORKSPACE_READY.value,
        TRIAL_INSTALLED: TrialStatus.INSTALLED.value,
        TRIAL_ADAPTER_READY: TrialStatus.ADAPTER_READY.value,
        TRIAL_PROBE_PASSED: TrialStatus.ADAPTER_READY.value,
        TRIAL_CONTRACTS_PASSED: TrialStatus.CONTRACTS_PASSED.value,
        TRIAL_MISSION_COMPLETED: TrialStatus.MISSION_COMPLETED.value,
        TRIAL_VERIFIED: TrialStatus.VERIFIED.value,
        TRIAL_ADOPTION_DECIDED: TrialStatus.VERIFIED.value,
        TRIAL_ADOPTED: TrialStatus.PROBATION.value,
        TRIAL_REJECTED: TrialStatus.REJECTED.value,
        TRIAL_DISABLED: TrialStatus.DISABLED.value,
    }
    for event in events:
        payload = dict(event.payload)
        if str(payload.get("trial_id")) != trial_id:
            continue
        if record is None:
            record = {"trial_id": trial_id, "events": []}
        record["events"].append(event.event_type)
        record.update(payload)
        if event.event_type in status_by_event:
            record["status"] = status_by_event[event.event_type]
        if event.event_type == TRIAL_DISABLED:
            record["disable"] = payload
            record["status"] = TrialStatus.RESTORED.value if payload.get("restored") else TrialStatus.DISABLED.value
    return record


def _append_trial_event(
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
        source_id=TRIAL_EVENT_SOURCE,
        idempotency_key=f"{event_type}:{trial_id}",
    ))


class CapabilityTrialPipeline:
    """Execute one selected solution through a durable disposable trial."""

    def __init__(
        self,
        database: FoundryDatabase,
        trial_root: str | Path,
        *,
        python_executable: str | Path | None = None,
    ) -> None:
        self.database = database
        self.trial_root = Path(trial_root).expanduser().resolve()
        self.python_executable = str(Path(python_executable or sys.executable).resolve())

    def _trial_id(self, descriptor: SelectedCapabilitySolution) -> str:
        return f"trial:{descriptor.descriptor_hash[:24]}"

    def _workspace(self, trial_id: str) -> Path:
        return self.trial_root / trial_id.replace(":", "-")

    def _stage_inputs(
        self,
        descriptor: SelectedCapabilitySolution,
        workspace: Path,
    ) -> tuple[tuple[TrialCase, ...], TrialCase, list[dict[str, str]]]:
        input_records: dict[str, dict[str, str]] = {}
        all_cases = list(descriptor.contract_cases) + [descriptor.mission]
        normalized_cases: list[TrialCase] = []
        for case in all_cases:
            payload = dict(case.payload)
            for field_name in descriptor.path_fields:
                value = payload.get(field_name)
                if value is None:
                    continue
                if not isinstance(value, str) or not value.strip():
                    raise TrialError(f"case {case.case_id} path field {field_name} is invalid")
                candidate = Path(value).expanduser()
                if not candidate.is_absolute():
                    candidate = (descriptor.source_path.parent / candidate).resolve()
                else:
                    candidate = candidate.resolve()
                if not candidate.is_file():
                    # A relative path may intentionally be created inside the
                    # disposable workspace by a future adapter.  It remains
                    # relative and the standard runner enforces containment.
                    continue
                case_directory = workspace / "inputs" / case.case_id
                if case_directory.exists() and (case_directory.is_symlink() or not case_directory.is_dir()):
                    raise TrialError(f"trial input directory {case.case_id!r} is unsafe")
                case_directory.mkdir(parents=True, exist_ok=True)
                destination = case_directory / candidate.name
                if destination.is_symlink() or destination.is_dir():
                    raise TrialError(f"trial input file {destination.name!r} is unsafe")
                shutil.copy2(candidate, destination)
                relative = destination.relative_to(workspace).as_posix()
                digest = _sha256(destination)
                payload[field_name] = relative
                input_records[relative] = {"path": relative, "sha256": digest}
            normalized_cases.append(TrialCase(case.case_id, payload, case.expected))
        return tuple(normalized_cases[:-1]), normalized_cases[-1], list(input_records.values())

    def _prepare_workspace(
        self,
        descriptor: SelectedCapabilitySolution,
        trial_id: str,
    ) -> tuple[Path, dict[str, Any], tuple[TrialCase, ...], TrialCase, list[dict[str, str]]]:
        workspace = self._workspace(trial_id)
        if self.trial_root == Path(self.trial_root.anchor):
            raise TrialError("trial root must not be a filesystem root")
        if self.trial_root.exists() and (self.trial_root.is_symlink() or not self.trial_root.is_dir()):
            raise TrialError("trial root is not a private directory")
        self.trial_root.mkdir(parents=True, exist_ok=True)
        if workspace.exists() and (workspace.is_symlink() or not workspace.is_dir()):
            raise TrialError("trial workspace is not a private directory")
        marker = workspace / "trial-marker.json"
        if marker.is_symlink():
            raise TrialError("trial workspace marker cannot be a symlink")
        if marker.exists():
            try:
                existing = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise TrialError("existing trial workspace marker is unreadable") from error
            if existing.get("descriptor_hash") != descriptor.descriptor_hash:
                raise TrialError("trial workspace identity conflicts with selected descriptor")
        else:
            workspace.mkdir(parents=True, exist_ok=True)
            _write_json(marker, {
                "protocol": TRIAL_PROTOCOL,
                "trial_id": trial_id,
                "descriptor_hash": descriptor.descriptor_hash,
                "source_sha256": descriptor.source_sha256,
            })
        for name in ("solution", "adapter", "evidence", "inputs"):
            directory = workspace / name
            if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
                raise TrialError(f"trial workspace directory {name!r} is unsafe")
            directory.mkdir(parents=True, exist_ok=True)
        for name in (
            "solution/solution.py", "adapter/adapter.py", "dependency-lock.json",
            "config.json", "adapter-manifest.json", "capability-descriptor.json",
        ):
            target = workspace / name
            if target.is_symlink() or target.is_dir():
                raise TrialError(f"trial workspace file {name!r} is unsafe")
        contract_cases, mission, input_records = self._stage_inputs(descriptor, workspace)
        solution = workspace / "solution" / "solution.py"
        if not solution.exists():
            shutil.copy2(descriptor.source_path, solution)
        if _sha256(solution) != descriptor.source_sha256:
            raise TrialError("installed solution source hash does not match descriptor")
        wrapper = workspace / "adapter" / "adapter.py"
        if not wrapper.exists():
            wrapper.write_text(ADAPTER_WRAPPER_SOURCE, encoding="utf-8")
        wrapper_hash = _sha256(wrapper)
        metadata = metadata_for_spec(descriptor.spec)
        resolved_dependencies: list[dict[str, Any]] = []
        for dependency in descriptor.dependencies:
            source_kind = dependency.source.lower()
            if dependency.name.lower() in {"python-stdlib", "python-runtime"}:
                resolved = True
            elif source_kind in {"stdlib", "python-runtime"}:
                resolved = importlib.util.find_spec(dependency.name.replace("-", "_")) is not None
            else:
                # A non-runtime dependency must be both content-addressed and
                # importable in the isolated runtime.  Arbitrary network
                # installation is intentionally outside this lane; a selected
                # solution that is not already locally available is rejected
                # instead of being recorded as falsely "resolved".
                resolved = (
                    dependency.sha256 is not None
                    and importlib.util.find_spec(dependency.name.replace("-", "_")) is not None
                )
            if not resolved:
                raise TrialError(f"dependency {dependency.name!r} is not available or hash-pinned")
            resolved_dependencies.append({**dependency.as_dict(), "resolved": True})
        lock_payload: dict[str, Any] = {
            "protocol": TRIAL_PROTOCOL,
            "solution_id": descriptor.solution_id,
            "source_sha256": descriptor.source_sha256,
            "dependencies": resolved_dependencies,
            "python": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
                "executable": self.python_executable,
                "executable_sha256": _sha256(Path(self.python_executable)) if Path(self.python_executable).is_file() else None,
            },
        }
        lock_path = workspace / "dependency-lock.json"
        lock_hash = _write_json(lock_path, lock_payload)
        config_hash = _write_json(workspace / "config.json", descriptor.config)
        manifest: dict[str, Any] = {
            "protocol": ADAPTER_PROTOCOL,
            "trial_protocol": TRIAL_PROTOCOL,
            "trial_id": trial_id,
            "solution_id": descriptor.solution_id,
            "registration_id": descriptor.registration_id,
            "descriptor_hash": descriptor.descriptor_hash,
            "capability_id": descriptor.spec.capability_id,
            "version": descriptor.spec.version,
            "action_kind": descriptor.spec.action_kind,
            "semantic": descriptor.spec.semantic.value,
            "effect_class": metadata.effect_class.value,
            "metadata": metadata.as_dict(),
            "solution_path": "solution/solution.py",
            "solution_sha256": descriptor.source_sha256,
            "wrapper_path": "adapter/adapter.py",
            "wrapper_sha256": wrapper_hash,
            "dependency_lock_path": "dependency-lock.json",
            "dependency_lock_sha256": lock_hash,
            "config_path": "config.json",
            "config_sha256": config_hash,
            "path_fields": list(descriptor.path_fields),
            "input_schema": metadata.input_schema,
            "output_schema": metadata.output_schema,
        }
        manifest_path = workspace / "adapter-manifest.json"
        manifest_hash = _write_json(manifest_path, manifest)
        descriptor_payload = descriptor.as_dict()
        descriptor_payload["trial_id"] = trial_id
        descriptor_payload["registration_id"] = descriptor.registration_id
        descriptor_payload["descriptor_hash"] = descriptor.descriptor_hash
        descriptor_payload["metadata"] = metadata.as_dict()
        _write_json(workspace / "capability-descriptor.json", descriptor_payload)
        _append_trial_event(
            self.database,
            TRIAL_WORKSPACE_READY,
            trial_id,
            {"workspace": str(workspace), "workspace_marker_sha256": _sha256(marker), "descriptor_hash": descriptor.descriptor_hash},
        )
        _append_trial_event(
            self.database,
            TRIAL_INSTALLED,
            trial_id,
            {"workspace": str(workspace), "solution_sha256": descriptor.source_sha256, "dependency_lock_sha256": lock_hash},
        )
        _append_trial_event(
            self.database,
            TRIAL_ADAPTER_READY,
            trial_id,
            {"manifest_path": str(manifest_path), "manifest_sha256": manifest_hash, "wrapper_sha256": wrapper_hash},
        )
        return workspace, manifest, contract_cases, mission, input_records

    def _run_verifier(self, bundle_path: Path) -> dict[str, Any]:
        verifier_path = Path(__file__).with_name("trial_verifier.py").resolve()
        env = sanitized_env()
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONPATH"] = ""
        try:
            process = subprocess.run(
                [self.python_executable, "-I", str(verifier_path), str(bundle_path)],
                cwd=str(bundle_path.parent.parent),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                env=env,
                **headless_run_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"verdict": "FAIL", "verifier_id": "jarvis.v12.independent-verifier", "findings": [redact_text(str(error))[:300]]}
        try:
            parsed = json.loads((process.stdout or "").strip().splitlines()[-1])
        except (IndexError, ValueError):
            parsed = {"verdict": "FAIL", "verifier_id": "jarvis.v12.independent-verifier", "findings": ["independent verifier returned no JSON"]}
        if not isinstance(parsed, dict):
            parsed = {"verdict": "FAIL", "verifier_id": "jarvis.v12.independent-verifier", "findings": ["independent verifier returned a non-object"]}
        return parsed

    def _result_from_record(
        self,
        descriptor: SelectedCapabilitySolution,
        trial_id: str,
        record: Mapping[str, Any],
    ) -> TrialResult:
        status = str(record.get("status") or TrialStatus.SELECTED.value)
        return TrialResult(
            trial_id=trial_id,
            status=status,
            workspace=str(record.get("workspace") or self._workspace(trial_id)),
            capability_id=descriptor.spec.capability_id,
            registration_id=descriptor.registration_id,
            descriptor_hash=descriptor.descriptor_hash,
            verification=record.get("verification") or (
                record.get("rejection", {}).get("verification")
                if isinstance(record.get("rejection"), Mapping)
                else None
            ),
            catalog=catalog_truth_snapshot(self.database),
            rejection=record.get("rejection"),
            disable=record.get("disable"),
        )

    def run(self, descriptor: SelectedCapabilitySolution) -> TrialResult:
        trial_id = self._trial_id(descriptor)
        prior = trial_record(self.database, trial_id)
        if prior is not None:
            prior_hash = str(prior.get("descriptor_hash") or descriptor.descriptor_hash)
            if prior_hash != descriptor.descriptor_hash:
                raise TrialError("trial identity is already bound to another descriptor")
            if prior.get("status") in {TrialStatus.PROBATION.value, TrialStatus.REJECTED.value, TrialStatus.DISABLED.value, TrialStatus.RESTORED.value}:
                return self._result_from_record(descriptor, trial_id, prior)
        _append_trial_event(
            self.database,
            TRIAL_SELECTED,
            trial_id,
            {
                "descriptor_hash": descriptor.descriptor_hash,
                "solution_id": descriptor.solution_id,
                "capability_id": descriptor.spec.capability_id,
                "registration_id": descriptor.registration_id,
                "selection_reason": descriptor.selection_reason,
            },
        )
        workspace: Path | None = None
        registered = False
        verification: Mapping[str, Any] | None = None
        verification_path: Path | None = None
        verification_hash: str | None = None
        stage = "selected"
        try:
            stage = "workspace"
            workspace, manifest, contract_cases, mission, input_records = self._prepare_workspace(descriptor, trial_id)
            manifest_path = workspace / "adapter-manifest.json"
            stage = "readiness_probe"
            probe = probe_installed_adapter(
                manifest_path,
                allowed_root=workspace,
                python_executable=self.python_executable,
            )
            probe_hash = _write_json(workspace / "evidence" / "readiness-probe.json", probe)
            if probe.get("claimed") not in {"COMPLETED", None} or probe.get("ready") is not True or probe.get("protocol") != ADAPTER_PROTOCOL:
                raise TrialError("readiness probe did not establish the standard adapter contract")
            _append_trial_event(self.database, TRIAL_PROBE_PASSED, trial_id, {"manifest_path": str(manifest_path), "manifest_sha256": _sha256(manifest_path), "probe_sha256": probe_hash})

            contract_reports: list[dict[str, Any]] = []
            stage = "contracts"
            for case in contract_cases:
                result = run_installed_adapter(
                    manifest_path,
                    case.payload,
                    allowed_root=workspace,
                    python_executable=self.python_executable,
                )
                result_hash = payload_hash(result)
                contract_reports.append({"case_id": case.case_id, "payload": dict(case.payload), "expected": dict(case.expected), "result": result, "result_sha256": result_hash})
            contract_report = {"protocol": TRIAL_PROTOCOL, "cases": contract_reports}
            contract_hash = _write_json(workspace / "evidence" / "contract-tests.json", contract_report)
            if not all(item["result"].get("claimed") == "COMPLETED" and _subset(item["expected"], item["result"]) for item in contract_reports):
                raise TrialError("one or more capability contract cases failed")
            _append_trial_event(self.database, TRIAL_CONTRACTS_PASSED, trial_id, {"evidence_path": str(workspace / "evidence" / "contract-tests.json"), "evidence_sha256": contract_hash, "case_count": len(contract_reports)})

            stage = "mission"
            mission_result = run_installed_adapter(
                manifest_path,
                mission.payload,
                allowed_root=workspace,
                python_executable=self.python_executable,
            )
            mission_record = {"case_id": mission.case_id, "payload": dict(mission.payload), "expected": dict(mission.expected), "result": mission_result, "result_sha256": payload_hash(mission_result)}
            mission_hash = _write_json(workspace / "evidence" / "trial-mission.json", mission_record)
            if mission_result.get("claimed") != "COMPLETED":
                raise TrialError("realistic trial mission did not complete")
            _append_trial_event(self.database, TRIAL_MISSION_COMPLETED, trial_id, {"evidence_path": str(workspace / "evidence" / "trial-mission.json"), "evidence_sha256": mission_hash})

            try:
                descriptor_payload = json.loads(
                    (workspace / "capability-descriptor.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as error:
                raise TrialError("installed capability descriptor is unreadable") from error
            if not isinstance(descriptor_payload, dict):
                raise TrialError("installed capability descriptor is not an object")
            bundle = {
                "protocol": "jarvis.capability.trial.bundle.v1",
                "trial_id": trial_id,
                "registration_id": descriptor.registration_id,
                "descriptor_hash": descriptor.descriptor_hash,
                "workspace": str(workspace),
                "descriptor_path": str(workspace / "capability-descriptor.json"),
                "descriptor_sha256": _sha256(workspace / "capability-descriptor.json"),
                "descriptor": descriptor_payload,
                "manifest_path": str(manifest_path),
                "manifest_sha256": _sha256(manifest_path),
                "manifest": manifest,
                "config_path": str(workspace / "config.json"),
                "config_sha256": _sha256(workspace / "config.json"),
                "config": dict(descriptor.config),
                "dependency_lock_path": str(workspace / "dependency-lock.json"),
                "dependency_lock_sha256": _sha256(workspace / "dependency-lock.json"),
                "probe": probe,
                "contract_cases": contract_reports,
                "mission": mission_record,
                "input_files": input_records,
            }
            bundle_path = workspace / "evidence" / "trial-bundle.json"
            bundle_hash = _write_json(bundle_path, bundle)
            stage = "independent_verifier"
            verification = self._run_verifier(bundle_path)
            verification_path = workspace / "evidence" / "independent-verification.json"
            verification_hash = _write_json(verification_path, verification)
            verification_id = f"verification:{trial_id}"
            if verification.get("verdict") != "PASS":
                rejection = {"stage": "independent_verifier", "reason": "independent verifier rejected trial", "verification_id": verification_id, "evidence_sha256": verification_hash}
                raise TrialError(canonical_json(rejection))
            _append_trial_event(self.database, TRIAL_VERIFIED, trial_id, {"verification_id": verification_id, "verifier_id": verification.get("verifier_id"), "verification_path": str(verification_path), "verification_sha256": verification_hash, "bundle_sha256": bundle_hash})

            prior_record = trial_record(self.database, trial_id) or {}
            stored_before = prior_record.get("catalog_before")
            before_catalog = (
                dict(stored_before)
                if isinstance(stored_before, Mapping)
                and stored_before.get("sha256") == prior_record.get("catalog_before_sha256")
                else catalog_truth_snapshot(self.database)
            )
            installed_spec = replace(descriptor.spec, executor=str(manifest_path))
            metadata = metadata_for_spec(installed_spec)
            _append_trial_event(
                self.database,
                TRIAL_ADOPTION_DECIDED,
                trial_id,
                {
                    "decision": "ADOPT_PROBATION",
                    "reason": "independent verifier PASS; bounded trust policy applies",
                    "verification_id": verification_id,
                    "registration_id": descriptor.registration_id,
                    "catalog_before_sha256": before_catalog["sha256"],
                    "catalog_before": before_catalog,
                },
            )
            stage = "adoption"
            try:
                register_capability(
                    self.database,
                    installed_spec,
                    note="independently verified disposable capability trial",
                    metadata=metadata,
                    trust=CapabilityTrust.PROBATION,
                    registration_id=descriptor.registration_id,
                    adoption={
                        "trial_id": trial_id,
                        "descriptor_hash": descriptor.descriptor_hash,
                        "verification_id": verification_id,
                        "verification_sha256": verification_hash,
                        "adapter_manifest_sha256": _sha256(manifest_path),
                        "workspace": str(workspace),
                    },
                )
                registered = True
                record_outcome(self.database, descriptor.spec.capability_id, success=True, verified=VerifyVerdict.MATCH, note="V12 independent trial verified")
            except Exception:
                if registered:
                    disable_capability(self.database, descriptor.spec.capability_id, registration_id=descriptor.registration_id, reason="adoption finalization failed")
                    registered = False
                raise
            after_catalog = catalog_truth_snapshot(self.database)
            adopted = {
                "workspace": str(workspace),
                "descriptor_hash": descriptor.descriptor_hash,
                "registration_id": descriptor.registration_id,
                "verification_id": verification_id,
                "verification_sha256": verification_hash,
                "verification": verification,
                "trust": CapabilityTrust.PROBATION.value,
                "catalog_before_sha256": before_catalog["sha256"],
                "catalog_after_sha256": after_catalog["sha256"],
            }
            _append_trial_event(self.database, TRIAL_ADOPTED, trial_id, adopted)
            return TrialResult(
                trial_id=trial_id,
                status=TrialStatus.PROBATION.value,
                workspace=str(workspace),
                capability_id=descriptor.spec.capability_id,
                registration_id=descriptor.registration_id,
                descriptor_hash=descriptor.descriptor_hash,
                verification=verification,
                catalog=after_catalog,
            )
        except Exception as error:
            current_registration = capability_registration(self.database, descriptor.spec.capability_id)
            owned_registration = (
                isinstance(current_registration, Mapping)
                and current_registration.get("registration_id") == descriptor.registration_id
                and isinstance(current_registration.get("adoption"), Mapping)
                and current_registration["adoption"].get("trial_id") == trial_id
            )
            if registered or owned_registration:
                try:
                    disable_capability(self.database, descriptor.spec.capability_id, registration_id=descriptor.registration_id, reason="failed trial finalization")
                except Exception:
                    pass
            rejection = {
                "decision": "REJECT",
                "stage": stage,
                "reason": redact_text(str(error))[:500],
                "workspace": str(workspace or self._workspace(trial_id)),
                "descriptor_hash": descriptor.descriptor_hash,
            }
            if verification is not None:
                rejection.update({
                    "verification_id": f"verification:{trial_id}",
                    "verification_path": str(verification_path) if verification_path is not None else None,
                    "verification_sha256": verification_hash,
                    "verdict": verification.get("verdict"),
                    "verifier_id": verification.get("verifier_id"),
                    "verification": dict(verification),
                })
            _append_trial_event(self.database, TRIAL_REJECTED, trial_id, {"rejection": rejection, "workspace": rejection["workspace"], "descriptor_hash": descriptor.descriptor_hash})
            return TrialResult(
                trial_id=trial_id,
                status=TrialStatus.REJECTED.value,
                workspace=rejection["workspace"],
                capability_id=descriptor.spec.capability_id,
                registration_id=descriptor.registration_id,
                descriptor_hash=descriptor.descriptor_hash,
                verification=verification,
                rejection=rejection,
                catalog=catalog_truth_snapshot(self.database),
            )

    def run_descriptor(self, descriptor: Mapping[str, Any] | str | Path) -> TrialResult:
        """Run a descriptor object or descriptor JSON file through the same path."""

        selected = (
            SelectedCapabilitySolution.from_file(descriptor)
            if isinstance(descriptor, (str, Path))
            else SelectedCapabilitySolution.from_mapping(descriptor)
        )
        return self.run(selected)

    def disable(self, descriptor: SelectedCapabilitySolution) -> TrialResult:
        trial_id = self._trial_id(descriptor)
        record = trial_record(self.database, trial_id)
        if record is None:
            raise TrialError(f"trial {trial_id} does not exist")
        if record.get("status") in {TrialStatus.DISABLED.value, TrialStatus.RESTORED.value}:
            return self._result_from_record(descriptor, trial_id, record)
        if record.get("status") != TrialStatus.PROBATION.value:
            raise TrialError(f"trial {trial_id} is not in PROBATION")
        adoption = record
        disable_result = disable_capability(
            self.database,
            descriptor.spec.capability_id,
            registration_id=descriptor.registration_id,
            reason="owner-disabled after disposable capability trial",
        )
        after = catalog_truth_snapshot(self.database)
        restored = after["sha256"] == adoption.get("catalog_before_sha256")
        payload = {
            "workspace": record.get("workspace") or str(self._workspace(trial_id)),
            "registration_id": descriptor.registration_id,
            "reason": disable_result.get("reason") or "owner-disabled after disposable capability trial",
            "catalog_before_sha256": adoption.get("catalog_before_sha256"),
            "catalog_after_sha256": after["sha256"],
            "restored": restored,
            "disabled": bool(disable_result.get("disabled") or disable_result.get("already_disabled")),
        }
        _append_trial_event(self.database, TRIAL_DISABLED, trial_id, payload)
        return TrialResult(
            trial_id=trial_id,
            status=TrialStatus.RESTORED.value if restored else TrialStatus.DISABLED.value,
            workspace=str(payload["workspace"]),
            capability_id=descriptor.spec.capability_id,
            registration_id=descriptor.registration_id,
            descriptor_hash=descriptor.descriptor_hash,
            verification=record.get("verification"),
            catalog=after,
            disable=payload,
        )


__all__ = [
    "CapabilityTrialPipeline",
    "DependencyPin",
    "SelectedCapabilitySolution",
    "TRIAL_PROTOCOL",
    "TrialCase",
    "TrialError",
    "TrialResult",
    "TrialStatus",
    "trial_record",
]
