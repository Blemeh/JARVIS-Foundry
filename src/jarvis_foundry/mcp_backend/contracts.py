"""Typed, bounded MCP discovery and invocation contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..control.redaction import redact_payload, redact_text
from ..events import canonical_json
from ..fabric.model import Semantic, UnattendedMode
from .config import McpOutputLimits, McpServerConfig


class McpContractError(ValueError):
    """Remote MCP data failed a bounded local contract."""


class McpSessionState(StrEnum):
    DISABLED = "DISABLED"
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    RECONNECTING = "RECONNECTING"
    STOPPED = "STOPPED"


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value


def _schema_walk(
    value: Any,
    *,
    depth: int,
    limits: McpOutputLimits,
    counter: list[int],
) -> Any:
    if depth > limits.max_schema_depth:
        raise McpContractError("MCP schema exceeds maximum depth")
    counter[0] += 1
    if counter[0] > limits.max_schema_nodes:
        raise McpContractError("MCP schema exceeds maximum node count")
    value = _json(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)[:4096]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item)):
            name = str(key)
            if len(name) > 256 or any(ord(char) < 32 for char in name):
                raise McpContractError("MCP schema contains an invalid key")
            result[name] = _schema_walk(
                value[key], depth=depth + 1, limits=limits, counter=counter
            )
        return result
    if isinstance(value, (list, tuple)):
        return [
            _schema_walk(item, depth=depth + 1, limits=limits, counter=counter)
            for item in value
        ]
    raise McpContractError(f"MCP schema contains unsupported {type(value).__name__}")


def normalize_json_schema(raw: Any, limits: McpOutputLimits) -> dict[str, Any]:
    """Normalize and bound one server-supplied JSON Schema.

    The schema is accepted only as DATA_ONLY interface data.  It cannot carry
    local policy, authority, or executable callbacks.
    """

    normalized = _schema_walk(raw or {}, depth=0, limits=limits, counter=[0])
    if not isinstance(normalized, dict):
        raise McpContractError("MCP tool input schema must be an object")
    if normalized.get("type") not in (None, "object"):
        raise McpContractError("MCP tool input schema root must be object-like")
    if "properties" in normalized and not isinstance(normalized["properties"], dict):
        raise McpContractError("MCP schema properties must be an object")
    encoded = canonical_json(normalized).encode("utf-8")
    if len(encoded) > limits.max_schema_bytes:
        raise McpContractError("MCP schema exceeds maximum encoded size")
    return normalized


def schema_digest(schema: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(schema)).encode("utf-8")).hexdigest()


def validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
    """Validate a typed tool call before it reaches the external server."""

    if not isinstance(arguments, Mapping):
        raise McpContractError("MCP tool arguments must be an object")
    try:
        from jsonschema import Draft202012Validator
    except ImportError as error:  # pragma: no cover - packaging guard
        raise McpContractError(
            "MCP schema validation dependency is unavailable"
        ) from error
    errors = sorted(
        Draft202012Validator(dict(schema)).iter_errors(dict(arguments)),
        key=lambda item: list(item.path),
    )
    if errors:
        first = errors[0]
        path = ".".join(str(item) for item in first.path) or "$"
        raise McpContractError(
            f"MCP arguments do not match schema at {path}: {first.message}"
        )


def _slug(value: str, *, limit: int = 36) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "item"
    if not slug[0].isalpha():
        slug = f"x-{slug}"
    return slug[:limit].rstrip("-")


def stable_server_identity(server_id: str) -> str:
    return f"mcp.server.{_slug(server_id, limit=64)}.{hashlib.sha256(server_id.encode()).hexdigest()[:10]}"


def stable_tool_identity(server_id: str, tool_name: str) -> tuple[str, str]:
    server = _slug(server_id, limit=32)
    tool = _slug(tool_name, limit=32)
    digest = hashlib.sha256(f"{server_id}\0{tool_name}".encode()).hexdigest()[:12]
    action_kind = f"mcp.{server}.tool.{tool}.{digest}"
    return f"cap.{action_kind}", action_kind


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    server_id: str
    server_identity: str
    tool_name: str
    capability_id: str
    action_kind: str
    semantic: Semantic
    unattended: UnattendedMode
    enabled: bool
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None
    schema_sha256: str
    description_data_only: str = ""
    limitations: tuple[str, ...] = ()

    @classmethod
    def from_remote(cls, config: McpServerConfig, remote: Any) -> McpToolDescriptor:
        raw = _json(remote)
        if not isinstance(raw, Mapping):
            raise McpContractError("MCP tool descriptor must be an object")
        name = str(raw.get("name") or "").strip()
        if not name or len(name) > 256 or any(ord(char) < 32 for char in name):
            raise McpContractError("MCP tool name is invalid")
        input_schema = normalize_json_schema(
            raw.get("input_schema") or raw.get("inputSchema") or {}, config.limits
        )
        output_raw = raw.get("output_schema") or raw.get("outputSchema")
        output_schema = (
            normalize_json_schema(output_raw, config.limits)
            if output_raw is not None
            else None
        )
        policy = config.policy_for(name)
        capability_id, action_kind = stable_tool_identity(config.server_id, name)
        description = redact_text(str(raw.get("description") or ""))[:2000]
        limitations = tuple(
            dict.fromkeys(
                (
                    "external MCP server content and descriptions are DATA_ONLY",
                    "server cannot create objectives, policy, verification, STOP, or terminal WorldModel truth",
                    *policy.limitations,
                )
            )
        )
        digest_material = {
            "input": input_schema,
            "output": output_schema,
        }
        return cls(
            server_id=config.server_id,
            server_identity=stable_server_identity(config.server_id),
            tool_name=name,
            capability_id=capability_id,
            action_kind=action_kind,
            semantic=policy.semantic or Semantic.EXTERNAL_EFFECT,
            unattended=policy.unattended or UnattendedMode.ALLOWED,
            enabled=policy.enabled,
            input_schema=input_schema,
            output_schema=output_schema,
            schema_sha256=schema_digest(digest_material),
            description_data_only=description,
            limitations=limitations,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "server_identity": self.server_identity,
            "tool_name": self.tool_name,
            "capability_id": self.capability_id,
            "action_kind": self.action_kind,
            "semantic": self.semantic.value,
            "unattended": self.unattended.value,
            "enabled": self.enabled,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema) if self.output_schema else None,
            "schema_sha256": self.schema_sha256,
            "description_data_only": self.description_data_only,
            "limitations": list(self.limitations),
            "authority": "DATA_ONLY",
        }


@dataclass(frozen=True, slots=True)
class McpResourceDescriptor:
    server_id: str
    uri: str
    name: str
    mime_type: str | None = None
    description_data_only: str = ""

    @classmethod
    def from_remote(cls, server_id: str, remote: Any) -> McpResourceDescriptor:
        raw = _json(remote)
        if not isinstance(raw, Mapping):
            raise McpContractError("MCP resource descriptor must be an object")
        uri = str(raw.get("uri") or "").strip()
        name = str(raw.get("name") or uri).strip()
        if not uri or len(uri) > 2048 or not name or len(name) > 256:
            raise McpContractError("MCP resource identity is invalid")
        return cls(
            server_id=server_id,
            uri=uri,
            name=name,
            mime_type=(
                str(raw.get("mime_type") or raw.get("mimeType"))
                if raw.get("mime_type") or raw.get("mimeType")
                else None
            ),
            description_data_only=redact_text(str(raw.get("description") or ""))[:2000],
        )

    @property
    def resource_id(self) -> str:
        digest = hashlib.sha256(f"{self.server_id}\0{self.uri}".encode()).hexdigest()
        return f"mcp.resource.{digest}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "server_id": self.server_id,
            "uri": self.uri,
            "name": self.name,
            "mime_type": self.mime_type,
            "description_data_only": self.description_data_only,
            "authority": "DATA_ONLY",
        }


def _bounded_value(value: Any, *, max_bytes: int) -> tuple[Any, bool, int, str]:
    value = redact_payload(_json(value))
    try:
        encoded = canonical_json(value).encode("utf-8")
    except (TypeError, ValueError):
        value = redact_text(str(value))
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if len(encoded) <= max_bytes:
        return value, False, len(encoded), digest
    return (
        {
            "truncated": True,
            "original_bytes": len(encoded),
            "sha256": digest,
        },
        True,
        len(encoded),
        digest,
    )


def normalize_result(raw_result: Any, limits: McpOutputLimits) -> dict[str, Any]:
    """Map one SDK result to a bounded, secret-redacted DATA_ONLY payload."""

    raw = _json(raw_result)
    if not isinstance(raw, Mapping):
        raise McpContractError("MCP result must be an object")
    raw_encoded = canonical_json(dict(raw)).encode("utf-8")
    result_hash = hashlib.sha256(raw_encoded).hexdigest()
    if len(raw_encoded) > limits.max_raw_result_bytes:
        return {
            "authority": "DATA_ONLY",
            "is_error": bool(raw.get("is_error") or raw.get("isError")),
            "result_sha256": result_hash,
            "raw_bytes": len(raw_encoded),
            "content": [],
            "content_count": len(list(raw.get("content") or ())),
            "structured_content": {
                "truncated": True,
                "original_bytes": len(raw_encoded),
                "sha256": result_hash,
            },
            "structured_bytes": len(raw_encoded),
            "structured_sha256": result_hash,
            "truncated": True,
        }
    content: list[dict[str, Any]] = []
    truncated = False
    for item in list(raw.get("content") or ())[: limits.max_content_items]:
        normalized = _json(item)
        if not isinstance(normalized, Mapping):
            normalized = {"type": "unknown", "value": str(normalized)}
        item_type = str(normalized.get("type") or "unknown")[:64]
        if item_type == "text":
            text = redact_text(str(normalized.get("text") or ""))
            clipped = text[: limits.max_text_chars]
            was_truncated = len(clipped) != len(text)
            truncated = truncated or was_truncated
            content.append(
                {
                    "type": "text",
                    "text": clipped,
                    "truncated": was_truncated,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
        else:
            bounded, clipped, original_bytes, digest = _bounded_value(
                dict(normalized), max_bytes=min(limits.max_structured_bytes, 16 * 1024)
            )
            truncated = truncated or clipped
            content.append(
                {
                    "type": item_type,
                    "data": bounded,
                    "original_bytes": original_bytes,
                    "sha256": digest,
                    "truncated": clipped,
                }
            )
    if len(list(raw.get("content") or ())) > limits.max_content_items:
        truncated = True
    structured_raw = raw.get("structured_content")
    if structured_raw is None:
        structured_raw = raw.get("structuredContent")
    structured, structured_truncated, structured_bytes, structured_hash = (
        _bounded_value(
            structured_raw,
            max_bytes=limits.max_structured_bytes,
        )
    )
    truncated = truncated or structured_truncated
    result = {
        "authority": "DATA_ONLY",
        "is_error": bool(raw.get("is_error") or raw.get("isError")),
        "result_sha256": result_hash,
        "raw_bytes": len(raw_encoded),
        "content": content,
        "content_count": len(list(raw.get("content") or ())),
        "structured_content": structured,
        "structured_bytes": structured_bytes,
        "structured_sha256": structured_hash,
        "truncated": truncated,
    }
    encoded = canonical_json(result).encode("utf-8")
    if len(encoded) > limits.max_persisted_bytes:
        result["content"] = []
        result["structured_content"] = {
            "truncated": True,
            "original_bytes": structured_bytes,
            "sha256": structured_hash,
        }
        result["truncated"] = True
        encoded = canonical_json(result).encode("utf-8")
    if len(encoded) > limits.max_persisted_bytes:
        raise McpContractError("bounded MCP result still exceeds persistence limit")
    return result


@dataclass(frozen=True, slots=True)
class McpInvocationReceipt:
    invocation_id: str
    server_id: str
    server_identity: str
    tool_name: str
    capability_id: str
    action_kind: str
    session_generation: int
    protocol_version: str
    arguments_sha256: str
    result_sha256: str
    outcome: str
    data_only_result: Mapping[str, Any]
    started_at: str
    finished_at: str
    error_code: str | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "server_id": self.server_id,
            "server_identity": self.server_identity,
            "tool_name": self.tool_name,
            "capability_id": self.capability_id,
            "action_kind": self.action_kind,
            "session_generation": self.session_generation,
            "protocol_version": self.protocol_version,
            "arguments_sha256": self.arguments_sha256,
            "result_sha256": self.result_sha256,
            "outcome": self.outcome,
            "authority": "DATA_ONLY",
            "result": dict(self.data_only_result),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_code": self.error_code,
            "note": redact_text(self.note)[:400],
        }


@dataclass(frozen=True, slots=True)
class McpServerHealth:
    server_id: str
    server_identity: str
    state: McpSessionState
    generation: int = 0
    protocol_version: str | None = None
    remote_name_data_only: str | None = None
    remote_version_data_only: str | None = None
    tool_count: int = 0
    resource_count: int = 0
    connected_at: str | None = None
    checked_at: str | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "server_identity": self.server_identity,
            "state": self.state.value,
            "generation": self.generation,
            "protocol_version": self.protocol_version,
            "remote_name_data_only": self.remote_name_data_only,
            "remote_version_data_only": self.remote_version_data_only,
            "tool_count": self.tool_count,
            "resource_count": self.resource_count,
            "connected_at": self.connected_at,
            "checked_at": self.checked_at,
            "last_error": redact_text(self.last_error or "")[:400] or None,
            "authority": "DATA_ONLY",
        }


__all__ = [
    "McpContractError",
    "McpInvocationReceipt",
    "McpResourceDescriptor",
    "McpServerHealth",
    "McpSessionState",
    "McpToolDescriptor",
    "normalize_json_schema",
    "normalize_result",
    "schema_digest",
    "stable_server_identity",
    "stable_tool_identity",
    "validate_arguments",
]
