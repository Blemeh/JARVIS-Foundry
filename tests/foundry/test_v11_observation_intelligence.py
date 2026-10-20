"""Focused proof for V11 source intelligence; all source activity is read-only."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_sources import (
    SourceObservation,
    SourceReadResult,
    SourceSpec,
    content_hash,
    read_github_source,
    read_project_source,
    read_web_source,
    read_world_source,
    run_source_observation_cycle,
)
from jarvis_foundry.events import Event
from jarvis_foundry.world_model.entities import WorldEntityStore
from jarvis_foundry.world_model.gaps import WorldKnowledgeGapStore
from jarvis_foundry.world_model.patterns import WorldPatternStore
from jarvis_foundry.world_model.relationships import WorldRelationshipStore
from jarvis_foundry.world_model.signals import WorldSignalStore
from jarvis_foundry.executive.intelligence import (
    _with_default_world_context_source,
    project_roots_from_sources,
)


def _db(tmp_path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "observation-intelligence.db")
    assert database.initialize() == 41
    return database


def _start(database: FoundryDatabase, cycle_id: str) -> None:
    database.append(Event.create(
        "initiative.cycle_started", {"cycle_id": cycle_id, "status": "RUNNING"},
        source_type="test", source_id="observation-intelligence",
        idempotency_key=f"initiative.cycle_started:{cycle_id}",
    ))


class _GitHubReadOnly:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.issue_updated_at = "2026-09-02T00:00:00Z"
        self.issue_state = "open"

    def read_api(self, action, endpoint):
        self.calls.append((action, endpoint))
        attempt = SimpleNamespace(outcome="COMPLETED", error_code=None)
        if endpoint == "/repos/owner/runtime":
            return attempt, {
                "default_branch": "main", "open_issues_count": 2,
                "updated_at": "2026-09-02T00:00:00Z", "visibility": "public",
            }
        if "/commits?" in endpoint:
            return attempt, [{
                "sha": "a" * 40, "html_url": "https://github.com/owner/runtime/commit/a",
                "commit": {"message": "release runtime", "committer": {"date": "2026-09-02T00:00:00Z"}},
            }]
        if "/issues?" in endpoint:
            return attempt, [
                {"number": 7, "title": "Retry gateway timeout", "body": "Fails on timeout",
                 "state": self.issue_state, "updated_at": self.issue_updated_at,
                 "labels": [{"name": "bug"}],
                 "html_url": "https://github.com/owner/runtime/issues/7"},
                {"number": 8, "title": "Retry gateway error", "body": "Fails again",
                 "state": "open", "updated_at": self.issue_updated_at,
                 "labels": [{"name": "bug"}],
                 "html_url": "https://github.com/owner/runtime/issues/8"},
            ]
        if "/pulls?" in endpoint:
            return attempt, [{
                "number": 3, "title": "Harden retries", "state": "open", "draft": False,
                "head": {"sha": "b" * 40}, "base": {"sha": "a" * 40},
                "updated_at": "2026-09-02T00:00:00Z",
                "html_url": "https://github.com/owner/runtime/pull/3",
            }]
        if "/actions/runs" in endpoint:
            return attempt, {"workflow_runs": [
                {"id": 10, "name": "CI", "conclusion": "failure", "head_sha": "a" * 40,
                 "created_at": "2026-09-02T00:00:00Z"},
                {"id": 11, "name": "CI", "conclusion": "failure", "head_sha": "b" * 40,
                 "created_at": "2026-09-02T01:00:00Z"},
            ]}
        if "/releases?" in endpoint:
            return attempt, [{
                "tag_name": "v3.14.1", "name": "Runtime 3.14.1",
                "body": "Security and compatibility fixes", "draft": False,
                "prerelease": False, "published_at": "2026-09-01T00:00:00Z",
                "html_url": "https://github.com/owner/runtime/releases/v3.14.1",
            }]
        if "/tags?" in endpoint:
            return attempt, [{"name": "v3.14.1", "commit": {"sha": "a" * 40}}]
        if "/git/trees/" in endpoint:
            return attempt, {"tree": [
                {"path": "pyproject.toml", "type": "blob", "sha": "c" * 40},
                {"path": "README.md", "type": "blob", "sha": "d" * 40},
            ]}
        raise AssertionError(endpoint)


def test_github_project_intelligence_is_bounded_structured_and_read_only():
    connector = _GitHubReadOnly()
    spec = SourceSpec("github-runtime", "github", "owner/runtime", config={
        "max_items": 5, "affects_current_runtime": True,
        "target_project": "JARVIS", "now": "2026-09-02T12:00:00Z",
    })
    first = read_github_source(spec, connector=connector)
    kinds = {item.payload["kind"] for item in first.observations}
    assert first.status == "READY"
    assert {"repository", "default_branch_state", "commit", "issue", "pull_request", "release", "tags",
            "workflow_run", "recurring_problem", "dependency_or_config"} <= kinds
    assert all(action.endswith(".read") for action, _endpoint in connector.calls)
    assert len(connector.calls) == 8
    assert any(item.candidate_type == "github_release" and item.proposed_outcome
               for item in first.observations)

    issue_before = next(item for item in first.observations
                        if item.source_identity.endswith(":issue:7"))
    connector.issue_updated_at = "2026-09-02T05:00:00Z"
    second = read_github_source(spec, connector=connector)
    issue_after = next(item for item in second.observations
                       if item.source_identity.endswith(":issue:7"))
    assert issue_after.content_hash == issue_before.content_hash
    assert issue_after.raw_content_hash != issue_before.raw_content_hash


def test_github_issue_timestamp_noise_is_suppressed_but_close_is_a_delta(tmp_path):
    database = _db(tmp_path)
    connector = _GitHubReadOnly()
    spec = SourceSpec("github-runtime", "github", "owner/runtime", 1, {
        "max_items": 5, "now": "2026-09-02T12:00:00Z",
    })
    reader = lambda source, stop: read_github_source(
        source, connector=connector, stop_check=stop,
    )
    base = datetime(2026, 9, 2, tzinfo=timezone.utc)
    _start(database, "github-1")
    cold = run_source_observation_cycle(
        database, "github-1", [spec], now=base, readers={"github": reader},
    )
    assert cold["observations_recorded"] > 0

    connector.issue_updated_at = "2026-09-02T05:00:00Z"
    _start(database, "github-2")
    timestamp_only = run_source_observation_cycle(
        database, "github-2", [spec], now=base + timedelta(seconds=1),
        readers={"github": reader},
    )
    assert timestamp_only["observations_recorded"] == 0
    assert timestamp_only["source_digest"]["noise_suppressed"] >= 1

    connector.issue_state = "closed"
    _start(database, "github-3")
    closed = run_source_observation_cycle(
        database, "github-3", [spec], now=base + timedelta(seconds=2),
        readers={"github": reader},
    )
    issue_delta = next(
        row for row in closed["source_digest"]["meaningful_changes"]
        if row["source_identity"].endswith(":issue:7")
    )
    assert issue_delta["change_type"] == "RESOLVED"
    assert issue_delta["changed_fields"] == ["state"]
    assert issue_delta["candidate_worthy"] is False


def test_web_meaningful_text_sections_and_formatting_noise_suppression(tmp_path):
    database = _db(tmp_path)
    source_sha = {"value": "a" * 64}
    text = {"value": "# Release 3.14.1\nPublished 2026-09-02\nFixes runtime crashes."}

    class Browser:
        def read(self, *_args, **_kwargs):
            return SimpleNamespace()

    class Extractor:
        def extract(self, *_args, **_kwargs):
            return SimpleNamespace(
                success=True,
                page=SimpleNamespace(final_url="https://watch.example/releases", source_sha256=source_sha["value"]),
                metadata={"title": "Runtime releases"}, text=text["value"],
                safe_text_sha256="f" * 64,
                attempt=SimpleNamespace(outcome="COMPLETED", error_code=None),
            )

    spec = SourceSpec("web-runtime", "web", "watch:runtime", 60, config={
        "urls": ["https://watch.example/releases"],
        "allowed_hosts": ["watch.example"],
        "proposed_outcomes": {"https://watch.example/releases": "Review runtime release compatibility"},
        "candidate_on": "change_only", "target_project": "JARVIS",
    })

    def reader(_spec, _stop):
        return read_web_source(spec, browser=Browser(), extractor=Extractor())

    first_read = reader(spec, lambda: False).observations[0]
    assert first_read.payload["versions"] == ["3.14.1"]
    assert first_read.payload["dates"] == ["2026-09-02"]
    assert first_read.payload["sections"]

    base = datetime(2026, 9, 2, tzinfo=timezone.utc)
    _start(database, "web-1")
    cold = run_source_observation_cycle(database, "web-1", [spec], now=base,
                                        readers={"web": reader})
    assert cold["observations_recorded"] == 1
    assert cold["candidates_created"] == 0  # baseline, not a change

    source_sha["value"] = "b" * 64
    text["value"] = "# Release 3.14.1\n\nPublished   2026-09-02\n\nFixes runtime crashes."
    second_read = reader(spec, lambda: False).observations[0]
    assert second_read.content_hash == first_read.content_hash
    _start(database, "web-2")
    replay = run_source_observation_cycle(
        database, "web-2", [spec], now=base + timedelta(seconds=60), readers={"web": reader},
    )
    assert replay["observations_recorded"] == 0
    assert replay["source_digest"]["noise_suppressed"] == 1
    assert replay["source_digest"]["candidate_worthy_signals"] == []


def test_local_project_intelligence_reports_state_files_indicators_and_artifact_drift(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "sensor@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Sensor"], check=True)
    package = root / "package.json"
    package.write_text('{"dependencies":{"alpha":"1.0"}}\n', encoding="utf-8")
    source = root / "source.txt"
    source.write_text("new source\n", encoding="utf-8")
    artifact = root / "artifact.txt"
    artifact.write_text("old artifact\n", encoding="utf-8")
    cache = root / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    (cache / "lastfailed").write_text('{"tests/test_one.py::test_one": true}', encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "package.json", "source.txt", "artifact.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True)
    source.write_text("newer source\n", encoding="utf-8")

    spec = SourceSpec("local", "project", str(root), config={
        "root": str(root), "project_id": "project", "max_files": 30,
        "generated_artifacts": {"artifact.txt": ["source.txt"]},
    })
    first = read_project_source(spec)
    kinds = {item.payload["kind"] for item in first.observations}
    assert {"git_project_state", "dependency_file", "project_indicator",
            "generated_artifact", "project_scan"} <= kinds
    assert next(item for item in first.observations
                if item.payload["kind"] == "git_project_state").payload["state"] == "DIRTY"
    assert next(item for item in first.observations
                if item.payload["kind"] == "project_indicator").payload["failure_count"] == 1

    dependency_before = next(item for item in first.observations
                             if item.payload["kind"] == "dependency_file")
    package.write_text('{\n  "dependencies": { "alpha": "1.0" }\n}\n', encoding="utf-8")
    second = read_project_source(spec)
    dependency_after = next(item for item in second.observations
                            if item.payload["kind"] == "dependency_file")
    assert dependency_after.content_hash == dependency_before.content_hash
    assert dependency_after.raw_content_hash != dependency_before.raw_content_hash


def test_world_model_intelligence_separates_epistemic_and_capability_gaps(tmp_path):
    database = _db(tmp_path)
    entities = WorldEntityStore(database)
    first = entities.record(
        "KNOWLEDGE_CLAIM", "Runtime is compatible", entity_id="fact-runtime-old",
        state="STALE", authority="VERIFIED_EVIDENCE", stale_after="2026-09-01T00:00:00Z",
        provenance={"evidence": "verifier:1"}, confidence=0.9,
    )
    second = entities.record(
        "KNOWLEDGE_CLAIM", "Runtime is incompatible", entity_id="fact-runtime-new",
        authority="VERIFIED_EVIDENCE", provenance={"evidence": "verifier:2"}, confidence=0.9,
    )
    WorldRelationshipStore(database).record(
        first, second, "CONTRADICTS", relationship_id="contradiction-runtime",
        provenance={"authority": "VERIFIED_EVIDENCE", "verified": True}, confidence=0.9,
    )
    gaps = WorldKnowledgeGapStore(database)
    gaps.record("UNKNOWN_CURRENT_VERSION", "Which runtime version is installed?", gap_id="gap-version", blocking=True)
    gaps.record("UNKNOWN_CAPABILITY", "Need a calendar adapter", gap_id="gap-capability", blocking=True)
    patterns = WorldPatternStore(database)
    patterns.record("PROVIDER_FAILURE", "Provider repeatedly times out", evidence=["obs-1"], confidence=0.8)
    patterns.record("PROVIDER_FAILURE", "Provider repeatedly times out", evidence=["obs-2"], confidence=0.8)

    result = read_world_source(
        SourceSpec("world", "world", "world:verified", config={"now": "2026-09-02T00:00:00Z"}),
        store=WorldSignalStore(database),
    )
    kinds = [item.payload["kind"] for item in result.observations]
    assert "stale_assumption" in kinds
    assert "contradiction" in kinds
    assert "repeated_failure" in kinds
    gap_rows = [item.payload for item in result.observations
                if item.payload["kind"] == "unresolved_unknown"]
    assert {row["gap_domain"] for row in gap_rows} == {"EPISTEMIC", "FABRIC_CAPABILITY"}
    assert next(row for row in gap_rows if row["gap_id"] == "gap-capability")["epistemic_gap"] is False


def test_default_executive_source_includes_world_context_without_promoting_history(tmp_path):
    database = _db(tmp_path)
    source_root = tmp_path / "project"
    source_root.mkdir()
    project = SourceSpec(
        "project:jarvis", "project", str(source_root),
        config={"root": str(source_root), "project_id": "jarvis"},
    )
    sources = _with_default_world_context_source((project,))
    world = next(source for source in sources if source.source_kind == "world")

    assert [source.source_kind for source in sources] == ["project", "world"]
    assert _with_default_world_context_source(sources) == sources
    assert project_roots_from_sources(sources) == {"jarvis": str(source_root)}

    WorldEntityStore(database).record(
        "MISSION", "old mission summary", entity_id="mission-old",
        state="STALE", authority="SYSTEM_OBSERVATION",
        provenance={"verified": True},
    )
    patterns = WorldPatternStore(database)
    patterns.record("MISSION_FAILURE", "old mission failed", evidence=["mission-old"], confidence=0.8)
    patterns.record("MISSION_FAILURE", "old mission failed", evidence=["mission-old-2"], confidence=0.8)
    WorldSignalStore(database).record(
        "OPPORTUNITY", "Current owner verified opportunity",
        summary="A current action is explicitly authorized.",
        affected_entities=["project:jarvis"], evidence=["owner-evidence:current"],
        provenance={"verified": True, "authority": "OWNER_AUTHORITY"},
        recommended_action="Inspect the current registered project's active objective.",
    )
    WorldSignalStore(database).record(
        "FOLLOW_UP", "Unverified historical context",
        evidence=["old-record:context"],
        provenance={"authority": "DATA_ONLY"},
        recommended_action="Do not use unverified historical context as new work.",
    )

    result = read_world_source(world, store=WorldSignalStore(database))
    candidates = [item for item in result.observations if item.candidate_on == "always"]
    assert [(item.candidate_type, item.proposed_outcome) for item in candidates] == [
        ("world_signal", "Inspect the current registered project's active objective."),
    ]
    assert all(item.payload.get("kind") != "stale_assumption" for item in candidates)
    assert not any(item.candidate_type == "world_stale_assumption" for item in result.observations)
    assert not any(item.candidate_type == "world_repeated_failure" for item in result.observations)
    assert not any(
        item.payload.get("title") == "Unverified historical context"
        for item in result.observations
    )

    _start(database, "world-context")
    cycle = run_source_observation_cycle(
        database, "world-context", sources, now=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert cycle["candidates_created"] == 1
    assert database.count("candidates") == 1
    with database.connect(readonly=True) as connection:
        executable_events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type IN "
            "('mission.created','executive.objective_recorded','effect.requested')"
        ).fetchone()[0]
    assert executable_events == 0


def test_digest_correlates_related_sources_without_duplicate_candidate(tmp_path):
    database = _db(tmp_path)
    base = datetime(2026, 9, 2, tzinfo=timezone.utc)
    opportunity = "project:JARVIS:runtime-compatibility"

    def observation(kind: str) -> SourceObservation:
        payload = {"kind": f"{kind}_change", "version": "3.14.1"}
        return SourceObservation(
            kind, f"{kind}:runtime-release", f"https://{kind}.example/runtime",
            payload, content_hash(payload), authority="DATA_ONLY",
            proposed_outcome="Review runtime 3.14.1 compatibility for JARVIS",
            target_project="JARVIS", alias_key=opportunity,
            candidate_type="runtime_release", confidence=0.9,
            opportunity_key=opportunity, significance_hint="runtime release changed",
        )

    readers = {
        "github": lambda _spec, _stop: SourceReadResult("READY", (observation("github"),)),
        "web": lambda _spec, _stop: SourceReadResult("READY", (observation("web"),)),
    }
    sources = [
        SourceSpec("github", "github", "owner/runtime", 60),
        SourceSpec("gmail-blocked", "gmail", "gmail:unconfigured", 60),
        SourceSpec("web", "web", "watch:runtime", 60),
    ]
    _start(database, "correlated")
    result = run_source_observation_cycle(
        database, "correlated", sources, now=base, readers=readers,
    )
    digest = result["source_digest"]
    assert result["candidates_created"] == 1
    assert result["duplicates_suppressed"] == 1
    assert {row["opportunity_key"] for row in digest["candidate_worthy_signals"]} == {opportunity}
    assert digest["blocked_sources"] == [{
        "source_identity": "gmail:unconfigured", "status": "BLOCKED",
        "reason": "Gmail observation is deferred; no configured read capability",
    }]
    assert database.count("candidates") == 1
    assert database.count("candidate_sources") == 2
    with database.connect(readonly=True) as connection:
        forbidden = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type IN "
            "('mission.created','executive.objective_recorded','effect.requested')"
        ).fetchone()[0]
    assert forbidden == 0
