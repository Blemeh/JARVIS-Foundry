"""V5-S2: ActionEnvelope + deterministic policy broker (architecture §7).

The broker implements the exact 15-step order and returns exactly one of
the seven contract codes. An LLM may explain a decision but may never
override it. These tests pin the codes, the precedence, the STOP/veto
race (decisions are never cached), duplicate-envelope dedup, and the
executor-side ticket check that makes broker bypass impossible.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.control.broker import (
    PolicyBroker,
    normalize_action_kind,
    record_decision,
    require_allowance,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.domain import ActionEnvelope, BrokerDecision
from jarvis_foundry.events import Event
from jarvis_foundry.systems import register_system

from test_registration import inspect_git_resource, make_repository, registration_event  # noqa: E402


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _envelope(**overrides: object) -> ActionEnvelope:
    values: dict[str, object] = {
        "action_id": "act-1",
        "mission_id": "mission-1",
        "resource_id": "res-1",
        "action_kind": "browser.navigate",
        "target": None,
        "capability": "navigate",
        "expected_effects": ("navigate to url",),
        "risk_class": "C1",
        "verification_required": True,
        "rollback": {"method": "navigate-back"},
        # V5-S2 architecture-contract fields
        "step_id": "step-1",
        "requested_by_agent_run_id": "run-1",
        "target_resources": ("sys-browser",),
        "capabilities_required": ("navigate",),
        "goal_alignment": "mission-1",
        "active_owner_vetoes_checked": (),
        "expected_effect": "navigate to url",
        "reversibility": "reversible",
        "before_state_capture": {"hash": "b4f00d"},
        "verification_plan": {"kind": "deterministic"},
        "rollback_plan": {"method": "navigate-back"},
        "idempotency_key": "act-1-key",
        "budget_estimate": {},
        "credential_requirements": (),
        "created_at": "2026-08-13T00:00:00Z",
    }
    values.update(overrides)
    return ActionEnvelope(**values)


def _register_browser(db: FoundryDatabase, **capabilities: str) -> None:
    register_system(db, "sys-browser", "browser", "local chromium",
                    capabilities=capabilities or {"navigate": "READY",
                                                  "extract": "READY"})


def _broker(db: FoundryDatabase, tmp_path: Path) -> PolicyBroker:
    return PolicyBroker(db, stop_path=tmp_path / "EMERGENCY_STOP")


# ---------------------------------------------------------------------------
# action-kind canonicalization (S1 verifier P9(b), S2 scope)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variant", [
    "browser.navigate", "Browser.Navigate", "BROWSER.NAVIGATE",
    " browser.navigate ", "browser/navigate", "browser:navigate",
    "browser..navigate", "browser. navigate",
])
def test_normalize_action_kind_canonical(variant: str) -> None:
    assert normalize_action_kind(variant) == "browser.navigate"


def test_normalize_action_kind_rejects_empty() -> None:
    for bad in ("", "   ", None, 7):
        with pytest.raises(ValueError):
            normalize_action_kind(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ALLOW path + envelope validation
# ---------------------------------------------------------------------------

def test_allows_complete_envelope(db: FoundryDatabase, tmp_path: Path) -> None:
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope())
    assert result.decision is BrokerDecision.ALLOW
    assert result.canonical_kind == "browser.navigate"
    assert result.resolved_targets == ("system:sys-browser",)


@pytest.mark.parametrize("override,why", [
    ({"action_id": ""}, "action"),
    ({"mission_id": ""}, "mission"),
    ({"action_kind": ""}, "kind"),
    ({"risk_class": "C9"}, "risk"),
    ({"reversibility": "sometimes"}, "reversibility"),
])
def test_malformed_envelope_not_ready(db: FoundryDatabase, tmp_path: Path,
                                      override: dict[str, object],
                                      why: str) -> None:
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope(**override))
    assert result.decision is BrokerDecision.NOT_READY
    assert why in result.reason


# ---------------------------------------------------------------------------
# step 1 — emergency STOP (fresh state every call; never cached)
# ---------------------------------------------------------------------------

def test_emergency_stopped_marker(db: FoundryDatabase, tmp_path: Path) -> None:
    _register_browser(db)
    marker = tmp_path / "EMERGENCY_STOP"
    marker.write_text("", encoding="utf-8")
    result = _broker(db, tmp_path).decide(_envelope())
    assert result.decision is BrokerDecision.EMERGENCY_STOPPED


def test_emergency_stopped_directive(db: FoundryDatabase, tmp_path: Path) -> None:
    _register_browser(db)
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-stop", "command_type": "STOP_EVERYTHING",
         "target": "GLOBAL", "exact_text": "stop everything", "reason": "test"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-stop"))
    result = _broker(db, tmp_path).decide(_envelope())
    assert result.decision is BrokerDecision.EMERGENCY_STOPPED


def test_emergency_stopped_frozen_supervisor(db: FoundryDatabase,
                                             tmp_path: Path) -> None:
    _register_browser(db)
    db.append(Event.create(
        "emergency_stop.frozen", {"instance_id": "i-1"},
        source_type="test", source_id="test",
        idempotency_key="emergency_stop.frozen:i-1"))
    result = _broker(db, tmp_path).decide(_envelope())
    assert result.decision is BrokerDecision.EMERGENCY_STOPPED


def test_emergency_stop_precedes_every_other_check(
        db: FoundryDatabase, tmp_path: Path) -> None:
    """Step 1 outranks everything: STOP + invalid target + veto -> STOPPED."""
    marker = tmp_path / "EMERGENCY_STOP"
    marker.write_text("", encoding="utf-8")
    result = _broker(db, tmp_path).decide(_envelope(target_resources=("ghost",)))
    assert result.decision is BrokerDecision.EMERGENCY_STOPPED


def test_stop_race_re_evaluated_each_call(db: FoundryDatabase,
                                          tmp_path: Path) -> None:
    """Decisions are never cached: a STOP raised between deliveries
    changes the decision (R-AUT-04)."""
    _register_browser(db)
    marker = tmp_path / "EMERGENCY_STOP"
    broker = _broker(db, tmp_path)
    assert broker.decide(_envelope()).decision is BrokerDecision.ALLOW
    marker.write_text("", encoding="utf-8")
    assert broker.decide(_envelope()).decision is BrokerDecision.EMERGENCY_STOPPED
    marker.unlink()
    assert broker.decide(_envelope()).decision is BrokerDecision.ALLOW


# ---------------------------------------------------------------------------
# step 2-3 — target resolution and registration
# ---------------------------------------------------------------------------

def test_invalid_target_unknown_system(db: FoundryDatabase,
                                       tmp_path: Path) -> None:
    result = _broker(db, tmp_path).decide(_envelope(target_resources=("sys-ghost",)))
    assert result.decision is BrokerDecision.INVALID_TARGET


@pytest.mark.parametrize("fuzzy", [
    "$HOME/notes", "%USERPROFILE%/notes", "{HOME}/notes",
    "/tmp/*.md", "/tmp/file[1].md", "/tmp/file?.md",
    "relative/path", "C:\\",
])
def test_invalid_target_fuzzy_or_root_paths(db: FoundryDatabase,
                                            tmp_path: Path,
                                            fuzzy: str) -> None:
    result = _broker(db, tmp_path).decide(
        _envelope(target_resources=(fuzzy,), capabilities_required=("read",)))
    assert result.decision is BrokerDecision.INVALID_TARGET


def test_invalid_target_escapes_registered_resource_root(
        db: FoundryDatabase, tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    resource = inspect_git_resource(repository, resource_id="res-1")
    db.append(registration_event(resource))
    outside = repository.parent / "outside.txt"
    result = _broker(db, tmp_path).decide(_envelope(
        target=None,
        target_resources=(str(outside),),
        resource_id="res-1",
        capability="",
        capabilities_required=("write_registered_worktree_files",)))
    assert result.decision is BrokerDecision.INVALID_TARGET
    inside = repository / "inside.txt"
    result = _broker(db, tmp_path).decide(_envelope(
        target=None,
        target_resources=(str(inside),),
        resource_id="res-1",
        capability="",
        capabilities_required=("write_registered_worktree_files",)))
    assert result.decision is BrokerDecision.ALLOW


def test_invalid_target_unregistered_resource_root(
        db: FoundryDatabase, tmp_path: Path) -> None:
    """A file target needs a REGISTERED resource root — an unregistered
    root is not a resolvable identity (registration step 3)."""
    result = _broker(db, tmp_path).decide(_envelope(
        target=None, target_resources=(str(tmp_path / "x.txt"),),
        resource_id="res-ghost"))
    assert result.decision is BrokerDecision.INVALID_TARGET


# ---------------------------------------------------------------------------
# step 4 — vetoes (canonical) and capability readiness
# ---------------------------------------------------------------------------

def test_veto_blocks_canonical_and_variant_kinds(db: FoundryDatabase,
                                                 tmp_path: Path) -> None:
    _register_browser(db)
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-veto", "command_type": "VETO_ACTION",
         "target": "browser.navigate", "exact_text": "veto navigate",
         "reason": "test"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-veto"))
    for variant in ("browser.navigate", "Browser.Navigate",
                    "BROWSER.NAVIGATE", " browser.navigate ",
                    "browser/navigate", "browser:navigate"):
        result = _broker(db, tmp_path).decide(_envelope(action_kind=variant))
        assert result.decision is BrokerDecision.VETOED, variant


def test_veto_scoped_to_kind_and_global(db: FoundryDatabase,
                                        tmp_path: Path) -> None:
    _register_browser(db)
    register_system(db, "sys-repo", "repo", "repo system",
                    capabilities={"read": "READY"})
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-veto", "command_type": "VETO_ACTION",
         "target": "browser.navigate", "exact_text": "veto navigate",
         "reason": "test"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-veto"))
    # unrelated kind stays allowed
    assert _broker(db, tmp_path).decide(_envelope(
        action_kind="repo.read", target_resources=("sys-repo",),
        capabilities_required=("read",))).decision is BrokerDecision.ALLOW
    # GLOBAL veto blocks any kind
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-global", "command_type": "VETO_ACTION",
         "target": "GLOBAL", "exact_text": "veto all", "reason": "test"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-global"))
    assert _broker(db, tmp_path).decide(_envelope(
        action_kind="repo.read", target_resources=("sys-repo",),
        capabilities_required=("read",))).decision is BrokerDecision.VETOED


def test_stop_mission_scoped_to_mission(db: FoundryDatabase,
                                        tmp_path: Path) -> None:
    _register_browser(db)
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-mission", "command_type": "STOP_MISSION",
         "target": "mission-1", "exact_text": "stop mission 1", "reason": "test"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-mission"))
    assert _broker(db, tmp_path).decide(_envelope()).decision is \
        BrokerDecision.VETOED
    assert _broker(db, tmp_path).decide(_envelope(
        mission_id="mission-2")).decision is BrokerDecision.ALLOW


def test_capability_not_ready(db: FoundryDatabase, tmp_path: Path) -> None:
    _register_browser(db, navigate="NOT_TESTED")
    result = _broker(db, tmp_path).decide(_envelope())
    assert result.decision is BrokerDecision.NOT_READY
    assert "navigate" in result.reason


def test_capability_unready_after_failed_probe(db: FoundryDatabase,
                                               tmp_path: Path) -> None:
    _register_browser(db, navigate="UNREADY")
    result = _broker(db, tmp_path).decide(_envelope())
    assert result.decision is BrokerDecision.NOT_READY


def test_capability_unregistered(db: FoundryDatabase, tmp_path: Path) -> None:
    _register_browser(db)  # only navigate/extract
    result = _broker(db, tmp_path).decide(_envelope(
        capabilities_required=("download",)))
    assert result.decision is BrokerDecision.NOT_READY


def test_goal_alignment_required(db: FoundryDatabase, tmp_path: Path) -> None:
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope(goal_alignment=None))
    assert result.decision is BrokerDecision.NOT_READY


# ---------------------------------------------------------------------------
# step 5 — secret/credential requirements (honest NOT_READY)
# ---------------------------------------------------------------------------

def test_credential_requirement_not_ready(db: FoundryDatabase,
                                          tmp_path: Path) -> None:
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope(
        credential_requirements=("gmail",)))
    assert result.decision is BrokerDecision.NOT_READY
    assert "gmail" in result.reason


def test_credential_requirement_ready_when_registered(db: FoundryDatabase,
                                                      tmp_path: Path) -> None:
    _register_browser(db)
    register_system(db, "sys-creds", "credentials", "credential broker",
                    capabilities={"gmail": "READY"})
    result = _broker(db, tmp_path).decide(_envelope(
        credential_requirements=("gmail",)))
    assert result.decision is BrokerDecision.ALLOW


# ---------------------------------------------------------------------------
# step 6 — budgets (fail closed when no policy; precedence over recovery)
# ---------------------------------------------------------------------------

def test_budget_exceeded(db: FoundryDatabase, tmp_path: Path) -> None:
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope(
        budget_estimate={"usd": 7.0}), budget={"usd": 5.0})
    assert result.decision is BrokerDecision.BUDGET_EXCEEDED
    result = _broker(db, tmp_path).decide(_envelope(
        budget_estimate={"usd": 0.4}), budget={"usd": 5.0})
    assert result.decision is BrokerDecision.ALLOW


def test_budget_declared_without_policy_not_ready(db: FoundryDatabase,
                                                  tmp_path: Path) -> None:
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope(
        budget_estimate={"usd": 0.4}))
    assert result.decision is BrokerDecision.NOT_READY


def test_budget_unknown_estimate_key_fail_closed(db: FoundryDatabase,
                                                 tmp_path: Path) -> None:
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope(
        budget_estimate={"calls": 3}), budget={"usd": 5.0})
    assert result.decision is BrokerDecision.NOT_READY


def test_budget_precedes_recovery(db: FoundryDatabase, tmp_path: Path) -> None:
    """Step 6 runs before step 7: BUDGET_EXCEEDED outranks
    MISSING_RECOVERY_PATH."""
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope(
        budget_estimate={"usd": 7.0}, reversibility="irreversible",
        rollback_plan={}), budget={"usd": 5.0})
    assert result.decision is BrokerDecision.BUDGET_EXCEEDED


# ---------------------------------------------------------------------------
# step 7-8 — recovery strategy and before-state capture
# ---------------------------------------------------------------------------

def test_missing_recovery_path(db: FoundryDatabase, tmp_path: Path) -> None:
    _register_browser(db)
    for override in ({"reversibility": "irreversible", "rollback_plan": {}},
                     {"risk_class": "C2", "rollback_plan": {}}):
        result = _broker(db, tmp_path).decide(_envelope(**override))
        assert result.decision is BrokerDecision.MISSING_RECOVERY_PATH, override
    # a declared executable method satisfies the recovery contract even
    # for irreversible actions (the default plan in _envelope())
    result = _broker(db, tmp_path).decide(_envelope(
        reversibility="irreversible"))
    assert result.decision is BrokerDecision.ALLOW
    result = _broker(db, tmp_path).decide(_envelope(
        reversibility="irreversible", rollback_plan={"method": "undo"}))
    assert result.decision is BrokerDecision.ALLOW


def test_before_state_capture_required_for_mutation(db: FoundryDatabase,
                                                    tmp_path: Path) -> None:
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope(before_state_capture={}))
    assert result.decision is BrokerDecision.NOT_READY
    result = _broker(db, tmp_path).decide(_envelope(
        before_state_capture={"ref": "var/evidence/before/x.json"}))
    assert result.decision is BrokerDecision.ALLOW


# ---------------------------------------------------------------------------
# step 10 — idempotency key + steps 12-13 — verification plan
# ---------------------------------------------------------------------------

def test_idempotency_key_required(db: FoundryDatabase, tmp_path: Path) -> None:
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope(idempotency_key=None))
    assert result.decision is BrokerDecision.NOT_READY


def test_verification_plan_required(db: FoundryDatabase, tmp_path: Path) -> None:
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope(verification_plan={}))
    assert result.decision is BrokerDecision.NOT_READY


def test_c3_requires_independent_verification(db: FoundryDatabase,
                                              tmp_path: Path) -> None:
    _register_browser(db)
    result = _broker(db, tmp_path).decide(_envelope(
        risk_class="C3", rollback_plan={"method": "undo"},
        verification_plan={"kind": "deterministic"}))
    assert result.decision is BrokerDecision.NOT_READY
    result = _broker(db, tmp_path).decide(_envelope(
        risk_class="C3", rollback_plan={"method": "undo"},
        verification_plan={"kind": "deterministic", "independent": True}))
    assert result.decision is BrokerDecision.ALLOW


# ---------------------------------------------------------------------------
# decision records: dedup, audit trail, executor ticket (bypass proof)
# ---------------------------------------------------------------------------

def _decision_events(db: FoundryDatabase, action_id: str) -> int:
    with db.connect() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'action.decided' "
            "AND json_extract(payload_json, '$.action_id') = ?",
            (action_id,)).fetchone()[0]


def test_duplicate_envelope_dedupes_to_one_decision(db: FoundryDatabase,
                                                    tmp_path: Path) -> None:
    _register_browser(db)
    broker = _broker(db, tmp_path)
    envelope = _envelope()
    record_decision(db, envelope, broker.decide(envelope))
    record_decision(db, envelope, broker.decide(envelope))
    assert _decision_events(db, "act-1") == 1  # identical re-delivery dedupes
    # a genuinely changed envelope is a NEW decision record (audit trail)
    changed = _envelope(goal_alignment="other-goal")
    record_decision(db, changed, broker.decide(changed))
    assert _decision_events(db, "act-1") == 2


def test_worker_bypass_refused_without_allowance(db: FoundryDatabase,
                                                 tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        require_allowance(db, "act-ghost")


def test_worker_bypass_refused_on_non_allow_decision(db: FoundryDatabase,
                                                     tmp_path: Path) -> None:
    _register_browser(db)
    broker = _broker(db, tmp_path)
    envelope = _envelope(goal_alignment=None)  # NOT_READY
    record_decision(db, envelope, broker.decide(envelope))
    with pytest.raises(PermissionError):
        require_allowance(db, "act-1")


def test_worker_allowed_only_after_allow_decision(db: FoundryDatabase,
                                                  tmp_path: Path) -> None:
    _register_browser(db)
    broker = _broker(db, tmp_path)
    envelope = _envelope()
    record_decision(db, envelope, broker.decide(envelope))
    require_allowance(db, "act-1")  # does not raise
    # a LATER non-allow decision revokes the ticket (STOP during queued work)
    marker = tmp_path / "EMERGENCY_STOP"
    marker.write_text("", encoding="utf-8")
    stopped = broker.decide(envelope)
    record_decision(db, envelope, stopped)
    with pytest.raises(PermissionError):
        require_allowance(db, "act-1")


def test_decisions_survive_projection_rebuild(db: FoundryDatabase,
                                              tmp_path: Path) -> None:
    _register_browser(db)
    broker = _broker(db, tmp_path)
    envelope = _envelope()
    record_decision(db, envelope, broker.decide(envelope))
    before = _decision_events(db, "act-1")
    db.rebuild_projections()
    assert _decision_events(db, "act-1") == before
