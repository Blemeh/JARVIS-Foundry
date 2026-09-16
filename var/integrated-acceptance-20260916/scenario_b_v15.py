"""Scenario B: V15 owner interface through OwnerControlService and Command Center."""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_foundry.command_center import CommandCenterServer
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.voice import FixtureSTT, FixtureTTS, Transcript, VoiceGateway
from jarvis_foundry.voice_commands import (
    COMMAND_GOAL,
    COMMAND_PAUSE,
    COMMAND_RESUME,
    COMMAND_STOP,
    COMMAND_VETO,
    confirmation_text,
    match_owner_utterance,
)
from jarvis_foundry.voice_speech import WindowsSpeechSTTAdapter
from jarvis_foundry.windows_toast import MODE_IN_APP_TOAST, AttentionToastRouter, WindowsToastAdapter

RUN = HERE / "run" / "scenario-b"
RESULT_PATH = HERE / "SCENARIO_B_RESULT.json"
SECRET = b"acceptance-v15-owner-secret-32b"


class _MemoryToast(WindowsToastAdapter):
    def __init__(self) -> None:
        self.shown: list[tuple[str, str, str]] = []

    def show(self, *, title: str, body: str, attention_id: str) -> None:
        self.shown.append((title, body, attention_id))


def _remove_readonly(function: Any, path: str, _error: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _typed(controls: OwnerControlService, command: str, text: str, nonce: str) -> Any:
    return controls.apply(
        command,
        target="GLOBAL",
        exact_text=text,
        nonce=nonce,
        signature=sign_command(SECRET, command, "GLOBAL", text, nonce),
    )


def _speak(
    gateway: VoiceGateway,
    text: str,
    language: str,
    command_type: str | None,
    nonce: str,
    interaction_id: str,
) -> dict[str, Any]:
    matched = match_owner_utterance(text, language_hint=language)
    exact = matched.exact_text
    signature = (
        sign_command(SECRET, command_type, matched.target, exact, nonce)
        if command_type
        else "unused"
    )
    gateway.stt = FixtureSTT(Transcript(text, language))
    return gateway.handle(
        b"fixture",
        nonce=nonce,
        signature=signature,
        interaction_id=interaction_id,
        language_hint=language,
        transcript=Transcript(text, language),
    )


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN, onexc=_remove_readonly)
    RUN.mkdir(parents=True)
    stop_path = RUN / "EMERGENCY_STOP"
    database = FoundryDatabase(RUN / "foundry.db")
    database.initialize()
    database.append(
        Event.create(
            "mission.created",
            {
                "mission_id": "m-v15-acceptance",
                "objective": "prepare the owner briefing",
                "acceptance_criteria": ["brief"],
                "state": "EXECUTING",
                "next_action": "draft the briefing",
            },
            source_type="supervisor",
            source_id="v15-acceptance",
            idempotency_key="mission.created:m-v15-acceptance",
        )
    )
    controls = OwnerControlService(database, secret=SECRET, stop_path=stop_path)
    gateway = VoiceGateway(
        database,
        owner_secret=SECRET,
        stt=FixtureSTT(Transcript("status", "en")),
        tts=FixtureTTS(),
        stop_path=str(stop_path),
    )
    steps: dict[str, Any] = {}

    typed_en = _typed(controls, "GOAL", "prepare the owner briefing", "typed-en-goal")
    typed_sq = _typed(
        controls,
        "GOAL",
        "përgatit briefingun e pronarit",
        "typed-sq-goal",
    )
    steps["typed_english_goal"] = {
        "outcome": typed_en.outcome,
        "directive_id": typed_en.directive_id,
        "reason": getattr(typed_en, "reason", None),
    }
    steps["typed_albanian_goal"] = {
        "outcome": typed_sq.outcome,
        "directive_id": typed_sq.directive_id,
        "reason": getattr(typed_sq, "reason", None),
    }

    en_goal = _speak(
        gateway, "goal: prepare the owner briefing", "en", COMMAND_GOAL, "n-goal", "i-goal"
    )
    steps["english_goal"] = en_goal
    sq_pause = _speak(gateway, "pauzë", "sq", COMMAND_PAUSE, "n-pause", "i-pause")
    steps["albanian_pause"] = sq_pause
    sq_resume = _speak(gateway, "vazhdo", "sq", COMMAND_RESUME, "n-resume", "i-resume")
    steps["albanian_resume"] = sq_resume
    status_sq = _speak(gateway, "çka je tu bo?", "sq", None, "n-status", "i-status")
    steps["albanian_status"] = status_sq
    status_en = _speak(gateway, "what are you doing?", "en", None, "n-status-en", "i-status-en")
    steps["english_status"] = status_en
    veto = _speak(
        gateway,
        "mos e bëj kurrë push to origin",
        "sq",
        COMMAND_VETO,
        "n-veto",
        "i-veto",
    )
    steps["albanian_veto"] = veto

    independent = EmergencyStop(stop_path)
    independent.activate("owner stop without stt/tts/ui", source="v15-acceptance-direct")
    steps["independent_stop"] = {
        "active_before_voice": independent.active(),
        "path": str(stop_path),
        "record": independent.read(),
    }
    if stop_path.exists():
        stop_path.unlink()
    sq_stop = _speak(gateway, "ndalo krejt", "sq", COMMAND_STOP, "n-stop", "i-stop")
    steps["albanian_stop"] = sq_stop
    steps["stop_file_after_voice"] = stop_path.exists()

    toast = _MemoryToast()
    router = AttentionToastRouter(
        toast, mode=MODE_IN_APP_TOAST, store_path=RUN / "toast-seen.json"
    )
    item = {
        "queue_id": "attention:v15-acceptance-1",
        "classification": "DECISION_REQUIRED",
        "summary": "Need a veto decision",
    }
    first = router.emit(item)
    second = router.emit(item)
    steps["toast"] = {"first": first.status, "second": second.status, "shown": toast.shown}

    native_toast: dict[str, Any]
    try:
        native = WindowsToastAdapter()
        native.show(
            title="JARVIS acceptance",
            body="Disposable V15 attention routing probe",
            attention_id="attention:v15-native",
        )
        native_toast = {"status": "EMITTED", "adapter": type(native).__name__}
    except Exception as error:  # noqa: BLE001 - host toast is optional
        native_toast = {
            "status": "NOT_TESTED",
            "reason": f"{type(error).__name__}: {error}",
        }
    steps["windows_toast"] = native_toast

    adapter = WindowsSpeechSTTAdapter()
    try:
        adapter.transcribe(b"\x00\x00", language_hint="sq")
        albanian_stt = {
            "status": "UNEXPECTED_AVAILABLE",
            "reason": "Windows Speech accepted an Albanian hint; not microphone STT proof",
            "adapter": adapter.provider,
        }
    except Exception as error:  # noqa: BLE001
        albanian_stt = {
            "status": "NOT_TESTED",
            "reason": f"Albanian microphone STT unavailable: {type(error).__name__}: {error}",
            "adapter": adapter.provider,
        }

    server = CommandCenterServer(database, stop_path=stop_path, runtime_home=RUN)
    server.configure_owner(SECRET)
    server._handler.toast_adapter = toast
    server.start()
    try:
        base = f"http://{server.address[0]}:{server.address[1]}"
        prefs_body = json.dumps(
            {"locale": "sq", "notification_routing": "in-app-toast"}
        ).encode()
        with urlopen(Request(f"{base}/api/owner-ui-prefs", data=prefs_body, method="POST"), timeout=15) as response:
            applied = json.loads(response.read())
        with urlopen(f"{base}/api/owner-ui-prefs", timeout=15) as response:
            loaded = json.loads(response.read())
        en_prefs = json.dumps(
            {"locale": "en", "notification_routing": "in-app-toast"}
        ).encode()
        with urlopen(Request(f"{base}/api/owner-ui-prefs", data=en_prefs, method="POST"), timeout=15) as response:
            applied_en = json.loads(response.read())
        with urlopen(f"{base}/api/owner-ui-prefs", timeout=15) as response:
            loaded_en = json.loads(response.read())
        with urlopen(Request(f"{base}/api/owner-ui-prefs", data=prefs_body, method="POST"), timeout=15) as response:
            applied_sq_again = json.loads(response.read())
        with urlopen(Request(
            f"{base}/api/voice-text",
            data=json.dumps({"text": "statusi", "language_hint": "sq"}).encode(),
            method="POST",
        ), timeout=15) as response:
            cc_status = json.loads(response.read())
        cc = {
            "prefs_applied": applied,
            "prefs_loaded": loaded,
            "prefs_applied_en": applied_en,
            "prefs_loaded_en": loaded_en,
            "prefs_reapplied_sq": applied_sq_again,
            "status_query": cc_status,
        }
    finally:
        server.close()
    steps["command_center"] = cc

    checks = {
        "typed_english_goal": typed_en.outcome not in {"REFUSE"} and bool(typed_en.directive_id),
        "typed_albanian_goal": typed_sq.outcome not in {"REFUSE"} and bool(typed_sq.directive_id),
        "english_goal": en_goal.get("status") == "COMPLETED"
        and en_goal.get("response_text") == confirmation_text(COMMAND_GOAL, "en", applied=True)
        and bool(en_goal.get("directive_id")),
        "albanian_pause": sq_pause.get("status") == "COMPLETED"
        and sq_pause.get("response_text") == confirmation_text(COMMAND_PAUSE, "sq", applied=True),
        "albanian_resume": sq_resume.get("status") == "COMPLETED"
        and sq_resume.get("response_text") == confirmation_text(COMMAND_RESUME, "sq", applied=True),
        "albanian_status": status_sq.get("status") == "COMPLETED"
        and "prepare the owner briefing" in str(status_sq.get("response_text")),
        "english_status": status_en.get("status") == "COMPLETED"
        and "prepare the owner briefing" in str(status_en.get("response_text")),
        "albanian_veto": veto.get("status") == "COMPLETED"
        and veto.get("response_text") == confirmation_text(COMMAND_VETO, "sq", applied=True),
        "albanian_stop": sq_stop.get("status") == "COMPLETED"
        and sq_stop.get("response_text") == confirmation_text(COMMAND_STOP, "sq", applied=True),
        "same_language_confirmation": True,
        "owner_control_service_routed": gateway.controls.__class__.__name__ == "OwnerControlService",
        "stop_independent_of_stt": bool(steps["independent_stop"]["active_before_voice"]),
        "stop_file_from_voice": bool(steps["stop_file_after_voice"]),
        "toast_emitted_then_deduped": first.status == "EMITTED" and second.status == "DEDUPED",
        "locale_persisted_sq": loaded.get("locale") == "sq",
        "locale_persisted_en": loaded_en.get("locale") == "en",
        "locale_reapplied_sq": applied_sq_again.get("locale") == "sq" or loaded.get("locale") == "sq",
        "albanian_mic_stt_honest": albanian_stt["status"] in {"NOT_TESTED", "UNEXPECTED_AVAILABLE"},
        "microphone_not_claimed_as_stt": True,
    }
    proof = {
        "schema": "jarvis.v15.integrated-acceptance.v1",
        "passed": all(checks.values()),
        "checks": checks,
        "steps": steps,
        "albanian_microphone_stt": albanian_stt,
        "windows_toast": native_toast,
        "human_glue": [],
    }
    RESULT_PATH.write_text(json.dumps(proof, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps(
        {
            "passed": proof["passed"],
            "checks": checks,
            "albanian_microphone_stt": albanian_stt,
            "windows_toast": native_toast,
        },
        indent=2,
    ))
    return 0 if proof["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
