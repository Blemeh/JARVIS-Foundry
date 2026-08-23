"""Exercise provider/voice/UI delivery outcomes against a disposable ledger."""

from __future__ import annotations

import json
import gc
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delivery import (
    DeliveryBoundary,
    DeliveryCancelled,
    DeliveryError,
    DeliveryOutcome,
    DeliveryRequest,
    DeliveryStatus,
    FakeDeliveryProvider,
)


def _request(key: str, *, max_attempts: int = 1) -> DeliveryRequest:
    return DeliveryRequest(
        request_id=f"harness-{key}", mission_id="mission-harness",
        action_id=key, channel="PROVIDER", provider="fake-local",
        target="local-sink", payload=f"payload for {key}",
        idempotency_key=key, max_attempts=max_attempts,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="jarvis-delivery-repro-") as path:
        database = FoundryDatabase(Path(path) / "foundry.db")
        database.initialize()
        provider = FakeDeliveryProvider(scripted=[
            DeliveryError("retry once", retryable=True),
            DeliveryOutcome.delivered(provider_request_id="accepted"),
            TimeoutError("bounded timeout"),
            DeliveryCancelled("owner cancelled"),
        ])
        boundary = DeliveryBoundary(database, {provider.provider_id: provider})
        retry = boundary.dispatch(_request("retry", max_attempts=2))
        timeout = boundary.dispatch(_request("timeout"))
        cancelled = boundary.dispatch(_request("cancelled"))
        replay = boundary.dispatch(_request("retry", max_attempts=2))
        if retry["status"] != DeliveryStatus.DELIVERED.value:
            raise AssertionError(retry)
        if timeout["status"] != DeliveryStatus.TIMED_OUT.value:
            raise AssertionError(timeout)
        if cancelled["status"] != DeliveryStatus.CANCELLED.value:
            raise AssertionError(cancelled)
        if replay != retry or len(provider.calls) != 4:
            raise AssertionError({"replay": replay, "calls": len(provider.calls)})
        result = {
            "harness": "delivery-boundary",
            "verdict": "PASS",
            "retry": retry,
            "timeout": timeout,
            "cancelled": cancelled,
            "replay": replay,
            "provider_calls": len(provider.calls),
            "events": database.event_count(),
        }
        del boundary, provider, database
        gc.collect()
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
