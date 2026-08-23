"""Exercise deterministic delivery and supervisor ownership races locally."""

from __future__ import annotations

import gc
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delivery import DeliveryBoundary, DeliveryOutcome, DeliveryRequest, DeliveryStatus, FakeDeliveryProvider
from jarvis_foundry.supervisor.leases import LeaseUnavailable
from jarvis_foundry.supervisor.service import Supervisor


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="jarvis-fault-repro-") as path:
        root = Path(path)
        database = FoundryDatabase(root / "foundry.db")
        database.initialize()
        started = threading.Event()
        release = threading.Event()

        class SlowProvider(FakeDeliveryProvider):
            def deliver(self, request):
                self.calls.append(request)
                started.set()
                release.wait(timeout=2)
                return DeliveryOutcome.delivered(provider_actual=self.provider_id)

        provider = SlowProvider()
        boundary = DeliveryBoundary(database, {provider.provider_id: provider})
        request = DeliveryRequest(
            request_id="fault-request", mission_id="fault-mission",
            action_id="fault-action", channel="PROVIDER",
            provider=provider.provider_id, target="local-sink",
            payload="bounded local payload", idempotency_key="fault-key",
        )
        results: list[dict[str, object]] = []

        def dispatch() -> None:
            results.append(boundary.dispatch(request))

        first = threading.Thread(target=dispatch)
        second = threading.Thread(target=dispatch)
        first.start()
        if not started.wait(timeout=2):
            raise AssertionError("first provider call did not start")
        second.start()
        time.sleep(0.05)
        release.set()
        first.join(timeout=3)
        second.join(timeout=3)
        if sorted(item["status"] for item in results) != [
                DeliveryStatus.DELIVERED.value, DeliveryStatus.UNKNOWN.value]:
            raise AssertionError(results)
        if len(provider.calls) != 1:
            raise AssertionError(f"provider calls duplicated: {len(provider.calls)}")

        first_owner = Supervisor(database, root / "STOP", instance_id="fault-owner")
        if first_owner.start() != "RUNNING":
            raise AssertionError("first supervisor did not start")
        duplicate = Supervisor(database, root / "STOP", instance_id="fault-owner")
        try:
            duplicate.start()
        except LeaseUnavailable:
            pass
        else:
            raise AssertionError("duplicate supervisor reused a live instance ID")
        first_owner.stop()
        result = {
            "harness": "runtime-fault-concurrency",
            "verdict": "PASS",
            "delivery_statuses": sorted(item["status"] for item in results),
            "provider_calls": len(provider.calls),
            "supervisor_duplicate": "BLOCKED",
            "events": database.event_count(),
        }
        del duplicate, first_owner, boundary, provider, database
        gc.collect()
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
