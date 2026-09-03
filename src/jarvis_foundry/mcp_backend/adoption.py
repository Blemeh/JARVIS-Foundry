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

from ..database import FoundryDatabase
from ..events import canonical_json
from ..fabric.model import CapabilityTrust, Semantic, UnattendedMode
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


__all__ = [
    "CANONICAL_MCP_RUNTIME",
    "MCP_ADOPTION_SEAM_VERSION",
    "SELECTED_SOLUTION_CONTRACT",
    "SelectedMcpAdoption",
    "selected_solution_mcp_adoption",
]
