"""V6-S10: autonomous improvement discovery.

Improvement candidates are discovered ONLY from recorded observations:
metric exceedances (measured against thresholds/MMI), guardrail
breaches, verifier findings, and task failures. A candidate without an
evidence reference is REFUSED (no fabricated problems); hypotheses are
template-derived from the observation (no invented prose); the MMI
direction derives from the threshold semantics; risk class derives
from the surfaces; everything records a discovery.observed event with
a sources hash.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.discover import (
    DiscoveryError,
    candidate_improvements,
    record_discovered_improvements,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile

CONTROLLER = "supervisor"
CANDIDATE_CONTROLLER = "candidate-builder-1"

METRIC_OBS = {
    "metric_exceedances": [{
        "metric": "suite_runtime_s",
        "value": 313.92,
        "threshold": 300.0,
        "evidence_ref": "var/evidence/v6-s0/suite-v6-s0-baseline.txt",
        "unit": "seconds",
    }],
}
GUARDRAIL_OBS = {
    "guardrail_breaches": [{
        "guardrail": "cost",
        "detail": "paid calls observed",
        "evidence_ref": "var/evidence/v6-s4/cost-probe.txt",
    }],
}
VERIFIER_OBS = {
    "verifier_findings": [{
        "severity": "MEDIUM",
        "finding": "recovery ignores the freeze flag",
        "evidence_ref": "var/evidence/v6-s8/verdict-deleg_bbce3e0a.json",
    }],
}
TASK_FAILURE_OBS = {
    "task_failures": [{
        "task": "s11-canary-run",
        "detail": "canary terminated with health breach",
        "evidence_ref": "var/evidence/v6-s7/canary-health.txt",
    }],
}


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


# ---------------------------------------------------------------------------
# observation -> candidate derivation
# ---------------------------------------------------------------------------

def test_metric_exceedance_candidate(db: FoundryDatabase) -> None:
    candidates = candidate_improvements(db, sources=METRIC_OBS,
                                        controller=CONTROLLER)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["baseline_metric"]["name"] == "suite_runtime_s"
    assert candidate["baseline_metric"]["value"] == 313.92
    assert candidate["mmi"]["direction"] == "decrease"
    assert candidate["evidence_refs"] == [
        "var/evidence/v6-s0/suite-v6-s0-baseline.txt"]
    assert "313.92" in candidate["problem"]
    assert "suite_runtime_s" in candidate["problem"]
    assert "threshold" in candidate["problem"]


def test_guardrail_breach_candidate(db: FoundryDatabase) -> None:
    candidates = candidate_improvements(db, sources=GUARDRAIL_OBS,
                                        controller=CONTROLLER)
    assert len(candidates) == 1
    assert "cost" in candidates[0]["problem"]
    assert candidates[0]["guardrails"]["cost"] == "no paid calls"


def test_verifier_finding_candidate(db: FoundryDatabase) -> None:
    candidates = candidate_improvements(db, sources=VERIFIER_OBS,
                                        controller=CONTROLLER)
    assert len(candidates) == 1
    assert "MEDIUM" in candidates[0]["problem"]
    assert "recovery ignores the freeze flag" in candidates[0]["problem"]
    assert candidates[0]["declared_risk"] in ("R0", "R1", "R2", "R3")


def test_task_failure_candidate(db: FoundryDatabase) -> None:
    candidates = candidate_improvements(db, sources=TASK_FAILURE_OBS,
                                        controller=CONTROLLER)
    assert len(candidates) == 1
    assert "s11-canary-run" in candidates[0]["problem"]
    assert candidates[0]["evidence_refs"] == [
        "var/evidence/v6-s7/canary-health.txt"]


# ---------------------------------------------------------------------------
# no fabrication
# ---------------------------------------------------------------------------

def test_candidate_without_evidence_refused(db: FoundryDatabase) -> None:
    with pytest.raises(DiscoveryError):
        candidate_improvements(
            db,
            sources={"metric_exceedances": [{
                "metric": "made_up_metric", "value": 1.0,
                "threshold": 0.5,  # NO evidence_ref
            }]},
            controller=CONTROLLER)


def test_non_numeric_value_refused(db: FoundryDatabase) -> None:
    with pytest.raises(DiscoveryError):
        candidate_improvements(
            db,
            sources={"metric_exceedances": [{
                "metric": "suite_runtime_s", "value": "fast",
                "threshold": 300.0,
                "evidence_ref": "var/evidence/v6-s0/suite.txt",
            }]},
            controller=CONTROLLER)


def test_unknown_observation_type_refused(db: FoundryDatabase) -> None:
    with pytest.raises(DiscoveryError):
        candidate_improvements(
            db,
            sources={"hallucinated_observations": [{
                "detail": "something",
                "evidence_ref": "var/evidence/x.txt",
            }]},
            controller=CONTROLLER)


def test_empty_sources_zero_candidates(db: FoundryDatabase) -> None:
    assert candidate_improvements(db, sources={},
                                  controller=CONTROLLER) == []


def test_candidate_controller_refused(db: FoundryDatabase) -> None:
    with pytest.raises(DiscoveryError):
        candidate_improvements(db, sources=METRIC_OBS,
                               controller=CANDIDATE_CONTROLLER)


# ---------------------------------------------------------------------------
# recording + idempotency
# ---------------------------------------------------------------------------

def test_record_discovered_improvements(db: FoundryDatabase) -> None:
    candidates = candidate_improvements(db, sources=METRIC_OBS,
                                        controller=CONTROLLER)
    recorded = record_discovered_improvements(
        db, candidates=candidates, sources=METRIC_OBS,
        controller=CONTROLLER)
    assert len(recorded) == 1
    row = db.row("improvement_records", "improvement_id", recorded[0])
    assert row is not None
    assert row["problem"]  # recorded with its derived problem text
    types = [e.event_type for e in db.events()]
    assert "discovery.observed" in types
    assert "improvement.recorded" in types
    # the discovery run is recorded
    assert db.count("discovery_runs") == 1


def test_discovery_redelivery_dedupes(db: FoundryDatabase) -> None:
    candidates = candidate_improvements(db, sources=METRIC_OBS,
                                        controller=CONTROLLER)
    record_discovered_improvements(db, candidates=candidates,
                                   sources=METRIC_OBS,
                                   controller=CONTROLLER)
    events_before = len(db.events())
    record_discovered_improvements(db, candidates=candidates,
                                   sources=METRIC_OBS,
                                   controller=CONTROLLER)
    assert len(db.events()) == events_before  # identical re-run dedupes
    assert db.count("discovery_runs") == 1
    assert db.count("improvement_records") == 1


def test_improvement_candidate_eligibility(
        db: FoundryDatabase) -> None:
    """Discovered improvements are immediately eligible (S1 gate)."""
    from jarvis_foundry.evolution.records import improvement_eligibility
    candidates = candidate_improvements(db, sources=METRIC_OBS,
                                        controller=CONTROLLER)
    recorded = record_discovered_improvements(
        db, candidates=candidates, sources=METRIC_OBS,
        controller=CONTROLLER)
    verdict = improvement_eligibility(db, recorded[0])
    assert verdict[0] is True  # (eligible, reason) tuple


# ---------------------------------------------------------------------------
# projection integrity
# ---------------------------------------------------------------------------

def test_projection_and_schema(db: FoundryDatabase) -> None:
    candidates = candidate_improvements(db, sources=METRIC_OBS,
                                        controller=CONTROLLER)
    record_discovered_improvements(db, candidates=candidates,
                                   sources=METRIC_OBS,
                                   controller=CONTROLLER)
    report = reconcile(db)
    assert report.consistent is True
    before = db.count("discovery_runs")
    db.rebuild_projections()
    assert db.count("discovery_runs") == before
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # pinned to the constant, >= 30
    assert SCHEMA_VERSION >= 30


# ---------------------------------------------------------------------------
# round-1 repairs (deleg_b65820c2 FAIL findings: secret-guard escaping
# asymmetry, handler type validation, all-or-nothing recording)
# ---------------------------------------------------------------------------

def test_escaped_quote_secret_shape_refused(db: FoundryDatabase) -> None:
    """S10 r1 (MEDIUM, D2): canonical_json escapes quotes inside string
    values — '"api_key": "abc123"' must ALSO be caught by the
    secret guard (the S1/S5/S9/S10 shared redaction pattern now spans
    backslash-escaped quotes)."""
    import json as _json
    from jarvis_foundry.events import canonical_json
    from jarvis_foundry.control.redaction import contains_secret_like
    # the exact shape the verifier used: a secret inside a STRING value
    payload = {"observation": '"api_key": "abc123"'}
    encoded = canonical_json(payload)
    assert "\\" in encoded or '\\"' in encoded  # quotes escaped
    assert contains_secret_like(encoded) is True  # now caught
    with pytest.raises(DiscoveryError):
        candidate_improvements(
            db,
            sources={"guardrail_breaches": [{
                "guardrail": "cost",
                "detail": '"api_key": "abc123"',
                "evidence_ref": "var/evidence/v6-s10/secret.txt",
            }]},
            controller=CONTROLLER)
    assert len(db.events()) == 0


def test_handler_rejects_non_numeric_counts(db: FoundryDatabase) -> None:
    """S10 r1 (MEDIUM, D6): raw discovery.observed with a
    non-numeric candidates_count/recorded_count is rolled back."""
    from jarvis_foundry.events import Event
    with pytest.raises(ValueError):
        db.append(Event.create(
            "discovery.observed",
            {"run_id": "discovery-abcdef123456",
             "sources_hash": "c" * 64, "candidates_count": "abc",
             "recorded_count": 1, "controller": CONTROLLER},
            source_type="supervisor", source_id="v6",
            idempotency_key="discovery.observed:discovery-abcdef123456"))
    assert db.count("discovery_runs") == 0
    assert len(db.events()) == 0  # full rollback
    with pytest.raises(ValueError):
        db.append(Event.create(
            "discovery.observed",
            {"run_id": "discovery-abcdef123456",
             "sources_hash": "c" * 64, "candidates_count": True,
             "recorded_count": 1, "controller": CONTROLLER},
            source_type="supervisor", source_id="v6",
            idempotency_key="discovery.observed:discovery-abcdef123456"))
    assert db.count("discovery_runs") == 0


def test_mixed_candidate_list_all_or_nothing(db: FoundryDatabase) -> None:
    """S10 r1 (LOW, D7): a [valid, invalid] candidate list records
    NOTHING — all candidates are validated before any is recorded."""
    valid = candidate_improvements(db, sources=METRIC_OBS,
                                   controller=CONTROLLER)[0]
    invalid = dict(valid)
    del invalid["evidence_refs"]  # fabricated-looking candidate
    with pytest.raises(DiscoveryError):
        record_discovered_improvements(
            db, candidates=[valid, invalid], sources=METRIC_OBS,
            controller=CONTROLLER)
    assert db.count("improvement_records") == 0  # NOTHING recorded
    assert len(db.events()) == 0
