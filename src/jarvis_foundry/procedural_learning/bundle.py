"""Freeze, adopt, and verify OpenAdapt-Flow bundles.

The Phase-1 lab already produced a qualified OpenAdapt bundle.  Phase 4 does
not copy its executable contents into Tier C: it adopts the frozen artifact by
hash and records a governed reference.  A separate deterministic compiler
entrypoint is provided for new demonstrations.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    BundleIntegrityError,
    BundleVersionError,
    PHASE4_CONTRACT_VERSION,
    ProcedureBundle,
    SUPPORTED_OPENADAPT_FLOW_VERSION,
    SUPPORTED_OPENADAPT_MANIFEST_SCHEMAS,
    SUPPORTED_OPENADAPT_VERSION,
    canonical_json,
    file_hash,
    payload_hash,
    safe_structural_steps,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleIntegrityError(f"cannot read JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BundleIntegrityError(f"artifact {path} is not a JSON object")
    return value


def _manifest_hash(manifest: dict[str, Any]) -> str:
    return payload_hash({k: v for k, v in manifest.items() if k != "manifest_hash"})


def _verify_artifact_files(artifact_dir: Path, manifest: dict[str, Any]) -> None:
    schema = manifest.get("schema_version")
    if schema not in SUPPORTED_OPENADAPT_MANIFEST_SCHEMAS:
        raise BundleVersionError(f"unsupported OpenAdapt manifest schema {schema!r}")
    hashes = manifest.get("file_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise BundleIntegrityError("bundle manifest has no file_hashes")
    for relative, expected in sorted(hashes.items()):
        rel = Path(str(relative))
        if rel.is_absolute() or ".." in rel.parts:
            raise BundleIntegrityError(f"unsafe bundle file path {relative!r}")
        path = artifact_dir / rel
        if not path.is_file():
            raise BundleIntegrityError(f"bundle file is missing: {relative}")
        actual = file_hash(path)
        if actual != str(expected):
            raise BundleIntegrityError(
                f"bundle file hash mismatch for {relative}: expected {expected}, got {actual}")
    workflow = artifact_dir / "workflow.json"
    if not workflow.is_file():
        raise BundleIntegrityError("OpenAdapt bundle has no workflow.json")


def verify_bundle(bundle: ProcedureBundle) -> ProcedureBundle:
    """Verify a frozen artifact and its JARVIS identity before execution."""
    artifact_dir = bundle.artifact_dir.expanduser().resolve()
    if artifact_dir != bundle.artifact_dir:
        # References are canonicalized at load time.  Keep this explicit so a
        # path substitution cannot quietly change the artifact identity.
        raise BundleIntegrityError("bundle artifact path is not canonical")
    manifest_path = artifact_dir / "manifest.json"
    manifest = _load_json(manifest_path)
    _verify_artifact_files(artifact_dir, manifest)
    actual_manifest_hash = _manifest_hash(manifest)
    recorded_manifest_hash = str(manifest.get("manifest_hash", ""))
    # OpenAdapt's own manifests do not carry this field; Phase 4's wrapper
    # stores it in a sidecar manifest.  When absent, the JARVIS bundle hash is
    # still checked below and OpenAdapt-Flow performs its native digest check.
    if recorded_manifest_hash and recorded_manifest_hash != actual_manifest_hash:
        raise BundleIntegrityError("JARVIS manifest hash mismatch")
    if str(manifest.get("openadapt_version", SUPPORTED_OPENADAPT_VERSION)) != SUPPORTED_OPENADAPT_VERSION:
        raise BundleVersionError("OpenAdapt version is not the qualified Phase-1 version")
    if str(manifest.get("openadapt_flow_version", SUPPORTED_OPENADAPT_FLOW_VERSION)) != SUPPORTED_OPENADAPT_FLOW_VERSION:
        raise BundleVersionError("OpenAdapt-Flow version is not the qualified Phase-1 version")
    expected = payload_hash({
        "procedure_id": bundle.procedure_id,
        "manifest_hash": actual_manifest_hash,
        "content_hash": str(manifest.get("content_digest", "")),
        "source_workflow_evidence": list(bundle.source_workflow_evidence),
        "source_candidate_id": bundle.source_candidate_id,
        "source_mission_id": bundle.source_mission_id,
        "input_schema": bundle.input_schema,
        "expected_preconditions": list(bundle.expected_preconditions),
        "expected_effects": list(bundle.expected_effects),
        "verification_plan": bundle.verification_plan,
        "required_capabilities": list(bundle.required_capabilities),
        "risk_class": bundle.risk_class,
        "supersedes": bundle.supersedes,
        "lineage": list(bundle.lineage),
    })
    if expected != bundle.bundle_hash:
        raise BundleIntegrityError("governed bundle hash mismatch")
    if bundle.bundle_version != PHASE4_CONTRACT_VERSION:
        raise BundleVersionError(f"unknown JARVIS bundle version {bundle.bundle_version!r}")
    return bundle


def adopt_openadapt_bundle(
    artifact_dir: str | Path,
    *,
    procedure_id: str,
    source_workflow_evidence: list[str] | tuple[str, ...],
    source_candidate_id: str = "",
    source_mission_id: str = "",
    input_schema: dict[str, Any] | None = None,
    expected_preconditions: list[str] | tuple[str, ...] = (),
    expected_effects: list[str] | tuple[str, ...] = (),
    verification_plan: dict[str, Any] | None = None,
    required_capabilities: list[str] | tuple[str, ...] = ("openadapt.browser",),
    risk_class: str = "C1",
    supersedes: str | None = None,
    lineage: list[str] | tuple[str, ...] = (),
    created_at: str | None = None,
) -> ProcedureBundle:
    """Adopt an already compiled OpenAdapt bundle without copying it."""
    directory = Path(artifact_dir).expanduser().resolve()
    if not procedure_id.strip():
        raise ValueError("procedure_id is required")
    manifest = _load_json(directory / "manifest.json")
    _verify_artifact_files(directory, manifest)
    openadapt_version = str(manifest.get("openadapt_version", SUPPORTED_OPENADAPT_VERSION))
    flow_version = str(manifest.get("openadapt_flow_version", manifest.get("provenance", {}).get("compiler_version", "")))
    if openadapt_version != SUPPORTED_OPENADAPT_VERSION:
        raise BundleVersionError(f"OpenAdapt {openadapt_version!r} is not qualified")
    if flow_version != SUPPORTED_OPENADAPT_FLOW_VERSION:
        raise BundleVersionError(f"OpenAdapt-Flow {flow_version!r} is not qualified")
    wrapped = dict(manifest)
    wrapped.update({
        "jarvis_bundle_version": PHASE4_CONTRACT_VERSION,
        "openadapt_version": openadapt_version,
        "openadapt_flow_version": flow_version,
    })
    # The external OpenAdapt manifest is immutable.  The JARVIS wrapper binds
    # the hash of those exact original bytes; it never rewrites the artifact.
    original_manifest_hash = _manifest_hash(manifest)
    wrapped["manifest_hash"] = original_manifest_hash
    bundle_identity = {
        "procedure_id": procedure_id,
        "manifest_hash": original_manifest_hash,
        "content_hash": str(wrapped.get("content_digest", "")),
        "source_workflow_evidence": list(source_workflow_evidence),
        "source_candidate_id": source_candidate_id,
        "source_mission_id": source_mission_id,
        "input_schema": dict(input_schema or {}),
        "expected_preconditions": list(expected_preconditions),
        "expected_effects": list(expected_effects),
        "verification_plan": dict(verification_plan or {}),
        "required_capabilities": list(required_capabilities),
        "risk_class": risk_class,
        "supersedes": supersedes,
        "lineage": list(lineage),
    }
    bundle = ProcedureBundle(
        procedure_id=procedure_id,
        artifact_dir=directory,
        artifact_manifest=wrapped,
        bundle_hash=payload_hash(bundle_identity),
        source_workflow_evidence=tuple(source_workflow_evidence),
        source_candidate_id=source_candidate_id,
        source_mission_id=source_mission_id,
        input_schema=dict(input_schema or {}),
        expected_preconditions=tuple(expected_preconditions),
        expected_effects=tuple(expected_effects),
        verification_plan=dict(verification_plan or {}),
        required_capabilities=tuple(required_capabilities),
        risk_class=risk_class,
        supersedes=supersedes,
        lineage=tuple(lineage),
        created_at=created_at or _now(),
    )
    return verify_bundle(bundle)


def build_deterministic_bundle(
    output_dir: str | Path,
    *,
    procedure_id: str,
    steps: list[dict[str, Any]],
    source_workflow_evidence: list[str] | tuple[str, ...],
    input_schema: dict[str, Any] | None = None,
    expected_preconditions: list[str] | tuple[str, ...] = (),
    expected_effects: list[str] | tuple[str, ...] = (),
    verification_plan: dict[str, Any] | None = None,
    source_candidate_id: str = "",
    source_mission_id: str = "",
    risk_class: str = "C1",
    supersedes: str | None = None,
    lineage: list[str] | tuple[str, ...] = (),
) -> ProcedureBundle:
    """Create a deterministic JARVIS wrapper from structural demonstration data.

    A real OpenAdapt-Flow compilation should normally be performed by
    :func:`jarvis_foundry.procedural_learning.adapter.compile_recording`; this
    helper is used for contract tests and offline shadow fixtures.
    """
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    normalized = safe_structural_steps(steps)
    workflow = {
        "schema_version": 1,
        "name": procedure_id,
        "steps": list(normalized),
        "params": dict(input_schema or {}),
        "expected_effects": list(expected_effects),
    }
    workflow_path = directory / "workflow.json"
    workflow_path.write_text(canonical_json(workflow) + "\n", encoding="utf-8")
    digest = file_hash(workflow_path)
    manifest = {
        "schema_version": 2,
        "content_digest": digest,
        "file_hashes": {"workflow.json": digest},
        "provenance": {"compiler_version": SUPPORTED_OPENADAPT_FLOW_VERSION},
        "openadapt_version": SUPPORTED_OPENADAPT_VERSION,
        "openadapt_flow_version": SUPPORTED_OPENADAPT_FLOW_VERSION,
        "jarvis_bundle_version": PHASE4_CONTRACT_VERSION,
    }
    manifest["manifest_hash"] = _manifest_hash(manifest)
    (directory / "manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    # The generated manifest is intentionally not re-written after adoption;
    # its hash is therefore stable and tamper-evident.
    return adopt_openadapt_bundle(
        directory,
        procedure_id=procedure_id,
        source_workflow_evidence=source_workflow_evidence,
        source_candidate_id=source_candidate_id,
        source_mission_id=source_mission_id,
        input_schema=input_schema,
        expected_preconditions=expected_preconditions,
        expected_effects=expected_effects,
        verification_plan=verification_plan,
        risk_class=risk_class,
        supersedes=supersedes,
        lineage=lineage,
    )


def write_reference_sidecar(bundle: ProcedureBundle, destination: str | Path) -> Path:
    """Write a small reviewable manifest without duplicating executables."""
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_json(bundle.reference()) + "\n", encoding="utf-8")
    return target
