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
from ..control.redaction import contains_secret_like, redact_text
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
    invocation_limit: asyncio.Semaphore | None = None
    session_task: asyncio.Task[None] | None = None
    session_close: asyncio.Event | None = None

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
        self._lifecycle_lock = threading.RLock()
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._sessions = {
            item.server_id: _SessionRuntime(
                config=item,
                state=(
                    McpSessionState.DISCONNECTED
                    if item.enabled
                    else McpSessionState.DISABLED
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
            self._loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
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
            raise McpInvocationTimeout(
                "MCP operation exceeded its local deadline"
            ) from error
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
            return Client(
                str(config.endpoint), read_timeout_seconds=config.request_timeout_s
            ), None
        try:
            import httpx2
        except ImportError as error:  # pragma: no cover - SDK dependency guard
            raise McpDependencyUnavailable(
                "httpx2 is required for authenticated MCP HTTP"
            ) from error
        http_client = httpx2.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=httpx2.Timeout(config.request_timeout_s),
        )
        await http_client.__aenter__()
        transport = streamable_http_client(
            str(config.endpoint), http_client=http_client
        )
        return Client(
            transport, read_timeout_seconds=config.request_timeout_s
        ), http_client

    async def _list_tools(
        self, runtime: _SessionRuntime
    ) -> tuple[dict[str, McpToolDescriptor], tuple[str, ...]]:
        tools: dict[str, McpToolDescriptor] = {}
        errors: list[str] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages = 0
        while True:
            pages += 1
            if pages > runtime.config.limits.max_discovery_pages:
                raise McpContractError("MCP tool discovery exceeded maximum pages")
            response = await runtime.client.list_tools(
                cursor=cursor, cache_mode="reload"
            )
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
                if len(tools) > runtime.config.limits.max_discovered_tools:
                    raise McpContractError(
                        "MCP tool discovery exceeded maximum tool count"
                    )
            cursor = getattr(response, "next_cursor", None)
            if not cursor:
                break
            if cursor in seen_cursors:
                raise McpContractError(
                    "MCP tool discovery repeated a pagination cursor"
                )
            seen_cursors.add(cursor)
        return tools, tuple(errors)

    async def _list_resources(
        self, runtime: _SessionRuntime
    ) -> dict[str, McpResourceDescriptor]:
        capabilities = getattr(runtime.client, "server_capabilities", None)
        if capabilities is None or getattr(capabilities, "resources", None) is None:
            return {}
        resources: dict[str, McpResourceDescriptor] = {}
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages = 0
        while True:
            pages += 1
            if pages > runtime.config.limits.max_discovery_pages:
                raise McpContractError("MCP resource discovery exceeded maximum pages")
            response = await runtime.client.list_resources(
                cursor=cursor, cache_mode="reload"
            )
            for remote in response.resources:
                descriptor = McpResourceDescriptor.from_remote(
                    runtime.config.server_id, remote
                )
                resources[descriptor.uri] = descriptor
                if len(resources) > runtime.config.limits.max_discovered_resources:
                    raise McpContractError(
                        "MCP resource discovery exceeded maximum resource count"
                    )
            cursor = getattr(response, "next_cursor", None)
            if not cursor:
                break
            if cursor in seen_cursors:
                raise McpContractError(
                    "MCP resource discovery repeated a pagination cursor"
                )
            seen_cursors.add(cursor)
        return resources

    async def _session_owner(
        self,
        runtime: _SessionRuntime,
        ready: asyncio.Future[McpServerHealth],
    ) -> None:
        """Own the SDK context for its full lifetime in one asyncio task.

        The official transports use AnyIO cancel scopes, which must be exited
        by the same task that entered them. Public calls can use the live
        client concurrently, but only this owner opens and closes the session.
        """
        client = None
        http_client = None
        failure: BaseException | None = None
        ready_error: BaseException | None = None
        try:
            client, http_client = await self._build_client(runtime)
            await asyncio.wait_for(
                client.__aenter__(), runtime.config.connect_timeout_s
            )
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
                str(getattr(info, "name", ""))[:256] or None
                if info is not None
                else None
            )
            runtime.remote_version = (
                str(getattr(info, "version", ""))[:128] or None
                if info is not None
                else None
            )
            runtime.protocol_version = (
                str(getattr(client, "protocol_version", "")) or None
            )
            runtime.tools = tools
            runtime.resources = resources
            runtime.discovery_errors = errors
            runtime.generation += 1
            runtime.connected_at = isoformat()
            runtime.checked_at = runtime.connected_at
            runtime.state = McpSessionState.READY
            runtime.last_error = "; ".join(errors)[:400] or None
            ready.set_result(runtime.health())
            assert runtime.session_close is not None
            await runtime.session_close.wait()
        except BaseException as error:  # noqa: BLE001 - includes task cancellation
            failure = error
            runtime.state = McpSessionState.DEGRADED
            runtime.checked_at = isoformat()
            runtime.last_error = redact_text(str(error))[:400]
            ready_error = (
                error
                if isinstance(error, McpBackendError)
                else McpServerUnavailable(
                    f"MCP server {runtime.config.server_id!r} failed handshake or discovery: "
                    f"{redact_text(str(error))}"
                )
            )
        finally:
            cleanup_error: BaseException | None = None
            if client is not None:
                try:
                    await asyncio.wait_for(
                        client.__aexit__(
                            type(failure) if failure is not None else None,
                            failure,
                            failure.__traceback__ if failure is not None else None,
                        ),
                        runtime.config.shutdown_timeout_s,
                    )
                except BaseException as error:  # noqa: BLE001 - cleanup boundary
                    cleanup_error = error
            if http_client is not None:
                try:
                    await asyncio.wait_for(
                        http_client.__aexit__(
                            type(failure) if failure is not None else None,
                            failure,
                            failure.__traceback__ if failure is not None else None,
                        ),
                        runtime.config.shutdown_timeout_s,
                    )
                except BaseException as error:  # noqa: BLE001 - cleanup boundary
                    cleanup_error = cleanup_error or error
            runtime.client = None
            runtime.http_client = None
            if cleanup_error is not None:
                runtime.last_error = redact_text(str(cleanup_error))[:400]
                ready_error = ready_error or McpServerUnavailable(
                    f"MCP server {runtime.config.server_id!r} cleanup failed: "
                    f"{redact_text(str(cleanup_error))}"
                )
            if not ready.done():
                ready.set_exception(
                    ready_error
                    or McpServerUnavailable(
                        f"MCP server {runtime.config.server_id!r} closed before readiness"
                    )
                )

    async def _connect(self, server_id: str, reconnect: bool) -> McpServerHealth:
        runtime = self._runtime(server_id)
        if not runtime.config.enabled:
            runtime.state = McpSessionState.DISABLED
            return runtime.health()
        if runtime.state is McpSessionState.READY and runtime.client is not None:
            return runtime.health()
        if runtime.session_task is not None and not runtime.session_task.done():
            raise McpServerUnavailable(
                f"MCP server {server_id!r} already owns an active session; reconnect it"
            )
        runtime.state = (
            McpSessionState.RECONNECTING if reconnect else McpSessionState.CONNECTING
        )
        runtime.last_error = None
        runtime.session_close = asyncio.Event()
        ready: asyncio.Future[McpServerHealth] = (
            asyncio.get_running_loop().create_future()
        )
        runtime.session_task = asyncio.create_task(
            self._session_owner(runtime, ready),
            name=f"mcp-session:{server_id}",
        )
        return await ready

    def connect(self, server_id: str) -> McpServerHealth:
        config = self._runtime(server_id).config
        with self._lifecycle_lock:
            return self._submit(
                self._connect(server_id, False),
                timeout=(config.connect_timeout_s * 3) + 2,
            )

    async def _disconnect(
        self, server_id: str, stopped: bool = False
    ) -> McpServerHealth:
        runtime = self._runtime(server_id)
        for task in tuple(runtime.invocation_tasks.values()):
            task.cancel()
        if runtime.invocation_tasks:
            await asyncio.gather(
                *runtime.invocation_tasks.values(), return_exceptions=True
            )
        runtime.invocation_tasks.clear()
        owner = runtime.session_task
        if runtime.session_close is not None:
            runtime.session_close.set()
        if owner is not None:
            try:
                await asyncio.wait_for(
                    owner,
                    runtime.config.shutdown_timeout_s * 2,
                )
            except BaseException as error:  # noqa: BLE001 - shutdown remains best effort
                runtime.last_error = redact_text(str(error))[:400]
                if not owner.done():
                    owner.cancel()
                    await asyncio.gather(owner, return_exceptions=True)
        runtime.session_task = None
        runtime.session_close = None
        runtime.state = (
            McpSessionState.STOPPED if stopped else McpSessionState.DISCONNECTED
        )
        runtime.checked_at = isoformat()
        return runtime.health()

    def disconnect(self, server_id: str, *, stopped: bool = False) -> McpServerHealth:
        config = self._runtime(server_id).config
        with self._lifecycle_lock:
            return self._submit(
                self._disconnect(server_id, stopped),
                timeout=(config.shutdown_timeout_s * 2) + 2,
            )

    def reconnect(self, server_id: str) -> McpServerHealth:
        config = self._runtime(server_id).config
        with self._lifecycle_lock:
            self.disconnect(server_id)
            return self._submit(
                self._connect(server_id, True),
                timeout=(config.connect_timeout_s * 3) + 2,
            )

    async def _refresh(
        self, server_id: str
    ) -> tuple[McpServerHealth, tuple[McpToolDescriptor, ...]]:
        runtime = self._runtime(server_id)
        if runtime.state is not McpSessionState.READY or runtime.client is None:
            raise McpServerUnavailable(f"MCP server {server_id!r} is not connected")
        try:
            # A cache-bypassing discovery request is the cross-version health
            # probe. MCP 2026 removed ping; SDK v2 transparently falls back to
            # the legacy initialize/session flow for older servers.
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
            runtime.last_error = redact_text(str(error))[:400]
            raise McpServerUnavailable(
                f"MCP server {server_id!r} failed readiness check: {redact_text(str(error))}"
            ) from error

    def refresh(
        self, server_id: str
    ) -> tuple[McpServerHealth, tuple[McpToolDescriptor, ...]]:
        config = self._runtime(server_id).config
        with self._lifecycle_lock:
            return self._submit(
                self._refresh(server_id), timeout=(config.request_timeout_s * 2) + 2
            )

    def tools(
        self, server_id: str, *, include_disabled: bool = False
    ) -> tuple[McpToolDescriptor, ...]:
        runtime = self._runtime(server_id)
        result = tuple(runtime.tools[name] for name in sorted(runtime.tools))
        return (
            result
            if include_disabled
            else tuple(item for item in result if item.enabled)
        )

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
        encoded_arguments = canonical_json(dict(arguments)).encode("utf-8")
        if len(encoded_arguments) > runtime.config.limits.max_argument_bytes:
            raise McpContractError("MCP arguments exceed maximum encoded size")
        if contains_secret_like(encoded_arguments.decode("utf-8")):
            raise McpContractError(
                "secret-like MCP arguments are refused; use credential refs"
            )
        arguments_hash = hashlib.sha256(encoded_arguments).hexdigest()
        started = isoformat()
        if runtime.invocation_limit is None:
            runtime.invocation_limit = asyncio.Semaphore(
                runtime.config.max_concurrent_invocations
            )
        wait_started = asyncio.get_running_loop().time()
        try:
            await asyncio.wait_for(runtime.invocation_limit.acquire(), timeout_s)
        except TimeoutError as error:
            raise McpInvocationTimeout(
                f"MCP tool {descriptor.tool_name!r} timed out waiting for a bounded slot"
            ) from error
        elapsed_wait = asyncio.get_running_loop().time() - wait_started
        remaining_timeout = max(0.001, timeout_s - elapsed_wait)
        call_task = asyncio.create_task(
            runtime.client.call_tool(
                descriptor.tool_name,
                dict(arguments),
                read_timeout_seconds=remaining_timeout,
            ),
            name=f"mcp:{invocation_id}",
        )
        runtime.invocation_tasks[invocation_id] = call_task
        deadline = asyncio.get_running_loop().time() + remaining_timeout
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
        except McpInvocationCancelled:
            raise
        except McpContractError:
            raise
        except Exception as error:
            runtime.state = McpSessionState.DEGRADED
            runtime.checked_at = isoformat()
            runtime.last_error = redact_text(str(error))[:400]
            raise McpServerUnavailable(
                f"MCP server {descriptor.server_id!r} failed during invocation: "
                f"{redact_text(str(error))}"
            ) from error
        finally:
            runtime.invocation_tasks.pop(invocation_id, None)
            runtime.invocation_limit.release()

    def invoke(
        self,
        action_kind: str,
        arguments: Mapping[str, Any],
        *,
        invocation_id: str | None = None,
        timeout_s: float | None = None,
        stop_check: Callable[[], bool] | None = None,
        effect_context: Mapping[str, Any] | None = None,
    ) -> McpInvocationReceipt:
        """Invoke a read-only tool directly.

        Mutating tools are deliberately unavailable on the client surface.
        They may only be entered by :class:`FabricMcpBackend` after it has
        validated the active canonical effect claim against the database.
        ``effect_context`` remains accepted for compatibility but can never
        authorize a mutation here.
        """

        descriptor = self.descriptor_for_action(action_kind)
        if descriptor.semantic.value != "read":
            raise McpContractError(
                "mutating MCP invocation requires FabricMcpBackend and an "
                "active canonical effect journal entry"
            )
        return self._fabric_invoke(
            action_kind,
            arguments,
            invocation_id=invocation_id,
            timeout_s=timeout_s,
            stop_check=stop_check,
            effect_context=effect_context,
        )

    def _fabric_invoke(
        self,
        action_kind: str,
        arguments: Mapping[str, Any],
        *,
        invocation_id: str | None = None,
        timeout_s: float | None = None,
        stop_check: Callable[[], bool] | None = None,
        effect_context: Mapping[str, Any] | None = None,
    ) -> McpInvocationReceipt:
        """Internal dispatch seam; caller must own journal authorization."""

        descriptor = self.descriptor_for_action(action_kind)
        runtime = self._runtime(descriptor.server_id)
        bounded_timeout = float(timeout_s or runtime.config.request_timeout_s)
        if bounded_timeout <= 0 or bounded_timeout > 3600:
            raise McpContractError(
                "MCP invocation timeout must be within (0, 3600] seconds"
            )
        identity = invocation_id or f"mcp-invocation:{uuid.uuid4().hex}"
        if descriptor.semantic.value != "read":
            context = dict(effect_context or {})
            required = {
                "request_id",
                "action_kind",
                "effect_id",
                "idempotency_key",
                "effect_class",
            }
            if (
                not required.issubset(context)
                or context.get("request_id") != identity
                or context.get("action_kind") != action_kind
                or context.get("effect_class") == "READ_ONLY"
            ):
                raise McpContractError(
                    "mutating MCP invocation requires canonical Fabric effect context"
                )
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
            raise McpToolUnavailable(
                "MCP resource must be discovered before it is read"
            )
        try:
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
        except Exception as error:
            runtime.state = McpSessionState.DEGRADED
            runtime.checked_at = isoformat()
            runtime.last_error = redact_text(str(error))[:400]
            raise McpServerUnavailable(
                f"MCP server {server_id!r} failed during resource read: "
                f"{redact_text(str(error))}"
            ) from error
        normalized.update(
            {
                "server_id": server_id,
                "resource_id": runtime.resources[uri].resource_id,
                "uri": uri,
            }
        )
        return normalized

    def read_resource(self, server_id: str, uri: str) -> dict[str, Any]:
        config = self._runtime(server_id).config
        return self._submit(
            self._read_resource(server_id, uri), timeout=config.request_timeout_s + 2
        )

    def close(self) -> None:
        with self._lifecycle_lock:
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
