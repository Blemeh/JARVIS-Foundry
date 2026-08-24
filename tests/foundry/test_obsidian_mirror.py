"""Phase K — disposable Obsidian mirror: the owner-facing human brain.

Two richly populated project notes + a research note, navigable with
wikilinks, governed metadata, and the full write protocol surface:
update, expected-hash concurrency conflict (both versions preserved),
snapshot/rollback, malformed frontmatter refusal, semantic verification
after write. The OWNER'S live vault is never touched; everything here is a
temp-directory mirror.
"""
from __future__ import annotations

import hashlib

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.obsidian import (ObsidianConflictError,
                                     ObsidianPolicyError)
from jarvis_foundry.software_intelligence.writeback import (
    record_engineering_outcome,
    render_project_note,
    write_project_note,
)
from obsidian_fixture import make_service

FM = ("---\norigin: jarvis\ncurrent_steward: jarvis\n"
      "system: jarvis-foundry\nversion: v1\n---\n")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture()
def db(tmp_path):
    database = FoundryDatabase(tmp_path / "mirror.db")
    database.initialize()
    return database


class TestObsidianMirror:
    def test_two_projects_render_navigable_brains(self, db, tmp_path):
        service = make_service(tmp_path, allow_projects=True)

        queuetwin = render_project_note(
            project_name="QueueTwin",
            resource_id="resource-queuetwin",
            goal="Simulate contact-center queues before staffing decisions",
            current_state="MVP accepted 2026-08-24; wake on first real cohort",
            decisions=["[[Projects/leadgen]] shares the FastAPI scaffold",
                       "SQLite WAL for v0"],
            failures=["first scheduler draft assumed Poisson arrivals"],
            lessons=["bounded budgets beat full dumps"],
            unknowns=["real arrival distribution unknown"],
            evidence_pointers=["evidence/bundle-mvp"])
        leadgen = render_project_note(
            project_name="LeadGen",
            resource_id="resource-leadgen",
            goal="Acquisition engine for the tech-services business",
            current_state="research phase; two sources contradicted",
            decisions=["[[Projects/queuetwin]] reuses auth module"],
            failures=[],
            lessons=["official docs outrank blog posts"],
            unknowns=["provider rate limits at scale"],
            evidence_pointers=[])
        w1 = write_project_note(service, note_name="Projects/queuetwin.md",
                                content=queuetwin)
        w2 = write_project_note(service, note_name="Projects/leadgen.md",
                                content=leadgen)
        assert w1.write_id and w2.write_id
        # wikilinks resolve as text navigation in both directions
        back = "Projects/queuetwin.md"
        body = service.read_note(f"JARVIS/V1/{back}")
        assert "[[Projects/leadgen]]" in body
        other = service.read_note("JARVIS/V1/Projects/leadgen.md")
        assert "[[Projects/queuetwin]]" in other
        # no machine-log wall: sections are short and labeled
        assert "# Project Intelligence - QueueTwin" in body
        assert "## Unknowns" in body

    def test_update_conflict_preserves_both_versions(self, db, tmp_path):
        service = make_service(tmp_path, allow_projects=True)
        note = "Projects/queuetwin.md"
        seed = FM + "# seed\n"
        write_project_note(service, note_name=note, content=seed)
        updated = write_project_note(
            service, note_name=note, content=FM + "# v2\n",
            expected_hash=_hash(seed))
        # a stale writer (still expecting seed bytes) collides with v2 disk
        with pytest.raises(ObsidianConflictError) as excinfo:
            write_project_note(service, note_name=note,
                               content=FM + "# stale concurrent edit\n",
                               expected_hash=_hash(seed))
        # both versions survive: disk keeps the concurrent edit under Audit/
        preserved = excinfo.value.preserved_path
        assert preserved, "conflict must preserve incoming bytes"
        on_disk = service.read_note(f"JARVIS/V1/{note}")
        assert "# v2" in on_disk

    def test_rollback_restores_byte_exact(self, db, tmp_path):
        service = make_service(tmp_path, allow_projects=True)
        note = "Projects/rollback-probe.md"
        seed = FM + "# original truth\n"
        write_project_note(service, note_name=note, content=seed)
        result = write_project_note(
            service, note_name=note, content=FM + "# rewritten\n",
            expected_hash=_hash(seed))
        service.rollback(result.write_id)
        assert service.read_note(f"JARVIS/V1/{note}") == seed

    def test_malformed_frontmatter_refused_before_any_mutation(self, db, tmp_path):
        service = make_service(tmp_path, allow_projects=True)
        note = "Projects/broken.md"
        with pytest.raises(ObsidianPolicyError):
            write_project_note(service, note_name=note,
                               content="# no frontmatter\n")
        # nothing was created
        path = service.writer.note_path(note)
        assert not path.exists()

    def test_semantic_verification_after_write(self, db, tmp_path):
        """The written brain must answer the owner's questions by content."""
        service = make_service(tmp_path, allow_projects=True)
        db.append(Event.create(
            "mission.created",
            {"mission_id": "m-note", "objective": "close adapter bug",
             "acceptance_criteria": ["pass"]},
            source_type="supervisor", source_id="test",
            idempotency_key="m-note"))
        outcome = record_engineering_outcome(
            db, mission_id="m-note", step_id="s1", objective="close adapter bug",
            builder_run_id="b", verifier_run_id="v", criteria=["pass"],
            verdict="PASS", findings=[], evidence_bundle_id="bundle-x",
            commit_sha="abc1234",
            lessons=["always replay persisted provider identity"])
        decisions = [f"closed adapter bug at commit abc1234 "
                     f"(verifier {outcome.verdict_id})"]
        note_text = render_project_note(
            project_name="Adapter", resource_id="resource-adapter",
            goal="keep adapters healthy", current_state="bug closed",
            decisions=decisions,
            failures=["earlier attempt asserted wrong state"],
            lessons=["always replay persisted provider identity"],
            unknowns=["upstream v5 plans"], evidence_pointers=[])
        write_project_note(service, note_name="Projects/adapter.md",
                           content=note_text)
        body = service.read_note("JARVIS/V1/Projects/adapter.md")
        answers = {
            "what happened": "commit abc1234" in body,
            "why trusted": "verifier verdict-s1" in body,
            "what failed": "asserted wrong state" in body,
            "lesson": "replay persisted provider identity" in body,
            "unknown": "upstream v5" in body,
        }
        assert all(answers.values()), answers
