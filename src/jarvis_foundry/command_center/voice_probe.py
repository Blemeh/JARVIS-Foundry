"""Honest probe of a REAL local Windows SAPI voice interface.

Fixture/mock STT/TTS is never used here. The probe invokes System.Speech
directly and records only real engine output; if the interface is not
available the result is NOT_TESTED with the actual probe output.
"""

from __future__ import annotations

import hashlib
import json
import subprocess  # nosec B404 - real local SAPI probe; no shell=True is ever used
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .proving import ProvingLayout

POWERSHELL = "powershell"


def _powershell_script(wav_dir: Path) -> str:
    wav_json = json.dumps(str(wav_dir))
    return f"""
$ErrorActionPreference = 'SilentlyContinue'
$out = [ordered]@{{ engine = 'System.Speech'; sapi_available = $false;
  tts = $null; stt = $null; raw_errors = @() }}
try {{
  Add-Type -AssemblyName System.Speech
  $out.sapi_available = $true
  $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $voices = @()
  foreach ($voice in $synth.GetInstalledVoices()) {{
    $info = $voice.VoiceInfo
    $voices += [ordered]@{{ name = $info.Name; culture = $info.Culture.Name;
      gender = [string]$info.Gender; age = [string]$info.Age }}
  }}
  $ttsReceipts = @()
  foreach ($entry in @(@('en', 'JARVIS Command Center probe.'), @('sq', 'Qendra e komandës JARVIS.'))) {{
    $lang = $entry[0]; $text = $entry[1]
    $wav = Join-Path {wav_json} ('sapi-tts-' + $lang + '.wav')
    try {{
      $synth.SetOutputToWaveFile($wav)
      $synth.Speak($text)
      $synth.SetOutputToNull()
      if (Test-Path -LiteralPath $wav) {{
        $file = Get-Item -LiteralPath $wav
        $bytes = [System.IO.File]::ReadAllBytes($wav)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLower()
        $ttsReceipts += [ordered]@{{ language = $lang; text = $text;
          wav = $wav; size = $file.Length; sha256 = $hash;
          riff_header = -join ([System.IO.File]::ReadAllBytes($wav)[0..3]) }}
      }}
    }} catch {{
      $out.raw_errors += ('tts-' + $lang + ': ' + $_.Exception.Message)
    }}
  }}
  $synth.Dispose()
  $out.tts = [ordered]@{{ available = ($ttsReceipts.Count -gt 0)
    voices = $voices; receipts = $ttsReceipts }}
  $recognizers = @([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers())
  if ($recognizers.Count -gt 0) {{
    try {{
      $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($recognizers[0].Id)
      $engine.SetInputToWaveFile((Join-Path {wav_json} 'sapi-tts-en.wav'))
      $result = $engine.Recognize()
      $out.stt = [ordered]@{{ available = $true; recognizer = $recognizers[0].Id;
        recognized_text = [string]$result.Text; confidence = [double]$result.Confidence }}
      $engine.Dispose()
    }} catch {{
      $out.stt = [ordered]@{{ available = $false; recognizers = $recognizers.Count;
        error = $_.Exception.Message }}
    }}
  }} else {{
    $out.stt = [ordered]@{{ available = $false; recognizers = 0;
      reason = 'no installed speech recognition engines' }}
  }}
}} catch {{
  $out.raw_errors += ('probe: ' + $_.Exception.Message)
}}
$out | ConvertTo-Json -Depth 8 -Compress
""".replace("{wav_json}", wav_json)


@dataclass(frozen=True)
class VoiceProbeResult:
    classification: str
    probe: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "jarvis-r10-voice-probe-v1",
            "classification": self.classification,
            "mock_used": False,
            "policy": "mock STT/TTS is never presented as real voice proof",
            "probe": self.probe,
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def probe_voice(layout: ProvingLayout, *, now_utc: str) -> VoiceProbeResult:
    """Probe a real local Windows SAPI interface and persist the receipt store."""
    layout.ensure()
    layout.audio.mkdir(parents=True, exist_ok=True)
    script = _powershell_script(layout.audio)
    completed = subprocess.run(  # nosec B603 - constant probe script via argv list, no shell
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    stdout = completed.stdout.strip()
    probe: dict[str, Any] = {}
    for line in reversed(stdout.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("engine") == "System.Speech":
            probe = parsed
            break
    if not probe:
        probe = {
            "engine": "System.Speech",
            "sapi_available": False,
            "raw_errors": [completed.stderr[:2000]],
            "parse_error": "no JSON probe payload parsed",
        }
    probe["probe_utc"] = now_utc
    probe["exit_code"] = completed.returncode
    probe["raw_output"] = (stdout + "\n" + completed.stderr)[:4000]
    tts = probe.get("tts", {})
    stt = probe.get("stt", {})
    tts_ok = bool(tts and tts.get("available") and tts.get("receipts"))
    stt_ok = bool(stt and stt.get("available") and stt.get("recognized_text"))
    if tts_ok and stt_ok:
        classification = "ACTIVE"
    elif tts_ok:
        classification = "TTS_ONLY"
    else:
        classification = "NOT_TESTED"
    wav_hashes: dict[str, str] = {}
    for receipt in tts.get("receipts", []):
        if isinstance(receipt, dict) and receipt.get("wav"):
            wav = Path(str(receipt["wav"]))
            if wav.is_file():
                wav_hashes[str(receipt.get("language", ""))] = _sha256_file(wav)
    probe["wav_sha256"] = wav_hashes
    result = VoiceProbeResult(classification=classification, probe=probe)
    layout.voice.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
