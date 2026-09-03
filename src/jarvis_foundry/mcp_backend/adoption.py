"""Stable self-expansion adoption seam for the canonical MCP backend.

This module understands only the selected-solution data contract.  It does
not import the Adapter Factory and does not implement an MCP transport.  A
selected MCP solution is translated into :class:`McpBackendConfig`, after
which the dedicated :class:`FabricMcpBackend` remains the only runtime.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from ..control.redaction import redact_text
from ..database import FoundryDatabase
from ..events import canonical_json
from ..fabric.model import (
    CapabilityTrust,
    Semantic,
    UnattendedMode,
    VerifyVerdict,
)
from .config import (
    McpBackendConfig,
    McpConfigurationError,
    McpServerConfig,
    McpToolPolicy,
    McpTransport,
)
from .fabric import FabricMcpBackend

SELECTED_SOLUTION_CONTRACT = "jarvis.v12.selected-solution-adapter.v1"
MCP_ADOPTION_SEAM_VERSION = "jarvis.v12.mcp-backend-adoption.v1"
CANONICAL_MCP_RUNTIME = "jarvis_foundry.mcp_backend.FabricMcpBackend"

_MCP_CLASSES = {
    "mcp",
    "mcp_stdio",
    "mcp_streamable_http",
    "streamable_http",
}


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise McpConfigurationError(f"{name} must be an object")
    return dict(value)


def _selected_server_id(solution_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", solution_id).strip("-.")
    if not slug or not slug[0].isalpha():
        slug = f"selected-{slug}"
    digest = hashlib.sha256(solution_id.encode("utf-8")).hexdigest()[:12]
    return f"{slug[:108]}-{digest}"


def _semantic(value: object) -> Semantic:
    try:
        return Semantic(str(value or "external_effect"))
    except ValueError as error:
        raise McpConfigurationError(
            f"selected MCP capability has unsupported semantic {value!r}"
        ) from error


def _unattended(value: object) -> UnattendedMode:
    try:
        return UnattendedMode(str(value or UnattendedMode.UNKNOWN.value))
    except ValueError as error:
        raise McpConfigurationError(
            f"selected MCP capability has unsupported unattended mode {value!r}"
        ) from error


@dataclass(frozen=True, slots=True)
class SelectedMcpAdoption:
    """A parsed, non-executing binding to the one canonical MCP runtime."""

    solution_id: str
    adapter_id: str
    descriptor_sha256: str
    config: McpBackendConfig
    contract_version: str = MCP_ADOPTION_SEAM_VERSION
    runtime: str = CANONICAL_MCP_RUNTIME
    trust_state: CapabilityTrust = CapabilityTrust.PROBATION

    def create_backend(self, database: FoundryDatabase) -> FabricMcpBackend:
        """Construct the dedicated backend; discovery still owns projection."""

        return FabricMcpBackend(database, self.config)

    def as_dict(self) -> dict[str, Any]:
        server = self.config.servers[0]
        return {
            "contract_version": self.contract_version,
            "selected_solution_contract": SELECTED_SOLUTION_CONTRACT,
            "solution_id": self.solution_id,
            "adapter_id": self.adapter_id,
            "descriptor_sha256": self.descriptor_sha256,
            "runtime": self.runtime,
            "server_id": server.server_id,
            "transport": server.transport.value,
            "trust_state": self.trust_state.value,
            "allowed_tools": list(server.allowed_tools),
            "second_mcp_runtime": False,
        }


def selected_solution_mcp_adoption(
    descriptor: Mapping[str, Any],
) -> SelectedMcpAdoption:
    """Translate a selected MCP solution into probationary backend config.

    Parsing performs no network/process work and no Fabric registration.
    Tool semantics come only from the selected JARVIS-owned descriptor; remote
    MCP descriptions and results remain DATA_ONLY during later discovery.
    """

    raw = _mapping(descriptor, "selected solution descriptor")
    contract = raw.get("contract_version")
    if contract not in {None, SELECTED_SOLUTION_CONTRACT}:
        raise McpConfigurationError(
            f"unsupported selected-solution contract {contract!r}"
        )
    solution_class = str(
        raw.get("solution_class") or raw.get("solution_kind") or ""
    ).strip().lower().replace("-", "_")
    if solution_class not in _MCP_CLASSES:
        raise McpConfigurationError(
            "selected solution is not an MCP solution"
        )
    solution_id = str(raw.get("solution_id") or raw.get("candidate_id") or "").strip()
    adapter_id = str(raw.get("adapter_id") or "").strip()
    if not solution_id or not adapter_id:
        raise McpConfigurationError(
            "selected MCP solution requires solution_id and adapter_id"
        )
    capabilities = raw.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise McpConfigurationError(
            "selected MCP solution requires at least one capability"
        )

    tools: dict[str, McpToolPolicy] = {}
    timeouts: list[float] = []
    for item in capabilities:
        capability = _mapping(item, "selected MCP capability")
        invocation = _mapping(
            capability.get("invocation"), "selected MCP invocation"
        )
        tool_name = str(invocation.get("target") or "").strip()
        if not tool_name:
            raise McpConfigurationError(
                "selected MCP capability invocation.target is required"
            )
        policy = McpToolPolicy(
            semantic=_semantic(capability.get("semantic")),
            effect_class=(
                str(capability["effect_class"])
                if capability.get("effect_class") else None
            ),
            unattended=_unattended(capability.get("unattended")),
            limitations=tuple(capability.get("limitations") or ()),
        )
        if tool_name in tools and tools[tool_name] != policy:
            raise McpConfigurationError(
                f"selected MCP tool {tool_name!r} has conflicting policies"
            )
        tools[tool_name] = policy
        timeout_ms = capability.get("timeout_ms")
        if timeout_ms is not None:
            try:
                timeout = float(timeout_ms) / 1000.0
            except (TypeError, ValueError) as error:
                raise McpConfigurationError(
                    "selected MCP capability timeout_ms must be numeric"
                ) from error
            if timeout <= 0 or timeout > 3600:
                raise McpConfigurationError(
                    "selected MCP capability timeout_ms is out of bounds"
                )
            timeouts.append(timeout)

    readiness = _mapping(raw.get("readiness"), "selected MCP readiness")
    explicit = raw.get("mcp_backend") or {}
    if not isinstance(explicit, Mapping):
        raise McpConfigurationError("mcp_backend must be an object")
    settings = dict(explicit)
    server_id = str(settings.pop("server_id", "") or _selected_server_id(solution_id))
    transport_name = str(settings.pop("transport", "") or "").strip().lower()
    if not transport_name:
        transport_name = (
            McpTransport.STREAMABLE_HTTP.value
            if solution_class in {"mcp_streamable_http", "streamable_http"}
            else McpTransport.STDIO.value
        )
    try:
        transport = McpTransport(transport_name)
    except ValueError as error:
        raise McpConfigurationError(
            f"selected MCP transport {transport_name!r} is unsupported"
        ) from error

    config_values: dict[str, Any] = {
        **settings,
        "server_id": server_id,
        "transport": transport.value,
        "allowed_tools": tuple(sorted(tools)),
        "disabled_tools": (),
        "tools": tools,
        "trust_state": CapabilityTrust.PROBATION,
        "default_semantic": Semantic.EXTERNAL_EFFECT,
        "default_unattended": UnattendedMode.INTERACTIVE_REQUIRED,
        "request_timeout_s": max(timeouts) if timeouts else 60.0,
    }
    if transport is McpTransport.STDIO:
        config_values.setdefault("command", readiness.get("target"))
        config_values.setdefault("args", tuple(readiness.get("arguments") or ()))
    else:
        config_values.setdefault("endpoint", raw.get("locator"))
    server = McpServerConfig(**config_values)
    encoded = canonical_json(raw)
    return SelectedMcpAdoption(
        solution_id=solution_id,
        adapter_id=adapter_id,
        descriptor_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        config=McpBackendConfig((server,)),
    )


def _active_selected_mcp_adoptions(
    database: FoundryDatabase,
) -> tuple[tuple[SelectedMcpAdoption, tuple[str, ...]], ...]:
    from ..fabric.catalog import capability_registration

    selected: dict[tuple[str, str], dict[str, Any]] = {}
    completed: dict[str, dict[str, Any]] = {}
    for event in database.events(
        readonly=True,
        event_types=(
            "fabric.self_expansion.solution_selected",
            "fabric.self_expansion.completed",
        ),
    ):
        payload = event.payload
        run_id = str(payload.get("run_id") or "")
        if not run_id:
            continue
        if event.event_type == "fabric.self_expansion.solution_selected":
            descriptor = payload.get("mcp_descriptor")
            candidate_id = str(payload.get("candidate_id") or "")
            if candidate_id and isinstance(descriptor, Mapping):
                selected[(run_id, candidate_id)] = dict(descriptor)
            continue
        receipt = payload.get("receipt")
        if (
            isinstance(receipt, Mapping)
            and receipt.get("status") == "REGISTERED"
            and receipt.get("runtime") == CANONICAL_MCP_RUNTIME
        ):
            completed[run_id] = dict(receipt)

    active: list[tuple[SelectedMcpAdoption, tuple[str, ...]]] = []
    for run_id, receipt in completed.items():
        candidate_id = str(receipt.get("candidate_id") or "")
        descriptor = selected.get((run_id, candidate_id))
        if descriptor is None:
            continue
        capabilities = descriptor.get("capabilities")
        if not isinstance(capabilities, list) or not capabilities:
            continue
        capability_ids = tuple(
            str(item.get("capability_id") or "")
            for item in capabilities
            if isinstance(item, Mapping)
        )
        if (
            not capability_ids
            or len(capability_ids) != len(capabilities)
            or any(not capability_id for capability_id in capability_ids)
        ):
            continue
        registrations = [
            capability_registration(database, capability_id)
            for capability_id in capability_ids
        ]
        if any(registration is None for registration in registrations):
            continue
        if len(registrations) == 1 and (
            registrations[0].get("registration_id")
            != receipt.get("registration_id")
        ):
            continue
        if any(
            str(registration.get("trust") or "")
            not in {
                CapabilityTrust.PROBATION.value,
                CapabilityTrust.ESTABLISHED.value,
            }
            for registration in registrations
            if registration is not None
        ):
            continue
        active.append(
            (
                selected_solution_mcp_adoption(descriptor),
                capability_ids,
            )
        )
    return tuple(
        sorted(
            active,
            key=lambda item: item[0].config.servers[0].server_id,
        )
    )


def restore_selected_mcp_backend(
    database: FoundryDatabase,
) -> FabricMcpBackend | None:
    """Reconstruct one canonical backend from completed active adoptions."""

    from ..fabric.catalog import record_outcome

    active = _active_selected_mcp_adoptions(database)
    if not active:
        return None
    servers: dict[str, Any] = {}
    capabilities_by_server: dict[str, set[str]] = {}
    for adoption, capability_ids in active:
        server = adoption.config.servers[0]
        prior = servers.get(server.server_id)
        if prior is not None and prior != server:
            note = (
                f"conflicting durable MCP settings for server {server.server_id}"
            )
            for capability_id in capability_ids:
                record_outcome(
                    database,
                    capability_id,
                    success=False,
                    verified=VerifyVerdict.MISMATCH,
                    note=note,
                )
            return None
        servers[server.server_id] = server
        capabilities_by_server.setdefault(server.server_id, set()).update(
            capability_ids
        )

    backend = FabricMcpBackend(
        database,
        McpBackendConfig(
            tuple(servers[server_id] for server_id in sorted(servers))
        ),
    )
    try:
        for server_id, expected_ids in capabilities_by_server.items():
            health, projected = backend.connect_server(
                server_id,
                include_disabled=False,
            )
            projected_ids = {item.capability_id for item in projected}
            if health.state.value != "READY" or projected_ids != expected_ids:
                raise McpConfigurationError(
                    f"restored MCP server {server_id} did not reproduce "
                    "its completed Catalog projection"
                )
        return backend
    except Exception as error:
        note = redact_text(
            f"durable MCP runtime restoration failed: "
            f"{type(error).__name__}: {error}"
        )[:400]
        for capability_ids in capabilities_by_server.values():
            for capability_id in capability_ids:
                record_outcome(
                    database,
                    capability_id,
                    success=False,
                    verified=VerifyVerdict.MISMATCH,
                    note=note,
                )
        backend.close(preserve_catalog_readiness=True)
        return None


__all__ = [
    "CANONICAL_MCP_RUNTIME",
    "MCP_ADOPTION_SEAM_VERSION",
    "SELECTED_SOLUTION_CONTRACT",
    "SelectedMcpAdoption",
    "restore_selected_mcp_backend",
    "selected_solution_mcp_adoption",
]
