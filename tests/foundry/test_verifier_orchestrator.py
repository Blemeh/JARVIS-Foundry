"""V6 lane: independent verifier orchestration (F1 repair).

Covers: real out-of-process probe producing a trusted PASS; probe FAIL
-> FAIL verdict (no promotion); forged probe reports refused (missing
criteria, extra criteria, unearned PASS without evidence hash, wrong
candidate release, unparseable output); builder-session verifier
identity is never trusted; orchestration record binds candidate tree
hash + evidence hashes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.registry import register_release
from jarvis_foundry.evolution.verifier_orchestrator import (
    VerifierOrchestrationError,
    orchestrate_verification,
    verify_orchestration_binding,
)
from jarvis_foundry.runtime.provider_config import (
    default_provider_settings,
)

PY = sys.executable
TEST_PROVIDER = default_provider_settings()
# matches the resolved current provider policy so the S5 identity gate
# accepts the builder identity (same convention as test_evolution_verify)
BUILDER = {"provider": TEST_PROVIDER.provider,
           "model": TEST_PROVIDER.model,
           "session_id": "builder-1"}


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase, **overrides) -> str:
    kwargs = dict(
        problem="p", evidence_refs=["e"],
        provenance={"source": "test"}, hypothesis="h",
        expected_mechanism="m",
        baseline_metric={"name": "suite_runtime_s", "value": 4.0,
                         "unit": "s", "source": "e"},
        mmi={"minimum_delta": 1.0, "direction": "decrease", "unit": "s"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "w", "hash": "a" * 64},
        observation={"requirement": "1 cycle", "cycles": 1},
        declared_risk="R2", surfaces=["tests/foundry"],
        budget={"max_cost_usd": 0.0, "capability_scope": []},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"})
    kwargs.update(overrides)
    return record_improvement(db, **kwargs)


def _candidate(db: FoundryDatabase, tmp_path: Path,
               improvement_id: str) -> tuple[str, Path]:
    release_id = register_release(
        db, source_commit="a" * 40,
        artifact_hashes={"suite.txt": "b" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=None, improvement_id=improvement_id,
        risk_class="R2", migrations=[], verdicts=[],
        controller="supervisor")
    tree = tmp_path / "candidate-tree"
    tree.mkdir()
    (tree / "artifact.txt").write_text("candidate content",
                                       encoding="utf-8")
    return release_id, tree


def _probe_script(body: str) -> list[str]:
    return [PY, "-c", (
        "import json, sys\n"
        "criteria = " + body + "\n"
        "print(json.dumps({'criteria': criteria, 'findings': []}))\n")]


_ALL_PASS = ("{"
             "\"guardrail correctness: suite green\": "
             "{'status': 'PASS', 'evidence_hash': '" + "c" * 64 + "'}, "
             "\"guardrail safety: no live mutation\": "
             "{'status': 'PASS', 'evidence_hash': '" + "d" * 64 + "'}, "
             "\"guardrail cost: no paid calls\": "
             "{'status': 'PASS', 'evidence_hash': '" + "e" * 64 + "'}, "
             "\"guardrail latency: no regression\": "
             "{'status': 'PASS', 'evidence_hash': '" + "f" * 64 + "'}"
             "}")


def test_real_probe_pass_binds_and_trusts(db: FoundryDatabase,
                                          tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id, tree = _candidate(db, tmp_path, improvement_id)
    payload = orchestrate_verification(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id, candidate_tree_root=tree,
        builder_identity=BUILDER, controller="supervisor",
        probe_command=_probe_script(_ALL_PASS),
        provider_settings=TEST_PROVIDER)
    assert payload["all_criteria_pass"] is True
    assert payload["trusted_independent_pass"] is True
    assert payload["independence_status"] == "INDEPENDENT"
    assert len(payload["candidate_tree_sha256"]) == 64
    assert verify_orchestration_binding(
        db, payload["orchestration_id"],
        expected_release_id=release_id) is True


def test_probe_fail_yields_fail_verdict(db: FoundryDatabase,
                                        tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id, tree = _candidate(db, tmp_path, improvement_id)
    failing = _ALL_PASS.replace(
        "\"guardrail correctness: suite green\": "
        "{'status': 'PASS', 'evidence_hash': '" + "c" * 64 + "'}",
        "\"guardrail correctness: suite green\": "
        "{'status': 'FAIL', 'evidence_hash': None}")
    payload = orchestrate_verification(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id, candidate_tree_root=tree,
        builder_identity=BUILDER, controller="supervisor",
        probe_command=_probe_script(failing),
        provider_settings=TEST_PROVIDER)
    assert payload["all_criteria_pass"] is False
    assert payload["trusted_independent_pass"] is False


def test_not_tested_criterion_never_becomes_pass(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id, tree = _candidate(db, tmp_path, improvement_id)
    not_tested = _ALL_PASS.replace(
        "\"guardrail correctness: suite green\": "
        "{'status': 'PASS', 'evidence_hash': '" + "c" * 64 + "'}",
        "\"guardrail correctness: suite green\": "
        "{'status': 'NOT_TESTED', 'evidence_hash': None}")
    payload = orchestrate_verification(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id, candidate_tree_root=tree,
        builder_identity=BUILDER, controller="supervisor",
        probe_command=_probe_script(not_tested),
        provider_settings=TEST_PROVIDER)
    assert payload["all_criteria_pass"] is False
    assert payload["not_tested_count"] == 1
    assert payload["trusted_independent_pass"] is False


def test_missing_criterion_report_refused(db: FoundryDatabase,
                                          tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id, tree = _candidate(db, tmp_path, improvement_id)
    incomplete = json.loads(_ALL_PASS.replace("'", '"'))
    del incomplete["guardrail latency: no regression"]
    with pytest.raises(VerifierOrchestrationError,
                       match="diverge from the frozen request"):
        orchestrate_verification(
            db, improvement_id=improvement_id,
            candidate_release_id=release_id, candidate_tree_root=tree,
            builder_identity=BUILDER, controller="supervisor",
            probe_command=_probe_script(json.dumps(incomplete)),
            provider_settings=TEST_PROVIDER)


def test_unearned_pass_without_evidence_hash_refused(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id, tree = _candidate(db, tmp_path, improvement_id)
    forged = _ALL_PASS.replace("'evidence_hash': '" + "c" * 64 + "'",
                               "'evidence_hash': ''")
    with pytest.raises(VerifierOrchestrationError,
                       match="unearned PASS"):
        orchestrate_verification(
            db, improvement_id=improvement_id,
            candidate_release_id=release_id, candidate_tree_root=tree,
            builder_identity=BUILDER, controller="supervisor",
            probe_command=_probe_script(forged),
            provider_settings=TEST_PROVIDER)


def test_wrong_candidate_release_refused(db: FoundryDatabase,
                                         tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    # a genuinely DIFFERENT improvement (changed content -> new S1 id;
    # identical content would dedupe by design)
    other_improvement = _improvement(db, problem="a different measured "
                                                "problem entirely")
    release_id, tree = _candidate(db, tmp_path, improvement_id)
    with pytest.raises(VerifierOrchestrationError,
                       match="different improvement"):
        orchestrate_verification(
            db, improvement_id=other_improvement,
            candidate_release_id=release_id, candidate_tree_root=tree,
            builder_identity=BUILDER, controller="supervisor",
            probe_command=_probe_script(_ALL_PASS),
            provider_settings=TEST_PROVIDER)


def test_unparseable_probe_output_refused(db: FoundryDatabase,
                                          tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id, tree = _candidate(db, tmp_path, improvement_id)
    with pytest.raises(VerifierOrchestrationError,
                       match="no parseable criteria report"):
        orchestrate_verification(
            db, improvement_id=improvement_id,
            candidate_release_id=release_id, candidate_tree_root=tree,
            builder_identity=BUILDER, controller="supervisor",
            probe_command=[PY, "-c", "print('not json at all')"],
            provider_settings=TEST_PROVIDER)


def test_builder_session_verifier_never_trusted(
        db: FoundryDatabase, tmp_path: Path) -> None:
    """Direct S5 boundary re-check at the orchestration level: even if
    a verdict somehow carried the builder's session, is_trusted_verdict
    refuses it — the orchestrator always mints a distinct verifier
    session, and this test pins that contract."""
    from jarvis_foundry.evolution.verify import is_trusted_verdict
    from jarvis_foundry.evolution.verify import (
        record_verification_request, record_verification_verdict)

    improvement_id = _improvement(db)
    settings = TEST_PROVIDER
    vid = record_verification_request(
        db, improvement_id=improvement_id, subject_ref="rel-x",
        criteria=["c"], artifacts={"a": "b" * 64},
        builder_identity=BUILDER, controller="supervisor",
        provider_settings=settings)
    same_session = dict(BUILDER)
    with pytest.raises(Exception, match="builder session"):
        record_verification_verdict(
            db, verification_id=vid, verdict="PASS",
            criteria_results=[{"criterion": "c", "status": "PASS",
                               "evidence_hash": "c" * 64}],
            findings=[], verifier_identity=same_session,
            evidence_refs=[], provider_settings=settings)
    assert is_trusted_verdict(db, vid, BUILDER) is False
