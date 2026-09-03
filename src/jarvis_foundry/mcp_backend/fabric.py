"""Projection of MCP tools into the single Capability Fabric catalog."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, Self

from ..database import FoundryDatabase
from ..effects import EffectClass, infer_effect_class
from ..fabric.catalog import (
    capability_metadata,
    listed_capabilities,
    record_outcome,
    register_capability,
)
from ..fabric.contracts import CapabilityMetadata
from ..fabric.model import CapabilitySpec, Kind, VerifyVerdict
from .client import McpClientBackend, McpServerUnavailable
from .config import McpBackendConfig
from .contracts import McpInvocationReceipt, McpToolDescriptor

MCP_ADAPTER_VERSION = "1.0.0"


def _metadata(descriptor: McpToolDescriptor, config: McpBackendConfig) -> CapabilityMetadata:
    server = config.server(descriptor.server_id)
    effect_class = infer_effect_class(
        descriptor.action_kind,
        descriptor.semantic.value,
        descriptor.server_identity,
    )
    mutating = effect_class is not EffectClass.READ_ONLY
    external = effect_class in {
        EffectClass.REVERSIBLE_EXTERNAL,
        EffectClass.IRREVERSIBLE_OR_HIGH_COST,
    }
    return CapabilityMetadata(
        capability_id=descriptor.capability_id,
        version=MCP_ADAPTER_VERSION,
        input_schema=f"mcp+jsonschema:sha256:{descriptor.schema_sha256}",
        output_schema="jarvis.mcp.bounded-receipt.v1",
        effect_class=effect_class,
        requirements=(descriptor.server_identity,),
        credential_refs=server.credential_refs,
        preconditions=(
            "server READY in MCP session lifecycle",
            "tool enabled by exact local JARVIS policy",
            "arguments valid against bounded discovered schema",
            "STOP inactive at dispatch",
        ),
        postconditions=(
            "bounded protocol receipt captured",
            "external content remains DATA_ONLY",
            "independent readback required for mutating verification",
        ),
        idempotency={
            "required": mutating,
            "strategy": "provider-key" if external else "effect-key",
            "replay": "reconcile-before-retry" if mutating else "safe-read-retry",
        },
        execute={
            "executor": "mcp",
            "action_kind": descriptor.action_kind,
            "server_id": descriptor.server_id,
            "server_identity": descriptor.server_identity,
            "tool_name": descriptor.tool_name,
            "semantic_source": "local-policy",
        },
        evidence={
            "receipt_schema": "jarvis.mcp.invocation-receipt.v1",
            "before_state_required": mutating,
            "independent_verifier": True,
            "remote_result_authority": "DATA_ONLY",
        },
        timeout_and_cancel={
            "timeout_ms": int(server.request_timeout_s * 1000),
            "cancel_supported": True,
            "timeout_outcome": "UNKNOWN" if mutating else "FAILED",
        },
        recovery={
            "max_attempts": 1 if mutating else 3,
            "retryable_outcomes": [] if mutating else ["FAILED"],
            "partial_effect": "FROZEN",
            "ambiguous_outcome": "FROZEN",
            "reconnect": True,
        },
        world_model={
            "verified_terminal_only": True,
            "source_kind": "mcp:observation" if not mutating else "mission:outcome",
            "external_content": "DATA_ONLY",
        },
        provenance={
            "authority": "Capability Fabric",
            "source": "mcp.discovery",
            "mcp_server_id": descriptor.server_id,
            "mcp_server_identity": descriptor.server_identity,
            "mcp_tool_name": descriptor.tool_name,
            "mcp_schema_sha256": descriptor.schema_sha256,
            "mcp_input_schema": dict(descriptor.input_schema),
            "mcp_output_schema": (
                dict(descriptor.output_schema) if descriptor.output_schema else None
            ),
            "remote_description_data_only": descriptor.description_data_only,
            "remote_content_authority": "DATA_ONLY",
        },
    )


def _spec(descriptor: McpToolDescriptor) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=descriptor.capability_id,
        version=MCP_ADAPTER_VERSION,
        kind=Kind.TOOL,
        action_kind=descriptor.action_kind,
        semantic=descriptor.semantic,
        executor=f"mcp:{descriptor.server_id}/{descriptor.tool_name}",
        unattended=descriptor.unattended,
        limitations=descriptor.limitations,
    )


class FabricMcpBackend:
    """Subordinate MCP lifecycle with Fabric as the only readiness authority."""

    def __init__(self, database: FoundryDatabase, config: McpBackendConfig) -> None:
        self.database = database
        self.config = config
        self.client = McpClientBackend(config)
        self.last_close_errors: tuple[str, ...] = ()

    def _catalog_capabilities(self, server_id: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for spec in listed_capabilities(self.database):
            metadata = capability_metadata(self.database, spec.capability_id)
            if metadata is None:
                continue
            provenance = metadata.provenance
            if (
                provenance.get("source") == "mcp.discovery"
                and provenance.get("mcp_server_id") == server_id
            ):
                result[spec.capability_id] = spec.action_kind
        return result

    def _mark_server_unavailable(self, server_id: str, note: str) -> None:
        for capability_id in self._catalog_capabilities(server_id):
            record_outcome(
                self.database,
                capability_id,
                success=False,
                verified=VerifyVerdict.MISMATCH,
                note=note,
            )

    def project(self, server_id: str) -> tuple[CapabilitySpec, ...]:
        prior = self._catalog_capabilities(server_id)
        projected: list[CapabilitySpec] = []
        current_ids: set[str] = set()
        for descriptor in self.client.tools(server_id, include_disabled=True):
            spec = _spec(descriptor)
            metadata = _metadata(descriptor, self.config)
            current_ids.add(spec.capability_id)
            existing = capability_metadata(self.database, spec.capability_id)
            if existing is None or existing.as_dict() != metadata.as_dict():
                register_capability(
                    self.database,
                    spec,
                    note="MCP discovery projection; remote content DATA_ONLY",
                    metadata=metadata,
                )
            if descriptor.enabled:
                record_outcome(
                    self.database,
                    spec.capability_id,
                    success=True,
                    verified=VerifyVerdict.MATCH,
                    note="MCP handshake, discovery, and bounded schema validation passed",
                )
            else:
                record_outcome(
                    self.database,
                    spec.capability_id,
                    success=False,
                    verified=VerifyVerdict.MISMATCH,
                    note="MCP tool disabled by local JARVIS policy",
                )
            projected.append(spec)
        for capability_id in set(prior) - current_ids:
            record_outcome(
                self.database,
                capability_id,
                success=False,
                verified=VerifyVerdict.MISMATCH,
                note="MCP tool disappeared from server discovery",
            )
        return tuple(sorted(projected, key=lambda item: item.capability_id))

    def connect_server(self, server_id: str) -> tuple[Any, tuple[CapabilitySpec, ...]]:
        health = self.client.connect(server_id)
        return health, self.project(server_id)

    def connect_all(self) -> dict[str, Any]:
        report: dict[str, Any] = {}
        for server in self.config.servers:
            if not server.enabled:
                report[server.server_id] = self.client.health(server.server_id).as_dict()
                self._mark_server_unavailable(
                    server.server_id, "MCP server disabled by local JARVIS policy"
                )
                continue
            try:
                health, capabilities = self.connect_server(server.server_id)
                report[server.server_id] = {
                    "health": health.as_dict(),
                    "capabilities": [item.as_dict() for item in capabilities],
                }
            except McpServerUnavailable as error:
                self._mark_server_unavailable(server.server_id, str(error))
                report[server.server_id] = {
                    "health": self.client.health(server.server_id).as_dict(),
                    "error": str(error),
                }
        return report

    def refresh_server(self, server_id: str) -> tuple[Any, tuple[CapabilitySpec, ...]]:
        try:
            health, _ = self.client.refresh(server_id)
        except McpServerUnavailable as error:
            self._mark_server_unavailable(server_id, str(error))
            raise
        return health, self.project(server_id)

    def disconnect_server(self, server_id: str, *, stopped: bool = False) -> Any:
        health = self.client.disconnect(server_id, stopped=stopped)
        self._mark_server_unavailable(
            server_id,
            "MCP session stopped by STOP" if stopped else "MCP session disconnected",
        )
        return health

    def reconnect_server(self, server_id: str) -> tuple[Any, tuple[CapabilitySpec, ...]]:
        try:
            health = self.client.reconnect(server_id)
        except McpServerUnavailable as error:
            self._mark_server_unavailable(server_id, str(error))
            raise
        return health, self.project(server_id)

    def invoke_action(
        self,
        action_kind: str,
        arguments: Mapping[str, Any],
        *,
        invocation_id: str,
        timeout_s: float | None = None,
        stop_check: Callable[[], bool] | None = None,
    ) -> McpInvocationReceipt:
        descriptor = self.client.descriptor_for_action(action_kind)
        try:
            return self.client.invoke(
                action_kind,
                arguments,
                invocation_id=invocation_id,
                timeout_s=timeout_s,
                stop_check=stop_check,
            )
        except McpServerUnavailable as error:
            self._mark_server_unavailable(descriptor.server_id, str(error))
            raise

    def close(self) -> None:
        errors: list[str] = []
        for server in self.config.servers:
            health = self.client.health(server.server_id)
            if health.state.value in {"READY", "CONNECTING", "RECONNECTING"}:
                try:
                    self.disconnect_server(server.server_id)
                except Exception as error:  # noqa: BLE001 - close remains best effort
                    errors.append(f"{server.server_id}: {error}")
        self.client.close()
        self.last_close_errors = tuple(errors)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


__all__ = ["MCP_ADAPTER_VERSION", "FabricMcpBackend"]
