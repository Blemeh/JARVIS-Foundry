from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jarvis_foundry.kernel.errors import AuthorityError, NonceReplayError
from jarvis_foundry.kernel.events import EventKindRegistry
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.owner_control import (
    FileStopGate,
    OwnerControlService,
    sign_command,
)

SECRET = b"r4-owner-secret"


def owner_registry() -> EventKindRegistry:
    return EventKindRegistry().register(
        "owner.directive.recorded",
        required_keys=("directive_id", "command_type", "target", "nonce"),
    )


def fresh_service(tmp_path: Path) -> tuple[OwnerControlService, Path]:
    ledger = Ledger(tmp_path / "kernel.db", registry=owner_registry())
    stop = tmp_path / "STOP"
    service = OwnerControlService(
        ledger,
        stop_gate=FileStopGate(stop),
        secret=SECRET,
        clock=None,
        ids=None,
    )
    return service, stop


def apply_command(
    service: OwnerControlService,
    command_type: str,
    nonce: str,
    target: str | None,
    *,
    signature: str | None = None,
) -> None:
    text = f"{command_type} {nonce}"
    if signature is None:
        signature = sign_command(SECRET, command_type, target, nonce, text)
    service.apply_command(
        command_type=command_type,
        target=target,
        exact_text=text,
        nonce=nonce,
        signature=signature,
    )


def test_owner_nonce_replay_is_rejected(tmp_path: Path) -> None:
    service, _stop = fresh_service(tmp_path)
    apply_command(service, "STOP", "nonce-1", "GLOBAL")
    with pytest.raises(NonceReplayError):
        apply_command(service, "STOP", "nonce-1", "GLOBAL")


def test_owner_stop_writes_sentinel(tmp_path: Path) -> None:
    service, stop = fresh_service(tmp_path)
    apply_command(service, "STOP", "nonce-stop", "GLOBAL")
    assert stop.exists()
    active = service.active_directives()
    assert any(directive.command_type == "STOP" for directive in active)


def test_stop_sentinel_active_and_clear(tmp_path: Path) -> None:
    service, stop = fresh_service(tmp_path)
    gate = FileStopGate(stop)
    assert gate.active() is False
    apply_command(service, "STOP", "nonce-stop", "GLOBAL")
    assert gate.active() is True
    apply_command(service, "RESUME", "nonce-resume", "GLOBAL")
    assert gate.active() is False


def test_unauthenticated_command_is_rejected(tmp_path: Path) -> None:
    service, _stop = fresh_service(tmp_path)
    with pytest.raises(AuthorityError):
        apply_command(
            service,
            "STOP",
            "nonce-forged",
            "GLOBAL",
            signature="0" * 64,
        )
    with pytest.raises(AuthorityError):
        service.apply_command(
            command_type="STOP",
            target="GLOBAL",
            exact_text="STOP forged",
            nonce="nonce-forged",
            signature=sign_command(
                b"wrong-secret", "STOP", "GLOBAL", "nonce-forged", "STOP forged"
            ),
        )


def test_veto_pause_resume_redirect_reprioritize(tmp_path: Path) -> None:
    service, _stop = fresh_service(tmp_path)
    apply_command(service, "VETO", "n-1", "action-A")
    apply_command(service, "PAUSE", "n-2", "GLOBAL")
    apply_command(service, "REDIRECT", "n-3", "mission-1")
    apply_command(service, "REPRIORITIZE", "n-4", "goal-1")
    directives = service.active_directives()
    command_types = {directive.command_type for directive in directives}
    assert command_types == {"VETO", "PAUSE", "REDIRECT", "REPRIORITIZE"}
    apply_command(service, "RESUME", "n-5", "GLOBAL")
    active = {directive.command_type for directive in service.active_directives()}
    assert "PAUSE" not in active
    assert "VETO" in active


def test_crash_is_atomic_leaves_no_partial_directive(tmp_path: Path) -> None:
    service, stop = fresh_service(tmp_path)
    service._fail_append = True
    with pytest.raises(RuntimeError):
        apply_command(service, "PAUSE", "nonce-crash", "GLOBAL")
    assert stop.exists() is False
    service._fail_append = False
    apply_command(service, "PAUSE", "nonce-crash", "GLOBAL")
    assert any(
        directive.command_type == "PAUSE" and directive.nonce == "nonce-crash"
        for directive in service.active_directives()
    )


def test_restart_preserves_durable_directives(tmp_path: Path) -> None:
    service, stop = fresh_service(tmp_path)
    apply_command(service, "PAUSE", "nonce-1", "GLOBAL")
    apply_command(service, "STOP", "nonce-2", "GLOBAL")
    restarted, _ = fresh_service(tmp_path)
    directive_types = {directive.command_type for directive in restarted.active_directives()}
    assert directive_types == {"PAUSE", "STOP"}
    assert stop.exists()


COMMANDS = ("PAUSE", "STOP", "VETO", "RESUME", "REDIRECT", "REPRIORITIZE")


@settings(max_examples=25, deadline=None)
@given(commands=st.lists(st.sampled_from(COMMANDS), min_size=0, max_size=14))
def test_owner_control_state_machine_property(
    tmp_path_factory: pytest.TempPathFactory, commands: list[str]
) -> None:
    tmp_path = tmp_path_factory.mktemp("owner-control")
    service, stop = fresh_service(tmp_path)
    model_active: set[str] = set()
    model_sentinel = False
    for position, command in enumerate(commands):
        nonce = f"nonce-{position}"
        if command == "RESUME":
            text = "resume jarvis"
            signature = sign_command(SECRET, "RESUME", "GLOBAL", nonce, text)
            service.apply_command(
                command_type="RESUME",
                target="GLOBAL",
                exact_text=text,
                nonce=nonce,
                signature=signature,
            )
            model_active.discard("STOP")
            model_active.discard("PAUSE")
            model_sentinel = False
        else:
            target = "action-A" if command == "VETO" else "GLOBAL"
            apply_command(service, command, nonce, target)
            model_active.add(command)
            if command == "STOP":
                model_sentinel = True
    observed = {directive.command_type for directive in service.active_directives()}
    assert observed == model_active
    assert stop.exists() == model_sentinel
