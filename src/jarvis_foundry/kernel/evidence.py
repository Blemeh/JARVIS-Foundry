"""Evidence bundles and independent verifier verdicts."""

from __future__ import annotations

from collections.abc import Mapping

from jarvis_foundry.kernel.canonical import sha256_bytes
from jarvis_foundry.kernel.errors import EvidenceError
from jarvis_foundry.kernel.records import EvidenceRecord, VerifierVerdict


def bind_artifacts(
    *,
    artifacts: Mapping[str, bytes],
    producer: str,
    evidence_id: str,
    recorded_at: str,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        producer=producer,
        artifact_hashes={name: sha256_bytes(content) for name, content in artifacts.items()},
        recorded_at=recorded_at,
    )


def verify_artifacts(record: EvidenceRecord, artifacts: Mapping[str, bytes]) -> bool:
    if set(record.artifact_hashes) != set(artifacts):
        return False
    return all(
        sha256_bytes(artifacts[name]) == record.artifact_hashes[name]
        for name in record.artifact_hashes
    )


def issue_verdict(
    *,
    evidence: EvidenceRecord,
    verifier_id: str,
    status: str,
    reason: str,
    verdict_id: str,
    verified_at: str,
) -> VerifierVerdict:
    if verifier_id == evidence.producer:
        raise EvidenceError("self-certification is forbidden: verifier equals producer")
    return VerifierVerdict(
        verdict_id=verdict_id,
        evidence_id=evidence.evidence_id,
        verifier_id=verifier_id,
        status=status,
        reason=reason,
        verified_at=verified_at,
        artifact_hashes=dict(evidence.artifact_hashes),
    )


def verify_verdict(verdict: VerifierVerdict, evidence: EvidenceRecord) -> bool:
    if verdict.verifier_id == evidence.producer:
        return False
    if verdict.evidence_id != evidence.evidence_id:
        return False
    if verdict.artifact_hashes != dict(evidence.artifact_hashes):
        return False
    return verdict.status in {"PASS", "FAIL"}
