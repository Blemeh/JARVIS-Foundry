"""Bounded MCP client backend behind the existing Capability Fabric."""

from .client import (
    McpBackendError,
    McpClientBackend,
    McpDependencyUnavailable,
    McpInvocationCancelled,
    McpInvocationTimeout,
    McpServerUnavailable,
    McpToolUnavailable,
)
from .config import (
    McpBackendConfig,
    McpConfigurationError,
    McpOutputLimits,
    McpServerConfig,
    McpToolPolicy,
    McpTransport,
    load_mcp_config,
)
from .contracts import (
    McpContractError,
    McpInvocationReceipt,
    McpResourceDescriptor,
    McpServerHealth,
    McpSessionState,
    McpToolDescriptor,
    normalize_json_schema,
    normalize_result,
    stable_server_identity,
    stable_tool_identity,
    validate_arguments,
)
from .fabric import MCP_ADAPTER_VERSION, FabricMcpBackend

__all__ = [
    "MCP_ADAPTER_VERSION",
    "FabricMcpBackend",
    "McpBackendConfig",
    "McpBackendError",
    "McpClientBackend",
    "McpConfigurationError",
    "McpContractError",
    "McpDependencyUnavailable",
    "McpInvocationCancelled",
    "McpInvocationReceipt",
    "McpInvocationTimeout",
    "McpOutputLimits",
    "McpResourceDescriptor",
    "McpServerConfig",
    "McpServerHealth",
    "McpServerUnavailable",
    "McpSessionState",
    "McpToolDescriptor",
    "McpToolPolicy",
    "McpToolUnavailable",
    "McpTransport",
    "load_mcp_config",
    "normalize_json_schema",
    "normalize_result",
    "stable_server_identity",
    "stable_tool_identity",
    "validate_arguments",
]
