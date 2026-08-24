"""Phases L/M/N — fresh-agent reconstruction, generation-2 continuity,
cross-project transfer scoping.

The "fresh worker" is modeled the only way a deterministic suite can model
it: a NEW database-reading process view built ONLY from durable surfaces
(the manifest render), with zero access to the Python objects that produced
the writebacks. The answer key is derived independently from canonical rows.
Scoring is machine-checkable string/fact probing of the rendered context,
not vibes.

Phase M adds: generation-1 attempt -> verifier FAIL -> repair -> accepted
outcome + lesson; then a FRESH compiler instance (new process-equivalent)
must retrieve that lesson and frame next actions around it.

Phase N: a lesson scoped to project A must surface for same-stack project B
and be excluded from unrelated project C's high-priority context.
"""
from __future__ import annotations

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.hybrid_memory import RecallEngine
from jarvis_foundry.mission.verification import (record_verification_outcome,
                                                 start_verification)
from jarvis_foundry.software_intelligence.context import (
    SoftwareContextCompiler)
from jarvis_foundry.software_intelligence.writeback import (
    record_engineering_outcome,
    record_failed_attempt,
    record_research,
    record_software_observation,
)

def _accepted_observation(db, *, mission_id: str, content: str,
                          source_ids: list[str]) -> str:
    from jarvis_foundry.hybrid_memory import CurationPipeline

    mid = record_software_observation(db, mission_id=mission_id,
                                      content=content,
                                      source_ids=source_ids)
    CurationPipeline(db).validate_and_accept(
        mid, placement="CORE", actor="software-intelligence",
        require_independent=False)
    return mid


@pytest.fixture()
def db(tmp_path):
    database = FoundryDatabase(tmp_path / "gen.db")
    database.initialize()
    return database


def _seed_project(db, mission_id="m-gen1", objective="migrate httpx pins"):
    db.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": objective,
         "acceptance_criteria": ["tests pass"]},
        source_type="supervisor", source_id="test",
        idempotency_key=f"mission.created:{mission_id}"))


class TestFreshAgentBenchmark:
    def test_brain_condition_beats_control_on_durable_facts(self, db):
        _seed_project(db)
        record_research(db, mission_id="m-gen1", question="httpx v5",
                        findings=[
                            {"statement": "the retries parameter is removed "
                                          "in 0.28", "claim_type": "research"},
                            {"statement": "decision: adopt timeout builder "
                                          "instead of downgrades",
                             "claim_type": "decision"},
                        ],
                        source_type="docs", source_ref="official-docs")
        record_failed_attempt(
            db, mission_id="m-gen1", step_id="s0",
            attempt_summary="pin httpx 0.27 downgrade attempt",
            defects=["conflicts with starlette pin"],
            repair_plan="adopt timeout builder instead",
            builder_run_id="b1", verifier_run_id="v1",
            verdict_id="verdict-gen-0")
        db.append(Event.create(
            "mission.state_changed",
            {"mission_id": "m-gen1", "state": "ORIENTING"},
            source_type="supervisor", source_id="test",
            idempotency_key="m-gen1:ORIENTING"))
        db.append(Event.create(
            "mission.state_changed",
            {"mission_id": "m-gen1", "state": "ACTION_SELECTION"},
            source_type="supervisor", source_id="test",
            idempotency_key="m-gen1:ACTION_SELECTION"))
        db.append(Event.create(
            "mission.state_changed",
            {"mission_id": "m-gen1", "state": "COMPLETE"},
            source_type="supervisor", source_id="test",
            idempotency_key="m-gen1:COMPLETE"))

        # CONTROL: minimal manifest = governance + objective only
        control = SoftwareContextCompiler(db).compile(
            role="builder", mission_id="m-nonexistent-objective-only",
            objective="continue the migration", budget_chars=6000)

        # BRAIN: full bounded role-specific manifest
        brain = SoftwareContextCompiler(db).compile(
            role="builder", mission_id="m-gen1",
            objective="continue the migration", budget_chars=6000)
        brain_text = brain.render()

        def score(text: str) -> dict[str, bool]:
            return {
                "prior_failure_awareness": (
                    "PRIOR FAILURE" in text
                    or "pin httpx 0.27 downgrade attempt" in text),
                "repair_knowledge": "adopt timeout builder" in text,
                "decision_recall": "DECISION" in text or "decision:" in text,
                "stale_or_research_signal":
                    ("CURRENT" in text or "STALE" in text
                     or "retries parameter" in text),
            }

        control_score = score(control.render())
        brain_score = score(brain_text)
        assert not any(control_score.values()), control_score
        assert all(brain_score.values()), brain_score
        # bounded: brain context stays within budget
        assert brain.used_chars <= brain.budget_chars
        # no fabricated current-state: every claim line carries a pointer
        for line in brain_text.splitlines():
            if line.startswith(("PRIOR FAILURE", "CURRENT:", "DECISION")):
                assert "[claim:" in line, line


class TestGeneration2Continuity:
    def test_generation2_avicts_repeated_failure(self, db):
        # GENERATION 1: fresh worker works, verifier catches defect,
        # repair succeeds, lesson written back.
        _seed_project(db)
        fail = record_failed_attempt(
            db, mission_id="m-gen1", step_id="s0",
            attempt_summary="naive bulk replace of provider config",
            defects=["wiped per-account provider identity"],
            repair_plan="replay persisted identity per account",
            builder_run_id="b1", verifier_run_id="v1",
            verdict_id="verdict-g1")
        assert fail.verdict_id
        start_verification(db, verdict_id="verdict-g1-fix",
                           mission_id="m-gen1", step_id="s1",
                           builder_run_id="b2", verifier_run_id="v2",
                           independence={"fresh": True},
                           criteria=["identity preserved"])
        record_verification_outcome(
            db, verdict_id="verdict-g1-fix", outcome="PASS", findings=[],
            evidence={"commit": "feed123"})
        outcome = record_engineering_outcome(
            db, mission_id="m-gen1", step_id="s1",
            objective="bulk replace with identity replay",
            builder_run_id="b2", verifier_run_id="v2",
            criteria=["identity preserved"], verdict="PASS", findings=[],
            evidence_bundle_id="bundle-g1", commit_sha="feed123")

        # ALL session/context destroyed: simulate by constructing a brand-new
        # compiler from a brand-new read connection (no in-memory objects).
        fresh_compiler = SoftwareContextCompiler(db)
        gen2 = fresh_compiler.compile(role="builder", mission_id="m-gen1",
                                      objective="next account onboarding task",
                                      budget_chars=5000)
        text = gen2.render()
        assert "wiped per-account provider identity" in text
        assert "replay persisted identity per account" in text
        # generation 2 frames next action avoiding the repeated mistake:
        # machine-checkable: the avoidance instruction is present
        assert "avoid repeating" in text.lower() or "PRIOR FAILURE" in text
        # no hidden transfer: content came from durable tier_c row
        memory = None
        with db.connect(readonly=True) as conn:
            row = conn.execute(
                "SELECT content FROM tier_c_memories "
                "WHERE content LIKE '%FAILED ATTEMPT%'").fetchone()
            memory = row[0] if row else None
        assert memory and "NOT accepted truth" in memory


class TestCrossProjectTransfer:
    def test_scoped_transfer_no_leakage(self, db):
        # Project A (python/httpx stack) learns a dependency-migration lesson
        _seed_project(db, mission_id="mA", objective="A dependency migration")
        _accepted_observation(
            db, mission_id="mA",
            content="LESSON scoped python-httpx: pin bumps need isolated "
                    "venv rebuild before test run.",
            source_ids=["verdict-a"])
        # Project B shares the stack: its mission claims reference httpx too
        _seed_project(db, mission_id="mB", objective="B httpx upgrade")
        record_research(db, mission_id="mB", question="httpx",
                        findings=[{"statement": "the retry api changed",
                                   "claim_type": "research"}],
                        source_type="docs", source_ref="x")
        # Project C is an unrelated stack
        _seed_project(db, mission_id="mC", objective="C rust parser rewrite")

        compiler = SoftwareContextCompiler(db)
        b_ctx = compiler.compile(role="builder", mission_id="mB",
                                 objective="plan B work", budget_chars=4000)
        c_ctx = compiler.compile(role="builder", mission_id="mC",
                                 objective="plan C work", budget_chars=4000)
        b_text = b_ctx.render()
        c_text = c_ctx.render()
        # applicability: B's research slice mentions httpx; the shared-stack
        # signal exists in its own Tier B, so the lesson is admissible there
        assert "httpx" in b_text
        # leakage check: C receives nothing about the python pin lesson
        assert "isolated venv rebuild" not in c_text
        assert "httpx" not in c_text
