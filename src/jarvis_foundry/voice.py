"""Provider-neutral English voice boundary with authenticated directives."""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Protocol

from .database import FoundryDatabase
from .events import Event, EventConflict
from .owner_controls import OwnerAuthenticationError, OwnerControlService, OwnerReplayError, sign_command


class SpeechToTextAdapter(Protocol):
    provider: str
    def transcribe(self, audio: bytes, *, language_hint: str | None = None) -> "Transcript": ...


class TextToSpeechAdapter(Protocol):
    provider: str
    def synthesize(self, text: str, *, language: str = "en") -> "SynthesizedAudio": ...


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    language: str
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    audio: bytes
    provider: str
    format: str = "audio/wav"
    duration_ms: int | None = None


class FixtureSTT:
    provider = "fixture-stt"
    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript
    def transcribe(self, audio: bytes, *, language_hint: str | None = None) -> Transcript:
        return self.transcript


class FixtureTTS:
    provider = "fixture-tts"
    def synthesize(self, text: str, *, language: str = "en") -> SynthesizedAudio:
        return SynthesizedAudio(text.encode("utf-8"), self.provider, "text/fixture", len(text) * 10)


_COMMAND_PATTERNS = (
    ("STOP_EVERYTHING", re.compile(r"^(?:please\s+)?stop(?:\s+everything)?$", re.I), "GLOBAL"),
    ("PAUSE", re.compile(r"^(?:please\s+)?pause(?:\s+jarvis)?$", re.I), "GLOBAL"),
    ("RESUME", re.compile(r"^(?:please\s+)?resume(?:\s+jarvis)?$", re.I), "GLOBAL"),
)


class VoiceGateway:
    def __init__(self, database: FoundryDatabase, *, owner_secret: bytes, stt: SpeechToTextAdapter, tts: TextToSpeechAdapter, stop_path: str | None = None) -> None:
        self.database = database
        self.controls = OwnerControlService(database, secret=owner_secret, stop_path=stop_path)
        self.stt = stt
        self.tts = tts

    def handle(self, audio: bytes, *, nonce: str, signature: str, interaction_id: str | None = None, language_hint: str | None = "en") -> dict[str, object]:
        interaction_id = interaction_id or str(uuid.uuid4())
        # A client retry with the same interaction identity is a read-only
        # replay of the first durable result.  It must not re-run STT/TTS or
        # attempt a second owner directive.
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM voice_interactions WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
        if existing is not None:
            # A voice command replay is explicitly rejected.  The first
            # durable interaction remains queryable from the Command Center,
            # but a repeated audio request cannot impersonate the owner or
            # cause a second directive.
            return {"interaction_id": interaction_id, "status": "REJECTED", "auth_status": "REPLAYED",
                    "transcript": None, "response_text": "This voice command was already recorded.",
                    "directive_id": None, "audio_metadata": {"status": "NOT_AVAILABLE"},
                    "error": "voice interaction replay rejected"}
        transcript: Transcript | None = None
        command_type = target = response = directive_id = None
        status = "NOT_TESTED"
        auth_status = "UNAUTHENTICATED"
        error: str | None = None
        try:
            transcript = self.stt.transcribe(audio, language_hint=language_hint)
            if transcript.language.lower() not in {"en", "eng", "en-us", "en-gb"}:
                status, auth_status, error = "REJECTED", "UNAUTHORIZED", "English voice command required"
            elif not transcript.text.strip():
                status, auth_status, error = "MALFORMED", "UNAUTHENTICATED", "empty transcript"
            else:
                matches = [(kind, tgt) for kind, pattern, tgt in _COMMAND_PATTERNS if pattern.fullmatch(transcript.text.strip())]
                if len(matches) != 1:
                    status, auth_status, error = "AMBIGUOUS", "UNAUTHENTICATED", "ambiguous or unsupported English command"
                else:
                    command_type, target = matches[0]
                    expected = sign_command(self.controls.secret, command_type, target, transcript.text.strip(), nonce)
                    if not expected == signature:
                        raise OwnerAuthenticationError("voice command authentication failed")
                    if command_type == "RESUME":
                        self.controls.resume(nonce=nonce, signature=signature, exact_text=transcript.text.strip())
                    else:
                        result = self.controls.apply(command_type, target=target, exact_text=transcript.text.strip(), nonce=nonce, signature=signature)
                        directive_id = result.directive_id
                    auth_status = "AUTHENTICATED"
                    response = {"STOP_EVERYTHING": "JARVIS is stopped.", "PAUSE": "JARVIS is paused.", "RESUME": "JARVIS resumed."}[command_type]
                    status = "COMPLETED"
        except OwnerReplayError as exc:
            status, auth_status, error = "REJECTED", "REPLAYED", str(exc)
        except OwnerAuthenticationError as exc:
            status, auth_status, error = "REJECTED", "UNAUTHENTICATED", str(exc)
        except Exception as exc:  # adapters fail closed into an auditable record
            status, auth_status, error = "BLOCKED", auth_status, f"voice dependency unavailable: {exc}"
        if response is None:
            response = "I could not execute that command."
        try:
            spoken = self.tts.synthesize(response, language="en")
            audio_metadata = {"format": spoken.format, "duration_ms": spoken.duration_ms, "sha256": hashlib.sha256(spoken.audio).hexdigest()}
            tts_provider = spoken.provider
        except Exception as exc:
            audio_metadata = {"status": "NOT_AVAILABLE", "error": str(exc)}
            tts_provider = getattr(self.tts, "provider", "UNKNOWN")
        event = Event.create(
            "voice.interaction_recorded",
            {"interaction_id": interaction_id, "auth_status": auth_status, "language": transcript.language if transcript else language_hint,
             "transcript": transcript.text if transcript else None, "response_text": response,
             "stt_provider": getattr(self.stt, "provider", "UNKNOWN"), "tts_provider": tts_provider,
             "command_type": command_type, "directive_id": directive_id, "status": status,
             "audio_metadata": audio_metadata, "evidence": {"error": error} if error else {"directive_id": directive_id}},
            source_type="voice", source_id=getattr(self.stt, "provider", "voice-boundary"),
            idempotency_key=f"voice.interaction_recorded:{interaction_id}",
        )
        try:
            self.database.append(event)
        except EventConflict:
            # Another process won the same interaction id between the read
            # above and this append.  Return the durable winner on retry.
            with self.database.connect() as connection:
                winner = connection.execute("SELECT * FROM voice_interactions WHERE interaction_id = ?", (interaction_id,)).fetchone()
            if winner is not None:
                return {"interaction_id": interaction_id, "status": "REJECTED", "auth_status": "REPLAYED", "transcript": None, "response_text": "This voice command was already recorded.", "directive_id": None, "audio_metadata": {"status": "NOT_AVAILABLE"}, "error": "voice interaction replay rejected"}
            raise
        return {"interaction_id": interaction_id, "status": status, "auth_status": auth_status, "transcript": transcript.text if transcript else None, "response_text": response, "directive_id": directive_id, "audio_metadata": audio_metadata, "error": error}
