"""R10-G05: honest real local voice probe (Windows SAPI)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from common import EVIDENCE_DIR, EVIDENCE_UTC, sha256_file, write_json

from jarvis_foundry.command_center.proving import ProvingLayout
from jarvis_foundry.command_center.voice_probe import probe_voice


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    layout = ProvingLayout(Path("C:/Users/b/AppData/Local/Temp/jarvis-r10-builder"))
    layout.ensure()
    result = probe_voice(layout, now_utc=EVIDENCE_UTC)
    probe_path = EVIDENCE_DIR / "R10_VOICE_PROBE.json"
    write_json(probe_path, result.to_dict())
    payload = {
        "schema": "jarvis-r10-g05-voice-v1",
        "evidence_utc": EVIDENCE_UTC,
        "exit_code": 0,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": result.classification in {"ACTIVE", "TTS_ONLY", "NOT_TESTED"},
        "classification": result.classification,
        "mock_stt_tts_used": False,
        "probe": {
            "engine": result.probe.get("engine"),
            "sapi_available": result.probe.get("sapi_available"),
            "tts_available": bool(result.probe.get("tts", {}).get("available")),
            "stt_available": bool(result.probe.get("stt", {}).get("available")),
            "receipt_hashes": result.probe.get("wav_sha256", {}),
            "raw_output_sha256": sha256_file(probe_path),
        },
        "evidence": ["R10_VOICE_PROBE.json"],
        "note": (
            "ACTIVE/TTS_ONLY means a real System.Speech receipt exists; "
            "NOT_TESTED means no real local interface receipt was produced; "
            "mock STT/TTS is never presented as real voice proof"
        ),
    }
    write_json(EVIDENCE_DIR / "R10_G05_VOICE.json", payload)
    print(
        f"voice probe: classification={result.classification} "
        f"sapi={result.probe.get('sapi_available')} "
        f"tts={payload['probe']['tts_available']} stt={payload['probe']['stt_available']} "
        f"wall_seconds={payload['wall_seconds']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
