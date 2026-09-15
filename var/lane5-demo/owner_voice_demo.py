"""Isolated owner-voice demo. Never touches live Foundry state."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.owner_controls import sign_command  # noqa: E402
from jarvis_foundry.voice import FixtureSTT, FixtureTTS, Transcript, VoiceGateway  # noqa: E402
from jarvis_foundry.voice_session import OwnerVoiceSession  # noqa: E402
from jarvis_foundry.voice_speech import SapiTTSAdapter, WindowsSpeechSTTAdapter, write_audio_file  # noqa: E402


def main() -> int:
    demo = Path(__file__).resolve().parent
    home = demo / "generated" / "isolated-owner"
    if home.exists():
        for child in home.glob("*"):
            if child.is_file():
                child.unlink()
    home.mkdir(parents=True, exist_ok=True)
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    secret = b"lane5-demo-owner-secret-32bytesx"
    stop_path = home / "EMERGENCY_STOP"

    spoken = SapiTTSAdapter().synthesize("goal: prepare the owner briefing", language="en")
    wav = write_audio_file(spoken, home / "english-goal.wav")
    heard = WindowsSpeechSTTAdapter().transcribe(spoken.audio, language_hint="en")
    print(f"generated_goal_wav={wav} stt={heard.text!r}")
    matched_text = heard.text if heard.text.lower().startswith("goal") else "goal: prepare the owner briefing"
    if matched_text != heard.text:
        print("STT did not yield a GOAL phrase; using canonical demo text for owner-control mapping")

    gateway = VoiceGateway(
        database,
        owner_secret=secret,
        stt=FixtureSTT(Transcript(heard.text or "goal: prepare the owner briefing", "en")),
        tts=FixtureTTS(),
        stop_path=str(stop_path),
    )
    from jarvis_foundry.voice_commands import match_owner_utterance
    matched = match_owner_utterance(matched_text, language_hint="en")
    nonce = "demo-goal-1"
    signature = sign_command(secret, matched.command_type or "GOAL", "GLOBAL", matched.exact_text, nonce)
    goal = gateway.handle(spoken.audio, nonce=nonce, signature=signature, interaction_id="demo-goal", transcript=Transcript(matched.original, "en"))
    print(f"GOAL status={goal['status']} response={goal['response_text']!r} directive={goal['directive_id']}")

    session = OwnerVoiceSession(
        database,
        owner_secret=secret,
        stop_path=str(stop_path),
        stt=FixtureSTT(Transcript("ndalo krejt", "sq")),
        tts=FixtureTTS(),
    )
    stopped = session.process_text("ndalo krejt", language_hint="sq")
    print(f"SQ STOP status={stopped['status']} response={stopped['response_text']!r} stop_file={stop_path.exists()}")

    resumed = OwnerVoiceSession(
        database,
        owner_secret=secret,
        stop_path=str(stop_path),
        stt=FixtureSTT(Transcript("vazhdo", "sq")),
        tts=FixtureTTS(),
    ).process_text("vazhdo", language_hint="sq")
    print(f"SQ RESUME status={resumed['status']} response={resumed['response_text']!r}")
    return 0 if goal["status"] == "COMPLETED" and stopped["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
