from __future__ import annotations

import pytest

from jarvis_foundry.kernel.errors import EvidenceError
from jarvis_foundry.kernel.evidence import (
    bind_artifacts,
    issue_verdict,
    verify_artifacts,
    verify_verdict,
)


def test_artifact_hashes_bind_content_and_detect_tampering() -> None:
    record = bind_artifacts(
        artifacts={"a.txt": b"alpha", "b.bin": b"\x00\x01"},
        producer="builder",
        evidence_id="e-1",
        recorded_at="2026-08-15T00:00:00Z",
    )
    assert len(record.artifact_hashes["a.txt"]) == 64
    assert verify_artifacts(record, {"a.txt": b"alpha", "b.bin": b"\x00\x01"}) is True
    assert verify_artifacts(record, {"a.txt": b"beta", "b.bin": b"\x00\x01"}) is False


def test_verifier_identity_is_separate_and_no_self_certification() -> None:
    record = bind_artifacts(
        artifacts={"a.txt": b"alpha"},
        producer="builder",
        evidence_id="e-1",
        recorded_at="2026-08-15T00:00:00Z",
    )
    with pytest.raises(EvidenceError):
        issue_verdict(
            evidence=record,
            verifier_id="builder",
            status="PASS",
            reason="self",
            verdict_id="v-1",
            verified_at="2026-08-15T00:00:00Z",
        )
    verdict = issue_verdict(
        evidence=record,
        verifier_id="independent-verifier",
        status="PASS",
        reason="fresh context",
        verdict_id="v-1",
        verified_at="2026-08-15T00:00:00Z",
    )
    assert verdict.verifier_id == "independent-verifier"
    assert verify_verdict(verdict, record) is True


def test_verdict_against_tampered_evidence_fails() -> None:
    record = bind_artifacts(
        artifacts={"a.txt": b"alpha"},
        producer="builder",
        evidence_id="e-1",
        recorded_at="2026-08-15T00:00:00Z",
    )
    verdict = issue_verdict(
        evidence=record,
        verifier_id="independent-verifier",
        status="PASS",
        reason="ok",
        verdict_id="v-1",
        verified_at="2026-08-15T00:00:00Z",
    )
    tampered = bind_artifacts(
        artifacts={"a.txt": b"tampered"},
        producer="builder",
        evidence_id="e-1",
        recorded_at="2026-08-15T00:00:00Z",
    )
    assert verify_verdict(verdict, tampered) is False
