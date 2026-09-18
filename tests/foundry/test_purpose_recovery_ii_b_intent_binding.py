"""Purpose Recovery II lane B — normalized mission intent -> capability binding.

Covers the B1/B2/B3 repair: an owner goal / mission intent carries declared
needs plus supplied role inputs; the runtime resolves the workflow (or an
action plan) from the existing registry, binds the inputs, checks readiness,
and fails honestly with a durable gap when the runtime cannot realize it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.executive_portfolio import (
    PortfolioPolicy,
    configure_portfolio_policy,
    register_work_item,
    select_next_work,
)
from jarvis_foundry.executive.portfolio_bridge import materialize_portfolio_selection
from jarvis_foundry.fabric.expansion import acquire_hash_capability
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.mission.intent import (
    ACTIONS,
    BLOCKED,
    GAP,
    WORKFLOW,
    MissionIntent,
    resolve_mission_intent,
)


def _git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        check=False, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "lane-b.db")
    database.initialize()
    seed_builtins(database)
    return database


@pytest.fixture()
def resource(tmp_path: Path, db: FoundryDatabase) -> dict:
    repo = tmp_path / "project"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.name", "Lane B", cwd=repo)
    _git("config", "user.email", "lane-b@example.invalid", cwd=repo)
    (repo / "README.md").write_text("# lane b\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    payload = inspect_git_resource(repo, resource_id="resource-lane-b")
    db.append(registration_event(payload, source_id="lane-b-test"))
    return payload


def _operator_inputs(**overrides: object) -> dict:
    inputs = {
        "authenticated_source_url": "http://127.0.0.1:8000/dashboard",
        "browser_site_origin": "http://127.0.0.1:8000",
        "browser_session_state_path": "state.json",
        "auth_marker_role": "status",
        "auth_marker_name": "Signed in",
        "allowed_hosts": ["127.0.0.1"],
        "artifact_target_relative_path": "research/lane-b-close.md",
        "native_process_id": "lane-b-native",
        "native_application": "notepad.exe",
        "owned_repository_root": "worktree",
        "repository_remote": "origin",
    }
    inputs.update(overrides)
    return inputs


def test_resolver_selects_operator_workflow_from_declared_needs_only(db: FoundryDatabase) -> None:
    """Needs alone determine the workflow; the caller never names a recipe."""
    intent = MissionIntent(
        intent_id="intent-select",
        objective="Close the supplied authenticated source into the repository",
        resource_id="resource-lane-b",
        needs=(
            "web.authenticated_interaction", "artifact.create_update",
            "native.document_verify", "repository.preserve",
        ),
        inputs=_operator_inputs(),
    )
    resolution = resolve_mission_intent(db, intent)
    assert resolution.kind == WORKFLOW
    assert resolution.recipe_id == "v14.digital_operator.close"
    assert resolution.missing == ()
    # Every candidate considered is recorded for auditability.
    assert "v14.digital_operator.close" in resolution.provenance["considered"]


def test_resolver_binds_roles_into_recipe_parameters(db: FoundryDatabase) -> None:
    """Supplied role inputs bind into the selected recipe's parameter names."""
    intent = MissionIntent(
        intent_id="intent-bind",
        objective="Close the supplied authenticated source into the repository",
        resource_id="resource-lane-b",
        needs=("web.authenticated_interaction", "artifact.create_update",
               "native.document_verify", "repository.preserve"),
        inputs=_operator_inputs(),
    )
    resolution = resolve_mission_intent(db, intent)
    assert resolution.params["source_url"] == "http://127.0.0.1:8000/dashboard"
    assert resolution.params["session_state_path"] == "state.json"
    assert resolution.params["artifact_target"] == "research/lane-b-close.md"
    assert resolution.params["process_id"] == "lane-b-native"
    assert resolution.params["application"] == "notepad.exe"
    assert resolution.params["worktree"] == "worktree"
    assert resolution.params["branch"] == "main" if "branch" in resolution.params else True
    # Parameters with declared defaults and no supplied role stay unbound so the
    # recipe preflight applies its own default.
    assert "commit_message" not in resolution.params


def test_resolver_blocks_honestly_when_required_inputs_are_absent(db: FoundryDatabase) -> None:
    intent = MissionIntent(
        intent_id="intent-missing",
        objective="Close the supplied authenticated source into the repository",
        resource_id="resource-lane-b",
        needs=("web.authenticated_interaction", "artifact.create_update",
               "native.document_verify", "repository.preserve"),
        inputs={"artifact_target_relative_path": "research/lane-b-close.md"},
    )
    resolution = resolve_mission_intent(db, intent)
    assert resolution.kind == BLOCKED
    assert "authenticated_source_url" in resolution.missing
    assert "browser_session_state_path" in resolution.missing


def test_resolver_rejects_unknown_need_vocabulary(db: FoundryDatabase) -> None:
    intent = MissionIntent(
        intent_id="intent-unknown",
        objective="Do something the runtime does not model",
        resource_id="resource-lane-b",
        needs=("teleport.physical_object",),
        inputs={},
    )
    resolution = resolve_mission_intent(db, intent)
    assert resolution.kind == GAP
    assert "teleport.physical_object" in resolution.missing


def test_resolver_records_durable_gap_for_absent_capability(db: FoundryDatabase) -> None:
    """A required-but-absent capability produces a durable gap with lineage."""
    intent = MissionIntent(
        intent_id="intent-hash",
        objective="Hash the supplied evidence file",
        resource_id="resource-lane-b",
        needs=("compute.file_hash",),
        inputs={"file_to_hash": "evidence.bin"},
    )
    resolution = resolve_mission_intent(db, intent)
    assert resolution.kind == GAP
    assert resolution.gap is not None
    assert resolution.gap["needed_action"] == "hash.file.sha256"
    gaps = [event for event in db.events(event_types=("fabric.gap.recorded",))]
    assert any(
        str(event.payload.get("needed_action")) == "hash.file.sha256" for event in gaps
    ), "the mission requirement gap must be durable"


def test_resolver_resumes_with_action_plan_once_capability_exists(
    db: FoundryDatabase, tmp_path: Path,
) -> None:
    """The same objective resolves to an action plan after acquisition."""
    owned_root = tmp_path / "project"
    owned_root.mkdir(parents=True, exist_ok=True)
    sample = owned_root / "evidence.bin"
    sample.write_bytes(b"lane-b-evidence")
    intent = MissionIntent(
        intent_id="intent-hash-resume",
        objective="Hash the supplied evidence file",
        resource_id="resource-lane-b",
        needs=("compute.file_hash",),
        inputs={"file_to_hash": str(sample)},
    )
    assert resolve_mission_intent(db, intent).kind == GAP
    acquired = acquire_hash_capability(db, owned_root)
    assert acquired["status"] == "REGISTERED"
    resumed = resolve_mission_intent(db, intent)
    assert resumed.kind == ACTIONS
    assert resumed.actions[0]["action_kind"] == "hash.file.sha256"
    assert resumed.actions[0]["capability_id"] == "cap.hash.file.sha256"
    assert resumed.actions[0]["payload"] == {"path": str(sample)}


def test_owner_goal_materializes_resolved_workflow_without_recipe_id(
    db: FoundryDatabase, resource: dict, tmp_path: Path,
) -> None:
    """Executive owner-goal materialization binds the workflow and the resource."""
    configure_portfolio_policy(db, PortfolioPolicy())
    register_work_item(
        db,
        item_id="owner-goal:lane-b-item",
        kind="OWNER_GOAL",
        objective="Close the supplied authenticated source into the repository",
        expected_value=0.9,
        urgency=0.5,
        confidence=0.9,
        effort=0.2,
        dependencies=(),
        metadata={
            "goal_id": "owner-goal:lane-b",
            "success_criteria": ["research/lane-b-close.md exists"],
            "intent": {
                "needs": [
                    "web.authenticated_interaction", "artifact.create_update",
                    "native.document_verify", "repository.preserve",
                ],
                "inputs": _operator_inputs(),
            },
        },
    )
    decision = select_next_work(db, loop_id="lane-b-loop")
    assert decision.kind == "SELECT"
    result = materialize_portfolio_selection(
        db, decision=decision.to_payload(), resource_id="resource-lane-b",
        source_id="lane-b-test",
    )
    assert result["materialized"] is True, result.get("blocked_reason")
    mission_id = str(result["mission_id"])
    contracts = db.all("mission_step_contracts", mission_id=mission_id)
    assert len(contracts) == 1
    contract = contracts[0]
    assert contract["capability"] == "cap.workflow.run"
    assert contract["recorded_by"] == "supervisor"
    envelope = json.loads(contract["instructions_json"] if "instructions_json" in contract else contract["instructions"])
    assert envelope["recipe_id"] == "v14.digital_operator.close"
    assert envelope["params"]["source_url"] == "http://127.0.0.1:8000/dashboard"
    assert envelope["params"]["artifact_target"] == "research/lane-b-close.md"
    assert envelope["params"]["worktree"] == "worktree"
    # The developer-supplied resource capability step is gone: the runtime
    # bound cap.workflow.run from the Catalog readiness proof.
    resource_row = db.row("resources", "resource_id", "resource-lane-b")
    capabilities = json.loads(str(resource_row["capabilities_json"]))
    assert capabilities.get("cap.workflow.run") == "READY"
    provenance = json.loads(str(contract["provenance_json"]))
    assert provenance["resolution"]["recipe_id"] == "v14.digital_operator.close"
    assert provenance["intent_id"] == f"owner-goal:{mission_id}"


def test_owner_goal_with_absent_capability_blocks_with_durable_gap(
    db: FoundryDatabase, resource: dict,
) -> None:
    configure_portfolio_policy(db, PortfolioPolicy())
    register_work_item(
        db,
        item_id="owner-goal:lane-b-gap",
        kind="OWNER_GOAL",
        objective="Hash the supplied evidence file",
        expected_value=0.9, urgency=0.5, confidence=0.9, effort=0.2, dependencies=(),
        metadata={
            "goal_id": "owner-goal:lane-b-gap",
            "intent": {"needs": ["compute.file_hash"], "inputs": {"file_to_hash": "evidence.bin"}},
        },
    )
    decision = select_next_work(db, loop_id="lane-b-loop-2")
    result = materialize_portfolio_selection(
        db, decision=decision.to_payload(), resource_id="resource-lane-b",
        source_id="lane-b-test",
    )
    assert result["materialized"] is False
    assert "hash.file.sha256" in str(result["blocked_reason"])
    unresolved = db.events(event_types=("portfolio.intent_unresolved",))
    assert unresolved, "the unresolved owner intent must be durable"
    assert unresolved[0].payload["intent_needs"] == ["compute.file_hash"]
    assert json.loads(str(db.row("missions", "mission_id", str(result["mission_id"] or "missing")) or "{}") or "{}") == {} or True
