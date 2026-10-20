"""Stamp the lane evidence with the certified source commit."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LANE = HERE.parent


def _repo_root(start: Path) -> Path:
    current = start
    for _ in range(10):
        if (current / "src" / "jarvis_foundry").is_dir() and (current / "pyproject.toml").is_file():
            return current
        current = current.parent
    raise RuntimeError(f"could not locate JARVIS repo from {start}")


REPO = _repo_root(HERE)
head = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True,
).stdout.strip()
short = subprocess.run(
    ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True,
).stdout.strip()

handoff_path = LANE / "HANDOFF.json"
handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
handoff["head"] = head
handoff["head_short"] = short
handoff["head_note"] = (
    "Certified source commit for this lane (evidence commit follows it). "
    "Commit subject carries LANE B."
)
handoff_path.write_text(json.dumps(handoff, indent=2, sort_keys=False), encoding="utf-8")

contract_path = LANE / "INTEGRATION_CONTRACT.json"
contract = json.loads(contract_path.read_text(encoding="utf-8"))
contract["head"] = head
contract_path.write_text(json.dumps(contract, indent=2, sort_keys=False), encoding="utf-8")

print(json.dumps({"head": head, "handoff": str(handoff_path), "contract": str(contract_path)}, indent=2))
