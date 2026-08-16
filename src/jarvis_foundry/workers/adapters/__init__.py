"""Worker adapters: the only boundary that talks to a real framework CLI."""

from .base import ProviderUnavailable, WorkerAdapter
from .fakes import ScriptedWorkerAdapter
from .hermes import HermesWorkerAdapter, make_hermes_worker_adapter
from .openhands import OpenHandsWorkerAdapter, make_openhands_worker_adapter
from .registry import WorkerRegistry

__all__ = [
    "ProviderUnavailable",
    "WorkerAdapter",
    "ScriptedWorkerAdapter",
    "HermesWorkerAdapter",
    "make_hermes_worker_adapter",
    "OpenHandsWorkerAdapter",
    "make_openhands_worker_adapter",
    "WorkerRegistry",
]
