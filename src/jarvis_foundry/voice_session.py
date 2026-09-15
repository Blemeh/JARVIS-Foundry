"""Local push-to-talk / typed owner voice session. Not VoiceAuthority."""
from __future__ import annotations

import ctypes
import secrets
import sys
import time
import uuid
from pathlib import Path

from .database import FoundryDatabase
from .owner_controls import sign_command
from .voice import FixtureSTT, FixtureTTS, Transcript, VoiceGateway
from .voice_commands import KIND_COMMAND, KIND_STATUS, failure_text, match_owner_utterance
from .voice_speech import WindowsSpeechSTTAdapter, WindowsTTSAdapter


class OwnerVoiceSession:
    def __init__(
        self,
        database: FoundryDatabase,
        *,
        owner_secret: bytes,
        stop_path: str | None = None,
        stt=None,
        tts=None,
    ) -> None:
        self.stt = stt or WindowsSpeechSTTAdapter()
        self.tts = tts or WindowsTTSAdapter()
        self.gateway = VoiceGateway(
            database,
            owner_secret=owner_secret,
            stt=self.stt,
            tts=self.tts,
            stop_path=stop_path,
        )
        self.secret = owner_secret

    def process_text(self, text: str, *, language_hint: str | None = None) -> dict[str, object]:
        transcript = Transcript(text, language_hint or "en")
        return self._process(b"", transcript)

    def process_audio(self, audio: bytes, *, language_hint: str | None = None) -> dict[str, object]:
        transcript = self.stt.transcribe(audio, language_hint=language_hint)
        return self._process(audio, transcript)

    def _process(self, audio: bytes, transcript: Transcript) -> dict[str, object]:
        matched = match_owner_utterance(transcript.text, language_hint=transcript.language)
        nonce = secrets.token_hex(16)
        interaction_id = str(uuid.uuid4())
        signature = ""
        if matched.kind == KIND_COMMAND and matched.command_type:
            signature = sign_command(
                self.secret, matched.command_type, matched.target, matched.exact_text, nonce,
            )
        elif matched.kind == KIND_STATUS:
            signature = "status"
        else:
            spoken = failure_text(matched.language)
            try:
                audio_out = self.tts.synthesize(spoken, language=matched.language)
            except Exception:
                audio_out = None
            return {
                "status": "AMBIGUOUS",
                "transcript": transcript.text,
                "response_text": spoken,
                "command_type": None,
                "language": matched.language,
                "audio_metadata": {"status": "NOT_AVAILABLE"} if audio_out is None else {"format": audio_out.format},
            }
        return self.gateway.handle(
            audio,
            nonce=nonce,
            signature=signature,
            interaction_id=interaction_id,
            language_hint=transcript.language,
            transcript=transcript,
        )


def record_wav(path: str | Path, *, seconds: float = 6.0) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("push-to-talk recording is Windows-only")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    winmm = ctypes.windll.winmm
    alias = "jarvis_ptt"
    if winmm.mciSendStringW(f"open new type waveaudio alias {alias}", None, 0, None) != 0:
        raise RuntimeError("could not open wave recorder")
    try:
        if winmm.mciSendStringW(f"record {alias}", None, 0, None) != 0:
            raise RuntimeError("could not start recording")
        time.sleep(max(0.5, float(seconds)))
        if winmm.mciSendStringW(f'save {alias} "{target}"', None, 0, None) != 0:
            raise RuntimeError("could not save recording")
    finally:
        winmm.mciSendStringW(f"close {alias}", None, 0, None)
    return target


def session_from_isolated(
    database: FoundryDatabase,
    *,
    owner_secret: bytes,
    stop_path: str | None = None,
    fixture_text: str | None = None,
    fixture_language: str = "en",
) -> OwnerVoiceSession:
    if fixture_text is not None:
        return OwnerVoiceSession(
            database,
            owner_secret=owner_secret,
            stop_path=stop_path,
            stt=FixtureSTT(Transcript(fixture_text, fixture_language)),
            tts=FixtureTTS(),
        )
    return OwnerVoiceSession(database, owner_secret=owner_secret, stop_path=stop_path)
