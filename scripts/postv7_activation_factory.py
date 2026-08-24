"""Run the disposable post-V7 account activation factory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.connectors.account_activation import run_fake_activation_factory


def main() -> int:
    result = run_fake_activation_factory()
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
