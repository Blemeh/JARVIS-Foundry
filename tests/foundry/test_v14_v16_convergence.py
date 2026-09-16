"""Focused contracts owned by the V14/V15/V16 convergence lane."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.connectors.web_research import (
    WebResearchError,
    browser_execution_authorization_from_environment,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import (
    FROZEN,
    discover_reconcilable_developer_effects,
    execute_fabric_effect,
)
from jarvis_foundry.fabric.recipes import load_recipe, validate_recipe


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def test_browser_authorization_config_is_explicit_and_default_off(tmp_path: Path) -> None:
    assert browser_execution_authorization_from_environment({}) is None
    config = tmp_path / "browser-authorization.json"
    config.write_text(json.dumps({
        "browser_execution_authorization": {
            "authorization_id": "authorization-demo-1",
            "site_origin": "http://127.0.0.1:8123",
            "owner_authorized": True,
            "dispatch_enabled": True,
            "allowed_actions": ["click", "fill", "submit"],
            "profile_id": "profile-demo",
            "owner_scope": "owner-disposable-demo",
            "credential_reference": "credential-browser-demo",
            "auth_marker": {"role": "heading", "name": "Signed in"},
            "allow_insecure_localhost": True,
        }
    }), encoding="utf-8")
    grant = browser_execution_authorization_from_environment({
        "JARVIS_BROWSER_AUTHORIZATION_FILE": str(config),
    })
    assert grant is not None
    assert grant.authorization_id == "authorization-demo-1"
    assert grant.site_origin == "http://127.0.0.1:8123"
    assert grant.owner_authorized is True and grant.dispatch_enabled is True
    assert grant.auth_marker == ("heading", "Signed in")

    config.write_text(json.dumps({"site_origin": "https://example.invalid"}), encoding="utf-8")
    with pytest.raises(WebResearchError):
        browser_execution_authorization_from_environment({
            "JARVIS_BROWSER_AUTHORIZATION_FILE": str(config),
        })


def test_digital_operator_recipe_uses_integrated_actions_and_commits() -> None:
    recipe = load_recipe("v14.digital_operator.close")
    assert validate_recipe(recipe) == []
    actions = [step.action_kind for step in recipe.steps]
    assert actions == [
        "browser.web.read",
        "file.write",
        "file.read",
        "desktop.open_artifact_and_assert",
        "git.inspect",
        "git.commit",
        "git.log",
        "git.push",
    ]
    opened = next(step for step in recipe.steps if step.step_id == "open")
    assert opened.payload["expected"] == "$readback.text"
    committed = next(step for step in recipe.steps if step.step_id == "commit")
    assert committed.payload["existing"] is True
    assert committed.payload["paths"] == ["$compose.target"]


def test_frozen_developer_effect_is_discoverable_with_original_intent(tmp_path: Path) -> None:
    database = _database(tmp_path)

    def ambiguous() -> dict[str, object]:
        raise TimeoutError("provider outcome unknown")

    result = execute_fabric_effect(
        database,
        action_key="effect-git-push-demo",
        system_id="git",
        target_ref="repo:demo",
        payload_hash="payload-hash-demo",
        kind="git.push",
        connector_id="fabric:cap.git.push",
        request_identity={
            "effect_id": "effect-git-push-demo",
            "recovery_intent": {
                "action_kind": "git.push",
                "reconciliation_action": "git.push.reconcile",
                "action_id": "push-demo",
                "remote": "origin",
                "branch": "main",
                "expected_head": "a" * 40,
                "request": {
                    "action_id": "push-demo",
                    "remote": "origin",
                    "branch": "main",
                },
            },
        },
        perform=ambiguous,
    )
    assert result["journal_state"] == FROZEN
    candidates = discover_reconcilable_developer_effects(database)
    assert len(candidates) == 1
    assert candidates[0]["action_key"] == "effect-git-push-demo"
    assert candidates[0]["reconciliation_action"] == "git.push.reconcile"
    assert candidates[0]["intent"]["expected_head"] == "a" * 40
