"""Post-V7 reusable procedure catalog, validator, and proof-factory tests."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_foundry.procedure_library import (
    ProcedureCatalog,
    ProcedureSandbox,
    RetryPolicy,
    VersionRecord,
    VersionRegistry,
    build_catalog,
    rank_observations,
    run_library_proof,
    verify_proof,
)
from jarvis_foundry.procedure_library.quality import QualityObservation


def test_catalog_is_typed_complete_and_selectable() -> None:
    catalog = build_catalog()
    report = catalog.validate()
    assert report.valid, report.as_dict()
    assert len(catalog.definitions) == 22
    assert len(set(catalog.ids)) == 22
    assert all(item.oracle is not None for item in catalog.definitions)
    assert all(item.evidence_outputs and item.known_failures for item in catalog.definitions)
    assert all("STOP" in item.stop_behavior for item in catalog.definitions)
    assert catalog.select(domain="software-engineering", statuses=("CANDIDATE",))[0].procedure_id == "jarvis.software-engineering"


def test_validator_rejects_unsafe_and_incomplete_contracts() -> None:
    catalog = build_catalog()
    source = catalog.get("jarvis.software-engineering")
    unsupported = replace(source, capabilities=("unsupported.capability",))
    assert not ProcedureCatalog((unsupported,)).validate().valid

    blind_retry = replace(
        source.steps[-1],
        retry_policy=RetryPolicy(max_attempts=2, retryable_failures=(), requires_reconciliation=False),
    )
    retry_report = ProcedureCatalog((replace(source, steps=(source.steps[0], blind_retry)),)).validate()
    assert any("retry" in issue.message for issue in retry_report.errors)

    no_oracle = replace(source, oracle=None)
    assert any(issue.field == "oracle" for issue in ProcedureCatalog((no_oracle,)).validate().errors)

    no_stop = replace(source, stop_behavior="continue without a guard")
    assert any(issue.field == "stop_behavior" for issue in ProcedureCatalog((no_stop,)).validate().errors)

    path_marker = "C:" + "\\\\" + "Users" + "\\\\" + "owner"
    secret_marker = "api_" + "key=fixture"
    secret = replace(source, purpose=f"use {path_marker} with {secret_marker}")
    secret_report = ProcedureCatalog((secret,)).validate()
    assert any("secret" in issue.message or "path" in issue.message for issue in secret_report.errors)


def test_validator_rejects_circular_dependencies() -> None:
    catalog = build_catalog()
    first = catalog.get("jarvis.bug-triage")
    second = catalog.get("jarvis.flake-diagnosis")
    cycle = ProcedureCatalog((
        replace(first, dependencies=(second.procedure_id,)),
        replace(second, dependencies=(first.procedure_id,)),
    ))
    report = cycle.validate()
    assert not report.valid
    assert sum("circular" in issue.message for issue in report.errors) == 2


def test_factory_proves_the_full_repertoire_in_disposable_state(tmp_path: Path) -> None:
    report = run_library_proof(build_catalog(), tmp_path / "postv7-skill-factory")
    assert report.passed
    assert report.proven_count == 22
    assert all(item.outcome == "PASS" for item in report.verifications)
    assert all(item.no_real_effects and item.scope_clean for item in report.proofs)
    assert report.catalog.select(statuses=("PROVEN_ONCE",))


def test_independent_verifier_rejects_tampered_oracle(tmp_path: Path) -> None:
    definition = build_catalog().get("jarvis.software-engineering")
    proof = ProcedureSandbox().run(definition, tmp_path / "tamper", run_id="builder-tamper")
    tampered = replace(proof, oracle_passed=False)
    result = verify_proof(definition, tampered)
    assert result.outcome == "FAIL"
    assert "success oracle failed" in result.findings
    assert result.verifier_run_id != proof.builder_run_id


def test_quality_ranking_uses_observed_outcomes_and_version_rollback() -> None:
    observations = (
        QualityObservation("jarvis.a", "1.0.0", "run:1", True, False, 10, 0.0, "PASS", "2026-08-24T00:00:00+00:00"),
        QualityObservation("jarvis.a", "1.0.0", "run:2", True, True, 20, 0.0, "PASS", "2026-08-24T01:00:00+00:00"),
        QualityObservation("jarvis.b", "1.0.0", "run:3", False, False, 10, 0.0, "FAIL", "2026-08-24T00:00:00+00:00"),
    )
    ranks = rank_observations(observations)
    assert ranks[0].procedure_id == "jarvis.a"
    registry = VersionRegistry()
    registry.add(VersionRecord("jarvis.synthetic", "1.0.0", "hash-1", "TRUSTED"))
    registry.add(VersionRecord("jarvis.synthetic", "2.0.0", "hash-2", "PROVEN_ONCE", "1.0.0"))
    registry.supersede("jarvis.synthetic", "1.0.0", "2.0.0")
    assert registry.select("jarvis.synthetic").version == "2.0.0"
    registry.rollback("jarvis.synthetic", "1.0.0")
    assert registry.select("jarvis.synthetic").version == "1.0.0"
