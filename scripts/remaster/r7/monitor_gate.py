"""R7-G02 real monitored change gate."""

from __future__ import annotations

import sys
import time
from typing import Any

from common import EVIDENCE_DIR, EVIDENCE_UTC, write_json
from gate_helpers import fixed_clock, monitor_proving_copy, new_store, staged_manifest

from jarvis_foundry.kernel.portfolio.monitor import MonitorService
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    manifest = staged_manifest()
    store = new_store("monitor")
    clock = fixed_clock()
    source_path, mutable = monitor_proving_copy("monitor-health")
    monitor = MonitorService(store, clock=clock)
    monitor.register_source("monitor-health", locator=str(mutable), freshness_seconds=900)
    baseline = monitor.poll("monitor-health", lambda: (mutable.read_bytes(), EVIDENCE_UTC))
    before_hash = store.cursor("monitor-health").last_content_hash
    mutable.write_text(
        mutable.read_text(encoding="utf-8") + "\n- monitored regression probe present.\n",
        encoding="utf-8",
    )
    change = monitor.poll("monitor-health", lambda: (mutable.read_bytes(), EVIDENCE_UTC))
    unchanged = monitor.poll("monitor-health", lambda: (mutable.read_bytes(), EVIDENCE_UTC))

    reopened = PortfolioStore(store.root)
    restarted = MonitorService(reopened, clock=clock)
    replay = restarted.poll("monitor-health", lambda: (mutable.read_bytes(), EVIDENCE_UTC))

    stale_store = new_store("monitor-stale")
    stale_monitor = MonitorService(stale_store, clock=clock)
    stale_monitor.register_source("stale-feed", locator=str(mutable), freshness_seconds=60)
    stale_monitor.poll("stale-feed", lambda: (b"old\n", "2026-08-15T16:00:00Z"))
    stale = stale_monitor.poll("stale-feed", lambda: (b"new\n", "2026-08-15T16:00:00Z"))

    rollback = monitor.rollback("monitor-health", reason="corrupt observation probe")
    passed = bool(
        baseline.changed is False
        and change.changed is True
        and change.meaningful is True
        and change.candidate_id is not None
        and unchanged.changed is False
        and replay.changed is False
        and replay.candidate_id == change.candidate_id
        and len(store.discoveries()) == 1
        and stale.stale is True
        and rollback["restored_content_hash"] == before_hash
    )
    payload: dict[str, Any] = {
        "schema": "jarvis-r7-monitor-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": 0.0,
        "passed": passed,
        "evidence_utc": EVIDENCE_UTC,
        "staging_hash": manifest["staging_hash"],
        "source": {
            "source_path": str(source_path),
            "mutable_proving_path": str(mutable),
        },
        "baseline": {
            "changed": baseline.changed,
            "content_hash": baseline.after_hash,
        },
        "change": {
            "changed": change.changed,
            "meaningful": change.meaningful,
            "change_id": change.change_id,
            "candidate_id": change.candidate_id,
            "duplicate_of": change.duplicate_of,
            "after_hash": change.after_hash,
            "reason": change.reason,
        },
        "duplicate_suppression": {
            "unchanged_changed": unchanged.changed,
            "discovery_count": len(store.discoveries()),
            "change_count": len(store.changes()),
        },
        "restart": {
            "replayed_changed": replay.changed,
            "replayed_candidate_id": replay.candidate_id,
            "cursor_content_hash": reopened.cursor("monitor-health").last_content_hash,
        },
        "stale_probe": {
            "stale": stale.stale,
            "changed": stale.changed,
            "reason": stale.reason,
        },
        "rollback": rollback,
    }
    output = EVIDENCE_DIR / "R7_MONITOR.json"
    write_json(output, payload)
    print(
        f"monitor: passed={passed} change={change.changed} candidate={change.candidate_id} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
