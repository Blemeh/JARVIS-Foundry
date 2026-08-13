"""V5-S4: local deterministic fake connectors (test + offline substrate).

Deterministic, in-memory fakes used by the test suite and any local
proving: read returns stored content, write stores, execute increments
a counter, and a deliberate ``fail_next`` flag simulates an outage for
the isolation/reroute probes. They are NOT claims about real online
connectors — those stay honestly NOT_READY until S8.
"""
from __future__ import annotations

from typing import Any


class ConnectorFailure(Exception):
    """Deterministic simulated outage (outage-isolation probes)."""


class FakeConnector:
    """Deterministic fake with the read/write/execute surface.

    ``fail_next`` makes the NEXT operation raise ConnectorFailure (and
    clears itself) — a deliberate, replayable outage.
    """

    def __init__(self, connector_id: str, kind: str = "fake") -> None:
        self.connector_id = connector_id
        self.kind = kind
        self._store: dict[str, str] = {}
        self._executions: dict[str, int] = {}
        self.fail_next = False

    def read(self, ref: str) -> str:
        self._maybe_fail()
        return self._store.get(ref, "")

    def write(self, ref: str, content: str) -> str:
        self._maybe_fail()
        self._store[ref] = content
        return ref

    def execute(self, ref: str) -> int:
        self._maybe_fail()
        self._executions[ref] = self._executions.get(ref, 0) + 1
        return self._executions[ref]

    def snapshot(self) -> dict[str, Any]:
        return {"connector_id": self.connector_id, "kind": self.kind,
                "store": dict(self._store),
                "executions": dict(self._executions)}

    def _maybe_fail(self) -> None:
        if self.fail_next:
            self.fail_next = False
            raise ConnectorFailure(
                f"fake connector {self.connector_id} simulated outage")
