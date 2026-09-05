from __future__ import annotations

from types import SimpleNamespace

from jarvis_foundry.fabric import loop
from jarvis_foundry.fabric.contracts import system_id_for_action
from jarvis_foundry.fabric.model import (
    ActionResult,
    ClaimOutcome,
    Selection,
    TaskIntent,
    VerifyVerdict,
)


def _result(claimed: ClaimOutcome) -> ActionResult:
    return ActionResult(
        capability_id="cap.primary",
        action_kind="desktop.control.click",
        claimed=claimed,
        verified=VerifyVerdict.UNKNOWN,
    )


def _patch_run_intent_dependencies(monkeypatch, calls, first_result) -> None:
    monkeypatch.setattr(loop, "seed_builtins", lambda _database: ())
    monkeypatch.setattr(loop, "catalog_snapshot", lambda _database: {})
    monkeypatch.setattr(
        loop,
        "route_provider",
        lambda **_kwargs: SimpleNamespace(as_dict=lambda: {}),
    )
    monkeypatch.setattr(
        loop,
        "select_capability",
        lambda _database, intent, action: Selection(
            task_id=intent.task_id,
            action_kind=action,
            capability_id="cap.primary",
            fallbacks=("cap.fallback",),
            reason="test selection",
        ),
    )

    def execute(_database, _ctx, intent, action, _payload):
        calls.append(intent.preferred)
        if intent.preferred == "cap.fallback":
            return ActionResult(
                capability_id="cap.fallback",
                action_kind=action,
                claimed=ClaimOutcome.COMPLETED,
                verified=VerifyVerdict.MATCH,
            )
        return first_result

    monkeypatch.setattr(loop, "execute_action", execute)


def test_unknown_effect_never_falls_through_to_another_provider(monkeypatch) -> None:
    calls: list[str | None] = []
    _patch_run_intent_dependencies(
        monkeypatch,
        calls,
        _result(ClaimOutcome.UNKNOWN),
    )

    result = loop.run_intent(
        object(),
        object(),
        TaskIntent(
            task_id="task.v13.unknown",
            required_actions=("desktop.control.click",),
        ),
        {"desktop.control.click": {}},
    )

    assert calls == [None]
    assert result["results"][0]["claimed"] == "UNKNOWN"
    assert result["success"] is False


def test_definite_failure_may_use_one_ranked_fallback(monkeypatch) -> None:
    calls: list[str | None] = []
    _patch_run_intent_dependencies(
        monkeypatch,
        calls,
        _result(ClaimOutcome.FAILED),
    )

    result = loop.run_intent(
        object(),
        object(),
        TaskIntent(
            task_id="task.v13.failed",
            required_actions=("desktop.control.click",),
        ),
        {"desktop.control.click": {}},
    )

    assert calls == [None, "cap.fallback"]
    assert result["results"][0]["capability_id"] == "cap.fallback"
    assert result["success"] is True


def test_browser_actions_have_one_effect_journal_system_identity() -> None:
    assert (
        system_id_for_action(
            "browser.web.execute",
            "https://127.0.0.1:8443/form",
        )
        == "browser"
    )
    assert system_id_for_action("http.write", "http://127.0.0.1:8000") == "http"
