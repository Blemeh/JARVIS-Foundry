"""Focused proof of the post-V7 cross-lane authorities."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.connectors.account_activation import (
    AccountActivationManager,
    FakeCredentialStore,
    FakeGmailProvider,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.executive_portfolio import rebuild_portfolio_state
from jarvis_foundry.operations.procedure import load_procedure
from jarvis_foundry.postv7 import (
    execute_library_step,
    plan_library_procedure,
    procedure_eligibility,
    project_account_capabilities,
    record_library_outcome,
    select_library_work,
)
from jarvis_foundry.procedure_library.contracts import ProofStatus
from jarvis_foundry.procedure_library.definitions import build_catalog
from jarvis_foundry.resilience.core import rebuild_incidents


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _proven(catalog, *procedure_ids):
    result = catalog
    for procedure_id in procedure_ids:
        result = result.promote(
            procedure_id,
            status=ProofStatus.PROVEN_ONCE.value,
            last_verified="2026-08-24",
            verifier_passed=True,
            evidence_refs=(f"proof:{procedure_id}",),
        )
    return result


def test_account_readiness_projects_to_capability_and_blocks_until_handoff(
    db: FoundryDatabase,
) -> None:
    credentials = FakeCredentialStore()
    provider = FakeGmailProvider()
    manager = AccountActivationManager(db, credentials)
    manager.register(
        provider,
        account_identity="alice@example.invalid",
        credential_reference=None,
        requested_scopes=("gmail.readonly", "gmail.compose"),
        activation_id="activation-postv7-gmail",
    )
    blocked = project_account_capabilities(
        db,
        "activation-postv7-gmail",
        {"communication.draft": ("gmail.compose",), "evidence.verify": ()},
    )
    assert blocked["projected"][0]["state"] == "DEGRADED"
    definition = _proven(build_catalog(), "jarvis.communication-draft").get("jarvis.communication-draft")
    receipt = procedure_eligibility(
        db,
        definition,
        blocked["resource_id"],
        activation_id="activation-postv7-gmail",
    )
    assert receipt.eligible is False
    assert any("activation_readiness" in reason for reason in receipt.blocked_reasons)
    assert not any(event.event_type == "capability.issued" for event in db.events())

    credentials.put(
        "cred:gmail:postv7",
        provider="gmail",
        account_identity="alice@example.invalid",
        scopes=("gmail.readonly", "gmail.compose"),
    )
    manager = AccountActivationManager(db, credentials)
    manager.register(
        provider,
        account_identity="alice@example.invalid",
        credential_reference="cred:gmail:postv7",
        requested_scopes=("gmail.readonly", "gmail.compose"),
        activation_id="activation-postv7-ready",
    )
    manager.activate("activation-postv7-ready")
    manager.enable("activation-postv7-ready")
    projected = project_account_capabilities(
        db,
        "activation-postv7-ready",
        {"communication.draft": ("gmail.compose",), "evidence.verify": ()},
    )
    ready = procedure_eligibility(
        db,
        definition,
        projected["resource_id"],
        activation_id="activation-postv7-ready",
    )
    assert ready.eligible is True

    manager.revoke("activation-postv7-ready", reason="cross-lane isolation proof")
    revoked = project_account_capabilities(
        db,
        "activation-postv7-ready",
        {"communication.draft": ("gmail.compose",), "evidence.verify": ()},
    )
    assert all(item["state"] == "VETOED" for item in revoked["projected"])
    assert procedure_eligibility(
        db,
        definition,
        revoked["resource_id"],
        activation_id="activation-postv7-ready",
    ).eligible is False


def test_portfolio_binds_typed_procedure_and_records_outcome(
    db: FoundryDatabase,
) -> None:
    catalog = _proven(build_catalog(), "jarvis.bug-triage")
    definition = catalog.get("jarvis.bug-triage")
    selected = select_library_work(
        db,
        catalog,
        loop_id="postv7-loop-001",
        resource_id="disposable:postv7",
        domain="bug-triage",
    )
    decision = selected["decision"]
    assert decision.kind == "SELECT"
    assert any(
        event.event_type == "procedure.library.portfolio_selected"
        and event.payload.get("procedure_id") == definition.procedure_id
        for event in db.events()
    )
    plan = plan_library_procedure(
        db,
        definition,
        mission_id="mission-postv7-001",
        target="fixture:bug-triage",
        resource_id="disposable:postv7",
        evidence_refs=("evidence:postv7:before",),
    )
    assert plan.steps[0].effect_ticket.startswith("postv7:")
    assert any(event.event_type == "operation.procedure.created" for event in db.events())
    assert any(event.event_type == "procedure.library.bound" for event in db.events())

    def effect(step, *_args, **_kwargs):
        return {
            "state": "COMPLETED",
            "after_state": {"step": step.step_id, "hash": "after-proof"},
            "postcondition_results": {item: True for item in step.postconditions},
        }

    def verify(effect_result, *_args, **_kwargs):
        return {"verdict": "PASS", "verifier_id": "verifier-postv7-independent"}

    execute_library_step(
        db,
        plan=plan,
        step_id="orient",
        resource_id="disposable:postv7",
        verifier=verify,
        effect_executor=effect,
        before_state={"identity": "fixture:bug-triage"},
        evidence_refs=("evidence:postv7:orient",),
    )
    finished = execute_library_step(
        db,
        plan=plan,
        step_id="execute",
        resource_id="disposable:postv7",
        verifier=verify,
        effect_executor=effect,
        before_state={"identity": "fixture:bug-triage"},
        evidence_refs=("evidence:postv7:execute",),
    )
    assert finished["outcome"] == "COMPLETED"
    assert load_procedure(db, plan.procedure_id).terminal_outcome == "COMPLETED"
    assert record_library_outcome(
        db, decision=decision, mission_id="mission-postv7-001", status="PASSED",
        actual={"procedure_id": definition.procedure_id},
    )
    assert any(event.event_type == "portfolio.outcome_recorded" for event in db.events())
    assert not any(
        event.event_type == "procedure.library.maturity_evidence_recorded"
        and event.payload.get("maturity_status") == "TRUSTED"
        for event in db.events()
    )


def test_ambiguous_effect_freezes_and_creates_incident_without_self_certification(
    db: FoundryDatabase,
) -> None:
    catalog = _proven(build_catalog(), "jarvis.bug-triage", "jarvis.flake-diagnosis")
    definition = catalog.get("jarvis.bug-triage")
    plan = plan_library_procedure(
        db,
        definition,
        mission_id="mission-postv7-ambiguous",
        target="fixture:bug-triage",
        resource_id="disposable:postv7",
        evidence_refs=("evidence:postv7:ambiguous",),
    )
    result = execute_library_step(
        db,
        plan=plan,
        step_id="orient",
        resource_id="disposable:postv7",
        verifier=lambda *_args, **_kwargs: {"verdict": "BLOCKED", "verifier_id": "verifier-postv7"},
        effect_executor=lambda *_args, **_kwargs: {"state": "UNKNOWN", "reason": "provider truth unavailable"},
        before_state={"identity": "fixture:bug-triage"},
        evidence_refs=("evidence:postv7:ambiguous",),
    )
    assert result["outcome"] == "FROZEN"
    incidents = rebuild_incidents(db)
    assert incidents and incidents[0].effect_action_key == plan.steps[0].effect_ticket
    unrelated = procedure_eligibility(
        db,
        catalog.get("jarvis.flake-diagnosis"),
        "disposable:postv7",
    )
    assert unrelated.eligible is True
    assert load_procedure(db, plan.procedure_id).step("orient").status.value == "FROZEN"
