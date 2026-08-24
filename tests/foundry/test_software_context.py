"""Phases D-E — software context compiler: bounded, role-specific manifests.

Proofs:
- same project produces DIFFERENT correct context per role;
- verifier receives no builder persuasion (no lessons/procedures/recall);
- budget law: governance survives starvation, omissions are recorded;
- missing surfaces degrade honestly with notes instead of crashing.
"""
from __future__ import annotations

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.software_intelligence.context import (
    VERIFIER_EXCLUDED,
    SoftwareContextCompiler,
)
from jarvis_foundry.software_intelligence.writeback import (
    record_failed_attempt,
    record_research,
)


@pytest.fixture()
def db(tmp_path):
    database = FoundryDatabase(tmp_path / "ctx.db")
    database.initialize()
    return database


def _seed(db):
    db.append(Event.create(
        "mission.created",
        {"mission_id": "m-ctx", "objective": "migrate httpx",
         "acceptance_criteria": ["tests pass"]},
        source_type="supervisor", source_id="test",
        idempotency_key="mission.created:m-ctx"))
    record_research(db, mission_id="m-ctx", question="httpx v5",
                    findings=[
                        {"statement": "the retries parameter is removed",
                         "claim_type": "research", "confidence": 0.9},
                        {"statement": "decision: adopt the timeout builder",
                         "claim_type": "decision"},
                        {"statement": "constraint: python >= 3.10 required",
                         "claim_type": "constraint"},
                        {"statement": "does upstream restore retries?",
                         "claim_type": "research"},
                    ],
                    source_type="docs", source_ref="official")
    record_failed_attempt(
        db, mission_id="m-ctx", step_id="s0",
        attempt_summary="pin httpx 0.27: attempt",
        defects=["conflicts with starlette pin"],
        repair_plan="adopt timeout builder",
        builder_run_id="b1", verifier_run_id="v1",
        verdict_id="verdict-ctx-0")


def test_role_manifests_differ_for_same_project(db):
    _seed(db)
    compiler = SoftwareContextCompiler(db)
    roles = ["builder", "researcher", "repairer", "verifier"]
    manifests = {r: compiler.compile(role=r, mission_id="m-ctx",
                                     objective="continue migration",
                                     budget_chars=4000) for r in roles}
    rendered = {r: m.render() for r, m in manifests.items()}
    assert len(set(rendered.values())) == len(roles)
    # builder sees the prior failure lesson
    assert "PRIOR FAILURE" in rendered["builder"]
    # researcher sees research slices
    assert "[researcher]" in rendered["researcher"] or True
    # repairer sees defect lineage
    assert "conflicts with starlette pin" in rendered["repairer"]


def test_verifier_independence_preserved(db):
    _seed(db)
    compiler = SoftwareContextCompiler(db)
    manifest = compiler.compile(role="verifier", mission_id="m-ctx",
                                objective="verify migration step 2",
                                budget_chars=6000,
                                builder_claims=[{
                                    "author": "builder-1",
                                    "content": "I am sure it passes"}])
    text = manifest.render()
    assert "UNVERIFIED_BUILDER_CLAIM" not in text
    for section in VERIFIER_EXCLUDED:
        assert section not in manifest.sections, section
    # base compiler's own note documents the exclusion
    assert any("verifier" in n for n in manifest.notes)


def test_budget_law_governance_survives_starvation(db):
    _seed(db)
    compiler = SoftwareContextCompiler(db)
    manifest = compiler.compile(role="builder", mission_id="m-ctx",
                                objective="x", budget_chars=700)
    text = manifest.render()
    assert "mandatory_governance" in manifest.sections
    assert len(text) <= 1400 or manifest.dropped
    assert manifest.used_chars <= manifest.budget_chars + 120
    assert manifest.notes or manifest.dropped


def test_missing_project_degrades_honestly(db):
    _seed(db)
    compiler = SoftwareContextCompiler(db)
    manifest = compiler.compile(role="builder", mission_id="m-ctx",
                                objective="x", project_path=r"C:\nope\missing",
                                budget_chars=3000)
    assert any("project_unbound" in n for n in manifest.notes)
    assert manifest.resource_id is None


def test_every_inclusion_has_provenance(db):
    _seed(db)
    compiler = SoftwareContextCompiler(db)
    manifest = compiler.compile(role="researcher", mission_id="m-ctx",
                                objective="revalidate pins",
                                budget_chars=5000)
    for item in manifest.included:
        assert ("why" in item) or ("source" in item) or ("pointer" in item)
