"""V5-4 deterministic resource discovery.

Discovery describes what is reachable and what an adapter is allowed to do;
it is not an authority grant.  The returned payload deliberately carries the
observation mode and reconciliation/rollback policy so a later effect ticket
cannot mistake a discovered endpoint for a proven live capability.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from ..control.registration import registration_event as _registration_event
from ..events import Event


class ResourceDiscoveryError(ValueError):
    """Raised when a resource identity or boundary is not safe to record."""


_PATH_KINDS = frozenset({"filesystem_root", "local_repository"})
_URL_KINDS = frozenset(
    {
        "http_service",
        "browser_endpoint",
        "git_remote",
        "fake_mail_service",
        "fake_calendar_service",
    }
)
_SUPPORTED_KINDS = _PATH_KINDS | _URL_KINDS
_HEALTH_STATES = frozenset(
    {"READY", "DEGRADED", "BLOCKED", "UNKNOWN", "DISABLED", "VETOED"}
)
_READINESS_STATES = frozenset({"READY", "PROBING", "NOT_TESTED", "BLOCKED"})

_DEFAULTS: dict[str, dict[str, Any]] = {
    "filesystem_root": {
        "semantics": ["read", "write"],
        "capabilities": {
            "read_registered_files": "READY",
            "write_registered_files": "READY",
            "network_probe": "NOT_APPLICABLE",
        },
        "rollback": "restore-from-before-state",
        "reconcile": "system-readback",
    },
    "local_repository": {
        "semantics": ["read", "write", "execute"],
        "capabilities": {
            "read_registered_files": "READY",
            "write_registered_worktree_files": "READY",
            "git_local_commit": "PROBING",
            "network_probe": "NOT_APPLICABLE",
        },
        "rollback": "restore-from-before-state",
        "reconcile": "system-readback",
    },
    "http_service": {
        "semantics": ["read", "write"],
        "capabilities": {
            "read": "DISCOVERED",
            "idempotent_write": "NOT_TESTED",
            "network_probe": "NOT_TESTED",
        },
        "rollback": "provider-or-compensating-operation",
        "reconcile": "provider-query",
    },
    "browser_endpoint": {
        "semantics": ["read", "execute"],
        "capabilities": {
            "navigate": "DISCOVERED",
            "post_effect_oracle": "NOT_TESTED",
            "network_probe": "NOT_TESTED",
        },
        "rollback": "browser-undo-or-reconcile",
        "reconcile": "system-readback",
    },
    "git_remote": {
        "semantics": ["read", "write"],
        "capabilities": {
            "read_refs": "DISCOVERED",
            "push": "NOT_TESTED",
            "network_probe": "NOT_TESTED",
        },
        "rollback": "remote-revert-commit",
        "reconcile": "provider-query",
    },
    "fake_mail_service": {
        "semantics": ["read", "communicate"],
        "capabilities": {
            "read": "READY",
            "send": "NOT_TESTED",
            "network_probe": "NOT_APPLICABLE",
        },
        "rollback": "withdraw-or-mark-compensated",
        "reconcile": "provider-query",
    },
    "fake_calendar_service": {
        "semantics": ["read", "communicate", "write"],
        "capabilities": {
            "read": "READY",
            "create_event": "NOT_TESTED",
            "network_probe": "NOT_APPLICABLE",
        },
        "rollback": "cancel-or-mark-compensated",
        "reconcile": "provider-query",
    },
}


def _canonical_url(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ResourceDiscoveryError("endpoint must be a trimmed string")
    if any(ord(character) < 0x20 for character in value):
        raise ResourceDiscoveryError("endpoint contains control characters")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https", "fake", "file", "ssh", "git"}:
        raise ResourceDiscoveryError("endpoint scheme is unsupported")
    if parsed.username is not None or parsed.password is not None:
        raise ResourceDiscoveryError("endpoint credentials are not allowed")
    if parsed.scheme.lower() in {"http", "https"} and not parsed.hostname:
        raise ResourceDiscoveryError("HTTP endpoint requires a host")
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    try:
        port = parsed.port
    except ValueError as error:
        raise ResourceDiscoveryError("endpoint port is invalid") from error
    netloc = hostname
    if port is not None:
        netloc = f"{hostname}:{port}"
    # Fragments are client-side presentation state, not a stable resource.
    path = parsed.path or ("/" if parsed.scheme.lower() in {"http", "https"} else "")
    return urlunsplit(
        SplitResult(parsed.scheme.lower(), netloc, path, parsed.query, "")
    )


def _canonical_path(value: str | Path, *, bounded_root: str | Path | None) -> tuple[str, str]:
    requested = Path(value).expanduser()
    resolved = requested.resolve(strict=False)
    if not resolved.exists() or not resolved.is_dir():
        raise ResourceDiscoveryError("filesystem resource must be an existing directory")
    if bounded_root is not None:
        bound = Path(bounded_root).expanduser().resolve(strict=False)
        try:
            inside = os.path.commonpath(
                [os.path.normcase(str(resolved)), os.path.normcase(str(bound))]
            ) == os.path.normcase(str(bound))
        except ValueError as error:
            raise ResourceDiscoveryError("resource is outside the bounded root") from error
        if not inside:
            raise ResourceDiscoveryError("resource is outside the bounded root")
    return str(requested), str(resolved)


def _resource_id(kind: str, endpoint: str) -> str:
    digest = hashlib.sha256(f"{kind}\x00{endpoint}".encode("utf-8")).hexdigest()
    return f"resource-{digest[:24]}"


def discover_resource(
    kind: str,
    endpoint: str | Path,
    *,
    resource_id: str | None = None,
    bounded_root: str | Path | None = None,
    provenance: dict[str, Any] | None = None,
    health_state: str | None = None,
    readiness_state: str | None = None,
    observed_capabilities: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a deterministic, registration-ready resource record.

    Paths are resolved locally. URL-like endpoints are parsed only; no network
    request, credential lookup, browser launch, or provider call occurs.
    """
    if kind not in _SUPPORTED_KINDS:
        raise ResourceDiscoveryError(f"unsupported resource kind: {kind!r}")
    defaults = _DEFAULTS[kind]
    if kind in _PATH_KINDS:
        root_path, resolved_root = _canonical_path(endpoint, bounded_root=bounded_root)
        canonical_endpoint = resolved_root
        default_readiness = "READY"
        default_health = "READY"
    else:
        canonical_endpoint = _canonical_url(str(endpoint))
        root_path = canonical_endpoint
        resolved_root = canonical_endpoint
        default_readiness = "NOT_TESTED"
        default_health = "UNKNOWN"

    selected_health = health_state or default_health
    selected_readiness = readiness_state or default_readiness
    if selected_health not in _HEALTH_STATES:
        raise ResourceDiscoveryError(f"invalid health state: {selected_health!r}")
    if selected_readiness not in _READINESS_STATES:
        raise ResourceDiscoveryError(f"invalid readiness state: {selected_readiness!r}")

    observed = dict(defaults["capabilities"])
    if observed_capabilities:
        observed.update(observed_capabilities)
    record_id = resource_id or _resource_id(kind, canonical_endpoint)
    if not isinstance(record_id, str) or not record_id.strip():
        raise ResourceDiscoveryError("resource_id must be a non-empty string")
    source = dict(provenance or {})
    source.setdefault("observation_mode", "local" if kind in _PATH_KINDS else "record-only")
    source.setdefault("authority", "discovery-is-not-authority")
    baseline = {
        "identity": {"resource_id": record_id, "kind": kind},
        "endpoint": canonical_endpoint,
        "health": selected_health,
        "readiness_state": selected_readiness,
        "provenance": source,
        "rollback_strategy": defaults["rollback"],
        "reconciliation_strategy": defaults["reconcile"],
        "observed_capabilities": observed,
    }
    capabilities = {
        "allowed_semantics": list(defaults["semantics"]),
        "observed": observed,
        "health": selected_health,
        "network_probe": observed.get("network_probe", "NOT_TESTED"),
    }
    return {
        "resource_id": record_id,
        "kind": kind,
        "root_path": root_path,
        "resolved_root": resolved_root,
        "endpoint": canonical_endpoint,
        "readiness_state": selected_readiness,
        "health": selected_health,
        "allowed_semantics": list(defaults["semantics"]),
        "provenance": source,
        "observed_capabilities": observed,
        "rollback_strategy": defaults["rollback"],
        "reconciliation_strategy": defaults["reconcile"],
        "baseline": baseline,
        "capabilities": capabilities,
    }


def registration_event(resource: dict[str, Any], *, source_id: str = "v5-resource-discovery") -> Event:
    """Create the canonical append-only ``project.registered`` event."""
    required = {"resource_id", "kind", "root_path", "resolved_root", "baseline", "capabilities", "readiness_state"}
    missing = sorted(required.difference(resource))
    if missing:
        raise ResourceDiscoveryError(f"resource record is missing: {', '.join(missing)}")
    return _registration_event(resource, source_id=source_id)


__all__ = ["ResourceDiscoveryError", "discover_resource", "registration_event"]
