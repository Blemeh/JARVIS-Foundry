"""V12 capability-solution discovery: reuse order, evidence, and no effects."""
from __future__ import annotations

import json
from pathlib import Path

from jarvis_foundry.capability_solution_discovery import (
    CANDIDATE_RECORDED,
    COMPARISON_RECORDED,
    REQUIREMENT_RECORDED,
    RECOMMENDATION_RECORDED,
    CapabilityGapRequirement,
    CapabilitySolutionDiscovery,
    CapabilitySolutionStore,
    CompatibilityRequirements,
    ReuseTier,
    SearchState,
    compare_solution_candidates,
    normalize_solution_candidate,
    search_external_solutions,
    search_fabric_catalog,
    search_git_archaeology,
    search_repository_source,
)
from jarvis_foundry.capability_requirements import resolve_capability_requirements
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.catalog import record_outcome
from jarvis_foundry.fabric.model import VerifyVerdict


def _gap(action: str, *, cap: str | None = None) -> CapabilityGapRequirement:
    return CapabilityGapRequirement(
        gap_id=f"gapreq-{action.replace('.', '-')}",
        requirement_set_id="capreq-test",
        requested_action=action,
        missing_capability_id=cap or f"cap.{action}",
        description=f"A real mission is blocked by missing {action}",
        required_actions=(action,),
        required_capability_ids=(cap or f"cap.{action}",),
        compatibility=CompatibilityRequirements(),
        candidate_identity={"candidate_id": "candidate-test", "candidate_type": "repository_improvement"},
    )


def test_requirement_is_durable_and_round_trips(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    requirement = _gap("mcp.client.invoke")
    CapabilitySolutionStore.record_requirement(db, requirement)
    CapabilitySolutionStore.record_requirement(db, requirement)
    rows = CapabilitySolutionStore.requirements(db)
    assert len(rows) == 1
    assert rows[0].as_dict() == requirement.as_dict()
    assert db.event_count(event_type=REQUIREMENT_RECORDED) == 1


def test_requirement_revision_is_append_only_and_replays_latest(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    original = _gap("windows.uia.click")
    CapabilitySolutionStore.record_requirement(db, original)
    revised_payload = original.as_dict()
    revised_payload["description"] = "The owner mission now requires a bounded UIA click readback."
    revised = CapabilityGapRequirement.from_mapping(revised_payload)
    CapabilitySolutionStore.record_requirement(db, revised)
    assert db.event_count(event_type=REQUIREMENT_RECORDED) == 2
    assert CapabilitySolutionStore.requirements(db, gap_id=original.gap_id) == (revised,)


def test_requirement_set_projects_only_real_missing_gap(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed_builtins(db)
    requirement_set = resolve_capability_requirements(
        db,
        {"candidate_id": "candidate-uia", "candidate_type": "test_opportunity"},
        action_metadata=[{"capability_id": "cap.windows.uia.execute"}],
    )
    gaps = CapabilityGapRequirement.from_requirement_set(requirement_set)
    assert len(gaps) == 1
    assert gaps[0].missing_capability_id == "cap.windows.uia.execute"
    assert gaps[0].required_actions == ()
    assert gaps[0].required_capability_ids == ("cap.windows.uia.execute",)


def test_fabric_catalog_is_first_and_can_cover_a_real_gap(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    seed_builtins(db)
    record_outcome(db, "cap.artifact.pdf.copy", success=True, verified=VerifyVerdict.MATCH, note="disposable readback")
    requirement = _gap("artifact.pdf.copy")
    candidates, report = search_fabric_catalog(db, requirement)
    assert report.state is SearchState.COMPLETED
    assert candidates and candidates[0].tier is ReuseTier.FABRIC_CATALOG
    assert candidates[0].coverage.matched_actions == ("artifact.pdf.copy",)
    updated, comparison, recommendation = compare_solution_candidates(requirement, candidates)
    assert comparison.winner_candidate_id == updated[0].candidate_id
    assert recommendation.status == "RECOMMENDED"
    assert recommendation.selected_candidate_id == updated[0].candidate_id


def test_source_and_history_searches_are_real_and_bounded() -> None:
    root = Path(__file__).resolve().parents[2]
    requirement = _gap("mcp.client.invoke")
    source_candidates, source_report = search_repository_source(requirement, root, max_files=80)
    history_candidates, history_report = search_git_archaeology(
        requirement, root, max_refs=12, max_worktrees=12, max_commits=12, max_heads=3, max_matches=12,
    )
    assert source_report.inspected.get("bounded") is True
    assert history_report.inspected.get("searcher") == "git-archaeology"
    assert history_report.inspected.get("commands")
    assert history_report.evidence[0]["operations_reuse_used"] is False
    assert source_report.read_only and history_report.read_only
    assert all(item.installed is False and item.registered is False for item in (*source_candidates, *history_candidates))


def test_external_search_uses_existing_github_research_and_data_only(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    requirement = _gap("mcp.client.invoke")

    def transport(endpoint: str) -> tuple[int, bytes]:
        if endpoint.startswith("/search/repositories"):
            return 200, json.dumps({"items": [{
                "full_name": "modelcontextprotocol/python-sdk",
                "name": "python-sdk",
                "description": "Official Python SDK for the Model Context Protocol",
                "html_url": "https://github.com/modelcontextprotocol/python-sdk",
                "language": "Python",
                "stargazers_count": 9000,
                "forks_count": 900,
                "open_issues_count": 80,
                "license": {"spdx_id": "MIT"},
                "archived": False,
                "pushed_at": "2026-08-30T00:00:00Z",
            }]}).encode()
        return 200, json.dumps({
            "full_name": "modelcontextprotocol/python-sdk",
            "language": "Python",
            "license": {"spdx_id": "MIT"},
            "stargazers_count": 9000,
            "forks_count": 900,
            "open_issues_count": 80,
            "archived": False,
            "pushed_at": "2026-08-30T00:00:00Z",
        }).encode()

    candidates, report = search_external_solutions(
        db, requirement, api_transport=transport, record_claims=True,
    )
    assert report.state is SearchState.COMPLETED
    assert candidates and candidates[0].tier is ReuseTier.EXTERNAL
    assert candidates[0].authority == "DATA_ONLY"
    assert candidates[0].data_only is True
    assert candidates[0].coverage.matched_actions == ("mcp.client.invoke",)
    assert candidates[0].maturity.state in {"PROVEN", "MATURE"}
    assert db.event_count(event_type="knowledge.claim_recorded") == 1


def test_external_metadata_coercion_is_bounded_and_fail_closed(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    requirement = _gap("mcp.client.invoke")

    def transport(endpoint: str) -> tuple[int, bytes]:
        body = {
            "items": [{
                "full_name": "modelcontextprotocol/python-sdk",
                "description": "Model Context Protocol Python SDK",
                "language": "Python",
                "stargazers_count": "not-a-number",
                "forks_count": None,
                "open_issues_count": object.__class__.__name__,
                "archived": "false",
                "license": {"spdx_id": "MIT"},
            }],
        } if endpoint.startswith("/search/repositories") else {
            "full_name": "modelcontextprotocol/python-sdk",
            "language": "Python",
            "stargazers_count": "not-a-number",
            "forks_count": None,
            "open_issues_count": "not-a-number",
            "archived": "false",
            "license": {"spdx_id": "MIT"},
        }
        return 200, json.dumps(body).encode()

    candidates, report = search_external_solutions(
        db, requirement, api_transport=transport, record_claims=False,
    )
    assert report.state is SearchState.COMPLETED
    assert len(candidates) == 1
    assert candidates[0].maturity.stars == 0
    assert candidates[0].maturity.archived is False


def test_comparison_rejects_custom_and_explains_reuse_order(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    requirement = _gap("mcp.client.invoke")
    external = normalize_solution_candidate(requirement, {
        "name": "Official MCP Python SDK",
        "solution_kind": "sdk",
        "locator": "github:modelcontextprotocol/python-sdk",
        "version": "main",
        "coverage": {"matched_actions": ["mcp.client.invoke"], "confidence": 0.8},
        "compatibility": {"state": "COMPATIBLE", "score": 0.85},
        "maturity": {"state": "MATURE", "score": 0.85, "stars": 9000, "license": "MIT"},
        "maintenance": {"state": "ACTIVE", "score": 0.9},
        "evidence": [{"ref": "https://api.github.com/repos/modelcontextprotocol/python-sdk"}],
    }, tier=ReuseTier.EXTERNAL)
    custom = normalize_solution_candidate(requirement, {
        "name": "Custom placeholder", "solution_kind": "custom_build",
        "locator": "custom:test", "coverage": {"matched_actions": ["mcp.client.invoke"]},
        "compatibility": {"state": "PARTIAL", "score": 0.6},
        "maturity": {"state": "UNKNOWN", "score": 0.0},
        "maintenance": {"state": "UNKNOWN", "score": 0.0},
        "evidence": [{"ref": "custom"}],
    }, tier=ReuseTier.CUSTOM_BUILD)
    updated, comparison, recommendation = compare_solution_candidates(requirement, (external, custom))
    assert recommendation.selected_candidate_id == external.candidate_id
    custom_row = next(item for item in updated if item.tier is ReuseTier.CUSTOM_BUILD)
    assert custom_row.disposition.value == "DEFERRED"
    assert "CUSTOM_BUILD_LAST_RESORT" in custom_row.rejection_reasons
    assert any(reason.startswith("REUSE_ORDER_DEFERRED_TO:") for reason in custom_row.rejection_reasons)
    assert comparison.decision == "RECOMMEND"


def test_service_persists_all_discovery_records_without_registration(tmp_path: Path) -> None:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    requirement = _gap("mcp.client.invoke")

    def transport(endpoint: str) -> tuple[int, bytes]:
        if endpoint.startswith("/search/repositories"):
            body = {"items": [{
                "full_name": "modelcontextprotocol/python-sdk",
                "description": "Official Python SDK for the Model Context Protocol",
                "language": "Python", "stargazers_count": 9000, "forks_count": 900,
                "license": {"spdx_id": "MIT"}, "archived": False,
                "pushed_at": "2026-08-30T00:00:00Z",
            }]}
        else:
            body = {"full_name": "modelcontextprotocol/python-sdk", "language": "Python", "stargazers_count": 9000, "forks_count": 900, "license": {"spdx_id": "MIT"}, "archived": False, "pushed_at": "2026-08-30T00:00:00Z"}
        return 200, json.dumps(body).encode()

    run = CapabilitySolutionDiscovery(db, repo_root=Path(__file__).resolve().parents[2], api_transport=transport).discover(
        requirement, search_all_tiers=True, record_claims=False,
    )
    assert run.recommendation.status == "RECOMMENDED"
    assert run.recommendation.selected_candidate_id
    assert db.event_count(event_type=REQUIREMENT_RECORDED) == 1
    assert db.event_count(event_type=CANDIDATE_RECORDED) == len(run.candidates)
    assert db.event_count(event_type=COMPARISON_RECORDED) == 1
    assert db.event_count(event_type=RECOMMENDATION_RECORDED) == 1
    assert not any(event.event_type.startswith("fabric.capability.registered") for event in db.events(readonly=True))
    assert all(not item.installed and not item.registered and not item.execution_authorized for item in run.candidates)
    replayed = CapabilitySolutionStore.candidates(db, gap_id=requirement.gap_id)
    assert len(replayed) == len(run.candidates)
    assert {item.candidate_id for item in replayed} == {item.candidate_id for item in run.candidates}
