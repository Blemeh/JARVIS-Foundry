"""Explicit local push-to-talk. Typed fallback if recording is skipped."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.voice_session import OwnerVoiceSession, record_wav  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="JARVIS local owner voice session")
    parser.add_argument("--text", help="typed utterance instead of microphone")
    parser.add_argument("--wav", help="existing wav path")
    parser.add_argument("--record", type=float, default=0, help="record N seconds after Enter")
    parser.add_argument("--lang", default="en", help="language hint en|sq")
    parser.add_argument("--db", default=str(Path(__file__).resolve().parent / "generated" / "ptt" / "foundry.db"))
    args = parser.parse_args()
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    database = FoundryDatabase(db_path)
    database.initialize()
    secret = b"lane5-local-ptt-secret-32bytesxx"
    session = OwnerVoiceSession(database, owner_secret=secret, stop_path=str(db_path.parent / "EMERGENCY_STOP"))
    if args.text:
        result = session.process_text(args.text, language_hint=args.lang)
    elif args.wav:
        result = session.process_audio(Path(args.wav).read_bytes(), language_hint=args.lang)
    elif args.record > 0:
        input("Press Enter to record, then wait...")
        wav = record_wav(db_path.parent / "utterance.wav", seconds=args.record)
        result = session.process_audio(wav.read_bytes(), language_hint=args.lang)
    else:
        parser.error("provide --text, --wav, or --record")
        return 2
    print(result)
    return 0 if result.get("status") in {"COMPLETED", "AMBIGUOUS", "REJECTED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
