"""Isolated Command Center /api/voice-text proof. Does not touch live JARVIS."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.command_center import CommandCenterServer  # noqa: E402
from jarvis_foundry.database import FoundryDatabase  # noqa: E402

generated = Path(__file__).resolve().parent / "generated"
generated.mkdir(parents=True, exist_ok=True)
home = generated / "cc-voice"
home.mkdir(parents=True, exist_ok=True)
db = FoundryDatabase(home / "foundry.db")
db.initialize()
server = CommandCenterServer(db, stop_path=home / "STOP", runtime_home=home)
server.configure_owner(b"lane5-demo-owner-secret-bytes32")
server.start()
try:
    base = f"http://{server.address[0]}:{server.address[1]}"
    body = json.dumps({"text": "jarvis, what are you doing", "language_hint": "en"}).encode()
    with urlopen(Request(f"{base}/api/voice-text", data=body, method="POST"), timeout=20) as response:
        payload = json.loads(response.read())
    (home / "voice-text.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(payload["status"], payload.get("response_text"), sep=" | ")
finally:
    server.close()
