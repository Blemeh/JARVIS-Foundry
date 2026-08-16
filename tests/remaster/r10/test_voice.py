"""Voice probe honesty: real local interface or NOT_TESTED with probe output."""

from __future__ import annotations

import json
from pathlib import Path

from r10_helpers import FIXED_UTC, make_layout, seed_layout

from jarvis_foundry.command_center.voice_probe import probe_voice


def test_voice_probe_is_real_interface_or_honest_not_tested(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    result = probe_voice(layout, now_utc=FIXED_UTC)
    assert result.classification in {"ACTIVE", "TTS_ONLY", "NOT_TESTED"}
    assert result.probe["engine"] == "System.Speech"
    assert result.to_dict()["mock_used"] is False
    if result.classification in {"ACTIVE", "TTS_ONLY"}:
        tts = result.probe.get("tts", {})
        assert tts.get("available") is True
        assert tts.get("receipts")
        for receipt in tts["receipts"]:
            assert receipt.get("sha256")
            assert receipt.get("size", 0) > 0
        assert result.probe.get("wav_sha256")
    else:
        assert result.probe.get("raw_output")
        assert result.probe.get("sapi_available") in (True, False)


def test_voice_probe_persists_durable_receipt_store(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    result = probe_voice(layout, now_utc=FIXED_UTC)
    assert layout.voice.is_file()
    stored = json.loads(layout.voice.read_text(encoding="utf-8"))
    assert stored["classification"] == result.classification
    assert stored["probe"]["probe_utc"] == FIXED_UTC


def test_voice_never_uses_fixture_adapters(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    result = probe_voice(layout, now_utc=FIXED_UTC)
    serialized = json.dumps(result.to_dict()).lower()
    assert "fixturestt" not in serialized
    assert "fixturetts" not in serialized
