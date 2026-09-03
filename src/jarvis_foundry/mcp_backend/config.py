"""Validated configuration for the bounded MCP capability backend.

Configuration chooses transport and JARVIS policy.  Remote MCP descriptions,
annotations, and schemas are untrusted DATA_ONLY inputs and never choose their
own semantic/effect class.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ..control.redaction import contains_secret_like
from ..fabric.model import Semantic, UnattendedMode

_IDENTITY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_HEADER = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")


class McpConfigurationError(ValueError):
    """An MCP server configuration is unsafe or malformed."""


class McpTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


@dataclass(frozen=True, slots=True)
class McpOutputLimits:
    """Persistence limits applied after the SDK decodes a protocol result."""

    max_content_items: int = 32
    max_text_chars: int = 32_000
    max_structured_bytes: int = 64 * 1024
    # Existing Fabric effect receipts are capped at 128 KiB; leave headroom
    # for canonical identity and verification fields around the MCP payload.
    max_persisted_bytes: int = 96 * 1024
    max_schema_bytes: int = 64 * 1024
    max_schema_depth: int = 24
    max_schema_nodes: int = 2_000
    max_argument_bytes: int = 256 * 1024
    max_raw_result_bytes: int = 8 * 1024 * 1024
    max_discovered_tools: int = 512
    max_discovered_resources: int = 512
    max_discovery_pages: int = 128

    def __post_init__(self) -> None:
        for name in (
            "max_content_items",
            "max_text_chars",
            "max_structured_bytes",
            "max_persisted_bytes",
            "max_schema_bytes",
            "max_schema_depth",
            "max_schema_nodes",
            "max_argument_bytes",
            "max_raw_result_bytes",
            "max_discovered_tools",
            "max_discovered_resources",
            "max_discovery_pages",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise McpConfigurationError(f"{name} must be a positive integer")
        if self.max_persisted_bytes < 4096:
            raise McpConfigurationError("max_persisted_bytes must be at least 4096")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> McpOutputLimits:
        return cls(**dict(value or {}))


@dataclass(frozen=True, slots=True)
class McpToolPolicy:
    """Local policy for one exact remote tool name."""

    enabled: bool = True
    semantic: Semantic | None = None
    unattended: UnattendedMode | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.semantic is not None and not isinstance(self.semantic, Semantic):
            object.__setattr__(self, "semantic", Semantic(self.semantic))
        if self.unattended is not None and not isinstance(
            self.unattended, UnattendedMode
        ):
            object.__setattr__(self, "unattended", UnattendedMode(self.unattended))
        object.__setattr__(
            self,
            "limitations",
            tuple(
                _bounded_text(item, "tool limitation", 500) for item in self.limitations
            ),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> McpToolPolicy:
        raw = dict(value or {})
        return cls(
            enabled=bool(raw.get("enabled", True)),
            semantic=(Semantic(str(raw["semantic"])) if raw.get("semantic") else None),
            unattended=(
                UnattendedMode(str(raw["unattended"]))
                if raw.get("unattended")
                else None
            ),
            limitations=tuple(raw.get("limitations") or ()),
        )


def _bounded_text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise McpConfigurationError(f"{name} must be a non-empty string")
    result = value.strip()
    if any(ord(char) < 32 and char not in "\t" for char in result):
        raise McpConfigurationError(f"{name} contains a control character")
    if len(result) > limit:
        raise McpConfigurationError(f"{name} exceeds {limit} characters")
    return result


def _identity(value: object, name: str) -> str:
    result = _bounded_text(value, name, 128)
    if not _IDENTITY.fullmatch(result):
        raise McpConfigurationError(f"{name} must be an exact identity")
    return result


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (tuple, list)):
        raise McpConfigurationError(f"{name} must be a list")
    return tuple(_bounded_text(item, f"{name} item", 256) for item in value)


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """One explicit MCP endpoint/process and its local policy boundary."""

    server_id: str
    transport: McpTransport
    enabled: bool = True
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    env_from: Mapping[str, str] = field(default_factory=dict)
    endpoint: str | None = None
    header_env: Mapping[str, str] = field(default_factory=dict)
    allow_insecure_http: bool = False
    connect_timeout_s: float = 15.0
    request_timeout_s: float = 60.0
    shutdown_timeout_s: float = 10.0
    max_concurrent_invocations: int = 4
    default_semantic: Semantic = Semantic.EXTERNAL_EFFECT
    default_unattended: UnattendedMode = UnattendedMode.INTERACTIVE_REQUIRED
    allowed_tools: tuple[str, ...] = ()
    disabled_tools: tuple[str, ...] = ()
    tools: Mapping[str, McpToolPolicy] = field(default_factory=dict)
    limits: McpOutputLimits = field(default_factory=McpOutputLimits)

    def __post_init__(self) -> None:
        object.__setattr__(self, "server_id", _identity(self.server_id, "server_id"))
        if not isinstance(self.transport, McpTransport):
            object.__setattr__(self, "transport", McpTransport(self.transport))
        if not isinstance(self.default_semantic, Semantic):
            object.__setattr__(
                self, "default_semantic", Semantic(self.default_semantic)
            )
        if not isinstance(self.default_unattended, UnattendedMode):
            object.__setattr__(
                self, "default_unattended", UnattendedMode(self.default_unattended)
            )
        object.__setattr__(self, "args", _string_tuple(self.args, "args"))
        object.__setattr__(
            self, "allowed_tools", _string_tuple(self.allowed_tools, "allowed_tools")
        )
        object.__setattr__(
            self, "disabled_tools", _string_tuple(self.disabled_tools, "disabled_tools")
        )
        normalized_tools: dict[str, McpToolPolicy] = {}
        for raw_name, policy in dict(self.tools).items():
            name = _bounded_text(raw_name, "tool name", 256)
            normalized_tools[name] = (
                policy
                if isinstance(policy, McpToolPolicy)
                else McpToolPolicy.from_mapping(policy)
            )
        object.__setattr__(self, "tools", normalized_tools)
        normalized_env: dict[str, str] = {}
        for key, value in dict(self.env).items():
            normalized_key = _identity(key, "environment key")
            normalized_value = _bounded_text(value, "environment value", 4096)
            if contains_secret_like(f"{normalized_key}={normalized_value}"):
                raise McpConfigurationError(
                    "secret-like stdio environment values must use env_from"
                )
            normalized_env[normalized_key] = normalized_value
        object.__setattr__(self, "env", normalized_env)
        normalized_env_from: dict[str, str] = {}
        for child_key, parent_key in dict(self.env_from).items():
            normalized_env_from[_identity(child_key, "environment key")] = _identity(
                parent_key, "environment reference"
            )
        object.__setattr__(self, "env_from", normalized_env_from)
        normalized_headers: dict[str, str] = {}
        for header, env_name in dict(self.header_env).items():
            if not isinstance(header, str) or not _HEADER.fullmatch(header):
                raise McpConfigurationError(
                    "header_env contains an invalid header name"
                )
            normalized_headers[header] = _identity(
                env_name, "header environment reference"
            )
        object.__setattr__(self, "header_env", normalized_headers)
        for name in ("connect_timeout_s", "request_timeout_s", "shutdown_timeout_s"):
            value = getattr(self, name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value <= 0
            ):
                raise McpConfigurationError(f"{name} must be positive")
        if (
            not isinstance(self.max_concurrent_invocations, int)
            or isinstance(self.max_concurrent_invocations, bool)
            or not 1 <= self.max_concurrent_invocations <= 64
        ):
            raise McpConfigurationError(
                "max_concurrent_invocations must be within 1..64"
            )
        if not isinstance(self.limits, McpOutputLimits):
            object.__setattr__(
                self, "limits", McpOutputLimits.from_mapping(self.limits)
            )
        if self.transport is McpTransport.STDIO:
            if not self.command:
                raise McpConfigurationError("stdio transport requires command")
            _bounded_text(self.command, "command", 2048)
            if self.endpoint is not None or self.header_env:
                raise McpConfigurationError(
                    "stdio transport cannot define HTTP settings"
                )
            if self.cwd is not None:
                cwd = Path(self.cwd).expanduser()
                if not cwd.is_absolute():
                    raise McpConfigurationError("stdio cwd must be absolute")
                object.__setattr__(self, "cwd", str(cwd.resolve()))
        else:
            if not self.endpoint:
                raise McpConfigurationError(
                    "streamable_http transport requires endpoint"
                )
            parsed = urlparse(self.endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise McpConfigurationError("endpoint must be an absolute HTTP(S) URL")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise McpConfigurationError(
                    "endpoint credentials/query/fragment are refused; use header_env"
                )
            loopback = parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme == "http" and not (loopback or self.allow_insecure_http):
                raise McpConfigurationError(
                    "plaintext Streamable HTTP is allowed only for loopback unless explicitly enabled"
                )
            if self.command is not None or self.args or self.cwd is not None:
                raise McpConfigurationError(
                    "streamable_http cannot define stdio settings"
                )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> McpServerConfig:
        if not isinstance(value, Mapping):
            raise McpConfigurationError("server configuration must be an object")
        raw = dict(value)
        tool_values = raw.get("tools") or {}
        if not isinstance(tool_values, Mapping):
            raise McpConfigurationError(
                "tools must be an object keyed by exact tool name"
            )
        return cls(
            server_id=str(raw.get("server_id") or ""),
            transport=McpTransport(str(raw.get("transport") or "")),
            enabled=bool(raw.get("enabled", True)),
            command=raw.get("command"),
            args=tuple(raw.get("args") or ()),
            cwd=raw.get("cwd"),
            env=dict(raw.get("env") or {}),
            env_from=dict(raw.get("env_from") or {}),
            endpoint=raw.get("endpoint"),
            header_env=dict(raw.get("header_env") or {}),
            allow_insecure_http=bool(raw.get("allow_insecure_http", False)),
            connect_timeout_s=float(raw.get("connect_timeout_s", 15.0)),
            request_timeout_s=float(raw.get("request_timeout_s", 60.0)),
            shutdown_timeout_s=float(raw.get("shutdown_timeout_s", 10.0)),
            max_concurrent_invocations=int(raw.get("max_concurrent_invocations", 4)),
            default_semantic=Semantic(
                str(raw.get("default_semantic") or Semantic.EXTERNAL_EFFECT.value)
            ),
            default_unattended=UnattendedMode(
                str(
                    raw.get("default_unattended")
                    or UnattendedMode.INTERACTIVE_REQUIRED.value
                )
            ),
            allowed_tools=tuple(raw.get("allowed_tools") or ()),
            disabled_tools=tuple(raw.get("disabled_tools") or ()),
            tools={
                str(name): McpToolPolicy.from_mapping(policy)
                for name, policy in tool_values.items()
            },
            limits=McpOutputLimits.from_mapping(raw.get("limits")),
        )

    def policy_for(self, tool_name: str) -> McpToolPolicy:
        explicit = self.tools.get(tool_name, McpToolPolicy())
        allowed = not self.allowed_tools or tool_name in self.allowed_tools
        enabled = (
            self.enabled
            and allowed
            and tool_name not in self.disabled_tools
            and explicit.enabled
        )
        return McpToolPolicy(
            enabled=enabled,
            semantic=explicit.semantic or self.default_semantic,
            unattended=explicit.unattended or self.default_unattended,
            limitations=explicit.limitations,
        )

    def resolved_environment(self) -> dict[str, str]:
        result = dict(self.env)
        for child_key, parent_key in self.env_from.items():
            value = os.environ.get(parent_key)
            if value is None:
                raise McpConfigurationError(
                    f"required environment reference {parent_key!r} is unavailable"
                )
            result[child_key] = value
        return result

    def resolved_headers(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for header, env_name in self.header_env.items():
            value = os.environ.get(env_name)
            if value is None:
                raise McpConfigurationError(
                    f"required header environment reference {env_name!r} is unavailable"
                )
            result[header] = value
        return result

    @property
    def credential_refs(self) -> tuple[str, ...]:
        return tuple(
            sorted(set(self.env_from.values()) | set(self.header_env.values()))
        )


@dataclass(frozen=True, slots=True)
class McpBackendConfig:
    servers: tuple[McpServerConfig, ...]

    def __post_init__(self) -> None:
        if not self.servers:
            raise McpConfigurationError("at least one MCP server is required")
        seen: set[str] = set()
        for server in self.servers:
            if server.server_id in seen:
                raise McpConfigurationError(f"duplicate server_id {server.server_id!r}")
            seen.add(server.server_id)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> McpBackendConfig:
        if not isinstance(value, Mapping):
            raise McpConfigurationError("MCP configuration must be an object")
        raw_servers = value.get("servers")
        if not isinstance(raw_servers, list):
            raise McpConfigurationError("servers must be a list")
        return cls(tuple(McpServerConfig.from_mapping(item) for item in raw_servers))

    def server(self, server_id: str) -> McpServerConfig:
        for item in self.servers:
            if item.server_id == server_id:
                return item
        raise McpConfigurationError(f"unknown MCP server {server_id!r}")


def load_mcp_config(path: str | Path) -> McpBackendConfig:
    source = Path(path).expanduser().resolve()
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    return McpBackendConfig.from_mapping(raw or {})


__all__ = [
    "McpBackendConfig",
    "McpConfigurationError",
    "McpOutputLimits",
    "McpServerConfig",
    "McpToolPolicy",
    "McpTransport",
    "load_mcp_config",
]
