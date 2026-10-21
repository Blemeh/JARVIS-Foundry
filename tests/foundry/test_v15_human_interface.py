"""Lane 5 bilingual matcher, remainder preservation, and owner-control mapping."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

from jarvis_foundry.command_center import CommandCenterServer
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import sign_command
from jarvis_foundry.voice import FixtureSTT, FixtureTTS, Transcript, VoiceGateway
from jarvis_foundry.voice_commands import (
    COMMAND_GOAL,
    COMMAND_PAUSE,
    COMMAND_RESUME,
    COMMAND_STOP,
    COMMAND_VETO,
    KIND_COMMAND,
    KIND_STATUS,
    match_owner_utterance,
)
from jarvis_foundry.windows_toast import MODE_IN_APP_TOAST, AttentionToastRouter, WindowsToastAdapter


CASES = (
    ("stop", COMMAND_STOP, "en", "stop", ""),
    ("stop everything", COMMAND_STOP, "en", "stop everything", ""),
    ("please pause", COMMAND_PAUSE, "en", "please pause", ""),
    ("resume", COMMAND_RESUME, "en", "resume", ""),
    ("continue", COMMAND_RESUME, "en", "continue", ""),
    ("handle this: prepare tomorrow", COMMAND_GOAL, "en", "prepare tomorrow", "prepare tomorrow"),
    ("goal: never miss the briefing", COMMAND_GOAL, "en", "never miss the briefing", "never miss the briefing"),
    ("never do push to origin", COMMAND_VETO, "en", "push to origin", "push to origin"),
    ("don't ever do delete the ledger", COMMAND_VETO, "en", "delete the ledger", "delete the ledger"),
    ("ndalo", COMMAND_STOP, "sq", "ndalo", ""),
    ("ndalo krejt", COMMAND_STOP, "sq", "ndalo krejt", ""),
    ("ndalo gjithçka", COMMAND_STOP, "sq", "ndalo gjithçka", ""),
    ("pauzë", COMMAND_PAUSE, "sq", "pauzë", ""),
    ("ndale për momentin", COMMAND_PAUSE, "sq", "ndale për momentin", ""),
    ("vazhdo", COMMAND_RESUME, "sq", "vazhdo", ""),
    ("merre këtë: përgatit nesër", COMMAND_GOAL, "sq", "përgatit nesër", "përgatit nesër"),
    ("qëllimi është shkruaj briefingun", COMMAND_GOAL, "sq", "shkruaj briefingun", "shkruaj briefingun"),
    ("mos e bëj kurrë push to main", COMMAND_VETO, "sq", "push to main", "push to main"),
)


def test_matcher_table_and_remainder_preservation() -> None:
    for text, command_type, language, exact_text, remainder in CASES:
        matched = match_owner_utterance(text)
        assert matched.kind == KIND_COMMAND, text
        assert matched.command_type == command_type, text
        assert matched.language == language, text
        assert matched.exact_text == exact_text, (text, matched.exact_text)
        assert matched.remainder == remainder, (text, matched.remainder)


def test_wake_prefix_is_stripped_before_match() -> None:
    for text, command_type, language in (
        ("jarvis, stop", COMMAND_STOP, "en"),
        ("hey jarvis pause", COMMAND_PAUSE, "en"),
        ("ok jarvis ndalo", COMMAND_STOP, "sq"),
        ("JARVIS vazhdo", COMMAND_RESUME, "sq"),
    ):
        matched = match_owner_utterance(text)
        assert matched.kind == KIND_COMMAND, text
        assert matched.command_type == command_type, text
        assert matched.language == language, text


def test_status_queries_are_not_owner_commands() -> None:
    for text, language in (
        ("what are you doing?", "en"),
        ("status", "en"),
        ("çka je tu bo?", "sq"),
        ("çfarë po bën", "sq"),
        ("statusi", "sq"),
    ):
        matched = match_owner_utterance(text)
        assert matched.kind == KIND_STATUS, text
        assert matched.command_type is None, text
        assert matched.language == language, text


def test_voice_gateway_maps_goal_and_albanian_stop(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    secret = b"lane5-owner-secret-bytes-32ok"
    stop = tmp_path / "STOP"
    gateway = VoiceGateway(
        database,
        owner_secret=secret,
        stt=FixtureSTT(Transcript("goal: prepare the owner briefing", "en")),
        tts=FixtureTTS(),
        stop_path=str(stop),
    )
    nonce = "goal-1"
    signature = sign_command(secret, COMMAND_GOAL, "GLOBAL", "prepare the owner briefing", nonce)
    result = gateway.handle(b"fixture", nonce=nonce, signature=signature, interaction_id="g1")
    assert result["status"] == "COMPLETED"
    assert result["response_text"] == "Goal recorded."
    assert result["directive_id"]
    row = database.row("directives", "directive_id", str(result["directive_id"]))
    assert row["command_type"] == "GOAL"
    assert row["exact_text"] == "prepare the owner briefing"

    sq_gateway = VoiceGateway(
        database,
        owner_secret=secret,
        stt=FixtureSTT(Transcript("ndalo krejt", "sq")),
        tts=FixtureTTS(),
        stop_path=str(stop),
    )
    sq_sig = sign_command(secret, COMMAND_STOP, "GLOBAL", "ndalo krejt", "stop-sq")
    stopped = sq_gateway.handle(b"fixture", nonce="stop-sq", signature=sq_sig, interaction_id="s1")
    assert stopped["status"] == "COMPLETED"
    assert stopped["response_text"] == "U ndal."
    assert stop.exists()


def test_voice_does_not_claim_success_when_unauthenticated(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    gateway = VoiceGateway(
        database,
        owner_secret=b"lane5-owner-secret-bytes-32ok",
        stt=FixtureSTT(Transcript("pause", "en")),
        tts=FixtureTTS(),
        stop_path=str(tmp_path / "STOP"),
    )
    result = gateway.handle(b"fixture", nonce="x", signature="nope", interaction_id="bad")
    assert result["status"] == "REJECTED"
    assert result["response_text"] != "Paused."


def test_status_uses_snapshot_truth(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "mission.created",
        {"mission_id": "m-status", "objective": "prepare tomorrow", "acceptance_criteria": ["brief"], "state": "EXECUTING", "next_action": "draft the briefing"},
        source_type="supervisor", source_id="test", idempotency_key="mission.created:m-status",
    ))
    gateway = VoiceGateway(
        database,
        owner_secret=b"lane5-owner-secret-bytes-32ok",
        stt=FixtureSTT(Transcript("what are you doing", "en")),
        tts=FixtureTTS(),
        stop_path=str(tmp_path / "STOP"),
    )
    result = gateway.handle(b"fixture", nonce="st", signature="unused", interaction_id="status-1")
    assert result["status"] == "COMPLETED"
    assert "prepare tomorrow" in str(result["response_text"])
    assert "AI thinking" not in str(result["response_text"])


def test_snapshot_exposes_shared_briefing_l10n(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    from jarvis_foundry.command_center import build_snapshot
    snapshot = build_snapshot(database, stop_path=tmp_path / "STOP")
    assert "briefing_l10n" in snapshot
    assert snapshot["briefing_l10n"]["en"]["lang"] == "en"
    assert snapshot["briefing_l10n"]["sq"]["lang"] == "sq"


class _MemoryToast(WindowsToastAdapter):
    def __init__(self) -> None:
        self.shown: list[tuple[str, str, str]] = []

    def show(self, *, title: str, body: str, attention_id: str) -> None:
        self.shown.append((title, body, attention_id))


def test_toast_router_dedups_attention_identity(tmp_path: Path) -> None:
    adapter = _MemoryToast()
    router = AttentionToastRouter(adapter, mode=MODE_IN_APP_TOAST, store_path=tmp_path / "seen.json")
    item = {"queue_id": "attention:directive-1", "classification": "DECISION_REQUIRED", "summary": "Need a veto decision"}
    first = router.emit(item)
    second = router.emit(item)
    assert first.status == "EMITTED" and first.delivered
    assert second.status == "DEDUPED"
    assert len(adapter.shown) == 1
    restarted = AttentionToastRouter(adapter, mode=MODE_IN_APP_TOAST, store_path=tmp_path / "seen.json")
    third = restarted.emit(item)
    assert third.status == "DEDUPED"


def test_command_center_voice_text_prefs_and_attention_toasts(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    secret = b"lane5-cc-voice-secret-bytes-32ok"
    server = CommandCenterServer(database, stop_path=tmp_path / "STOP", runtime_home=tmp_path)
    server.configure_owner(secret)
    adapter = _MemoryToast()
    server._handler.toast_adapter = adapter
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        voice_body = json.dumps({"text": "jarvis ndalo", "language_hint": "sq"}).encode()
        with urlopen(Request(f"{base}/api/voice-text", data=voice_body, method="POST"), timeout=15) as response:
            spoken = json.loads(response.read())
        assert spoken["status"] == "COMPLETED"
        assert spoken["command_type"] == COMMAND_STOP
        assert spoken["response_text"] == "U ndal."
        assert (tmp_path / "STOP").exists()

        prefs_body = json.dumps({"locale": "sq", "notification_routing": "in-app-toast"}).encode()
        with urlopen(Request(f"{base}/api/owner-ui-prefs", data=prefs_body, method="POST"), timeout=15) as response:
            prefs = json.loads(response.read())
        assert prefs["status"] == "APPLIED"
        assert prefs["locale"] == "sq"
        with urlopen(f"{base}/api/owner-ui-prefs", timeout=15) as response:
            loaded = json.loads(response.read())
        assert loaded["locale"] == "sq"

        def fake_snapshot(self):
            return {
                "decision_queue": {
                    "open": [{
                        "queue_id": "attention:cc-1",
                        "classification": "DECISION_REQUIRED",
                        "summary": "Need a veto decision",
                    }],
                },
            }

        server._handler._cached_snapshot = fake_snapshot
        with urlopen(Request(f"{base}/api/attention-toasts", data=b"{}", method="POST"), timeout=15) as response:
            delivery = json.loads(response.read())
        assert delivery["status"] == "READY"
        assert delivery["mode"] == "in-app-toast"
        assert delivery["results"][0]["status"] == "EMITTED"
        assert adapter.shown == [("DECISION_REQUIRED", "Need a veto decision", "attention:cc-1")]
        with urlopen(f"{base}/api/attention-toasts", timeout=15) as response:
            last = json.loads(response.read())
        assert last["results"][0]["attention_id"] == "attention:cc-1"
    finally:
        server.close()

def test_only_fresh_owner_actionable_items_toast(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from jarvis_foundry.owner_ui import deliver_open_attention_toasts

    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)

    def item(queue_id: str, classification: str, opened_at: str) -> dict:
        return {"queue_id": queue_id, "classification": classification, "summary": queue_id, "opened_at": opened_at}

    adapter = _MemoryToast()
    items = [
        item("fresh-decision", "DECISION_REQUIRED", "2026-10-21T09:00:00Z"),
        item("fresh-veto", "OWNER_VETO_ACTIVE", "2026-10-21T08:00:00Z"),
        item("unknown-noise", "UNKNOWN", "2026-10-21T11:59:00Z"),
        item("inform-only", "INFORM", "2026-10-21T11:59:00Z"),
        item("stale-decision", "DECISION_REQUIRED", "2026-09-28T21:40:00Z"),
        item("ageless-decision", "DECISION_REQUIRED", ""),
    ]
    results = deliver_open_attention_toasts(
        items, adapter=adapter, mode=MODE_IN_APP_TOAST, store_path=tmp_path / "seen.json", now=now,
    )
    expected = ["ageless-decision", "fresh-decision", "fresh-veto"]  # unknown age is never held back
    assert sorted(attention for _t, _b, attention in adapter.shown) == expected
    assert {r["attention_id"] for r in results} == set(expected)
    again = deliver_open_attention_toasts(
        items, adapter=adapter, mode=MODE_IN_APP_TOAST, store_path=tmp_path / "seen.json", now=now,
    )
    assert {r["status"] for r in again} == {"DEDUPED"}
    assert len(adapter.shown) == 3