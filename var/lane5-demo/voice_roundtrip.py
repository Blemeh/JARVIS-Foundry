"""Text -> TTS audio -> STT transcript. Isolated. No live JARVIS."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.voice_speech import (  # noqa: E402
    EdgeTTSAdapter,
    SapiTTSAdapter,
    WindowsSpeechSTTAdapter,
    write_audio_file,
)


def main() -> int:
    out = Path(__file__).resolve().parent / "generated"
    out.mkdir(parents=True, exist_ok=True)
    sapi = SapiTTSAdapter()
    edge = EdgeTTSAdapter()
    stt = WindowsSpeechSTTAdapter()

    en = sapi.synthesize("Stopped.", language="en")
    en_path = write_audio_file(en, out / "roundtrip-en.wav")
    en_transcript = stt.transcribe(en.audio, language_hint="en")
    print(f"EN TTS bytes={len(en.audio)} format={en.format} path={en_path}")
    print(f"EN STT transcript={en_transcript.text!r} language={en_transcript.language}")

    sq = edge.synthesize("U ndal.", language="sq")
    sq_path = write_audio_file(sq, out / "roundtrip-sq.mp3")
    print(f"SQ TTS bytes={len(sq.audio)} format={sq.format} path={sq_path}")
    try:
        stt.transcribe(sq.audio, language_hint="sq")
    except Exception as error:
        print(f"SQ STT unavailable as expected: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
