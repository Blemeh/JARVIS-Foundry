"""Provider-neutral voice boundary with authenticated owner directives."""
from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from typing import Protocol

from .command_center import build_snapshot
from .database import FoundryDatabase
from .events import Event, EventConflict
from .owner_controls import OwnerAuthenticationError, OwnerControlService, OwnerReplayError, sign_command
from .voice_commands import (
    COMMAND_RESUME,
    KIND_COMMAND,
    KIND_STATUS,
    confirmation_text,
    failure_text,
    match_owner_utterance,
    normalize_language,
)
from .voice_status import render_owner_status


class SpeechUnavailable(RuntimeError):
    pass


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


class VoiceGateway:
    def __init__(self, database: FoundryDatabase, *, owner_secret: bytes, stt: SpeechToTextAdapter, tts: TextToSpeechAdapter, stop_path: str | None = None) -> None:
        self.database = database
        self.controls = OwnerControlService(database, secret=owner_secret, stop_path=stop_path)
        self.stt = stt
        self.tts = tts

    def handle(
        self,
        audio: bytes,
        *,
        nonce: str,
        signature: str,
        interaction_id: str | None = None,
        language_hint: str | None = "en",
        transcript: Transcript | None = None,
    ) -> dict[str, object]:
        interaction_id = interaction_id or str(uuid.uuid4())
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM voice_interactions WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
        if existing is not None:
            return {"interaction_id": interaction_id, "status": "REJECTED", "auth_status": "REPLAYED",
                    "transcript": None, "response_text": "This voice command was already recorded.",
                    "directive_id": None, "audio_metadata": {"status": "NOT_AVAILABLE"},
                    "error": "voice interaction replay rejected"}
        captured: Transcript | None = transcript
        command_type = target = response = directive_id = None
        status = "NOT_TESTED"
        auth_status = "UNAUTHENTICATED"
        error: str | None = None
        spoken_language = normalize_language(language_hint)
        try:
            if captured is None:
                captured = self.stt.transcribe(audio, language_hint=language_hint)
            if not captured.text.strip():
                status, auth_status, error = "MALFORMED", "UNAUTHENTICATED", "empty transcript"
            else:
                matched = match_owner_utterance(captured.text, language_hint=captured.language or language_hint)
                spoken_language = matched.language
                if matched.kind == KIND_STATUS:
                    snapshot = build_snapshot(self.database, stop_path=self.controls.stop.path if self.controls.stop else None)
                    response = render_owner_status(snapshot, matched.language)
                    status, auth_status = "COMPLETED", "AUTHENTICATED"
                elif matched.kind != KIND_COMMAND or matched.command_type is None:
                    status, auth_status, error = "AMBIGUOUS", "UNAUTHENTICATED", "ambiguous or unsupported command"
                    response = failure_text(matched.language)
                else:
                    command_type, target = matched.command_type, matched.target
                    expected = sign_command(self.controls.secret, command_type, target, matched.exact_text, nonce)
                    if not hmac.compare_digest(expected, signature or ""):
                        raise OwnerAuthenticationError("voice command authentication failed")
                    applied = False
                    if command_type == COMMAND_RESUME:
                        self.controls.resume(nonce=nonce, signature=signature, exact_text=matched.exact_text)
                        applied = True
                    else:
                        result = self.controls.apply(command_type, target=target, exact_text=matched.exact_text, nonce=nonce, signature=signature)
                        directive_id = result.directive_id
                        applied = result.outcome not in {"REFUSE"}
                    auth_status = "AUTHENTICATED"
                    response = confirmation_text(command_type, matched.language, applied=applied)
                    status = "COMPLETED" if applied else "REJECTED"
                    if not applied:
                        error = "owner control refused the command"
        except OwnerReplayError as exc:
            status, auth_status, error = "REJECTED", "REPLAYED", str(exc)
        except OwnerAuthenticationError as exc:
            status, auth_status, error = "REJECTED", "UNAUTHENTICATED", str(exc)
        except Exception as exc:
            status, auth_status, error = "BLOCKED", auth_status, f"voice dependency unavailable: {exc}"
        if response is None:
            response = failure_text(spoken_language)
        try:
            spoken = self.tts.synthesize(str(response), language=spoken_language)
            audio_metadata = {"format": spoken.format, "duration_ms": spoken.duration_ms, "sha256": hashlib.sha256(spoken.audio).hexdigest()}
            tts_provider = spoken.provider
        except Exception as exc:
            audio_metadata = {"status": "NOT_AVAILABLE", "error": str(exc)}
            tts_provider = getattr(self.tts, "provider", "UNKNOWN")
        event = Event.create(
            "voice.interaction_recorded",
            {"interaction_id": interaction_id, "auth_status": auth_status, "language": captured.language if captured else language_hint,
             "transcript": captured.text if captured else None, "response_text": response,
             "stt_provider": getattr(self.stt, "provider", "UNKNOWN"), "tts_provider": tts_provider,
             "command_type": command_type, "directive_id": directive_id, "status": status,
             "audio_metadata": audio_metadata, "evidence": {"error": error} if error else {"directive_id": directive_id}},
            source_type="voice", source_id=getattr(self.stt, "provider", "voice-boundary"),
            idempotency_key=f"voice.interaction_recorded:{interaction_id}",
        )
        try:
            self.database.append(event)
        except EventConflict:
            with self.database.connect() as connection:
                winner = connection.execute("SELECT * FROM voice_interactions WHERE interaction_id = ?", (interaction_id,)).fetchone()
            if winner is not None:
                return {"interaction_id": interaction_id, "status": "REJECTED", "auth_status": "REPLAYED", "transcript": None, "response_text": "This voice command was already recorded.", "directive_id": None, "audio_metadata": {"status": "NOT_AVAILABLE"}, "error": "voice interaction replay rejected"}
            raise
        return {
            "interaction_id": interaction_id,
            "status": status,
            "auth_status": auth_status,
            "transcript": captured.text if captured else None,
            "response_text": response,
            "directive_id": directive_id,
            "audio_metadata": audio_metadata,
            "error": error,
            "command_type": command_type,
            "language": spoken_language,
        }
