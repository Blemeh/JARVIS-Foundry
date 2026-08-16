"""R9 real, fake, and unavailable adapter implementations."""

from __future__ import annotations

from jarvis_foundry.capabilities_live.adapters.base import (
    AfterState,
    BeforeState,
    CanaryAdapter,
    ExecutionResult,
    Outcome,
    Receipt,
)
from jarvis_foundry.capabilities_live.adapters.fakes import FailClosedFakeAdapter
from jarvis_foundry.capabilities_live.adapters.files import FilesAdapter
from jarvis_foundry.capabilities_live.adapters.git import GitAdapter
from jarvis_foundry.capabilities_live.adapters.services import ServicesAdapter
from jarvis_foundry.capabilities_live.adapters.unavailable import (
    UNAVAILABLE_CONNECTORS,
    UnavailableConnector,
)

__all__ = [
    "UNAVAILABLE_CONNECTORS",
    "AfterState",
    "BeforeState",
    "CanaryAdapter",
    "ExecutionResult",
    "FailClosedFakeAdapter",
    "FilesAdapter",
    "GitAdapter",
    "Outcome",
    "Receipt",
    "ServicesAdapter",
    "UnavailableConnector",
]
