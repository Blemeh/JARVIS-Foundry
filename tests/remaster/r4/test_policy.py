from __future__ import annotations

import pytest

from jarvis_foundry.kernel.errors import AuthorityError, FinancialActionDeniedError
from jarvis_foundry.kernel.policy import FINANCIAL_ACTION_CLASSES, PolicyEngine, PolicyRule
from jarvis_foundry.kernel.records import ActionEnvelope


def envelope(
    action_class: str, target: str = "C:/worktree", scope: str = "worktree"
) -> ActionEnvelope:
    return ActionEnvelope(
        action_id="a-1",
        mission_id="m-1",
        action_class=action_class,
        target=target,
        scope=scope,
        policy_version=1,
        decision="PENDING",
        reason="",
        authorized_by="",
    )


def test_deterministic_allow_and_deny() -> None:
    engine = PolicyEngine(
        rules=[
            PolicyRule(action_class="file.read", effect="ALLOW", reason="read allowed"),
            PolicyRule(action_class="file.write", effect="DENY", reason="write denied"),
        ]
    )
    allowed = engine.authorize(envelope("file.read"))
    assert allowed.decision == "ALLOWED"
    assert allowed.authorized_by == "policy-engine"
    with pytest.raises(AuthorityError):
        engine.authorize(envelope("file.write"))
    with pytest.raises(AuthorityError):
        engine.authorize(envelope("unlisted.class"))


def test_financial_action_classes_are_deterministically_denied() -> None:
    assert FINANCIAL_ACTION_CLASSES
    engine = PolicyEngine(rules=[])
    for action_class in FINANCIAL_ACTION_CLASSES:
        with pytest.raises(FinancialActionDeniedError):
            engine.authorize(envelope(action_class))
        assert engine.decide(envelope(action_class)).decision == "DENIED"


def test_default_deny_and_scope_matching() -> None:
    engine = PolicyEngine(
        rules=[
            PolicyRule(action_class="file.read", effect="ALLOW", scope="worktree"),
        ]
    )
    assert engine.decide(envelope("file.read", target="C:/worktree")).decision == "ALLOWED"
    outside = envelope("file.read", target="C:/outside", scope="outside")
    assert engine.decide(outside).decision == "DENIED"


def test_decide_is_pure_and_deterministic() -> None:
    engine = PolicyEngine(rules=[PolicyRule(action_class="file.read", effect="ALLOW")])
    first = engine.decide(envelope("file.read"))
    second = engine.decide(envelope("file.read"))
    assert first == second
    assert engine.decide(envelope("file.read")).decision == "ALLOWED"
