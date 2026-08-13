"""V6-S5: independent verifier boundary.

Verification runs outside candidate control against frozen source,
artifacts, metrics and manifests. A structured verdict (PASS/FAIL/
BLOCKED/NOT_TESTED) is bound to exact hashes; exit code zero alone is
NOT_TESTED (D-032). A verdict without a recorded request is refused
(forgery guard). One verdict per verification. Verifier identity is
recorded (provider/model/session); the D-031 worker constraint is
enforced (opencode_go + deepseek-v4-flash, no silent substitution),
same-provider limitations are flagged honestly, and a candidate can
never invoke a trusted promotion verdict for itself.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.verify import (
    VerificationError,
    is_trusted_verdict,
    record_verification_request,
    record_verification_verdict,
    verification_status,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile

DIRECTIVE_IDENTITY = {"provider": "opencode_go",
                      "model": "deepseek-v4-flash",
                      "session_id": "session-builder-1"}
OTHER_IDENTITY = {"provider": "opencode_go",
                  "model": "deepseek-v4-flash",
                  "session_id": "session-verifier-1"}
FOREIGN_IDENTITY = {"provider": "vertex_ai",
                    "model": "gemini-pro",
                    "session_id": "session-foreign-1"}


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase) -> str:
    return record_improvement(
        db,
        problem="suite runtime exceeds the 5-minute budget on this host",
        evidence_refs=["var/evidence/v6-s0/suite-v6-s0-baseline.txt"],
        provenance={"source": "v6-s0 baseline measurement",
                    "observed_at": "2026-08-13T15:00:00Z"},
        hypothesis="Splitting fault-injection tests reduces wall-clock "
                   "runtime",
        expected_mechanism="parallel session isolation removes serialized "
                           "daemon-spawn waits",
        baseline_metric={"name": "suite_runtime_s", "value": 313.92,
                         "unit": "seconds",
                         "source": "suite-v6-s0-baseline.txt"},
        mmi={"minimum_delta": 30.0, "direction": "decrease",
             "unit": "seconds"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "tests/foundry",
                  "hash": "139f26833c9690b521460664f6ab9de0f0cbdbb61b9d547850004bf4c44dafef"},
        observation={"requirement": "3 full-suite runs", "cycles": 3},
        declared_risk="R2",
        surfaces=["tests/foundry", "pyproject.toml"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )


def _request(db: FoundryDatabase, improvement_id: str,
             **overrides: object) -> str:
    params: dict[str, object] = {
        "subject_ref": "release-candidate-0001",
        "criteria": ["criterion-1: suite green", "criterion-2: latency"],
        "artifacts": {"suite.txt": "a" * 64},
        "builder_identity": DIRECTIVE_IDENTITY,
        "controller": "v6-s5-test",
    }
    params.update(overrides)
    return record_verification_request(
        db, improvement_id=improvement_id, **params)  # type: ignore[arg-type]


def _verdict(verification_id: str, **overrides: object) -> dict[str, object]:
    verdict: dict[str, object] = {
        "verification_id": verification_id,
        "verdict": "PASS",
        "criteria_results": [
            {"criterion": "criterion-1: suite green", "status": "PASS",
             "evidence_hash": "b" * 64},
            {"criterion": "criterion-2: latency", "status": "PASS",
             "evidence_hash": "c" * 64},
        ],
        "findings": [],
        "verifier_identity": OTHER_IDENTITY,
        "evidence_refs": ["var/evidence/v6-s5/probe-1.txt"],
    }
    verdict.update(overrides)
    return verdict


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------

def test_request_recorded_and_fresh_per_attempt(db: FoundryDatabase) -> None:
    """Each verification is a FRESH attempt (new id); event-level
    re-delivery of the same request dedupes."""
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    assert verification_id.startswith("verification-")
    second = _request(db, improvement_id)
    assert second != verification_id
    events = [e for e in db.events()
              if e.event_type == "verification.requested"]
    assert len(events) == 2
    assert verification_status(db, verification_id) == "REQUESTED"
    assert verification_status(db, second) == "REQUESTED"
    # identical re-delivery of the SAME event dedupes (created=False)
    from jarvis_foundry.events import Event
    payload = dict(events[0].payload)
    _sequence, created = db.append(Event.create(
        "verification.requested", payload,
        source_type=events[0].source_type, source_id=events[0].source_id,
        idempotency_key=events[0].idempotency_key))
    assert created is False
    assert db.count("verifications") == 2


def test_request_unknown_improvement_refused(db: FoundryDatabase) -> None:
    with pytest.raises(VerificationError):
        _request(db, "improvement-nope")


def test_request_secret_criteria_refused(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    with pytest.raises(VerificationError):
        _request(db, improvement_id,
                 criteria=["leak api_key=sk-live-1234 in the log"])
    assert len(db.events()) == 1  # only the improvement record


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------

def test_verdict_recorded(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    record_verification_verdict(db, **_verdict(verification_id))  # type: ignore[arg-type]
    assert verification_status(db, verification_id) == "VERDICTED"
    row = db.row("verifications", "verification_id", verification_id)
    assert row is not None
    assert row["verdict"] == "PASS"
    # D-031: every JARVIS worker resolves to opencode_go, so the
    # same-provider limitation is honestly recorded (1), never hidden
    assert row["same_provider_limitation"] == 1
    events = [e for e in db.events()
              if e.event_type == "verification.verdict_recorded"]
    assert len(events) == 1


def test_verdict_without_request_refused(db: FoundryDatabase) -> None:
    with pytest.raises(VerificationError):
        record_verification_verdict(  # type: ignore[arg-type]
            db, **_verdict("verification-forged-0001"))
    assert len(db.events()) == 0


def test_invalid_verdict_value_refused(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    for bad in ("PASSED", "yes", "green", ""):
        with pytest.raises(VerificationError):
            record_verification_verdict(  # type: ignore[arg-type]
                db, **_verdict(verification_id, verdict=bad))
    assert verification_status(db, verification_id) == "REQUESTED"


def test_second_verdict_refused(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    record_verification_verdict(db, **_verdict(verification_id))  # type: ignore[arg-type]
    with pytest.raises(VerificationError):
        record_verification_verdict(  # type: ignore[arg-type]
            db, **_verdict(verification_id))
    events = [e for e in db.events()
              if e.event_type == "verification.verdict_recorded"]
    assert len(events) == 1


def test_exit_zero_alone_is_not_verdict(db: FoundryDatabase) -> None:
    """D-032: a structured verdict is required — empty criteria results
    are refused (exit code 0 alone can never be recorded as PASS)."""
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    with pytest.raises(VerificationError):
        record_verification_verdict(  # type: ignore[arg-type]
            db, **_verdict(verification_id, criteria_results=[]))
    assert verification_status(db, verification_id) == "REQUESTED"


def test_artifact_hash_mismatch_refused(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    with pytest.raises(VerificationError):
        record_verification_verdict(  # type: ignore[arg-type]
            db, **_verdict(verification_id,
                           artifacts={"suite.txt": "f" * 64}))
    assert verification_status(db, verification_id) == "REQUESTED"


def test_fail_and_blocked_verdicts_accepted(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    record_verification_verdict(  # type: ignore[arg-type]
        db, **_verdict(verification_id, verdict="FAIL",
                       findings=[{"severity": "HIGH",
                                  "finding": "suite red"}]))
    assert verification_status(db, verification_id) == "VERDICTED"
    row = db.row("verifications", "verification_id", verification_id)
    assert row is not None and row["verdict"] == "FAIL"


# ---------------------------------------------------------------------------
# identity and self-certification
# ---------------------------------------------------------------------------

def test_self_certification_refused(db: FoundryDatabase) -> None:
    """A candidate can never invoke a trusted promotion verdict for
    itself: verdicts from the BUILDER's own session are rejected."""
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    with pytest.raises(VerificationError):
        record_verification_verdict(  # type: ignore[arg-type]
            db, **_verdict(verification_id,
                           verifier_identity=DIRECTIVE_IDENTITY))
    assert verification_status(db, verification_id) == "REQUESTED"


def test_directive_identity_enforced(db: FoundryDatabase) -> None:
    """D-031: JARVIS internal workers must resolve to
    opencode_go/deepseek-v4-flash — a foreign identity is refused
    (no silent substitution)."""
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    with pytest.raises(VerificationError):
        record_verification_verdict(  # type: ignore[arg-type]
            db, **_verdict(verification_id,
                           verifier_identity=FOREIGN_IDENTITY))
    assert verification_status(db, verification_id) == "REQUESTED"


def test_same_provider_limitation_recorded_honestly(
        db: FoundryDatabase) -> None:
    """Same-provider verification is ACCEPTED but flagged honestly."""
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    same_provider = dict(OTHER_IDENTITY)
    same_provider["session_id"] = "session-verifier-2"
    record_verification_verdict(  # type: ignore[arg-type]
        db, **_verdict(verification_id, verifier_identity=same_provider))
    row = db.row("verifications", "verification_id", verification_id)
    assert row is not None and row["same_provider_limitation"] == 1


def test_trusted_verdict_helper(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    record_verification_verdict(db, **_verdict(verification_id))  # type: ignore[arg-type]
    assert is_trusted_verdict(db, verification_id,
                              builder_identity=DIRECTIVE_IDENTITY) is True
    # an unknown verification is never trusted
    assert is_trusted_verdict(db, "verification-nope",
                              builder_identity=DIRECTIVE_IDENTITY) is False
    # a FAIL verdict is never a trusted PASS
    fail_id = _request(db, improvement_id,  # type: ignore[arg-type]
                       subject_ref="release-candidate-0002")
    record_verification_verdict(  # type: ignore[arg-type]
        db, **_verdict(fail_id, verdict="FAIL",
                       findings=[{"severity": "MEDIUM",
                                  "finding": "x"}]))
    assert is_trusted_verdict(db, fail_id,
                              builder_identity=DIRECTIVE_IDENTITY) is False


# ---------------------------------------------------------------------------
# projection integrity
# ---------------------------------------------------------------------------

def test_projection_and_schema(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    record_verification_verdict(db, **_verdict(verification_id))  # type: ignore[arg-type]
    report = reconcile(db)
    assert report.consistent is True
    before = db.count("verifications")
    db.rebuild_projections()
    assert db.count("verifications") == before
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # pinned to the constant, >= 25
    assert SCHEMA_VERSION >= 25


# ---------------------------------------------------------------------------
# round-2 repairs (deleg_036ee808 findings)
# ---------------------------------------------------------------------------

def test_non_hex_artifact_hash_refused(db: FoundryDatabase) -> None:
    """S5 r2 (MEDIUM): artifact hashes must be 64-char LOWERCASE HEX —
    a 64-char non-hex value is refused."""
    improvement_id = _improvement(db)
    with pytest.raises(VerificationError):
        _request(db, improvement_id, artifacts={"suite.txt": "z" * 64})
    with pytest.raises(VerificationError):
        _request(db, improvement_id, artifacts={"suite.txt": "A" * 64})
    assert len(db.events()) == 1  # only the improvement record


def test_foreign_criterion_in_verdict_refused(db: FoundryDatabase) -> None:
    """S5 r2 (MEDIUM): verdict criteria_results must be members of the
    request's frozen criteria list."""
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    foreign = _verdict(verification_id)
    foreign["criteria_results"] = [  # type: ignore[assignment]
        {"criterion": "criterion-99: invented", "status": "PASS",
         "evidence_hash": "b" * 64},
    ]
    with pytest.raises(VerificationError):
        record_verification_verdict(db, **foreign)  # type: ignore[arg-type]
    assert verification_status(db, verification_id) == "REQUESTED"


def test_dropped_criterion_in_verdict_accepted(db: FoundryDatabase) -> None:
    """S5 r2: a verdict may verify a SUBSET of the frozen criteria
    (each verified criterion must be a member; unverified criteria
    remain pending for other verifiers)."""
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    subset = _verdict(verification_id)
    subset["criteria_results"] = [  # type: ignore[assignment]
        {"criterion": "criterion-1: suite green", "status": "PASS",
         "evidence_hash": "b" * 64},
    ]
    record_verification_verdict(db, **subset)  # type: ignore[arg-type]
    assert verification_status(db, verification_id) == "VERDICTED"


def test_trusted_verdict_malformed_builder_identity(
        db: FoundryDatabase) -> None:
    """S5 r2 (LOW): is_trusted_verdict fails closed on a malformed
    builder identity (no session_id / wrong type / empty)."""
    improvement_id = _improvement(db)
    verification_id = _request(db, improvement_id)
    record_verification_verdict(db, **_verdict(verification_id))  # type: ignore[arg-type]
    for bad in (None, {}, {"session_id": ""}, {"session_id": 42},
                "session-builder-1"):
        assert is_trusted_verdict(db, verification_id,
                                  builder_identity=bad) is False  # type: ignore[arg-type]
    assert is_trusted_verdict(db, verification_id,
                              builder_identity=DIRECTIVE_IDENTITY) is True
