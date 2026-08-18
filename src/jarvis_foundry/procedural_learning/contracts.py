"""Governed Phase-4 procedural-learning contracts.

The OpenAdapt artifact remains the executable truth.  JARVIS stores only a
small, hash-bound reference plus provenance in Tier C.  The types in this
module deliberately contain no browser-control code; they are safe to use in
shadow tests and in a verifier that has no OpenAdapt installation.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..control.redaction import contains_secret_like, redact_text

PHASE4_CONTRACT_VERSION = "1.0.0"
SUPPORTED_OPENADAPT_VERSION = "1.12.1"
SUPPORTED_OPENADAPT_FLOW_VERSION = "1.31.0"
SUPPORTED_OPENADAPT_MANIFEST_SCHEMAS = frozenset({2})


class ProcedureError(ValueError):
    """A governed procedure artifact or execution refusal."""


class BundleIntegrityError(ProcedureError):
    """A frozen bundle no longer matches its recorded bytes."""


class BundleVersionError(ProcedureError):
    """A bundle is unknown or incompatible with this adapter."""


class OpenAdaptUnavailable(ProcedureError):
    """The qualified OpenAdapt-Flow executable is unavailable."""


class ActionAuthorityError(ProcedureError):
    """An execution attempt did not pass JARVIS authority checks."""


class EffectVerificationError(ProcedureError):
    """The independent system-of-record oracle rejected the claimed effect."""


class AmbiguousEffect(EffectVerificationError):
    """The outcome cannot be safely classified; it must be frozen."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


_INJECTION = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|"
    r"jailbreak|act\s+as\s+the\s+owner|reveal\s+instructions)",
    re.IGNORECASE,
)


def safe_page_text(value: str) -> str:
    """Keep page text informational and non-authoritative.

    Secret-like assignments are refused before persistence.  Prompt-injection
    language is represented by a fixed marker rather than becoming a control
    instruction in a bundle, Tier-C record, or evidence event.
    """
    original = str(value)
    if contains_secret_like(original):
        raise ProcedureError("secret-like page text refused at bundle boundary")
    text = redact_text(original)
    if _INJECTION.search(text):
        return "[UNTRUSTED_PAGE_TEXT]"
    return text


def safe_structural_steps(steps: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Normalize a demonstration into structural, deterministic step data."""
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise ProcedureError(f"step {index} is not an object")
        step: dict[str, Any] = {}
        for key in sorted(raw):
            value = raw[key]
            if key in {"page_text", "ocr_text", "context_text", "intent"}:
                step[key] = safe_page_text(str(value))
            elif key in {"selector", "role", "name", "action", "param",
                         "field_label", "effect", "expected_effect", "target"}:
                if isinstance(value, (str, int, float, bool)) or value is None:
                    step[key] = safe_page_text(str(value)) if isinstance(value, str) else value
                else:
                    raise ProcedureError(f"step {index} field {key} is not scalar")
            elif key in {"x", "y", "width", "height"}:
                if not isinstance(value, (int, float)):
                    raise ProcedureError(f"step {index} geometry is not numeric")
                # Geometry is metadata only; irreversible actions cannot rely
                # on it without a structural/identity anchor in the adapter.
                step[key] = value
            elif key in {"secret", "irreversible"}:
                step[key] = bool(value)
            elif key in {"anchor", "metadata"}:
                if not isinstance(value, dict):
                    raise ProcedureError(f"step {index} {key} must be an object")
                step[key] = {str(k): safe_page_text(str(v)) if isinstance(v, str) else v
                             for k, v in sorted(value.items())}
            else:
                # Unknown demonstration fields are retained only as a safe
                # scalar; executable code and arbitrary callables never enter.
                if isinstance(value, (str, int, float, bool)) or value is None:
                    step[key] = safe_page_text(str(value)) if isinstance(value, str) else value
                else:
                    raise ProcedureError(f"step {index} field {key} is unsupported")
        if step.get("secret"):
            raise ProcedureError(f"step {index} is marked secret and cannot be bundled")
        if step.get("ambiguous"):
            raise ProcedureError(f"step {index} has an ambiguous target/control")
        if step.get("irreversible") and not any(
                step.get(field) for field in ("selector", "role", "name", "anchor")):
            raise ProcedureError(
                f"step {index} is irreversible without a structural identity anchor")
        normalized.append(step)
    return tuple(normalized)


def validate_parameters(
    input_schema: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, str]:
    """Validate the bounded parameter surface before an external launch."""
    if not isinstance(input_schema, dict) or not isinstance(params, dict):
        raise ProcedureError("procedure parameters must be object mappings")
    if contains_secret_like(canonical_json(params)):
        raise ProcedureError("secret-like procedure parameter refused")
    schema_keys = {str(key) for key in input_schema}
    param_keys = {str(key) for key in params}
    unknown = sorted(param_keys - schema_keys)
    if unknown:
        raise ProcedureError(f"unknown procedure parameters: {unknown}")
    normalized: dict[str, str] = {}
    for key in sorted(schema_keys):
        spec = input_schema.get(key)
        if not isinstance(spec, dict):
            raise ProcedureError(f"parameter schema for {key!r} is invalid")
        if bool(spec.get("required", False)) and key not in params:
            raise ProcedureError(f"required procedure parameter missing: {key}")
        if key not in params:
            continue
        value = params[key]
        if not isinstance(value, str):
            raise ProcedureError(f"procedure parameter {key!r} must be a string")
        if spec.get("type", "string") not in ("string", None):
            raise ProcedureError(f"procedure parameter {key!r} has unsupported type")
        max_length = spec.get("maxLength")
        if max_length is not None and len(value) > int(max_length):
            raise ProcedureError(f"procedure parameter {key!r} exceeds maxLength")
        normalized[key] = value
    return normalized


@dataclass(frozen=True, slots=True)
class ProcedureBundle:
    """A frozen OpenAdapt bundle plus its JARVIS governance identity."""

    procedure_id: str
    artifact_dir: Path
    artifact_manifest: dict[str, Any]
    bundle_hash: str
    source_workflow_evidence: tuple[str, ...] = ()
    source_candidate_id: str = ""
    source_mission_id: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    expected_preconditions: tuple[str, ...] = ()
    expected_effects: tuple[str, ...] = ()
    verification_plan: dict[str, Any] = field(default_factory=dict)
    required_capabilities: tuple[str, ...] = ("openadapt.browser",)
    risk_class: str = "C1"
    supersedes: str | None = None
    lineage: tuple[str, ...] = ()
    created_at: str = ""

    @property
    def bundle_version(self) -> str:
        return str(self.artifact_manifest.get("jarvis_bundle_version", PHASE4_CONTRACT_VERSION))

    @property
    def openadapt_version(self) -> str:
        return str(self.artifact_manifest.get("openadapt_version", ""))

    @property
    def openadapt_flow_version(self) -> str:
        return str(self.artifact_manifest.get("openadapt_flow_version", ""))

    @property
    def content_hash(self) -> str:
        return str(self.artifact_manifest.get("content_digest", ""))

    def reference(self) -> dict[str, Any]:
        """Tier-C-safe executor reference; never embeds executable steps."""
        return {
            "reference_version": PHASE4_CONTRACT_VERSION,
            "procedure_id": self.procedure_id,
            "bundle_path": str(self.artifact_dir),
            "bundle_hash": self.bundle_hash,
            "content_hash": self.content_hash,
            "manifest_hash": str(self.artifact_manifest.get("manifest_hash", "")),
            "artifact_file_hashes": dict(
                self.artifact_manifest.get("jarvis_file_hashes", {})),
            "bundle_version": self.bundle_version,
            "openadapt_version": self.openadapt_version,
            "openadapt_flow_version": self.openadapt_flow_version,
            "required_capabilities": list(self.required_capabilities),
            "risk_class": self.risk_class,
            "source_workflow_evidence": list(self.source_workflow_evidence),
            "source_candidate_id": self.source_candidate_id,
            "source_mission_id": self.source_mission_id,
            "input_schema": self.input_schema,
            "expected_preconditions": list(self.expected_preconditions),
            "expected_effects": list(self.expected_effects),
            "verification_plan": self.verification_plan,
            "supersedes": self.supersedes,
            "lineage": list(self.lineage),
            "created_at": self.created_at,
            "executor": {
                "kind": "openadapt-flow",
                "mode": "reference-only",
                "authority": "jarvis.action_envelope",
            },
        }


@dataclass(frozen=True, slots=True)
class ReplayOutcome:
    status: str
    run_id: str
    action_id: str | None
    bundle_hash: str
    model_calls: int | None
    report_path: str | None
    effect_state: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    recovery_classification: str | None = None
