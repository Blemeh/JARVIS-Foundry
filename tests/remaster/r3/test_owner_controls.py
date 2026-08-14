from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import (
    OwnerControlService,
    OwnerReplayError,
    sign_command,
)
from jarvis_foundry.supervisor.estop import EmergencyStop

SECRET = b"r3-owner-secret"


def fresh_service(tmp_path: Path) -> tuple[FoundryDatabase, OwnerControlService, Path]:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    stop = tmp_path / "STOP"
    service = OwnerControlService(database, secret=SECRET, stop_path=stop)
    return database, service, stop


def apply_command(
    service: OwnerControlService, command_type: str, nonce: str, target: str | None
) -> None:
    text = f"{command_type} {nonce}"
    signature = sign_command(SECRET, command_type, target, text, nonce)
    service.apply(
        command_type,
        target=target,
        exact_text=text,
        nonce=nonce,
        signature=signature,
    )


def observed_state(database: FoundryDatabase, stop: Path) -> dict[str, bool | list[str]]:
    with database.connect() as connection:
        rows = connection.execute("SELECT command_type, active FROM directives").fetchall()
    active = sorted(str(row[0]) for row in rows if row[1] == 1)
    return {"active": active, "sentinel": stop.exists()}


def test_owner_nonce_replay_is_rejected(tmp_path: Path) -> None:
    _database, service, _stop = fresh_service(tmp_path)
    apply_command(service, "STOP_EVERYTHING", "nonce-1", "GLOBAL")
    with pytest.raises(OwnerReplayError):
        apply_command(service, "STOP_EVERYTHING", "nonce-1", "GLOBAL")


def test_owner_stop_writes_sentinel(tmp_path: Path) -> None:
    database, service, stop = fresh_service(tmp_path)
    apply_command(service, "STOP_EVERYTHING", "nonce-stop", "GLOBAL")
    assert stop.exists()
    rows = observed_state(database, stop)
    active = rows["active"]
    assert isinstance(active, list)
    assert "STOP_EVERYTHING" in active
    assert rows["sentinel"] is True


def test_stop_sentinel_active_and_clear(tmp_path: Path) -> None:
    estop = EmergencyStop(tmp_path / "STOP")
    record = estop.activate("owner requested stop", source="r3-test")
    assert estop.active() is True
    read = estop.read()
    assert read is not None
    assert read["reason"] == "owner requested stop"
    assert record["stop_id"] == read["stop_id"]
    assert estop.clear() is True
    assert estop.active() is False
    assert estop.clear() is False


def test_owner_redirect_keeps_veto_and_new_allowance(tmp_path: Path) -> None:
    database, service, stop = fresh_service(tmp_path)
    apply_command(service, "VETO_ACTION", "nonce-veto", "action-A")
    database.append(
        Event.create(
            "action.allowed",
            {
                "action_id": "action-B",
                "mission_id": "mission-1",
                "decision": "ALLOWED",
                "reason": "redirected",
                "target": "/tmp/B",
            },
            source_type="policy",
            source_id="r3-test",
            idempotency_key="action.allowed:action-B",
        )
    )
    state = observed_state(database, stop)
    active = state["active"]
    assert isinstance(active, list)
    assert "VETO_ACTION" in active
    assert database.row("action_decisions", "action_id", "action-A") is None
    redirected = database.row("action_decisions", "action_id", "action-B")
    assert redirected is not None
    assert redirected["decision"] == "ALLOWED"


COMMANDS = ("PAUSE", "STOP_EVERYTHING", "VETO_ACTION", "RESUME")


@settings(max_examples=25, deadline=None)
@given(commands=st.lists(st.sampled_from(COMMANDS), min_size=0, max_size=14))
def test_owner_control_state_machine_property(
    tmp_path_factory: pytest.TempPathFactory, commands: list[str]
) -> None:
    tmp_path = tmp_path_factory.mktemp("owner-control")
    database, service, stop = fresh_service(tmp_path)
    model_active: set[str] = set()
    model_sentinel = False
    for position, command in enumerate(commands):
        nonce = f"nonce-{position}"
        if command == "RESUME":
            text = "resume jarvis"
            signature = sign_command(SECRET, "RESUME", "GLOBAL", text, nonce)
            service.resume(nonce=nonce, signature=signature, exact_text=text)
            model_active.discard("STOP_EVERYTHING")
            model_active.discard("PAUSE")
            model_sentinel = False
        else:
            target = "action-A" if command == "VETO_ACTION" else "GLOBAL"
            apply_command(service, command, nonce, target)
            if command == "PAUSE":
                model_active.add("PAUSE")
            elif command == "STOP_EVERYTHING":
                model_active.add("STOP_EVERYTHING")
                model_sentinel = True
            elif command == "VETO_ACTION":
                model_active.add("VETO_ACTION")
        observed = observed_state(database, stop)
        active = observed["active"]
        assert isinstance(active, list)
        assert set(active) == model_active
        assert observed["sentinel"] == model_sentinel
