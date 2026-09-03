"""Independent verifier for a canonical Adapter Factory trial bundle."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def verify(bundle_path: Path) -> dict[str, Any]:
    findings: list[str] = []
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {
            "verdict": "FAIL",
            "verifier_id": "jarvis.v12.canonical-artifact-verifier",
            "verifier_pid": os.getpid(),
            "findings": [f"bundle unreadable: {type(error).__name__}"],
        }
    if bundle.get("protocol") != "jarvis.capability.artifact-trial.bundle.v1":
        findings.append("unsupported trial bundle protocol")
    manifest = Path(str(bundle.get("manifest_path") or "")).resolve()
    if not manifest.is_file() or _sha256(manifest) != bundle.get("manifest_sha256"):
        findings.append("adapter manifest hash mismatch")
    if bundle.get("catalog_before_sha256") != bundle.get("catalog_during_sha256"):
        findings.append("Catalog changed during pre-adoption trial")
    if int(bundle.get("builder_pid") or 0) == os.getpid():
        findings.append("verifier is not process-independent")
    for case in bundle.get("contract_cases") or ():
        if not isinstance(case, Mapping):
            findings.append("contract case is not an object")
            continue
        result = case.get("result") or {}
        if result.get("claimed") != "COMPLETED" or not _subset(
            case.get("expected") or {}, result
        ):
            findings.append(f"contract mismatch: {case.get('case_id')}")
    mission = bundle.get("mission") or {}
    try:
        from jarvis_foundry.adapter_factory import invoke_artifact, load_verified_artifact

        root, observed_manifest, descriptor = load_verified_artifact(manifest)
        if observed_manifest.get("protocol") != "jarvis.capability.adapter.v1":
            findings.append("artifact protocol mismatch")
        if descriptor.descriptor_hash != bundle.get("descriptor_hash"):
            findings.append("descriptor identity mismatch")
        independent = invoke_artifact(
            root,
            str(bundle.get("capability_id") or ""),
            mission.get("payload") or {},
            python_executable=sys.executable,
            allowed_root=Path(str(bundle.get("workspace") or "")).resolve(),
        )
        if independent.get("claimed") != "COMPLETED" or not _subset(
            mission.get("expected") or {}, independent
        ):
            findings.append("independent mission replay mismatch")
    except Exception as error:
        independent = {"claimed": "FAILED", "error": type(error).__name__}
        findings.append(f"independent invocation failed: {type(error).__name__}")
    return {
        "verdict": "PASS" if not findings else "FAIL",
        "verifier_id": "jarvis.v12.canonical-artifact-verifier",
        "verifier_pid": os.getpid(),
        "builder_pid": bundle.get("builder_pid"),
        "independent_result": independent,
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        result = {
            "verdict": "FAIL",
            "verifier_id": "jarvis.v12.canonical-artifact-verifier",
            "verifier_pid": os.getpid(),
            "findings": ["one bundle path is required"],
        }
    else:
        result = verify(Path(args[0]).resolve())
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
