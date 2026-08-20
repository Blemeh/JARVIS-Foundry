"""V6-S5: independent verifier boundary.

Verification runs OUTSIDE candidate control against frozen source,
artifacts, metrics and manifests. A structured verdict
(PASS/FAIL/BLOCKED/NOT_TESTED) is bound to exact hashes; exit code
zero alone is NOT_TESTED (D-032). A verdict without a recorded
verification request is refused (forgery guard). One verdict per
verification. Verifier identity (provider/model/session) is recorded;
the D-031 worker constraint is enforced against the resolved current
provider policy (no silent substitution); same-provider limitations
are flagged honestly; and a candidate can never invoke a trusted
promotion verdict for itself (builder-session verdicts are refused).
Persisted historical identities are accepted only when durable state
proves the exact provider/model pair belongs to the run being replayed.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from ..control.redaction import contains_secret_like
from ..database import FoundryDatabase
from ..events import Event, canonical_json, payload_hash
from ..supervisor.authority import SUPERVISOR_CONTROLLER

from ..runtime.provider_config import (
    ProviderSettings,
    matches_persisted_identity,
)

VERDICTS = ("PASS", "FAIL", "BLOCKED", "NOT_TESTED")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class VerificationError(ValueError):
    """A verification request or verdict is invalid or refused."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def _durable_run_identity(
    database: FoundryDatabase, run_id: str,
) -> dict[str, Any] | None:
    """Provider/model from the canonical agent_runs projection only."""
    row = database.row("agent_runs", "run_id", run_id)
    if row is None:
        return None
    provider = row.get("provider")
    model = row.get("model")
    if not isinstance(provider, str) or not provider.strip():
        return None
    if not isinstance(model, str) or not model.strip():
        return None
    return {"provider": provider.strip(), "model": model.strip()}


def _identity_ok(
    settings: ProviderSettings,
    identity: dict[str, Any],
    *,
    database: FoundryDatabase | None = None,
    run_id: str | None = None,
) -> bool:
    """Current policy or durable agent_runs identity for the cited run only."""
    if settings.is_currently_authorized_identity(identity):
        return True
    if database is not None and run_id is not None:
        persisted = _durable_run_identity(database, run_id)
        if persisted is not None:
            return matches_persisted_identity(persisted, identity)
    return False


def record_verification_request(
    database: FoundryDatabase, *, improvement_id: str, subject_ref: str,
    criteria: list[str], artifacts: dict[str, str],
    builder_identity: dict[str, Any], controller: str,
    provider_settings: ProviderSettings,
    builder_run_id: str | None = None,
    source_type: str = "supervisor", source_id: str = "v6",
) -> str:
    """Record a verification REQUEST (created by the controller — the
    candidate can never request its own trusted verification)."""
    _require(database.row("improvement_records", "improvement_id",
                          improvement_id) is not None,
             f"improvement {improvement_id} does not exist — refused")
    _require(isinstance(subject_ref, str) and subject_ref.strip(),
             "subject_ref must be a non-empty string")
    _require(isinstance(criteria, list) and criteria and all(
        isinstance(c, str) and c.strip() for c in criteria),
        "criteria must be a non-empty list of strings")
    _require(isinstance(artifacts, dict) and artifacts,
             "artifacts must be a non-empty mapping of name -> sha256")
    for name, digest in artifacts.items():
        _require(isinstance(digest, str) and _HEX64.match(digest),
                 f"artifact hash for {name!r} must be a 64-char "
                 "lowercase hex sha256 — refused")
    _require(
        isinstance(controller, str) and controller == SUPERVISOR_CONTROLLER,
        "only the exact Supervisor controller identity 'supervisor' may "
        "create verification requests - refused",
    )
    _require(
        _identity_ok(
            provider_settings,
            builder_identity,
            database=database,
            run_id=builder_run_id,
        ),
        "builder identity must match current provider policy or the "
        "persisted execution identity for this run — refused",
    )
    if contains_secret_like(canonical_json({"criteria": criteria})):
        raise VerificationError(
            "verification criteria contain secret-like content — "
            "refused at the boundary (V5-S7 guard family)")

    verification_id = "verification-" + uuid.uuid4().hex[:12]
    payload = {
        "verification_id": verification_id,
        "improvement_id": improvement_id,
        "subject_ref": subject_ref,
        "criteria": list(criteria),
        "artifacts": dict(artifacts),
        "builder_identity": dict(builder_identity),
        "controller": controller,
    }
    database.append(Event.create(
        "verification.requested", payload,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"verification.requested:{verification_id}"))
    return verification_id


def _requested(database: FoundryDatabase,
               verification_id: str) -> dict[str, Any] | None:
    row = database.row("verifications", "verification_id",
                       verification_id)
    return row


def record_verification_verdict(
    database: FoundryDatabase, *, verification_id: str, verdict: str,
    criteria_results: list[dict[str, Any]], findings: list[dict[str, Any]],
    verifier_identity: dict[str, Any], evidence_refs: list[str],
    provider_settings: ProviderSettings,
    verifier_run_id: str | None = None,
    artifacts: dict[str, str] | None = None,
    source_type: str = "supervisor", source_id: str = "v6",
) -> None:
    """Record the ONE structured verdict for a verification.

    Refusals: no request exists (forgery); verdict not in
    PASS/FAIL/BLOCKED/NOT_TESTED; empty criteria results (exit-code-
    zero-alone is NOT_TESTED, D-032); a second verdict for the same
    verification; artifact hashes differing from the request; a
    verifier identity equal to the builder's session (self-
    certification); a verifier identity violating D-031.
    """
    request = _requested(database, verification_id)
    _require(request is not None,
             f"verification {verification_id} has no recorded request — "
             "refused (forgery guard)")
    _require(request["status"] == "REQUESTED",
             f"verification {verification_id} already has a verdict — "
             "one verdict per verification, refused")
    _require(verdict in VERDICTS,
             f"verdict must be one of {VERDICTS} (D-032: exit code 0 "
             "alone is NOT_TESTED)")
    _require(isinstance(criteria_results, list) and criteria_results,
             "structured verdict requires per-criterion results — "
             "exit-code-zero alone is NOT_TESTED (D-032)")
    import json as _json
    request_criteria = _json.loads(request["criteria_json"])
    for result in criteria_results:
        _require(isinstance(result, dict)
                 and result.get("criterion")
                 and result.get("status") in VERDICTS,
                 "each criterion result needs a criterion and a "
                 "status in PASS/FAIL/BLOCKED/NOT_TESTED")
        _require(result["criterion"] in request_criteria,
                 f"criterion {result['criterion']!r} is not part of the "
                 "verification request's frozen criteria — refused "
                 "(frozen-criteria contract)")
    _require(isinstance(findings, list), "findings must be a list")
    _require(
        _identity_ok(
            provider_settings,
            verifier_identity,
            database=database,
            run_id=verifier_run_id,
        ),
        "verifier identity must match current provider policy or the "
        "persisted execution identity for this run — refused "
        "(no silent substitution)",
    )
    _require(isinstance(evidence_refs, list), "evidence_refs must be "
             "a list")
    if artifacts is not None:
        import json as _json
        requested_artifacts = _json.loads(request["artifacts_json"])
        _require(artifacts == requested_artifacts,
                 "verdict artifact hashes differ from the verification "
                 "request — the verdict must bind the frozen artifacts, "
                 "refused")

    import json as _json
    builder_identity = _json.loads(request["builder_identity_json"])
    _require(verifier_identity.get("session_id") !=
             builder_identity.get("session_id"),
             "verifier session equals the builder session — a "
             "candidate can never invoke a trusted verdict for itself, "
             "refused")

    verdict_hash = payload_hash({
        "verification_id": verification_id, "verdict": verdict,
        "criteria_results": criteria_results,
        "findings": findings,
        "verifier_identity": verifier_identity,
        "evidence_refs": evidence_refs})
    same_provider = (
        verifier_identity.get("provider") ==
        builder_identity.get("provider"))
    database.append(Event.create(
        "verification.verdict_recorded",
        {"verification_id": verification_id, "verdict": verdict,
         "criteria_results": criteria_results,
         "findings": findings,
         "verifier_identity": dict(verifier_identity),
         "evidence_refs": evidence_refs,
         "verdict_hash": verdict_hash,
         "same_provider_limitation": bool(same_provider)},
        source_type=source_type, source_id=source_id,
        idempotency_key=f"verification.verdict_recorded:{verification_id}"))


def verification_status(database: FoundryDatabase,
                        verification_id: str) -> str:
    row = _requested(database, verification_id)
    if row is None:
        raise VerificationError(
            f"verification {verification_id} does not exist")
    return str(row["status"])


def is_trusted_verdict(database: FoundryDatabase, verification_id: str,
                       builder_identity: dict[str, Any]) -> bool:
    """The promotion gate: TRUE only for a VERDICTED PASS whose
    verifier session differs from the builder's session. A malformed
    builder identity is never trusted (fails closed)."""
    if not isinstance(builder_identity, dict) or \
            not isinstance(builder_identity.get("session_id"), str) or \
            not builder_identity["session_id"].strip():
        return False
    row = _requested(database, verification_id)
    if row is None or row["status"] != "VERDICTED":
        return False
    if row["verdict"] != "PASS":
        return False
    import json as _json
    verifier = _json.loads(row["verifier_identity_json"])
    if verifier.get("session_id") == builder_identity.get("session_id"):
        return False
    return True
