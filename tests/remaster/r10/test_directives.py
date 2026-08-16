"""Canonical durable directive system: UI/voice/Hermes are never authority."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from r10_helpers import EVIDENCE_ROOT, fixed_clock, make_layout, seed_layout

from jarvis_foundry.command_center.directives import (
    DIRECTIVE_COMMANDS,
    DirectiveConflictError,
    DirectiveError,
    DirectiveService,
)
from jarvis_foundry.command_center.ledger import DirectiveLedger
from jarvis_foundry.command_center.proving import ProvingLayout
from jarvis_foundry.command_center.server import serve
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def _service(layout: ProvingLayout) -> DirectiveService:
    return DirectiveService(
        PortfolioStore(layout.portfolio),
        DirectiveLedger(layout.directive_ledger),
        clock=fixed_clock(),
    )


def test_all_six_commands_are_durable(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    service = _service(layout)
    cases = [
        ("STOP", "GLOBAL", "stop everything now", "", "STOP"),
        ("VETO", "cand-11d1ef18f372", "veto this candidate", "", "VETO"),
        ("PAUSE", "operators", "pause operators work", "", "PAUSE"),
        ("RESUME", "GLOBAL", "resume normal operation", "", "RESUME"),
        ("REPRIORITIZE", "cand-11d1ef18f372", "raise priority", "3", "REPRIORITIZE"),
        ("REDIRECT", "operators", "redirect to proving target", "proving/operators", "REDIRECT"),
    ]
    for index, (command, target, reason, value, expected) in enumerate(cases):
        receipt = service.apply(
            command_type=command,
            target=target,
            reason=reason,
            nonce=f"nonce-{index}",
            value=value,
        )
        assert receipt.command_type == expected
        assert receipt.created is True
    assert service.ledger.count() == 6
    store = PortfolioStore(layout.portfolio)
    commands = {directive.command_type for directive in store.control_directives()}
    assert commands == set(DIRECTIVE_COMMANDS)


def test_control_state_derives_from_durable_records(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    service = _service(layout)
    service.apply(command_type="STOP", target="GLOBAL", reason="stop", nonce="s1")
    state = service.state()
    assert state.stop_active is True
    service.apply(command_type="RESUME", target="GLOBAL", reason="resume", nonce="r1")
    state = service.state()
    assert state.stop_active is False
    service.apply(command_type="VETO", target="cand-x", reason="veto", nonce="v1")
    assert "cand-x" in service.state().vetoed_candidates
    service.apply(command_type="PAUSE", target="leadgen", reason="pause", nonce="p1")
    assert "leadgen" in service.state().paused_projects
    service.apply(
        command_type="REDIRECT", target="operators", reason="move", value="proving/new", nonce="d1"
    )
    assert service.state().redirects[0]["new_target"] == "proving/new"


def test_replay_is_idempotent_and_conflicts_are_rejected(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    service = _service(layout)
    first = service.apply(command_type="PAUSE", target="GLOBAL", reason="pause", nonce="same")
    second = service.apply(command_type="PAUSE", target="GLOBAL", reason="pause", nonce="same")
    assert first.created is True and second.created is False
    with pytest.raises(DirectiveConflictError):
        service.apply(command_type="STOP", target="GLOBAL", reason="different", nonce="same")


def test_validation_rejects_unsupported_or_incomplete_commands(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    service = _service(layout)
    with pytest.raises(DirectiveError):
        service.apply(command_type="HACK", target="GLOBAL", reason="x", nonce="n1")
    with pytest.raises(DirectiveError):
        service.apply(command_type="PAUSE", target="GLOBAL", reason="", nonce="n2")
    with pytest.raises(DirectiveError):
        service.apply(command_type="PAUSE", target="", reason="x", nonce="n3")
    with pytest.raises(DirectiveError):
        service.apply(
            command_type="REPRIORITIZE", target="cand-x", reason="x", nonce="n4", value="high"
        )


def test_stop_survives_ui_failure_via_durable_store(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    running = serve(layout, evidence_root=EVIDENCE_ROOT, seed=False)
    base = running.base_url
    body = {
        "command_type": "STOP",
        "target": "GLOBAL",
        "reason": "owner stop through UI",
        "nonce": "ui-stop-1",
        "value": "",
    }
    try:
        with urlopen(
            Request(
                f"{base}/api/directives",
                data=json.dumps(body).encode("utf-8"),
                method="POST",
            ),
            timeout=5,
        ) as response:
            payload = json.loads(response.read())
        assert payload["status"] == "APPLIED"
        assert payload["control"]["stop_active"] is True
    finally:
        running.close()

    # The UI process is gone; a fresh process reads the same durable store.
    fresh = _service(layout)
    fresh.ledger.verify()
    assert fresh.state().stop_active is True
    assert fresh.ledger.count() == 1
    directive = next(
        item for item in fresh.store.control_directives() if item.command_type == "STOP"
    )
    assert directive.nonce == "ui-stop-1"


def test_restart_preserves_directives_and_reconciles_store(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    seed_count = PortfolioStore(layout.portfolio).event_count()
    first = _service(layout)
    first.apply(
        command_type="VETO", target="cand-x", reason="veto before restart", nonce="restart-1"
    )
    second = _service(layout)
    second.ledger.verify()
    store = PortfolioStore(layout.portfolio)
    store.verify()
    assert second.state().vetoed_candidates == ("cand-x",)
    assert second.ledger.count() == 1
    assert store.event_count() == seed_count + 1
