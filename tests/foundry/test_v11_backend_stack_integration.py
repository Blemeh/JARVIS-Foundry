"""Focused semantic seams for the V11 backend product stack."""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_sources import SourceSpec
from jarvis_foundry.executive.owner_context import seed_owner_context
from jarvis_foundry.fabric.catalog import record_outcome
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.initiative_a2 import InitiativeA2Bounds, run_initiative_a2_cycle
from jarvis_foundry.mission.verification_planning import READY, compile_verification_plan
from jarvis_foundry.project_context import (
    ProjectContextResolver,
    ProjectProfileService,
    ProjectTarget,
)


# Anchor the fixture to the current UTC clock so candidate expiry
# (executive scoring and research freshness both compare against
# wall-clock UTC) does not spuriously reject the intended seam
# candidate. A frozen past timestamp silently became "STALE" once the
# 168h candidate TTL elapsed, which is a fixture time-bomb rather than a
# property of the inert-seam behaviour under test.
NOW = datetime.now(UTC).replace(microsecond=0)
LOCAL_CAPABILITIES = (
    "cap.file.read",
    "cap.file.write",
    "cap.git.inspect",
    "cap.git.diff",
    "cap.git.commit",
    "cap.script.run",
    "cap.tests.run",
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _repo(root: Path) -> None:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "V11 stack seam")
    _git(root, "config", "user.email", "v11-stack@example.invalid")
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'stack-seam'\nversion = '0.1'\n"
        "requires-python = '>=3.13'\n\n"
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8",
    )
    (root / "work.md").write_text(
        "TODO: investigate a bounded reliability improvement\n", encoding="utf-8",
    )
    _git(root, "add", "--all")
    _git(root, "commit", "-q", "-m", "fixture baseline")


def _seed(database: FoundryDatabase) -> None:
    digest = "a" * 64
    seed_owner_context(
        database,
        goals=[{
            "text": "Make JARVIS reliable and evidence driven",
            "source_ref": "IDEA.md",
            "source_hash": digest,
        }],
        projects=[{
            "text": "JARVIS Foundry",
            "source_ref": "docs/CURRENT_STATE.md",
            "source_hash": digest,
        }],
        standing_rejections=[{
            "text": "Do not execute discovered work during A2",
            "source_ref": "docs/handoffs/frozen-roadmap.md",
            "source_hash": digest,
        }],
    )
    seed_builtins(database)
    for capability_id in LOCAL_CAPABILITIES:
        record_outcome(
            database,
            capability_id,
            success=True,
            verified=VerifyVerdict.MATCH,
            note="disposable V11 stack seam proof",
            at="2026-09-03T00:00:00Z",
        )


def test_a2_research_fabric_project_and_verification_seams_stay_inert(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owner-project"
    _repo(root)
    database = FoundryDatabase(tmp_path / "foundry.db")
    assert database.initialize() == 40
    _seed(database)
    source = SourceSpec(
        "project-seam",
        "project",
        str(root),
        1,
        {
            "root": str(root),
            "project_id": "JARVIS-Foundry",
            "max_files": 20,
            "required_capabilities": list(LOCAL_CAPABILITIES),
            "proposed_operation": "modify",
        },
    )

    def research_reader(_database: FoundryDatabase, _candidate: dict) -> dict:
        return {
            "status": "COMPLETED",
            "source_evidence": [{
                "source_kind": "local",
                "source_ref": "repo://owner-project/work.md",
                "source_title": "Owner project work item",
                "statement": "The bounded TODO is a candidate for a local reliability review.",
                "stance": "SUPPORTING",
                "claim_key": "bounded_reliability_review",
                "confidence": 0.9,
                "retrieved_at": NOW,
                "fresh_until": NOW,
                "evidence_types": ["PROJECT_CONTEXT"],
                "outcome": "COMPLETED",
            }],
            "budget_used": 0.5,
        }

    first = run_initiative_a2_cycle(
        database,
        cycle_id="stack-seam",
        sources=[source],
        bounds=InitiativeA2Bounds(do_nothing_score=0.20),
        research_reader=research_reader,
        now=NOW,
    )
    assert first["outcome"] == "WAIT"
    assert first["metrics"]["candidates_researched"] == 1
    assert first["metrics"]["objective_creations"] == 0
    assert first["metrics"]["mission_creations"] == 0
    assert first["metrics"]["external_writes"] == 0
    assert first["decision"].candidate_id
    candidate_id = str(first["decision"].candidate_id)
    candidate = database.row("candidates", "candidate_id", candidate_id)
    assert candidate is not None

    # The queue has a dossier keyed by the v40 evidence revision, and scoring
    # exposes the same Fabric-only requirement set at the ELIGIBLE boundary.
    research_rows = database.all("research_queue")
    assert len(research_rows) == 1
    assert research_rows[0]["input_hash"]
    assert research_rows[0]["evidence_json"]
    selected = database.all("candidate_judgments")
    assert any(
        row["candidate_id"] == candidate_id and row["outcome"] == "ELIGIBLE"
        for row in selected
    )

    profile_service = ProjectProfileService(
        tmp_path / "profile-cache", clock=lambda: NOW,
    )
    target = ProjectTarget("JARVIS-Foundry", root)
    resolver = ProjectContextResolver(profile_service)
    seam_candidate = {
        "candidate_id": candidate_id,
        "objective_id": f"initiative-objective:{candidate_id}",
        "objective": str(candidate["objective"]),
        "target_project": "JARVIS-Foundry",
        "structured_outcome": {
            "summary": "The bounded reliability change has an observable Python behavior.",
            "project_type": "python",
            "task_kind": "code",
            "changed_paths": ["src/app.py", "tests/test_app.py"],
            "test_targets": ["tests/test_app.py::test_value"],
            "postconditions": [{
                "kind": "behavior",
                "target": "app.VALUE",
                "assertion": "the module exposes the expected typed value",
                "evidence": "independent targeted pytest receipt",
            }],
        },
    }
    context = resolver.for_a3(seam_candidate, target)
    replay_context = resolver.for_a3(seam_candidate, target)
    assert context["authority"] == "ADVISORY_READ_ONLY"
    assert context["execution"]["allowed"] is False
    assert context["execution"]["commands_executed"] is False
    assert context["profile_ref"]["profile_hash"] == replay_context["profile_ref"]["profile_hash"]
    assert replay_context["profile_ref"]["lifecycle"] == "UNCHANGED_REPLAY"

    plan = compile_verification_plan(seam_candidate, root, context)
    replay_plan = compile_verification_plan(seam_candidate, root, replay_context)
    assert plan.status == READY
    assert plan.project_context_status == "AVAILABLE"
    assert plan.tests_to_run[0].argv == (
        "python", "-m", "pytest", "tests/test_app.py::test_value",
    )
    assert plan.to_payload() == replay_plan.to_payload()
    assert all(
        item["execute"] is False
        for item in context["actionable_context"]["commands"]["test"]["items"]
    )

    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0
    assert not any(
        event.event_type.startswith(("effect.", "system.action_"))
        for event in database.events()
    )
