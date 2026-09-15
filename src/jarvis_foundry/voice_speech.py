"""Windows-local STT/TTS adapters. Fixtures stay in voice.py for tests."""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import wave
from pathlib import Path

from .voice import SpeechUnavailable, SynthesizedAudio, Transcript

_EDGE_EN = "en-US-JennyNeural"
_EDGE_SQ = "sq-AL-AnilaNeural"


def _read_wav_duration_ms(path: Path) -> int | None:
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 1
            return int(frames / rate * 1000)
    except (OSError, wave.Error):
        return None


class SapiTTSAdapter:
    provider = "windows-sapi"

    def synthesize(self, text: str, *, language: str = "en") -> SynthesizedAudio:
        lang = (language or "en").lower()
        if lang.startswith("sq"):
            raise SpeechUnavailable("Windows SAPI has no Albanian voice on this PC")
        try:
            import win32com.client
        except ImportError as error:
            raise SpeechUnavailable("pywin32 is required for SAPI TTS") from error
        spoken = text.strip() or " "
        fd, raw_path = tempfile.mkstemp(prefix="jarvis-sapi-", suffix=".wav")
        os.close(fd)
        path = Path(raw_path)
        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Open(str(path), 3)
            voice.AudioOutputStream = stream
            voice.Speak(spoken)
            stream.Close()
            audio = path.read_bytes()
            if not audio:
                raise SpeechUnavailable("SAPI produced empty audio")
            return SynthesizedAudio(audio, self.provider, "audio/wav", _read_wav_duration_ms(path))
        except SpeechUnavailable:
            raise
        except Exception as error:
            raise SpeechUnavailable(f"SAPI TTS failed: {error}") from error
        finally:
            path.unlink(missing_ok=True)


class EdgeTTSAdapter:
    provider = "edge-tts"

    def synthesize(self, text: str, *, language: str = "en") -> SynthesizedAudio:
        try:
            import edge_tts
        except ImportError as error:
            raise SpeechUnavailable("edge-tts is not installed") from error
        lang = (language or "en").lower()
        voice = _EDGE_SQ if lang.startswith("sq") else _EDGE_EN
        spoken = text.strip() or " "
        fd, raw_path = tempfile.mkstemp(prefix="jarvis-edge-", suffix=".mp3")
        os.close(fd)
        path = Path(raw_path)
        try:
            async def _run() -> None:
                communicator = edge_tts.Communicate(spoken, voice)
                await communicator.save(str(path))

            asyncio.run(_run())
            audio = path.read_bytes()
            if not audio:
                raise SpeechUnavailable("edge-tts produced empty audio")
            return SynthesizedAudio(audio, self.provider, "audio/mpeg", None)
        except SpeechUnavailable:
            raise
        except Exception as error:
            raise SpeechUnavailable(f"edge-tts failed: {error}") from error
        finally:
            path.unlink(missing_ok=True)


class WindowsSpeechSTTAdapter:
    provider = "windows-speech"

    def transcribe(self, audio: bytes, *, language_hint: str | None = None) -> Transcript:
        hint = (language_hint or "en").lower()
        if hint.startswith("sq"):
            raise SpeechUnavailable("Windows Speech has no Albanian recognizer configured")
        if not audio:
            raise SpeechUnavailable("empty audio")
        fd, raw_path = tempfile.mkstemp(prefix="jarvis-stt-", suffix=".wav")
        os.close(fd)
        path = Path(raw_path)
        try:
            path.write_bytes(audio)
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine; "
                f"$engine.SetInputToWaveFile('{str(path).replace(chr(39), chr(39)+chr(39))}'); "
                "$engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar)); "
                "$result = $engine.Recognize(); "
                "if ($null -eq $result) { Write-Output '' } else { Write-Output $result.Text }; "
                "$engine.Dispose()"
            )
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "windows speech failed").strip()
                raise SpeechUnavailable(detail)
            text = (completed.stdout or "").strip()
            if not text:
                raise SpeechUnavailable("Windows Speech returned an empty transcript")
            return Transcript(text, "en", None)
        finally:
            path.unlink(missing_ok=True)


class WindowsTTSAdapter:
    """English via SAPI when possible; Albanian via Edge TTS."""

    provider = "windows-tts"

    def __init__(self) -> None:
        self._sapi = SapiTTSAdapter()
        self._edge = EdgeTTSAdapter()

    def synthesize(self, text: str, *, language: str = "en") -> SynthesizedAudio:
        lang = (language or "en").lower()
        if lang.startswith("sq"):
            spoken = self._edge.synthesize(text, language="sq")
            return SynthesizedAudio(spoken.audio, f"{self.provider}/{spoken.provider}", spoken.format, spoken.duration_ms)
        try:
            spoken = self._sapi.synthesize(text, language="en")
            return SynthesizedAudio(spoken.audio, f"{self.provider}/{spoken.provider}", spoken.format, spoken.duration_ms)
        except SpeechUnavailable:
            spoken = self._edge.synthesize(text, language="en")
            return SynthesizedAudio(spoken.audio, f"{self.provider}/{spoken.provider}", spoken.format, spoken.duration_ms)


def default_tts_adapter() -> WindowsTTSAdapter:
    return WindowsTTSAdapter()


def default_stt_adapter() -> WindowsSpeechSTTAdapter:
    return WindowsSpeechSTTAdapter()


def write_audio_file(audio: SynthesizedAudio, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(audio.audio)
    return target
