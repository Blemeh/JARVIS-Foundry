from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis_foundry.discovery_sources import SourceSpec, read_github_source
from jarvis_foundry.executive import intelligence


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/Blemeh/JARVIS-Foundry.git", "Blemeh/JARVIS-Foundry"),
        ("git@github.com:Blemeh/JARVIS-Foundry.git", "Blemeh/JARVIS-Foundry"),
        ("ssh://git@github.com/Blemeh/JARVIS-Foundry.git", "Blemeh/JARVIS-Foundry"),
        ("https://github.com.evil.example/Blemeh/JARVIS-Foundry.git", None),
        ("https://user:secret@github.com/Blemeh/JARVIS-Foundry.git", None),
        ("https://gitlab.com/Blemeh/JARVIS-Foundry.git", None),
    ],
)
def test_github_remote_is_normalized_without_accepting_credentials_or_other_hosts(
    remote: str,
    expected: str | None,
) -> None:
    assert intelligence._github_repository_from_remote(remote) == expected


def test_registered_project_adds_bounded_github_watch_and_keeps_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "foundry"
    root.mkdir()
    monkeypatch.setattr(
        intelligence,
        "assemble_project_inventory",
        lambda _database: [{"root": str(root), "resource_id": "foundry-resource"}],
    )
    monkeypatch.setattr(
        intelligence,
        "registered_project_rows",
        lambda _database: [{"resource_id": "foundry-resource"}],
    )
    monkeypatch.setattr(
        intelligence,
        "_github_repository_for_root",
        lambda _root: "Blemeh/JARVIS-Foundry",
    )

    sources = intelligence.observation_sources_from_registered_projects(object())

    project = next(source for source in sources if source.source_kind == "project")
    github = next(source for source in sources if source.source_kind == "github")
    assert github.source_identity == "Blemeh/JARVIS-Foundry"
    assert github.config["repo"] == "Blemeh/JARVIS-Foundry"
    assert github.config["target_project"] == "foundry-resource"
    assert github.poll_interval_s == 300
    assert intelligence.project_roots_from_sources(sources) == {
        "foundry-resource": str(root),
    }
    assert project.config["resource_id"] == "foundry-resource"


def test_registered_projects_deduplicate_github_watch_by_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(
        intelligence,
        "assemble_project_inventory",
        lambda _database: [
            {"root": str(first), "resource_id": "a-resource"},
            {"root": str(second), "resource_id": "b-resource"},
        ],
    )
    monkeypatch.setattr(
        intelligence,
        "registered_project_rows",
        lambda _database: [
            {"resource_id": "a-resource"},
            {"resource_id": "b-resource"},
        ],
    )
    monkeypatch.setattr(
        intelligence,
        "_github_repository_for_root",
        lambda _root: "Blemeh/JARVIS-Foundry",
    )

    sources = intelligence.observation_sources_from_registered_projects(object())

    github = [source for source in sources if source.source_kind == "github"]
    assert len(github) == 1
    assert github[0].config["target_project"] == "a-resource"


def test_explicit_registered_project_sources_are_augmented_by_the_executive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "foundry"
    root.mkdir()
    project = SourceSpec(
        source_id="project:foundry-resource",
        source_kind="project",
        source_identity=str(root),
        config={"root": str(root), "resource_id": "foundry-resource"},
    )
    monkeypatch.setattr(
        intelligence,
        "registered_project_rows",
        lambda _database: [{"resource_id": "foundry-resource"}],
    )
    monkeypatch.setattr(
        intelligence,
        "_github_repository_for_root",
        lambda _root: "Blemeh/JARVIS-Foundry",
    )

    sources = intelligence._with_registered_github_sources(object(), (project,))

    github = next(source for source in sources if source.source_kind == "github")
    assert github.config["target_project"] == "foundry-resource"
    assert intelligence.project_roots_from_sources(sources) == {
        "foundry-resource": str(root),
    }


class _GitHubFixture:
    def read_api(self, _action: str, endpoint: str):
        if endpoint == "/repos/Blemeh/JARVIS-Foundry":
            document = {"default_branch": "main", "visibility": "private"}
        elif "/issues?" in endpoint:
            document = [{
                "number": 17,
                "title": "Complete current owner task",
                "state": "open",
                "labels": [],
                "updated_at": "2026-09-26T00:00:00Z",
                "html_url": "https://github.com/Blemeh/JARVIS-Foundry/issues/17",
                "body": "A current registered project issue.",
            }]
        elif "/actions/runs?" in endpoint:
            document = {"workflow_runs": []}
        elif "/trees/" in endpoint:
            document = {"tree": []}
        else:
            document = []
        return SimpleNamespace(outcome="COMPLETED", error_code=None), document


class _WorkflowFailureFixture(_GitHubFixture):
    def __init__(self, updated_at: str) -> None:
        self.updated_at = updated_at

    def read_api(self, action: str, endpoint: str):
        if "/actions/runs?" in endpoint:
            return SimpleNamespace(outcome="COMPLETED", error_code=None), {
                "workflow_runs": [{
                    "id": 42,
                    "name": "windows-ci",
                    "conclusion": "failure",
                    "head_sha": "a" * 40,
                    "created_at": self.updated_at,
                    "updated_at": self.updated_at,
                    "html_url": "https://github.com/Blemeh/JARVIS-Foundry/actions/runs/42",
                }],
            }
        return super().read_api(action, endpoint)


def test_github_candidates_target_the_registered_local_project() -> None:
    spec = SourceSpec(
        source_id="github:foundry-resource",
        source_kind="github",
        source_identity="Blemeh/JARVIS-Foundry",
        poll_interval_s=300,
        config={
            "repo": "Blemeh/JARVIS-Foundry",
            "target_project": "foundry-resource",
        },
    )

    result = read_github_source(spec, connector=_GitHubFixture())

    assert result.status == "READY"
    issue = next(item for item in result.observations if item.candidate_type == "github_issue")
    assert issue.target_project == "foundry-resource"
    assert issue.authority == "DATA_ONLY"
    assert issue.proposed_outcome == "Evaluate and resolve GitHub issue #17: Complete current owner task"


@pytest.mark.parametrize(
    ("updated_at", "expected_candidate"),
    [
        ("2026-08-19T03:42:59Z", False),
        ("2026-09-25T03:42:59Z", True),
    ],
)
def test_github_workflow_failures_expire_before_candidate_generation(
    updated_at: str,
    expected_candidate: bool,
) -> None:
    spec = SourceSpec(
        source_id="github:foundry-resource",
        source_kind="github",
        source_identity="Blemeh/JARVIS-Foundry",
        poll_interval_s=300,
        config={
            "repo": "Blemeh/JARVIS-Foundry",
            "target_project": "foundry-resource",
            "now": "2026-09-26T12:00:00Z",
        },
    )

    result = read_github_source(
        spec,
        connector=_WorkflowFailureFixture(updated_at),
    )

    failures = [
        item for item in result.observations
        if item.candidate_type == "github_workflow_failure"
    ]
    assert bool(failures) is expected_candidate
