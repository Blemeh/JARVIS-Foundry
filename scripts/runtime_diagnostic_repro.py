"""Prove typed runtime health and redacted correlation on a disposable home."""

from __future__ import annotations

import gc
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delivery import DeliveryBoundary, DeliveryRequest, FakeDeliveryProvider
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.supervisor.service import Supervisor


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="jarvis-diagnostic-repro-") as path:
        root = Path(path)
        database = FoundryDatabase(root / "foundry.db")
        database.initialize()
        stop_path = root / "EMERGENCY_STOP"
        supervisor = Supervisor(database, stop_path, instance_id="diagnostic-harness")
        if supervisor.start() != "RUNNING":
            raise AssertionError("disposable supervisor did not start")

        provider = FakeDeliveryProvider()
        boundary = DeliveryBoundary(database, {provider.provider_id: provider})
        boundary.dispatch(DeliveryRequest(
            request_id="diagnostic-delivery",
            mission_id="diagnostic-mission",
            action_id="diagnostic-action",
            channel="UI",
            provider=provider.provider_id,
            target="local-ui",
            payload="diagnostic message",
            idempotency_key="diagnostic-delivery-key",
        ))
        running = supervisor.doctor()
        EmergencyStop(stop_path).activate("diagnostic fault")
        blocked = supervisor.doctor()
        supervisor.stop()
        result = {
            "harness": "runtime-diagnostic",
            "verdict": "PASS",
            "running_health": running["health"],
            "blocked_health": blocked["health"],
            "correlation": running["diagnostic"]["correlation"],
            "event_count": running["event_count"],
        }
        if running["health"]["status"] not in {"DEGRADED", "RUNNING"}:
            raise AssertionError(running["health"])
        if blocked["health"]["status"] != "BLOCKED":
            raise AssertionError(blocked["health"])
        if "diagnostic-mission" not in result["correlation"]["mission_ids"]:
            raise AssertionError(result["correlation"])
        del supervisor, boundary, provider, database
        gc.collect()
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
