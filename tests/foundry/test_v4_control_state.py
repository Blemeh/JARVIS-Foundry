"""V4-3 durable pause/STOP state read-model proof."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import OwnerControlService, sign_command


SECRET = b"v4-control-state-secret"


def _apply(
    database: FoundryDatabase,
    command_type: str,
    *,
    target: str = "GLOBAL",
    text: str,
    nonce: str,
    directive_id: str,
) -> str:
    signature = sign_command(SECRET, command_type, target, text, nonce)
    return OwnerControlService(
        database, secret=SECRET, owner_id="control-state-test"
    ).apply(
        command_type,
        target=target,
        exact_text=text,
        nonce=nonce,
        signature=signature,
        directive_id=directive_id,
    ).directive_id


def test_missing_runtime_evidence_is_unknown_not_running(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "UPDATE supervisor_state SET status = 'UNKNOWN' WHERE singleton = 1"
        )

    snapshot = build_snapshot(database, stop_path=tmp_path / "STOP")

    assert snapshot["controls"]["runtime_state"] == "UNKNOWN"
    assert snapshot["controls"]["state_reason"]
    assert snapshot["controls"]["resume_available"] is False


def test_pause_survives_projection_rebuild_and_resume_preserves_other_controls(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "supervisor.heartbeat",
        {"status": "RUNNING"},
        source_type="supervisor",
        source_id="control-state-test",
        idempotency_key="supervisor.heartbeat:control-state-test",
    ))
    pause_id = _apply(
        database,
        "PAUSE",
        text="pause the institution for owner review",
        nonce="pause-1",
        directive_id="pause-1",
    )
    constraint_id = _apply(
        database,
        "CONSTRAINT",
        text="no external effects during review",
        nonce="constraint-1",
        directive_id="constraint-1",
    )
    veto_id = _apply(
        database,
        "VETO_ACTION",
        target="mission-review",
        text="hold the review mission",
        nonce="veto-1",
        directive_id="veto-1",
    )

    paused = build_snapshot(database, stop_path=tmp_path / "STOP")
    assert paused["controls"]["runtime_state"] == "PAUSED"
    assert paused["controls"]["paused"] is True
    assert paused["controls"]["resume_available"] is True
    assert pause_id in paused["controls"]["state_evidence"]["pause_directive_ids"]

    database.rebuild_projections()
    rebuilt = build_snapshot(database, stop_path=tmp_path / "STOP")
    assert rebuilt["controls"]["runtime_state"] == "PAUSED"

    service = OwnerControlService(
        database, secret=SECRET, owner_id="control-state-test"
    )
    resume_nonce = "resume-1"
    service.resume(
        nonce=resume_nonce,
        exact_text="resume after owner review",
        signature=sign_command(
            SECRET,
            "RESUME",
            "GLOBAL",
            "resume after owner review",
            resume_nonce,
        ),
    )
    resumed = build_snapshot(database, stop_path=tmp_path / "STOP")

    assert resumed["controls"]["paused"] is False
    assert resumed["controls"]["runtime_state"] == "BLOCKED"
    assert resumed["controls"]["resume_available"] is False
    assert resumed["controls"]["constraints"][0]["directive_id"] == constraint_id
    assert resumed["controls"]["vetoes"][0]["directive_id"] == veto_id
    assert database.row("directives", "directive_id", constraint_id)["active"] == 1
    assert database.row("directives", "directive_id", veto_id)["active"] == 1
    assert database.row("directives", "directive_id", pause_id)["active"] == 0


def test_stop_has_precedence_over_pause_and_clears_durably(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    _apply(
        database,
        "PAUSE",
        text="pause before emergency stop",
        nonce="pause-2",
        directive_id="pause-2",
    )
    stop_path = tmp_path / "STOP"
    service = OwnerControlService(
        database, secret=SECRET, stop_path=stop_path, owner_id="control-state-test"
    )
    stop_text = "stop everything now"
    stop_nonce = "stop-1"
    result = service.stop_now(
        reason=stop_text,
        nonce=stop_nonce,
        signature=sign_command(
            SECRET, "STOP_EVERYTHING", "GLOBAL", stop_text, stop_nonce
        ),
    )

    stopped = build_snapshot(database, stop_path=stop_path)
    assert stopped["controls"]["runtime_state"] == "STOPPED"
    assert stopped["controls"]["state_evidence"]["stopped"] is True
    assert result.directive_id in stopped["controls"]["state_evidence"]["stop_directive_ids"]

    resume_nonce = "resume-2"
    service.resume(
        nonce=resume_nonce,
        signature=sign_command(
            SECRET, "RESUME", "GLOBAL", "resume jarvis", resume_nonce
        ),
    )
    resumed = build_snapshot(database, stop_path=stop_path)
    assert resumed["controls"]["runtime_state"] == "STOPPED"
    assert resumed["controls"]["resume_available"] is False
    assert not stop_path.exists()
