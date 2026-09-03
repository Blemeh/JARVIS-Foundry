"""Persistent official-SDK MCP client sessions on a dedicated asyncio loop."""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self

from ..clock import isoformat
from ..events import canonical_json
from .config import McpBackendConfig, McpServerConfig, McpTransport
from .contracts import (
    McpContractError,
    McpInvocationReceipt,
    McpResourceDescriptor,
    McpServerHealth,
    McpSessionState,
    McpToolDescriptor,
    normalize_result,
    stable_server_identity,
    validate_arguments,
)


class McpBackendError(RuntimeError):
    """Base error for a bounded MCP session operation."""


class McpDependencyUnavailable(McpBackendError):
    """The optional official MCP SDK is not installed."""


class McpServerUnavailable(McpBackendError):
    """A configured server is disconnected or failed health checking."""


class McpToolUnavailable(McpBackendError):
    """A tool is absent or disabled by local JARVIS policy."""


class McpInvocationTimeout(TimeoutError, McpBackendError):
    """A bounded invocation timed out and was cancelled."""


class McpInvocationCancelled(McpBackendError):
    """A bounded invocation was cancelled explicitly or by STOP."""


@dataclass(slots=True)
class _SessionRuntime:
    config: McpServerConfig
    state: McpSessionState
    generation: int = 0
    client: Any | None = None
    http_client: Any | None = None
    tools: dict[str, McpToolDescriptor] = field(default_factory=dict)
    resources: dict[str, McpResourceDescriptor] = field(default_factory=dict)
    protocol_version: str | None = None
    remote_name: str | None = None
    remote_version: str | None = None
    connected_at: str | None = None
    checked_at: str | None = None
    last_error: str | None = None
    discovery_errors: tuple[str, ...] = ()
    invocation_tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)

    def health(self) -> McpServerHealth:
        return McpServerHealth(
            server_id=self.config.server_id,
            server_identity=stable_server_identity(self.config.server_id),
            state=self.state,
            generation=self.generation,
            protocol_version=self.protocol_version,
            remote_name_data_only=self.remote_name,
            remote_version_data_only=self.remote_version,
            tool_count=len(self.tools),
            resource_count=len(self.resources),
            connected_at=self.connected_at,
            checked_at=self.checked_at,
            last_error=self.last_error,
        )


class McpClientBackend:
    """Lifecycle owner for configured MCP sessions.

    MCP I/O is async, while the established Fabric execution surface is sync.
    A single dedicated event-loop thread preserves long-lived sessions without
    creating a scheduler or execution authority.
    """

    def __init__(self, config: McpBackendConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._sessions = {
            item.server_id: _SessionRuntime(
                config=item,
                state=(
                    McpSessionState.DISCONNECTED
                    if item.enabled else McpSessionState.DISABLED
                ),
            )
            for item in config.servers
        }
        self._thread = threading.Thread(
            target=self._run_loop,
            name="jarvis-mcp-client-loop",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise McpBackendError("MCP event loop failed to start")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.close()

    @staticmethod
    def _sdk() -> tuple[Any, Any, Any]:
        try:
            from mcp import Client, StdioServerParameters
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as error:  # pragma: no cover - packaging guard
            raise McpDependencyUnavailable(
                "install jarvis-foundry[mcp] to use the MCP backend"
            ) from error
        return Client, StdioServerParameters, streamable_http_client

    def _submit(self, coroutine: Any, *, timeout: float) -> Any:
        if self._closed:
            coroutine.close()
            raise McpBackendError("MCP backend is closed")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as error:
            future.cancel()
            raise McpInvocationTimeout("MCP operation exceeded its local deadline") from error
        except concurrent.futures.CancelledError as error:
            raise McpInvocationCancelled("MCP operation was cancelled") from error

    def _runtime(self, server_id: str) -> _SessionRuntime:
        try:
            return self._sessions[server_id]
        except KeyError as error:
            raise McpServerUnavailable(f"unknown MCP server {server_id!r}") from error

    async def _build_client(self, runtime: _SessionRuntime) -> tuple[Any, Any | None]:
        Client, StdioServerParameters, streamable_http_client = self._sdk()
        config = runtime.config
        if config.transport is McpTransport.STDIO:
            params = StdioServerParameters(
                command=str(config.command),
                args=list(config.args),
                env=config.resolved_environment() or None,
                cwd=config.cwd,
            )
            return Client(params, read_timeout_seconds=config.request_timeout_s), None
        headers = config.resolved_headers()
        if not headers:
            return Client(str(config.endpoint), read_timeout_seconds=config.request_timeout_s), None
        try:
            import httpx2
        except ImportError as error:  # pragma: no cover - SDK dependency guard
            raise McpDependencyUnavailable("httpx2 is required for authenticated MCP HTTP") from error
        http_client = httpx2.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=httpx2.Timeout(config.request_timeout_s),
        )
        await http_client.__aenter__()
        transport = streamable_http_client(str(config.endpoint), http_client=http_client)
        return Client(transport, read_timeout_seconds=config.request_timeout_s), http_client

    async def _list_tools(self, runtime: _SessionRuntime) -> tuple[dict[str, McpToolDescriptor], tuple[str, ...]]:
        tools: dict[str, McpToolDescriptor] = {}
        errors: list[str] = []
        cursor: str | None = None
        while True:
            response = await runtime.client.list_tools(cursor=cursor, cache_mode="reload")
            for remote in response.tools:
                try:
                    descriptor = McpToolDescriptor.from_remote(runtime.config, remote)
                except (McpContractError, ValueError) as error:
                    errors.append(str(error)[:400])
                    continue
                if descriptor.tool_name in tools:
                    errors.append(f"duplicate MCP tool {descriptor.tool_name!r}")
                    continue
                tools[descriptor.tool_name] = descriptor
            cursor = getattr(response, "next_cursor", None)
            if not cursor:
                break
        return tools, tuple(errors)

    async def _list_resources(self, runtime: _SessionRuntime) -> dict[str, McpResourceDescriptor]:
        capabilities = getattr(runtime.client, "server_capabilities", None)
        if capabilities is None or getattr(capabilities, "resources", None) is None:
            return {}
        resources: dict[str, McpResourceDescriptor] = {}
        cursor: str | None = None
        while True:
            response = await runtime.client.list_resources(cursor=cursor, cache_mode="reload")
            for remote in response.resources:
                descriptor = McpResourceDescriptor.from_remote(runtime.config.server_id, remote)
                resources[descriptor.uri] = descriptor
            cursor = getattr(response, "next_cursor", None)
            if not cursor:
                break
        return resources

    async def _connect(self, server_id: str, reconnect: bool) -> McpServerHealth:
        runtime = self._runtime(server_id)
        if not runtime.config.enabled:
            runtime.state = McpSessionState.DISABLED
            return runtime.health()
        if runtime.state is McpSessionState.READY and runtime.client is not None:
            return runtime.health()
        runtime.state = (
            McpSessionState.RECONNECTING if reconnect else McpSessionState.CONNECTING
        )
        runtime.last_error = None
        client = None
        http_client = None
        try:
            client, http_client = await self._build_client(runtime)
            await asyncio.wait_for(client.__aenter__(), runtime.config.connect_timeout_s)
            runtime.client = client
            runtime.http_client = http_client
            tools, errors = await asyncio.wait_for(
                self._list_tools(runtime), runtime.config.connect_timeout_s
            )
            resources = await asyncio.wait_for(
                self._list_resources(runtime), runtime.config.connect_timeout_s
            )
            info = getattr(client, "server_info", None)
            runtime.remote_name = (
                str(getattr(info, "name", ""))[:256] or None if info is not None else None
            )
            runtime.remote_version = (
                str(getattr(info, "version", ""))[:128] or None if info is not None else None
            )
            runtime.protocol_version = str(getattr(client, "protocol_version", "")) or None
            runtime.tools = tools
            runtime.resources = resources
            runtime.discovery_errors = errors
            runtime.generation += 1
            runtime.connected_at = isoformat()
            runtime.checked_at = runtime.connected_at
            runtime.state = McpSessionState.READY
            runtime.last_error = "; ".join(errors)[:400] or None
            return runtime.health()
        except BaseException as error:
            if client is not None:
                try:
                    await client.__aexit__(type(error), error, error.__traceback__)
                except Exception as close_error:  # noqa: BLE001 - preserve primary connect error
                    runtime.last_error = f"client cleanup failed: {close_error}"[:400]
            if http_client is not None:
                try:
                    await http_client.__aexit__(type(error), error, error.__traceback__)
                except Exception as close_error:  # noqa: BLE001 - preserve primary connect error
                    runtime.last_error = f"HTTP cleanup failed: {close_error}"[:400]
            runtime.client = None
            runtime.http_client = None
            runtime.state = McpSessionState.DEGRADED
            runtime.checked_at = isoformat()
            runtime.last_error = str(error)[:400]
            if isinstance(error, (McpBackendError, McpContractError)):
                raise
            raise McpServerUnavailable(
                f"MCP server {server_id!r} failed to connect: {error}"
            ) from error

    def connect(self, server_id: str) -> McpServerHealth:
        config = self._runtime(server_id).config
        return self._submit(
            self._connect(server_id, False),
            timeout=config.connect_timeout_s + 2,
        )

    async def _disconnect(self, server_id: str, stopped: bool = False) -> McpServerHealth:
        runtime = self._runtime(server_id)
        for task in tuple(runtime.invocation_tasks.values()):
            task.cancel()
        if runtime.invocation_tasks:
            await asyncio.gather(*runtime.invocation_tasks.values(), return_exceptions=True)
        runtime.invocation_tasks.clear()
        client = runtime.client
        http_client = runtime.http_client
        runtime.client = None
        runtime.http_client = None
        if client is not None:
            try:
                await asyncio.wait_for(
                    client.__aexit__(None, None, None), runtime.config.shutdown_timeout_s
                )
            except BaseException as error:  # noqa: BLE001 - shutdown remains best effort
                runtime.last_error = str(error)[:400]
        if http_client is not None:
            try:
                await asyncio.wait_for(
                    http_client.__aexit__(None, None, None), runtime.config.shutdown_timeout_s
                )
            except Exception as error:  # noqa: BLE001 - shutdown remains best effort
                runtime.last_error = str(error)[:400]
        runtime.state = (
            McpSessionState.STOPPED if stopped else McpSessionState.DISCONNECTED
        )
        runtime.checked_at = isoformat()
        return runtime.health()

    def disconnect(self, server_id: str, *, stopped: bool = False) -> McpServerHealth:
        config = self._runtime(server_id).config
        return self._submit(
            self._disconnect(server_id, stopped),
            timeout=config.shutdown_timeout_s + 2,
        )

    def reconnect(self, server_id: str) -> McpServerHealth:
        config = self._runtime(server_id).config
        self.disconnect(server_id)
        return self._submit(
            self._connect(server_id, True),
            timeout=config.connect_timeout_s + 2,
        )

    async def _refresh(self, server_id: str) -> tuple[McpServerHealth, tuple[McpToolDescriptor, ...]]:
        runtime = self._runtime(server_id)
        if runtime.state is not McpSessionState.READY or runtime.client is None:
            raise McpServerUnavailable(f"MCP server {server_id!r} is not connected")
        try:
            await runtime.client.send_ping()
            tools, errors = await self._list_tools(runtime)
            runtime.resources = await self._list_resources(runtime)
            runtime.tools = tools
            runtime.discovery_errors = errors
            runtime.checked_at = isoformat()
            runtime.last_error = "; ".join(errors)[:400] or None
            return runtime.health(), tuple(tools[name] for name in sorted(tools))
        except BaseException as error:
            runtime.state = McpSessionState.DEGRADED
            runtime.checked_at = isoformat()
            runtime.last_error = str(error)[:400]
            raise McpServerUnavailable(
                f"MCP server {server_id!r} failed readiness check: {error}"
            ) from error

    def refresh(self, server_id: str) -> tuple[McpServerHealth, tuple[McpToolDescriptor, ...]]:
        config = self._runtime(server_id).config
        return self._submit(
            self._refresh(server_id), timeout=config.request_timeout_s + 2
        )

    def tools(self, server_id: str, *, include_disabled: bool = False) -> tuple[McpToolDescriptor, ...]:
        runtime = self._runtime(server_id)
        result = tuple(runtime.tools[name] for name in sorted(runtime.tools))
        return result if include_disabled else tuple(item for item in result if item.enabled)

    def resources(self, server_id: str) -> tuple[McpResourceDescriptor, ...]:
        runtime = self._runtime(server_id)
        return tuple(runtime.resources[uri] for uri in sorted(runtime.resources))

    def health(self, server_id: str) -> McpServerHealth:
        return self._runtime(server_id).health()

    def health_all(self) -> tuple[McpServerHealth, ...]:
        return tuple(self.health(server.server_id) for server in self.config.servers)

    def descriptor_for_action(self, action_kind: str) -> McpToolDescriptor:
        for runtime in self._sessions.values():
            for descriptor in runtime.tools.values():
                if descriptor.action_kind == action_kind:
                    if not descriptor.enabled:
                        raise McpToolUnavailable(
                            f"MCP tool {descriptor.tool_name!r} is disabled by local policy"
                        )
                    return descriptor
        raise McpToolUnavailable(f"no discovered MCP tool for {action_kind!r}")

    async def _invoke(
        self,
        descriptor: McpToolDescriptor,
        arguments: Mapping[str, Any],
        invocation_id: str,
        timeout_s: float,
        stop_check: Callable[[], bool] | None,
    ) -> McpInvocationReceipt:
        runtime = self._runtime(descriptor.server_id)
        if runtime.state is not McpSessionState.READY or runtime.client is None:
            raise McpServerUnavailable(
                f"MCP server {descriptor.server_id!r} is not connected"
            )
        current = runtime.tools.get(descriptor.tool_name)
        if current is None or not current.enabled:
            raise McpToolUnavailable(
                f"MCP tool {descriptor.tool_name!r} is absent or disabled"
            )
        validate_arguments(current.input_schema, arguments)
        arguments_hash = hashlib.sha256(
            canonical_json(dict(arguments)).encode("utf-8")
        ).hexdigest()
        started = isoformat()
        call_task = asyncio.create_task(
            runtime.client.call_tool(
                descriptor.tool_name,
                dict(arguments),
                read_timeout_seconds=timeout_s,
            ),
            name=f"mcp:{invocation_id}",
        )
        runtime.invocation_tasks[invocation_id] = call_task
        deadline = asyncio.get_running_loop().time() + timeout_s
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    call_task.cancel()
                    await asyncio.gather(call_task, return_exceptions=True)
                    raise McpInvocationTimeout(
                        f"MCP tool {descriptor.tool_name!r} exceeded {timeout_s:.3f}s"
                    )
                done, _ = await asyncio.wait({call_task}, timeout=min(0.1, remaining))
                if call_task in done:
                    raw = call_task.result()
                    break
                if stop_check is not None and stop_check():
                    call_task.cancel()
                    await asyncio.gather(call_task, return_exceptions=True)
                    raise McpInvocationCancelled("STOP cancelled MCP invocation")
            normalized = normalize_result(raw, runtime.config.limits)
            outcome = "FAILED" if normalized["is_error"] else "COMPLETED"
            return McpInvocationReceipt(
                invocation_id=invocation_id,
                server_id=descriptor.server_id,
                server_identity=descriptor.server_identity,
                tool_name=descriptor.tool_name,
                capability_id=descriptor.capability_id,
                action_kind=descriptor.action_kind,
                session_generation=runtime.generation,
                protocol_version=str(runtime.protocol_version or "UNKNOWN"),
                arguments_sha256=arguments_hash,
                result_sha256=str(normalized["result_sha256"]),
                outcome=outcome,
                data_only_result=normalized,
                started_at=started,
                finished_at=isoformat(),
                error_code=("mcp_tool_error" if normalized["is_error"] else None),
            )
        except TimeoutError as error:
            if isinstance(error, McpInvocationTimeout):
                raise
            call_task.cancel()
            await asyncio.gather(call_task, return_exceptions=True)
            raise McpInvocationTimeout(
                f"MCP tool {descriptor.tool_name!r} exceeded {timeout_s:.3f}s"
            ) from error
        finally:
            runtime.invocation_tasks.pop(invocation_id, None)

    def invoke(
        self,
        action_kind: str,
        arguments: Mapping[str, Any],
        *,
        invocation_id: str | None = None,
        timeout_s: float | None = None,
        stop_check: Callable[[], bool] | None = None,
    ) -> McpInvocationReceipt:
        descriptor = self.descriptor_for_action(action_kind)
        runtime = self._runtime(descriptor.server_id)
        bounded_timeout = float(timeout_s or runtime.config.request_timeout_s)
        if bounded_timeout <= 0 or bounded_timeout > 3600:
            raise McpContractError("MCP invocation timeout must be within (0, 3600] seconds")
        identity = invocation_id or f"mcp-invocation:{uuid.uuid4().hex}"
        return self._submit(
            self._invoke(
                descriptor, dict(arguments), identity, bounded_timeout, stop_check
            ),
            timeout=bounded_timeout + 2,
        )

    async def _cancel(self, invocation_id: str) -> bool:
        for runtime in self._sessions.values():
            task = runtime.invocation_tasks.get(invocation_id)
            if task is not None:
                task.cancel()
                return True
        return False

    def cancel(self, invocation_id: str) -> bool:
        return bool(self._submit(self._cancel(invocation_id), timeout=2))

    async def _read_resource(self, server_id: str, uri: str) -> dict[str, Any]:
        runtime = self._runtime(server_id)
        if runtime.state is not McpSessionState.READY or runtime.client is None:
            raise McpServerUnavailable(f"MCP server {server_id!r} is not connected")
        if uri not in runtime.resources:
            raise McpToolUnavailable("MCP resource must be discovered before it is read")
        raw = await runtime.client.read_resource(uri)
        payload = raw.model_dump(mode="json", by_alias=True)
        normalized = normalize_result(
            {
                "content": payload.get("contents") or (),
                "structured_content": None,
                "is_error": False,
            },
            runtime.config.limits,
        )
        normalized.update({
            "server_id": server_id,
            "resource_id": runtime.resources[uri].resource_id,
            "uri": uri,
        })
        return normalized

    def read_resource(self, server_id: str, uri: str) -> dict[str, Any]:
        config = self._runtime(server_id).config
        return self._submit(
            self._read_resource(server_id, uri), timeout=config.request_timeout_s + 2
        )

    def close(self) -> None:
        if self._closed:
            return
        for server in self.config.servers:
            runtime = self._runtime(server.server_id)
            if runtime.client is not None:
                try:
                    self.disconnect(server.server_id)
                except McpBackendError:
                    pass
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            raise McpBackendError("MCP event loop did not stop cleanly")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


__all__ = [
    "McpBackendError",
    "McpClientBackend",
    "McpDependencyUnavailable",
    "McpInvocationCancelled",
    "McpInvocationTimeout",
    "McpServerUnavailable",
    "McpToolUnavailable",
]
