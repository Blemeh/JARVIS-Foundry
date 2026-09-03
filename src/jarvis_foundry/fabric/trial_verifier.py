"""Independent, read-only verifier for a V12 capability trial bundle.

The verifier is intentionally a small separate process.  It never imports or
executes the candidate solution; it checks the frozen adapter evidence,
recomputes file hashes, and evaluates the descriptor's expected observations.
That keeps a builder/adapter claim from becoming its own acceptance authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

VERIFIER_ID = "jarvis.v12.independent-verifier"
VERIFIER_PROTOCOL = "jarvis.capability.trial.bundle.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: Any) -> str:
    # Match Foundry's canonical_json byte-for-byte, including ASCII escaping;
    # evidence hashes must be portable across the independent process.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _subset(expected: Any, actual: Any) -> bool:
    """Match a frozen expected object without accepting extra claims as proof."""

    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _subset(value, actual[key])
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and expected == actual
    return expected == actual


def _criterion(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    return {"criterion": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def _contained(path: Path, root: Path) -> bool:
    """Return whether *path* is strictly contained by *root*."""

    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _scrub_payload(value: Any, path_fields: list[str]) -> Any:
    """Normalize disposable input locations while retaining all other data."""

    def clone(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): clone(child) for key, child in item.items()}
        if isinstance(item, list):
            return [clone(child) for child in item]
        return item

    if not isinstance(value, Mapping):
        return clone(value)
    result = clone(value)
    for field in path_fields:
        if field in result:
            result[field] = "<disposable-input>"
    return result


def _descriptor_identity_hash(descriptor: Mapping[str, Any]) -> str | None:
    """Recompute the path-independent selected-solution identity."""

    try:
        path_fields = [str(item) for item in (descriptor.get("path_fields") or ["path"])]
        raw_spec = descriptor["spec"]
        if not isinstance(raw_spec, Mapping):
            return None
        spec = {str(key): value for key, value in raw_spec.items() if key != "executor"}
        cases = descriptor.get("contract_cases")
        mission = descriptor.get("mission")
        if not isinstance(cases, list) or not isinstance(mission, Mapping):
            return None
        identity = {
            "protocol": "jarvis.capability.trial.v1",
            "solution_id": descriptor["solution_id"],
            "spec": spec,
            "source_sha256": descriptor["source_sha256"],
            "dependencies": descriptor["dependencies"],
            "contract_cases": [
                {
                    "case_id": item["case_id"],
                    "payload": _scrub_payload(item["payload"], path_fields),
                    "expected": item["expected"],
                }
                for item in cases
                if isinstance(item, Mapping)
            ],
            "mission": {
                "case_id": mission["case_id"],
                "payload": _scrub_payload(mission["payload"], path_fields),
                "expected": mission["expected"],
            },
            "config": descriptor["config"],
            "path_fields": path_fields,
        }
    except (KeyError, TypeError, ValueError):
        return None
    return hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()


def _descriptor_case_projection(value: Mapping[str, Any], path_fields: list[str]) -> dict[str, Any] | None:
    if not all(key in value for key in ("case_id", "payload", "expected")):
        return None
    if not isinstance(value.get("payload"), Mapping) or not isinstance(value.get("expected"), Mapping):
        return None
    return {
        "case_id": value["case_id"],
        "payload": _scrub_payload(value["payload"], path_fields),
        "expected": value["expected"],
    }


def verify_bundle(bundle_path: str | Path) -> dict[str, Any]:
    path = Path(bundle_path).expanduser().resolve()
    criteria: list[dict[str, Any]] = []
    findings: list[str] = []
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {
            "verdict": "FAIL",
            "verifier_id": VERIFIER_ID,
            "criteria": [_criterion("bundle-readable", False, str(error))],
            "findings": ["bundle is not readable JSON"],
        }
    if not isinstance(bundle, dict):
        return {
            "verdict": "FAIL",
            "verifier_id": VERIFIER_ID,
            "criteria": [_criterion("bundle-object", False)],
            "findings": ["bundle must be an object"],
        }
    criteria.append(_criterion(
        "bundle-protocol", bundle.get("protocol") == VERIFIER_PROTOCOL,
        "unsupported or missing trial bundle protocol",
    ))
    workspace = Path(str(bundle.get("workspace") or "")).expanduser().resolve()
    workspace_ok = workspace.is_dir() and workspace != Path(workspace.anchor)
    criteria.append(_criterion("disposable-workspace", workspace_ok, str(workspace)))
    marker_path = workspace / "trial-marker.json"
    marker_ok = workspace_ok and marker_path.is_file()
    marker: dict[str, Any] = {}
    if marker_ok:
        try:
            parsed_marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker = parsed_marker if isinstance(parsed_marker, dict) else {}
        except (OSError, ValueError):
            marker = {}
    bundle_descriptor_for_marker = bundle.get("descriptor")
    marker_source_hash = (
        bundle_descriptor_for_marker.get("source_sha256")
        if isinstance(bundle_descriptor_for_marker, Mapping)
        else None
    )
    criteria.append(_criterion(
        "workspace-marker",
        marker.get("protocol") == "jarvis.capability.trial.v1"
        and marker.get("trial_id") == bundle.get("trial_id")
        and marker.get("descriptor_hash") == bundle.get("descriptor_hash")
        and marker.get("source_sha256") == marker_source_hash,
        "workspace marker is missing or bound to another trial",
    ))
    criteria.append(_criterion(
        "bundle-location",
        workspace_ok and _contained(path, workspace),
        "trial bundle is not stored inside its disposable workspace",
    ))
    manifest_path = Path(str(bundle.get("manifest_path") or "")).expanduser().resolve()
    manifest_ok = workspace_ok and _contained(manifest_path, workspace) and manifest_path.is_file()
    criteria.append(_criterion("manifest-present", manifest_ok, str(manifest_path)))
    manifest: dict[str, Any] = {}
    if manifest_ok:
        try:
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = parsed if isinstance(parsed, dict) else {}
        except (OSError, ValueError):
            manifest = {}
    criteria.append(_criterion(
        "manifest-protocol", manifest.get("protocol") == "jarvis.capability.adapter.v1",
        "adapter protocol is not the standard V12 contract",
    ))
    embedded_descriptor = bundle.get("descriptor")
    if manifest_ok:
        criteria.append(_criterion(
            "manifest-hash",
            bundle.get("manifest_sha256") == _sha256(manifest_path),
            "frozen manifest hash changed",
        ))
        embedded = bundle.get("manifest")
        criteria.append(_criterion(
            "manifest-frozen",
            isinstance(embedded, Mapping) and dict(embedded) == manifest,
            "bundle manifest differs from installed manifest",
        ))
        criteria.append(_criterion(
            "manifest-identity",
            manifest.get("trial_id") == bundle.get("trial_id")
            and manifest.get("registration_id") == bundle.get("registration_id")
            and manifest.get("descriptor_hash") == bundle.get("descriptor_hash")
            and manifest.get("protocol") == "jarvis.capability.adapter.v1",
            "installed manifest identity is not bound to this trial",
        ))
        for field, label in (("wrapper_path", "wrapper"), ("solution_path", "solution")):
            rel = manifest.get(field)
            target = (manifest_path.parent / str(rel)).resolve() if isinstance(rel, str) else workspace / "missing"
            try:
                target.relative_to(workspace)
                contained = True
            except ValueError:
                contained = False
            present = contained and target.is_file()
            criteria.append(_criterion(f"{label}-contained", present, str(target)))
            expected_hash = manifest.get(f"{label}_sha256")
            criteria.append(_criterion(
                f"{label}-hash",
                present and expected_hash == _sha256(target),
                f"{label} hash does not match manifest",
            ))
    descriptor_path = Path(str(bundle.get("descriptor_path") or "")).expanduser().resolve()
    descriptor_ok = workspace_ok and _contained(descriptor_path, workspace) and descriptor_path.is_file()
    criteria.append(_criterion("descriptor-present", descriptor_ok, str(descriptor_path)))
    descriptor: dict[str, Any] = {}
    if descriptor_ok:
        try:
            parsed_descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            descriptor = parsed_descriptor if isinstance(parsed_descriptor, dict) else {}
        except (OSError, ValueError):
            descriptor = {}
        criteria.append(_criterion(
            "descriptor-hash",
            bundle.get("descriptor_sha256") == _sha256(descriptor_path),
            "frozen capability descriptor changed",
        ))
        criteria.append(_criterion(
            "descriptor-frozen",
            isinstance(bundle.get("descriptor"), Mapping) and dict(bundle["descriptor"]) == descriptor,
            "bundle descriptor differs from installed descriptor",
        ))
        criteria.append(_criterion(
            "descriptor-identity",
            descriptor.get("descriptor_hash") == bundle.get("descriptor_hash")
            and descriptor.get("registration_id") == bundle.get("registration_id")
            and descriptor.get("protocol") == "jarvis.capability.trial.v1"
            and descriptor.get("trial_id") == bundle.get("trial_id")
            and isinstance(descriptor.get("spec"), Mapping)
            and descriptor["spec"].get("capability_id") == manifest.get("capability_id")
            and descriptor["spec"].get("action_kind") == manifest.get("action_kind")
            and descriptor["spec"].get("version") == manifest.get("version")
            and descriptor.get("source_sha256") == manifest.get("solution_sha256")
            and descriptor.get("path_fields") == manifest.get("path_fields"),
            "descriptor identity does not bind the installed manifest",
        ))
        criteria.append(_criterion(
            "descriptor-hash-self-consistent",
            descriptor.get("descriptor_hash") == _descriptor_identity_hash(descriptor),
            "descriptor hash does not match its canonical identity",
        ))
        descriptor_metadata = descriptor.get("metadata")
        manifest_metadata = manifest.get("metadata")
        criteria.append(_criterion(
            "metadata-bound",
            isinstance(descriptor_metadata, Mapping)
            and isinstance(manifest_metadata, Mapping)
            and dict(descriptor_metadata) == dict(manifest_metadata)
            and descriptor_metadata.get("capability_id") == manifest.get("capability_id")
            and descriptor_metadata.get("version") == manifest.get("version")
            and isinstance(descriptor_metadata.get("execute"), Mapping)
            and descriptor_metadata["execute"].get("action_kind") == manifest.get("action_kind")
            and manifest_metadata.get("effect_class") == manifest.get("effect_class")
            and manifest_metadata.get("input_schema") == manifest.get("input_schema")
            and manifest_metadata.get("output_schema") == manifest.get("output_schema"),
            "adapter metadata differs from the selected capability contract",
        ))
    config_path = Path(str(bundle.get("config_path") or "")).expanduser().resolve()
    config_ok = workspace_ok and _contained(config_path, workspace) and config_path.is_file()
    criteria.append(_criterion("config-present", config_ok, str(config_path)))
    config: dict[str, Any] = {}
    if config_ok:
        try:
            parsed_config = json.loads(config_path.read_text(encoding="utf-8"))
            config = parsed_config if isinstance(parsed_config, dict) else {}
        except (OSError, ValueError):
            config = {}
        criteria.append(_criterion(
            "config-hash",
            bundle.get("config_sha256") == _sha256(config_path)
            and manifest.get("config_sha256") == _sha256(config_path),
            "installed configuration changed after staging",
        ))
        criteria.append(_criterion(
            "config-frozen",
            isinstance(bundle.get("config"), Mapping)
            and dict(bundle["config"]) == config
            and isinstance(descriptor.get("config"), Mapping)
            and dict(descriptor["config"]) == config,
            "bundle configuration differs from the selected descriptor",
        ))
    lock_path = Path(str(bundle.get("dependency_lock_path") or "")).expanduser().resolve()
    lock_ok = workspace_ok and _contained(lock_path, workspace) and lock_path.is_file()
    criteria.append(_criterion("dependency-lock", lock_ok, str(lock_path)))
    if lock_ok:
        lock: dict[str, Any] = {}
        try:
            parsed_lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock = parsed_lock if isinstance(parsed_lock, dict) else {}
        except (OSError, ValueError):
            lock = {}
        criteria.append(_criterion(
            "dependency-lock-hash",
            bundle.get("dependency_lock_sha256") == _sha256(lock_path),
            "dependency lock changed after installation",
        ))
        descriptor_dependencies = descriptor.get("dependencies") if isinstance(descriptor, Mapping) else None
        lock_dependencies = lock.get("dependencies")
        normalized_lock_dependencies = []
        if isinstance(lock_dependencies, list):
            normalized_lock_dependencies = [
                {key: item.get(key) for key in ("name", "version", "source", "sha256")}
                for item in lock_dependencies
                if isinstance(item, Mapping) and item.get("resolved") is True
            ]
        criteria.append(_criterion(
            "dependency-lock-bound",
            lock.get("protocol") == "jarvis.capability.trial.v1"
            and isinstance(embedded_descriptor, Mapping)
            and lock.get("solution_id") == embedded_descriptor.get("solution_id")
            and lock.get("source_sha256") == manifest.get("solution_sha256")
            and isinstance(descriptor_dependencies, list)
            and normalized_lock_dependencies == descriptor_dependencies
            and isinstance(lock_dependencies, list)
            and len(normalized_lock_dependencies) == len(lock_dependencies),
            "resolved dependency record differs from the selected descriptor",
        ))
        runtime = lock.get("python")
        runtime_ok = isinstance(runtime, Mapping) and all(
            isinstance(runtime.get(key), str) and bool(runtime.get(key))
            for key in ("implementation", "version", "executable")
        )
        executable = Path(str(runtime.get("executable") or "")).expanduser() if isinstance(runtime, Mapping) else Path()
        if runtime_ok:
            runtime_ok = (
                executable.is_file()
                and runtime.get("executable_sha256") == _sha256(executable)
                and isinstance(bundle.get("workspace"), str)
            )
        criteria.append(_criterion("dependency-runtime", runtime_ok, "runtime identity is not reproducible"))

    probe = bundle.get("probe")
    criteria.append(_criterion(
        "readiness-probe",
        isinstance(probe, Mapping)
        and probe.get("ready") is True
        and probe.get("protocol") == "jarvis.capability.adapter.v1",
        "adapter did not report protocol-level readiness",
    ))
    if isinstance(probe, Mapping):
        criteria.append(_criterion(
            "probe-identity",
            probe.get("adapter_manifest_sha256") == bundle.get("manifest_sha256")
            and probe.get("capability_id") == manifest.get("capability_id")
            and probe.get("action_kind") == manifest.get("action_kind"),
            "readiness evidence is not bound to the installed manifest",
        ))

    contract_cases = bundle.get("contract_cases")
    contracts_ok = isinstance(contract_cases, list) and bool(contract_cases)
    if not contracts_ok:
        criteria.append(_criterion("contract-cases-present", False, "no frozen contract cases"))
    else:
        for case in contract_cases:
            if not isinstance(case, Mapping):
                criteria.append(_criterion("contract-case-shape", False, "case is not an object"))
                continue
            case_id = str(case.get("case_id") or "unknown")
            result = case.get("result")
            expected = case.get("expected")
            result_hash = case.get("result_sha256")
            encoded = _canonical(result)
            passed = (
                isinstance(result, Mapping)
                and result.get("claimed") == "COMPLETED"
                and result.get("adapter_protocol") == "jarvis.capability.adapter.v1"
                and result.get("adapter_manifest_sha256") == bundle.get("manifest_sha256")
                and result.get("capability_id") == manifest.get("capability_id")
                and result.get("action_kind") == manifest.get("action_kind")
                and _subset(expected, result)
                and result_hash == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            )
            criteria.append(_criterion(f"contract:{case_id}", passed, "adapter result diverged from frozen expectation"))

        descriptor_cases = descriptor.get("contract_cases") if isinstance(descriptor, Mapping) else None
        descriptor_fields = descriptor.get("path_fields") if isinstance(descriptor, Mapping) else None
        projection_fields = [str(field) for field in descriptor_fields] if isinstance(descriptor_fields, list) else []
        projections = [
            _descriptor_case_projection(item, projection_fields)
            for item in contract_cases
        ]
        expected_projections = [
            _descriptor_case_projection(item, projection_fields)
            for item in (descriptor_cases if isinstance(descriptor_cases, list) else [])
        ]
        criteria.append(_criterion(
            "contract-cases-bound",
            len(projections) == len(expected_projections)
            and bool(expected_projections)
            and all(item is not None for item in projections)
            and all(item is not None for item in expected_projections)
            and projections == expected_projections,
            "bundle contract cases differ from the frozen descriptor",
        ))

    mission = bundle.get("mission")
    mission_result = mission.get("result") if isinstance(mission, Mapping) else None
    mission_expected = mission.get("expected") if isinstance(mission, Mapping) else None
    mission_hash = mission.get("result_sha256") if isinstance(mission, Mapping) else None
    mission_encoded = _canonical(mission_result)
    mission_ok = (
        isinstance(mission_result, Mapping)
        and mission_result.get("claimed") == "COMPLETED"
        and mission_result.get("adapter_protocol") == "jarvis.capability.adapter.v1"
        and mission_result.get("adapter_manifest_sha256") == bundle.get("manifest_sha256")
        and mission_result.get("capability_id") == manifest.get("capability_id")
        and mission_result.get("action_kind") == manifest.get("action_kind")
        and _subset(mission_expected, mission_result)
        and mission_hash == hashlib.sha256(mission_encoded.encode("utf-8")).hexdigest()
    )
    criteria.append(_criterion("realistic-trial-mission", mission_ok, "mission output is not independently verified"))
    descriptor_mission = descriptor.get("mission") if isinstance(descriptor, Mapping) else None
    raw_descriptor_fields = descriptor.get("path_fields") if isinstance(descriptor, Mapping) else None
    mission_fields = [str(field) for field in raw_descriptor_fields] if isinstance(raw_descriptor_fields, list) else []
    mission_projection = _descriptor_case_projection(mission, mission_fields) if isinstance(mission, Mapping) else None
    expected_mission_projection = _descriptor_case_projection(descriptor_mission, mission_fields) if isinstance(descriptor_mission, Mapping) else None
    criteria.append(_criterion(
        "mission-bound",
        mission_projection is not None and mission_projection == expected_mission_projection,
        "bundle mission differs from the frozen descriptor",
    ))

    inputs = bundle.get("input_files")
    criteria.append(_criterion(
        "input-evidence-present",
        isinstance(inputs, list),
        "staged input evidence is missing",
    ))
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, Mapping):
                criteria.append(_criterion("input-evidence-shape", False))
                continue
            target = (workspace / str(item.get("path") or "")).resolve()
            contained = _contained(target, workspace)
            criteria.append(_criterion(
                f"input:{item.get('path')}",
                contained and target.is_file() and item.get("sha256") == _sha256(target),
                "trial input changed or escaped workspace",
            ))

    passed = bool(criteria) and all(item["status"] == "PASS" for item in criteria)
    if not passed:
        findings.append("one or more independent trial criteria failed")
    return {
        "verdict": "PASS" if passed else "FAIL",
        "verifier_id": VERIFIER_ID,
        "verifier_protocol": VERIFIER_PROTOCOL,
        "trial_id": bundle.get("trial_id"),
        "registration_id": bundle.get("registration_id"),
        "criteria": criteria,
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a V12 capability trial bundle")
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    result = verify_bundle(args.bundle)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["VERIFIER_ID", "VERIFIER_PROTOCOL", "verify_bundle"]
