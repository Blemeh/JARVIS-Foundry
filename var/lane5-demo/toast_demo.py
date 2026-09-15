"""Real Windows toast + attention-id dedup. Isolated. No live JARVIS."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.windows_toast import AttentionToastRouter, WindowsToastAdapter  # noqa: E402


def main() -> int:
    out = Path(__file__).resolve().parent / "generated"
    out.mkdir(parents=True, exist_ok=True)
    router = AttentionToastRouter(
        WindowsToastAdapter(),
        store_path=out / "toast-seen.json",
    )
    item = {
        "queue_id": "attention:lane5-toast-demo",
        "classification": "DECISION_REQUIRED",
        "summary": "Lane 5 Windows toast proof",
    }
    first = router.emit(item)
    second = router.emit(item)
    print(f"first={first.status} delivered={first.delivered}")
    print(f"second={second.status} delivered={second.delivered}")
    return 0 if first.delivered and second.status == "DEDUPED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
