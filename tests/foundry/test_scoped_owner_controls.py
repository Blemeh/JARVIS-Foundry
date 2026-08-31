from __future__ import annotations

from pathlib import Path

from jarvis_foundry.control.broker import emergency_stop_reason
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.owner_directives import active_owner_directives

from test_mission_loop import mission_fixture


SECRET = b"scoped-owner-controls-secret"


def _apply(database, command_type: str, *, target: str, text: str, nonce: str,
           directive_id: str, stop_path: Path, metadata: dict | None = None):
    signature = sign_command(
        SECRET, command_type, target, text, nonce, metadata=metadata,
    )
    return OwnerControlService(
        database, secret=SECRET, stop_path=stop_path,
    ).apply(
        command_type, target=target, exact_text=text, nonce=nonce,
        signature=signature, directive_id=directive_id, metadata=metadata,
    )


def test_pause_resume_is_scoped_and_survives_projection_rebuild(tmp_path):
    database, _repository, _policy, _router = mission_fixture(tmp_path)
    stop_path = tmp_path / "STOP"
    paused = _apply(
        database, "PAUSE", target="mission-1", text="pause this mission",
        nonce="pause-1", directive_id="pause-1", stop_path=stop_path,
    )

    assert emergency_stop_reason(
        database, stop_path, identities={"mission-1"},
    ) == "PAUSE directive pause-1 is active"
    assert emergency_stop_reason(
        database, stop_path, identities={"mission-2"},
    ) is None

    text = "resume only this mission"
    nonce = "resume-1"
    signature = sign_command(SECRET, "RESUME", paused.directive_id, text, nonce)
    revoked = OwnerControlService(
        database, secret=SECRET, stop_path=stop_path,
    ).resume(
        target=paused.directive_id, exact_text=text,
        nonce=nonce, signature=signature,
    )
    assert revoked
    assert not active_owner_directives(database)
    database.rebuild_projections()
    assert emergency_stop_reason(
        database, stop_path, identities={"mission-1"},
    ) is None


def test_revoke_global_stop_clears_only_after_durable_revocation(tmp_path):
    database, _repository, _policy, _router = mission_fixture(tmp_path)
    stop_path = tmp_path / "STOP"
    stopped = _apply(
        database, "STOP_EVERYTHING", target="GLOBAL", text="halt all work",
        nonce="stop-1", directive_id="stop-1", stop_path=stop_path,
    )
    assert stopped.directive_id == "stop-1"
    assert stop_path.is_file()

    text = "revoke the global stop"
    nonce = "revoke-1"
    signature = sign_command(SECRET, "REVOKE", "stop-1", text, nonce)
    result = OwnerControlService(
        database, secret=SECRET, stop_path=stop_path,
    ).apply(
        "REVOKE", target="stop-1", exact_text=text, nonce=nonce,
        signature=signature,
    )
    assert result.outcome == "REVOKED"
    assert not stop_path.exists()
    assert emergency_stop_reason(database, stop_path, identities={"GLOBAL"}) is None


def test_cancel_mission_and_gate_reevaluation_are_durable(tmp_path):
    database, repository, _policy, _router = mission_fixture(tmp_path)
    stop_path = tmp_path / "STOP"
    cancelled = _apply(
        database, "CANCEL_MISSION", target="mission-1", text="cancel it",
        nonce="cancel-1", directive_id="cancel-1", stop_path=stop_path,
    )
    assert cancelled.outcome == "CANCELLED"
    assert database.row("missions", "mission_id", "mission-1")["state"] == "CANCELLED"

    reevaluated = _apply(
        database, "GATE_REEVALUATE", target="mission-1",
        text="recheck the acceptance gate", nonce="gate-1",
        directive_id="gate-1", stop_path=stop_path,
        metadata={
            "step_id": "step-1", "builder_run_id": "builder-missing",
            "target_path": str(repository / "result.txt"),
        },
    )
    assert reevaluated.outcome == "REFUSE"
    event = next(
        event for event in database.events()
        if event.event_type == "acceptance.gate_reevaluated"
        and event.payload.get("directive_id") == "gate-1"
    )
    assert event.payload["decision"] == "REFUSE"
    database.rebuild_projections()
    assert database.row("missions", "mission_id", "mission-1")["state"] == "CANCELLED"
