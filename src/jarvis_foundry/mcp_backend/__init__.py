"""Bounded MCP client backend behind the existing Capability Fabric."""

from .adoption import (
    CANONICAL_MCP_RUNTIME,
    MCP_ADOPTION_SEAM_VERSION,
    SELECTED_SOLUTION_CONTRACT,
    SelectedMcpAdoption,
    restore_selected_mcp_backend,
    selected_solution_mcp_adoption,
)

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
    "CANONICAL_MCP_RUNTIME",
    "MCP_ADAPTER_VERSION",
    "MCP_ADOPTION_SEAM_VERSION",
    "SELECTED_SOLUTION_CONTRACT",
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
    "SelectedMcpAdoption",
    "load_mcp_config",
    "normalize_json_schema",
    "normalize_result",
    "restore_selected_mcp_backend",
    "stable_server_identity",
    "stable_tool_identity",
    "selected_solution_mcp_adoption",
    "validate_arguments",
]
