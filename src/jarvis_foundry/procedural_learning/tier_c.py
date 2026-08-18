"""Tier-C integration for governed procedure references.

Tier C remains experiential metadata.  The executable OpenAdapt bundle stays
outside the memory tables and is addressed only by an immutable hash-bound
reference.
"""
from __future__ import annotations

import json
from typing import Any

from ..control.redaction import contains_secret_like
from ..hybrid_memory.procedures import ProcedureRegistry
from .bundle import adopt_openadapt_bundle, verify_bundle
from .contracts import ActionAuthorityError, ProcedureBundle, canonical_json


def register_tier_c_reference(
    database: Any,
    bundle: ProcedureBundle,
    *,
    name: str,
    description: str,
    trigger_terms: list[str],
    confidence: float = 0.5,
    mission_id: str = "",
    evidence_ids: list[str] | None = None,
) -> str:
    reference = bundle.reference()
    body = canonical_json(reference)
    if contains_secret_like(body):
        raise ActionAuthorityError("secret-like content refused in Tier-C reference")
    # The explicit guard documents the non-duplication contract.  A future
    # schema extension must not quietly turn this metadata row into a second
    # executable store.
    if any(key in body for key in ('"steps"', '"workflow_code"', '"python"')):
        raise ActionAuthorityError("Tier-C reference must not duplicate executable steps")
    registry = ProcedureRegistry(database)
    return registry.add(
        name,
        description,
        body,
        list(trigger_terms),
        scope="global",
        confidence=confidence,
        mission_id=mission_id,
        created_by="procedural-learning",
        source_ids=list(bundle.source_workflow_evidence),
        evidence_ids=list(evidence_ids or ()),
    )


def tier_c_reference(database: Any, procedure_id: str) -> dict[str, Any]:
    record = ProcedureRegistry(database).get(procedure_id)
    if record is None:
        raise ActionAuthorityError(f"unknown Tier-C procedure {procedure_id}")
    try:
        reference = json.loads(str(record.get("body", "")))
    except ValueError as exc:
        raise ActionAuthorityError("Tier-C procedure body is not a JSON reference") from exc
    if not isinstance(reference, dict) or reference.get("executor", {}).get("mode") != "reference-only":
        raise ActionAuthorityError("Tier-C procedure is not a reference-only executor")
    return reference


def verify_tier_c_reference(database: Any, procedure_id: str) -> ProcedureBundle:
    """Rehydrate and verify the exact bundle named by Tier C."""
    reference = tier_c_reference(database, procedure_id)
    path = reference.get("bundle_path")
    if not isinstance(path, str) or not path:
        raise ActionAuthorityError("Tier-C reference has no bundle path")
    try:
        bundle = adopt_openadapt_bundle(
            path,
            procedure_id=str(reference.get("procedure_id", "")),
            source_workflow_evidence=list(reference.get("source_workflow_evidence", ())),
            source_candidate_id=str(reference.get("source_candidate_id", "")),
            source_mission_id=str(reference.get("source_mission_id", "")),
            input_schema=dict(reference.get("input_schema", {})),
            expected_preconditions=list(reference.get("expected_preconditions", ())),
            expected_effects=list(reference.get("expected_effects", ())),
            verification_plan=dict(reference.get("verification_plan", {})),
            required_capabilities=list(reference.get("required_capabilities", ())),
            risk_class=str(reference.get("risk_class", "C1")),
            supersedes=reference.get("supersedes"),
            lineage=list(reference.get("lineage", ())),
            created_at=reference.get("created_at"),
        )
        verify_bundle(bundle)
    except Exception as exc:  # normalize tamper/mismatch to authority refusal
        raise ActionAuthorityError(f"Tier-C bundle reference is invalid: {exc}") from exc
    if bundle.bundle_hash != reference.get("bundle_hash"):
        raise ActionAuthorityError("Tier-C bundle hash does not match the frozen record")
    if bundle.bundle_version != reference.get("bundle_version"):
        raise ActionAuthorityError("Tier-C bundle version mismatch")
    if bundle.content_hash != reference.get("content_hash"):
        raise ActionAuthorityError("Tier-C content hash mismatch")
    return bundle

