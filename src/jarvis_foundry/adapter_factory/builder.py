"""Deterministic build, isolated installation, and removal for V12 adapters."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import base64
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
import zipfile
from pathlib import Path
from typing import Any, Mapping

from ..fabric.contracts import CapabilityMetadata
from ..platform.windows_subprocess import headless_run_kwargs, sanitized_env
from .model import (
    ADAPTER_PROTOCOL,
    ARTIFACT_VERSION,
    AdapterBuildArtifact,
    AdapterFactoryError,
    AdapterShape,
    DependencyKind,
    SelectedSolutionDescriptor,
    SolutionClass,
)
from .runtime import DISABLED_FILE, MANIFEST_FILE, load_verified_artifact, probe_descriptor


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value)


def _generated_adapter_source(descriptor: SelectedSolutionDescriptor) -> str:
    capabilities = ", ".join(item.capability_id for item in descriptor.capabilities)
    return f'''\
"""Generated thin adapter for {descriptor.solution_name}.

Adapter identity: {descriptor.adapter_id}
Solution class: {descriptor.solution_class.value}
Capabilities: {capabilities}

The declarative mapping lives in descriptor.json.  The reviewable runtime in
jarvis_foundry.adapter_factory.runtime performs schema validation, bounded
invocation, cancellation, and receipt normalization.  This file owns no
catalog registration, effect journal, scheduler, or provider policy.
"""
from pathlib import Path

from jarvis_foundry.adapter_factory.runtime import child_main


DESCRIPTOR = Path(__file__).with_name("descriptor.json")


def main(argv=None):
    return child_main(DESCRIPTOR, argv)


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _generated_contract_tests(descriptor: SelectedSolutionDescriptor) -> str:
    cases = [
        {"capability_id": capability.capability_id, **case.as_dict()}
        for capability in descriptor.capabilities
        for case in capability.contract_cases
    ]
    return f'''\
"""Focused generated contract tests for {descriptor.adapter_id}."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from jarvis_foundry.adapter_factory.runtime import invoke_artifact, load_verified_artifact, probe_artifact


ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads({json.dumps(json.dumps(cases, sort_keys=True))})


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and subset(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(subset(a, b) for a, b in zip(expected, actual))
    return expected == actual


def materialize(value):
    if isinstance(value, str):
        return value.replace("$artifact_root", str(ROOT))
    if isinstance(value, list):
        return [materialize(item) for item in value]
    if isinstance(value, dict):
        return {{key: materialize(item) for key, item in value.items()}}
    return value


class GeneratedAdapterContractTests(unittest.TestCase):
    def test_artifact_hashes_and_identity(self):
        _root, manifest, descriptor = load_verified_artifact(ROOT)
        self.assertEqual(manifest["adapter_id"], {descriptor.adapter_id!r})
        self.assertEqual(descriptor.adapter_id, {descriptor.adapter_id!r})

    def test_readiness(self):
        result = probe_artifact(ROOT, python_executable=sys.executable)
        self.assertTrue(result.get("ready"), result)

    def test_frozen_cases(self):
        self.assertTrue(CASES, "the generated adapter must carry at least one focused case")
        for case in CASES:
            with self.subTest(case=case["case_id"]):
                result = invoke_artifact(
                    ROOT, case["capability_id"], materialize(case["payload"]),
                    python_executable=sys.executable,
                    allowed_root=ROOT,
                )
                self.assertTrue(subset(materialize(case["expected"]), result), result)


if __name__ == "__main__":
    unittest.main()
'''


def _install_instructions(descriptor: SelectedSolutionDescriptor) -> str:
    lines = [
        f"# Install {descriptor.adapter_id}", "",
        "This is a candidate artifact. Installation does not register it in the Fabric Catalog.", "",
        "1. Create an isolated Python environment.",
        "2. Install the exact JARVIS candidate wheel into that environment.",
        "3. Satisfy the dependency declarations in `dependency-manifest.json`.",
        "4. Run `adapter.py probe --manifest adapter-manifest.json`.",
        "5. Run `tests/test_contract.py` before handing the artifact to Session 7.", "",
        "Every command must use argv execution; no generated shell fragments are required.", "",
    ]
    return "\n".join(lines)


def _removal_instructions(descriptor: SelectedSolutionDescriptor) -> str:
    return "\n".join([
        f"# Disable or remove {descriptor.adapter_id}", "",
        "Disable writes only `ADAPTER_DISABLED.json` inside this artifact.",
        "Removal first verifies the manifest, exact adapter identity, and artifact root,",
        "then removes only this generated directory. It does not change Fabric catalog state.",
        "Session 7 owns catalog disable/restoration after adoption.", "",
    ])


def _metadata(descriptor: SelectedSolutionDescriptor, capability, executor: str) -> CapabilityMetadata:
    external = capability.effect_class.value in {"REVERSIBLE_EXTERNAL", "IRREVERSIBLE_OR_HIGH_COST"}
    secret_refs = tuple(item.name for item in descriptor.configuration if item.secret)
    return CapabilityMetadata(
        capability_id=capability.capability_id,
        version=capability.version,
        input_schema=capability.input_schema_id,
        output_schema=capability.output_schema_id,
        effect_class=capability.effect_class,
        requirements=capability.requirements,
        credential_refs=secret_refs,
        preconditions=capability.preconditions,
        postconditions=tuple(str(item.get("name") or item.get("field") or "postcondition") if isinstance(item, Mapping) else str(item) for item in capability.postconditions),
        idempotency={
            "required": capability.effect_class.value != "READ_ONLY",
            "strategy": "provider-key" if external else "effect-key",
            "replay": "reconcile-before-retry" if external else "no-op",
        },
        execute={
            "executor": executor,
            "action_kind": capability.action_kind,
            "adapter_id": descriptor.adapter_id,
            "solution_class": descriptor.solution_class.value,
            "adapter_shape": descriptor.adapter_shape.value,
        },
        evidence={
            "receipt_schema": "jarvis.v10.adapter.receipt.v1",
            "before_state_required": capability.effect_class.value != "READ_ONLY",
            "independent_verifier": True,
            "generated_contract_cases": len(capability.contract_cases),
        },
        timeout_and_cancel={
            "timeout_ms": capability.timeout_ms,
            "cancel_supported": capability.cancel_supported,
            "timeout_outcome": "UNKNOWN",
        },
        recovery={
            "max_attempts": 1 if external else 3,
            "retryable_outcomes": [] if external else ["FAILED"],
            "partial_effect": capability.reconciliation.ambiguity_outcome,
            "ambiguous_outcome": capability.reconciliation.ambiguity_outcome,
            "reconciliation": capability.reconciliation.as_dict(),
        },
        world_model={"verified_terminal_only": True, "source_kind": "mission:outcome"},
        provenance={
            "authority": "Capability Fabric",
            "source": "adapter_factory",
            "solution_id": descriptor.solution_id,
            "descriptor_hash": descriptor.descriptor_hash,
            "selection": dict(descriptor.selection),
        },
    )


def inspect_solution(descriptor: SelectedSolutionDescriptor) -> dict[str, Any]:
    """Inspect only the bounded interface surface declared by Session 6."""

    surface: dict[str, Any] = {
        "solution_class": descriptor.solution_class.value,
        "locator": descriptor.locator,
        "capability_ids": [item.capability_id for item in descriptor.capabilities],
        "action_kinds": [item.action_kind for item in descriptor.capabilities],
    }
    try:
        if descriptor.solution_class is SolutionClass.CLI:
            surface["resolved_executable"] = shutil.which(descriptor.readiness.target)
        elif descriptor.solution_class in {SolutionClass.PYTHON_SDK, SolutionClass.JARVIS_COMPONENT}:
            module_name = descriptor.readiness.target
            module = __import__(module_name, fromlist=["*"])
            surface["module_file"] = str(getattr(module, "__file__", ""))
            callables: dict[str, str] = {}
            for capability in descriptor.capabilities:
                target = capability.invocation.target
                target_module, _, attribute = target.partition(":")
                if target_module != module_name or not attribute:
                    continue
                value: Any = module
                for part in attribute.split("."):
                    value = getattr(value, part)
                if callable(value):
                    try:
                        callables[target] = str(inspect.signature(value))
                    except (TypeError, ValueError):
                        callables[target] = "UNKNOWN"
            surface["callables"] = callables
        elif descriptor.solution_class is SolutionClass.HTTP_API:
            surface["methods"] = sorted({item.invocation.method or "GET" for item in descriptor.capabilities})
            surface["allowed_origins"] = sorted({origin for item in descriptor.capabilities for origin in item.invocation.allowed_origins})
        elif descriptor.solution_class is SolutionClass.MCP_STDIO:
            surface["server_command"] = [descriptor.readiness.target, *descriptor.readiness.arguments]
            surface["tools"] = [item.invocation.target for item in descriptor.capabilities]
        probe = probe_descriptor(descriptor)
    except Exception as error:
        probe = {"ready": False, "state": "NOT_READY", "error_code": "inspection_failed", "detail": str(error)[:400]}
    return {
        "inspection_version": "jarvis.v12.solution-inspection.v1",
        "adapter_id": descriptor.adapter_id,
        "solution_id": descriptor.solution_id,
        "descriptor_hash": descriptor.descriptor_hash,
        "surface": surface,
        "readiness": probe,
        "read_only": True,
        "mutations": {"install": False, "catalog": False, "provider_effect": False, "live": False},
    }


def _artifact_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != MANIFEST_FILE and path.name != DISABLED_FILE:
            result[path.relative_to(root).as_posix()] = _sha256(path)
    return result


class AdapterFactory:
    """Build understandable candidate artifacts; never register them."""

    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)

    def build(self, descriptor: SelectedSolutionDescriptor) -> AdapterBuildArtifact:
        if not isinstance(descriptor, SelectedSolutionDescriptor):
            raise AdapterFactoryError("build requires a SelectedSolutionDescriptor")
        destination = (self.output_root / descriptor.artifact_id).resolve()
        try:
            destination.relative_to(self.output_root)
        except ValueError as error:
            raise AdapterFactoryError("adapter artifact escapes output root") from error
        if destination.exists():
            root, manifest, loaded = load_verified_artifact(destination)
            if loaded.descriptor_hash != descriptor.descriptor_hash:
                raise AdapterFactoryError("existing adapter identity has different descriptor content")
            return AdapterBuildArtifact(
                descriptor.artifact_id, descriptor.adapter_id, descriptor.descriptor_hash,
                root, root / MANIFEST_FILE, dict(manifest["files"]),
            )

        staging = Path(tempfile.mkdtemp(prefix=f".{_safe_name(descriptor.adapter_id)}-", dir=self.output_root))
        try:
            _write_json(staging / "descriptor.json", descriptor.as_dict())
            (staging / "adapter.py").write_text(_generated_adapter_source(descriptor), encoding="utf-8")
            _write_json(staging / "inspection.json", inspect_solution(descriptor))
            _write_json(staging / "dependency-manifest.json", {
                "contract_version": "jarvis.v12.adapter-dependencies.v1",
                "adapter_id": descriptor.adapter_id,
                "dependencies": [item.as_dict() for item in descriptor.dependencies],
                "configuration": [item.as_dict() for item in descriptor.configuration],
                "install_isolated": True,
                "catalog_registration": False,
            })
            fabric_records = []
            for capability in descriptor.capabilities:
                executor = "adapter-manifest.json"
                fabric_records.append({
                    "spec": capability.fabric_spec(executor=executor).as_dict(),
                    "metadata": _metadata(descriptor, capability, executor).as_dict(),
                    "registration_owner": "SESSION_7",
                    "registered": False,
                })
                schema_root = staging / "schemas" / _safe_name(capability.capability_id)
                _write_json(schema_root / "input.schema.json", capability.input_schema)
                _write_json(schema_root / "output.schema.json", capability.output_schema)
            _write_json(staging / "fabric-capabilities.json", {
                "contract_version": "jarvis.v10.adapter.v1",
                "adapter_id": descriptor.adapter_id,
                "capabilities": fabric_records,
                "catalog_mutated": False,
            })
            _write_json(staging / "trial-admission.json", {
                "contract_version": "jarvis.v12.adapter-trial-admission.v1",
                "adapter_id": descriptor.adapter_id,
                "artifact_id": descriptor.artifact_id,
                "descriptor_hash": descriptor.descriptor_hash,
                "adapter_manifest": "adapter-manifest.json",
                "dependencies": [item.as_dict() for item in descriptor.dependencies],
                "contract_cases": [
                    {
                        "capability_id": capability.capability_id,
                        "cases": [case.as_dict() for case in capability.contract_cases],
                    }
                    for capability in descriptor.capabilities
                ],
                "requested_trust": "PROBATION",
                "registration_owner": "SESSION_7",
                "registered": False,
            })
            connector_strategy = "none"
            strategies = {item.reconciliation.strategy for item in descriptor.capabilities}
            if "provider_query" in strategies:
                connector_strategy = "provider-query"
            elif strategies & {"repeat_read", "system_readback"}:
                connector_strategy = "system-readback"
            _write_json(staging / "connector-spec.json", {
                "contract_version": "jarvis.connector.v5",
                "connector_id": descriptor.adapter_id,
                "kind": descriptor.solution_class.value,
                "version": descriptor.capabilities[0].version,
                "actions": {item.action_kind: item.semantic.value for item in descriptor.capabilities},
                "capabilities": {item.capability_id: "DISCOVERED" for item in descriptor.capabilities},
                "retry_policy": {"max_attempts": 1, "backoff_s": []},
                "target_kinds": [descriptor.solution_class.value],
                "idempotency_strategy": "provider-key" if any(
                    item.effect_class.value in {"REVERSIBLE_EXTERNAL", "IRREVERSIBLE_OR_HIGH_COST"}
                    for item in descriptor.capabilities
                ) else "effect-key",
                "reconciliation_strategy": connector_strategy,
                "supports_cancellation": all(item.cancel_supported for item in descriptor.capabilities),
                "cost_metadata": {},
                "evidence_fields": ["receipt_id", "effect_id", "observed_outcome"],
                "stop_policy": "broker-and-driver",
                "registered": False,
            })
            if descriptor.adapter_shape is AdapterShape.WORKER:
                _write_json(staging / "worker-contract.json", {
                    "contract_version": "jarvis.worker-step.v1",
                    "adapter_id": descriptor.adapter_id,
                    "input": "WorkerStepContract.action_envelope.payload",
                    "output": "ResultEnvelope",
                    "entrypoint": "jarvis_foundry.adapter_factory.worker_bridge:GeneratedWorkerAdapter",
                    "registered": False,
                })
            _write_json(staging / "disable-remove.json", {
                "contract_version": "jarvis.v12.adapter-lifecycle.v1",
                "adapter_id": descriptor.adapter_id,
                "disable_marker": DISABLED_FILE,
                "disable_reason": descriptor.disable_reason,
                "remove_requires": ["verified manifest", "exact adapter_id", "artifact-root containment"],
                "catalog_owner": "SESSION_7",
                "catalog_mutated": False,
            })
            (staging / "INSTALL.md").write_text(_install_instructions(descriptor), encoding="utf-8")
            (staging / "REMOVE.md").write_text(_removal_instructions(descriptor), encoding="utf-8")
            tests = staging / "tests"
            tests.mkdir(parents=True, exist_ok=True)
            (tests / "test_contract.py").write_text(_generated_contract_tests(descriptor), encoding="utf-8")

            # Parse generated source without leaving untracked bytecode outside
            # the manifest's exact file inventory.
            compile((staging / "adapter.py").read_text(encoding="utf-8"), "adapter.py", "exec")
            compile((tests / "test_contract.py").read_text(encoding="utf-8"), "test_contract.py", "exec")

            files = _artifact_files(staging)
            adapter_hash = files["adapter.py"]
            path_fields = sorted({
                name
                for capability in descriptor.capabilities
                for name, schema in (capability.input_schema.get("properties") or {}).items()
                if isinstance(schema, Mapping) and schema.get("format") == "path"
            })
            manifest = {
                "protocol": ADAPTER_PROTOCOL,
                "contract_version": ARTIFACT_VERSION,
                "artifact_id": descriptor.artifact_id,
                "adapter_id": descriptor.adapter_id,
                "descriptor_hash": descriptor.descriptor_hash,
                "descriptor_path": "descriptor.json",
                "descriptor_sha256": files["descriptor.json"],
                "wrapper_path": "adapter.py",
                "wrapper_sha256": adapter_hash,
                # Compatibility with Session 7's current installed-adapter
                # verifier; this factory artifact is already the solution.
                "solution_path": "adapter.py",
                "solution_sha256": adapter_hash,
                "config_path": None,
                "capability_id": descriptor.capabilities[0].capability_id,
                "action_kind": descriptor.capabilities[0].action_kind,
                "capabilities": [item.capability_id for item in descriptor.capabilities],
                "path_fields": path_fields,
                "files": files,
                "registered": False,
                "trust": "UNADOPTED",
            }
            _write_json(staging / MANIFEST_FILE, manifest)
            os.replace(staging, destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        root, manifest, _ = load_verified_artifact(destination)
        return AdapterBuildArtifact(
            descriptor.artifact_id, descriptor.adapter_id, descriptor.descriptor_hash,
            root, root / MANIFEST_FILE, dict(manifest["files"]),
        )


def disable_artifact(root_or_manifest: str | Path, *, reason: str) -> dict[str, Any]:
    root, manifest, descriptor = load_verified_artifact(root_or_manifest)
    marker = root / DISABLED_FILE
    payload = {
        "contract_version": "jarvis.v12.adapter-disabled.v1",
        "adapter_id": descriptor.adapter_id,
        "artifact_id": manifest["artifact_id"],
        "descriptor_hash": descriptor.descriptor_hash,
        "reason": str(reason).strip() or descriptor.disable_reason,
    }
    if marker.exists():
        existing = _json_object(marker)
        if existing.get("adapter_id") != descriptor.adapter_id:
            raise AdapterFactoryError("disabled marker identity mismatch")
        return {**existing, "disabled": True, "replayed": True}
    _write_json(marker, payload)
    return {**payload, "disabled": True, "replayed": False}


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AdapterFactoryError(f"invalid JSON file: {path}") from error
    if not isinstance(value, dict):
        raise AdapterFactoryError(f"expected JSON object: {path}")
    return value


def enable_artifact(root_or_manifest: str | Path) -> dict[str, Any]:
    root, manifest, descriptor = load_verified_artifact(root_or_manifest)
    marker = root / DISABLED_FILE
    if marker.exists():
        existing = _json_object(marker)
        if existing.get("adapter_id") != descriptor.adapter_id:
            raise AdapterFactoryError("disabled marker identity mismatch")
        marker.unlink()
    return {"adapter_id": descriptor.adapter_id, "artifact_id": manifest["artifact_id"], "enabled": True}


def remove_artifact(root_or_manifest: str | Path, *, expected_adapter_id: str) -> dict[str, Any]:
    root, manifest, descriptor = load_verified_artifact(root_or_manifest)
    if descriptor.adapter_id != expected_adapter_id:
        raise AdapterFactoryError("refusing removal: expected adapter identity does not match")
    if root.name != descriptor.artifact_id or root.parent == root or len(root.parts) < 3:
        raise AdapterFactoryError("refusing removal: target is not an exact adapter artifact root")
    record = {
        "adapter_id": descriptor.adapter_id,
        "artifact_id": manifest["artifact_id"],
        "descriptor_hash": descriptor.descriptor_hash,
        "removed_root": str(root),
        "removed_files": sorted(manifest["files"]),
        "removed": True,
        "catalog_mutated": False,
    }
    shutil.rmtree(root)
    if root.exists():
        raise AdapterFactoryError("adapter artifact removal did not complete")
    return record


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _wheel_member(path: str, data: bytes) -> tuple[str, bytes, str]:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return path, data, f"sha256={digest},{len(data)}"


def _build_candidate_wheel(project_root: Path, wheels: Path) -> Path:
    """Build the exact pure-Python candidate without a global build backend."""

    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject.get("project") or {}
    if project.get("name") != "jarvis-foundry":
        raise AdapterFactoryError("isolated install requires the JARVIS Foundry project")
    version = str(project.get("version") or "").strip()
    if not version:
        raise AdapterFactoryError("candidate project version is missing")
    package_root = project_root / "src" / "jarvis_foundry"
    if not package_root.is_dir():
        raise AdapterFactoryError("candidate package source is missing")
    dist_info = f"jarvis_foundry-{version}.dist-info"
    members: list[tuple[str, bytes, str]] = []
    for path in sorted(item for item in package_root.rglob("*") if item.is_file()):
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            continue
        arcname = (Path("jarvis_foundry") / path.relative_to(package_root)).as_posix()
        members.append(_wheel_member(arcname, path.read_bytes()))
    metadata = "\n".join([
        "Metadata-Version: 2.4", "Name: jarvis-foundry", f"Version: {version}",
        "Summary: Durable autonomy kernel and independent supervisor for JARVIS Foundry",
        "Requires-Python: >=3.13", "", "",
    ]).encode("utf-8")
    wheel_metadata = "\n".join([
        "Wheel-Version: 1.0", "Generator: jarvis-adapter-factory",
        "Root-Is-Purelib: true", "Tag: py3-none-any", "", "",
    ]).encode("utf-8")
    entry_points = "\n".join([
        "[console_scripts]", "jarvis = jarvis_foundry.cli.main:main",
        "jarvis-adapter-factory = jarvis_foundry.adapter_factory.cli:main", "",
    ]).encode("utf-8")
    members.extend([
        _wheel_member(f"{dist_info}/METADATA", metadata),
        _wheel_member(f"{dist_info}/WHEEL", wheel_metadata),
        _wheel_member(f"{dist_info}/entry_points.txt", entry_points),
        _wheel_member(f"{dist_info}/top_level.txt", b"jarvis_foundry\n"),
    ])
    record_path = f"{dist_info}/RECORD"
    record = "".join(f'{name},"{digest}"\n' for name, _data, digest in members)
    record += f"{record_path},,\n"
    members.append((record_path, record.encode("utf-8"), ""))
    wheel = wheels / f"jarvis_foundry-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data, _digest in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return wheel


def install_isolated_runtime(
    artifact: AdapterBuildArtifact | str | Path,
    *,
    environment_root: str | Path,
    project_root: str | Path,
) -> dict[str, Any]:
    """Install the exact candidate package into a fresh, artifact-local venv."""

    artifact_root = artifact.root if isinstance(artifact, AdapterBuildArtifact) else Path(artifact)
    root, manifest, descriptor = load_verified_artifact(artifact_root)
    environment = Path(environment_root).expanduser().resolve()
    if environment.exists():
        raise AdapterFactoryError(f"isolated environment already exists: {environment}")
    environment.parent.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True, clear=False).create(environment)
    python = _venv_python(environment)
    wheels = environment / "wheels"
    wheels.mkdir()
    env = sanitized_env()
    try:
        wheel = _build_candidate_wheel(Path(project_root).resolve(), wheels)
        installed = subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, check=False, env=env, **headless_run_kwargs(),
        )
        if installed.returncode != 0:
            raise AdapterFactoryError(f"candidate wheel install failed: {installed.stderr[-800:]}")
        dependency_results: list[dict[str, Any]] = []
        for dependency in descriptor.dependencies:
            if dependency.kind is DependencyKind.PYTHON and dependency.source not in {"stdlib", "python-runtime", "jarvis-wheel"}:
                requirement = f"{dependency.name}=={dependency.version}"
                result = subprocess.run(
                    [str(python), "-m", "pip", "install", requirement],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=300, check=False, env=env, **headless_run_kwargs(),
                )
                dependency_results.append({"name": dependency.name, "returncode": result.returncode})
                if result.returncode != 0 and dependency.required:
                    raise AdapterFactoryError(f"required Python dependency install failed: {dependency.name}")
            else:
                dependency_results.append({"name": dependency.name, "returncode": 0, "mode": "runtime-probe"})
        freeze = subprocess.run(
            [str(python), "-m", "pip", "freeze", "--all"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=60,
            check=False, env=env, **headless_run_kwargs(),
        )
        result = {
            "contract_version": "jarvis.v12.adapter-isolated-install.v1",
            "adapter_id": descriptor.adapter_id,
            "artifact_id": manifest["artifact_id"],
            "adapter_manifest_sha256": _sha256(root / MANIFEST_FILE),
            "python": str(python),
            "candidate_wheel": wheel.name,
            "candidate_wheel_sha256": _sha256(wheel),
            "dependencies": dependency_results,
            "installed_packages": sorted(line for line in freeze.stdout.splitlines() if line.strip()),
            "catalog_mutated": False,
            "live_mutated": False,
        }
        _write_json(environment / "adapter-runtime-install.json", result)
        return result
    except BaseException:
        shutil.rmtree(environment, ignore_errors=True)
        raise


def remove_isolated_runtime(environment_root: str | Path, *, expected_python: str | Path) -> dict[str, Any]:
    environment = Path(environment_root).expanduser().resolve()
    python = _venv_python(environment)
    if python != Path(expected_python).expanduser().resolve() or not python.is_file():
        raise AdapterFactoryError("refusing runtime removal: isolated Python identity mismatch")
    if environment.parent == environment or len(environment.parts) < 3:
        raise AdapterFactoryError("refusing runtime removal: target is too broad")
    shutil.rmtree(environment)
    return {"environment": str(environment), "removed": not environment.exists()}


__all__ = [
    "AdapterFactory", "disable_artifact", "enable_artifact", "inspect_solution",
    "install_isolated_runtime", "remove_artifact", "remove_isolated_runtime",
]
