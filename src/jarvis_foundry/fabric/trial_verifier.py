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
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
    manifest_path = Path(str(bundle.get("manifest_path") or "")).expanduser().resolve()
    manifest_ok = workspace_ok and manifest_path.is_file()
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
    lock_path = Path(str(bundle.get("dependency_lock_path") or "")).expanduser().resolve()
    lock_ok = workspace_ok and lock_path.is_file()
    criteria.append(_criterion("dependency-lock", lock_ok, str(lock_path)))
    if lock_ok:
        criteria.append(_criterion(
            "dependency-lock-hash",
            bundle.get("dependency_lock_sha256") == _sha256(lock_path),
            "dependency lock changed after installation",
        ))

    probe = bundle.get("probe")
    criteria.append(_criterion(
        "readiness-probe",
        isinstance(probe, Mapping)
        and probe.get("ready") is True
        and probe.get("protocol") == "jarvis.capability.adapter.v1",
        "adapter did not report protocol-level readiness",
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
                and _subset(expected, result)
                and result_hash == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            )
            criteria.append(_criterion(f"contract:{case_id}", passed, "adapter result diverged from frozen expectation"))

    mission = bundle.get("mission")
    mission_result = mission.get("result") if isinstance(mission, Mapping) else None
    mission_expected = mission.get("expected") if isinstance(mission, Mapping) else None
    mission_hash = mission.get("result_sha256") if isinstance(mission, Mapping) else None
    mission_encoded = _canonical(mission_result)
    mission_ok = (
        isinstance(mission_result, Mapping)
        and mission_result.get("claimed") == "COMPLETED"
        and mission_result.get("adapter_protocol") == "jarvis.capability.adapter.v1"
        and _subset(mission_expected, mission_result)
        and mission_hash == hashlib.sha256(mission_encoded.encode("utf-8")).hexdigest()
    )
    criteria.append(_criterion("realistic-trial-mission", mission_ok, "mission output is not independently verified"))

    inputs = bundle.get("input_files")
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, Mapping):
                criteria.append(_criterion("input-evidence-shape", False))
                continue
            target = (workspace / str(item.get("path") or "")).resolve()
            try:
                target.relative_to(workspace)
                contained = True
            except ValueError:
                contained = False
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
