"""R4-G09 deterministic replay gate: zero-to-current identical projections."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, sha256_file, write_json


def build_historical_ledger(
    path: Path,
) -> tuple[Any, Any]:
    from jarvis_foundry.kernel.clock import FixedClock
    from jarvis_foundry.kernel.events import EventKindRegistry, LedgerEvent
    from jarvis_foundry.kernel.ledger import Ledger

    registry_used = (
        EventKindRegistry()
        .register("goal.created", required_keys=("goal_id",))
        .register("mission.created", required_keys=("mission_id",))
        .register("supervisor.started", required_keys=("instance_id",))
        .register("owner.directive.recorded", required_keys=("directive_id", "command_type"))
        .register("effect.intent", required_keys=("intent_id", "idempotency_key"))
        .register("effect.receipt", required_keys=("receipt_id", "intent_id", "status"))
        .register("release.created", required_keys=("release_id", "artifact_id"))
        .register("release.transitioned", required_keys=("release_id", "stage"))
        .register("incident.opened", required_keys=("incident_id",))
    )
    ledger = Ledger(path, registry=registry_used, clock=FixedClock("2026-08-15T00:00:00Z"))
    events = [
        LedgerEvent.create(
            event_type="goal.created",
            payload={"goal_id": "goal-1", "title": "alpha", "status": "ACTIVE"},
            source_type="replay-gate",
            source_id="gate",
            idempotency_key="goal:1",
        ),
        LedgerEvent.create(
            event_type="mission.created",
            payload={"mission_id": "mission-1", "objective": "beta", "status": "PLANNED"},
            source_type="replay-gate",
            source_id="gate",
            idempotency_key="mission:1",
        ),
        LedgerEvent.create(
            event_type="supervisor.started",
            payload={"instance_id": "instance-1", "trust_state": "LAB"},
            source_type="replay-gate",
            source_id="gate",
            idempotency_key="supervisor:1",
        ),
        LedgerEvent.create(
            event_type="owner.directive.recorded",
            payload={
                "directive_id": "directive-1",
                "command_type": "PAUSE",
                "target": "GLOBAL",
            },
            source_type="replay-gate",
            source_id="gate",
            idempotency_key="owner:1",
        ),
        LedgerEvent.create(
            event_type="effect.intent",
            payload={"intent_id": "intent-1", "idempotency_key": "effect:1"},
            source_type="replay-gate",
            source_id="gate",
            idempotency_key="effect:1",
        ),
        LedgerEvent.create(
            event_type="effect.receipt",
            payload={"receipt_id": "receipt-1", "intent_id": "intent-1", "status": "SUCCEEDED"},
            source_type="replay-gate",
            source_id="gate",
            idempotency_key="receipt:1",
        ),
        LedgerEvent.create(
            event_type="release.created",
            payload={"release_id": "release-1", "artifact_id": "art-1", "version": "1.0.0"},
            source_type="replay-gate",
            source_id="gate",
            idempotency_key="release:1",
        ),
        LedgerEvent.create(
            event_type="release.transitioned",
            payload={"release_id": "release-1", "stage": "PROMOTED"},
            source_type="replay-gate",
            source_id="gate",
            idempotency_key="release:2",
        ),
        LedgerEvent.create(
            event_type="incident.opened",
            payload={"incident_id": "incident-1", "severity": "HIGH", "status": "OPEN"},
            source_type="replay-gate",
            source_id="gate",
            idempotency_key="incident:1",
        ),
    ]
    for event in events:
        ledger.append(event)
    return ledger, registry_used


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R4_REPLAY.json")
    parser.add_argument("--raw", type=Path, default=EVIDENCE_DIR / "R4_REPLAY_RAW.txt")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args(argv)
    import time

    from jarvis_foundry.kernel.errors import ProjectionUnknownEventError
    from jarvis_foundry.kernel.events import LedgerEvent
    from jarvis_foundry.kernel.projections import (
        DEFAULT_PROJECTION_HANDLERS,
        ProjectionDispatcher,
        ProjectionRegistry,
        snapshot_bytes,
    )

    started = time.monotonic()
    output: list[str] = []
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-r4-replay-"))
    try:
        ledger, registry_used = build_historical_ledger(temp_dir / "history.db")
        dispatcher = ProjectionDispatcher(ProjectionRegistry(DEFAULT_PROJECTION_HANDLERS))
        first = dispatcher.replay(ledger.replay())
        second = dispatcher.replay(ledger.replay())
        identical = snapshot_bytes(first) == snapshot_bytes(second)
        output.append(f"identical_replay={identical}")
        output.append(f"events={len(ledger.replay())}")
        output.append(f"chain_valid={ledger.verify_chain().valid}")
        reopened = __import__("jarvis_foundry.kernel.ledger", fromlist=["Ledger"]).Ledger(
            temp_dir / "history.db", registry=registry_used
        )
        restart_identical = snapshot_bytes(first) == snapshot_bytes(
            dispatcher.replay(reopened.replay())
        )
        output.append(f"restart_identical={restart_identical}")
        unknown = LedgerEvent.create(
            event_type="mystery.kind",
            payload={},
            source_type="replay-gate",
            source_id="gate",
            idempotency_key="mystery:1",
        )
        refused = False
        try:
            dispatcher.replay([unknown])
        except ProjectionUnknownEventError:
            refused = True
        output.append(f"unknown_event_refused={refused}")
        raw_text = "\n".join(output)
        payload = {
            "schema": "jarvis-r4-replay-v1",
            "identical_replay": identical,
            "restart_identical": restart_identical,
            "events_replayed": len(ledger.replay()),
            "chain_valid": ledger.verify_chain().valid,
            "unknown_event_refused": refused,
            "passed": identical and restart_identical and refused,
            "wall_seconds": round(time.monotonic() - started, 3),
            "exit_code": 0,
        }
        if record_enabled():
            args.raw.write_text(raw_text, encoding="utf-8")
            payload["raw_output"] = args.raw.name
            payload["raw_output_sha256"] = sha256_file(args.raw)
            write_json(args.output, payload)
        print(raw_text)
        return 0 if payload["passed"] else 1
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
