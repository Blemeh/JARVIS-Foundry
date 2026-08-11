"""WP4 — Hermes role adapter: abstract role routing, context manifests,
structured results, leases, heartbeats, provider fallback, and adapter
boundaries for isolated role sessions.
"""

from .adapter import FakeHermesAdapter, HermesOneshotAdapter, ProviderUnavailable
from .dispatcher import RoleDispatcher
from .manifest import ContextManifest
from .results import RoleResult
from .router import RoleConfig, RoleRouter

__all__ = [
    "ContextManifest",
    "FakeHermesAdapter",
    "HermesOneshotAdapter",
    "ProviderUnavailable",
    "RoleConfig",
    "RoleDispatcher",
    "RoleResult",
    "RoleRouter",
]
