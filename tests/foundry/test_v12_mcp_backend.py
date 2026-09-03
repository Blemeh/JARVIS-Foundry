"""V12 bounded MCP backend and real disposable server proof."""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import COMPLETED, FROZEN, effect_state
from jarvis_foundry.fabric import (
    ExecutorContext,
    Readiness,
    TaskIntent,
    execute_action,
    observed_readiness,
)
from jarvis_foundry.fabric.catalog import capability_metadata
from jarvis_foundry.fabric.model import Semantic
from jarvis_foundry.mcp_backend import (
    FabricMcpBackend,
    McpBackendConfig,
    McpConfigurationError,
    McpContractError,
    McpOutputLimits,
    McpServerConfig,
    McpServerUnavailable,
    McpSessionState,
    McpTransport,
    load_mcp_config,
    normalize_json_schema,
    normalize_result,
    stable_tool_identity,
    validate_arguments,
)

SERVER_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "v12_disposable_mcp_server.py"
)


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _config(
    tmp_path: Path,
    *,
    transport: str = "stdio",
    endpoint: str | None = None,
    extra_tool: bool = False,
    limits: Mapping[str, int] | None = None,
) -> McpBackendConfig:
    env = {"JARVIS_MCP_DISPOSABLE_ROOT": str(tmp_path / "disposable")}
    if extra_tool:
        env["JARVIS_MCP_EXTRA_TOOL"] = "1"
    server: dict[str, Any] = {
        "server_id": "v12-test",
        "transport": transport,
        "env": env,
        "request_timeout_s": 4,
        "tools": {
            "echo": {"semantic": "read"},
            "slow_echo": {"semantic": "read"},
            "write_disposable": {"semantic": "write"},
            "slow_write_disposable": {"semantic": "write"},
            "extra_echo": {"semantic": "read"},
        },
    }
    if limits:
        server["limits"] = dict(limits)
    if transport == "stdio":
        server.update(
            {
                "command": sys.executable,
                "args": ["-u", str(SERVER_SCRIPT)],
            }
        )
    else:
        server.pop("env")
        server["endpoint"] = endpoint
    return McpBackendConfig.from_mapping({"servers": [server]})


def _action(backend: FabricMcpBackend, name: str) -> str:
    return next(
        item.action_kind
        for item in backend.client.tools("v12-test")
        if item.tool_name == name
    )


def _intent(identifier: str, action_kind: str) -> TaskIntent:
    return TaskIntent(task_id=identifier, required_actions=(action_kind,))


def _readback_verifier(root: Path):
    resolved = root.resolve()

    def verify(context: Mapping[str, Any]) -> Mapping[str, Any]:
        arguments = context["arguments"]
        target = (resolved / str(arguments["relative_path"])).resolve()
        target.relative_to(resolved)
        actual = target.read_text(encoding="utf-8") if target.is_file() else None
        expected = str(arguments["content"])
        digest = (
            hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else ""
        )
        return {
            "readback_verified": actual == expected,
            "reconciled": True,
            "after_state_ref": f"sha256:{digest}",
            "readback": {"matches": actual == expected, "sha256": digest},
        }

    return verify


def test_config_is_explicit_conservative_and_transport_safe(tmp_path: Path) -> None:
    config = McpServerConfig(
        server_id="bounded",
        transport=McpTransport.STDIO,
        command=sys.executable,
        allowed_tools=("read",),
        disabled_tools=("disabled",),
        tools={"read": {"semantic": "read"}},
    )
    assert config.policy_for("read").semantic is Semantic.READ
    assert config.policy_for("read").enabled is True
    assert config.policy_for("unknown").enabled is False
    assert config.default_semantic is Semantic.EXTERNAL_EFFECT
    with pytest.raises(McpConfigurationError):
        McpServerConfig(
            server_id="remote",
            transport=McpTransport.STREAMABLE_HTTP,
            endpoint="http://example.com/mcp",
        )
    with pytest.raises(McpConfigurationError):
        McpServerConfig(
            server_id="relative",
            transport=McpTransport.STDIO,
            command=sys.executable,
            cwd="relative/path",
        )
    with pytest.raises(McpConfigurationError):
        McpServerConfig(
            server_id="secret",
            transport=McpTransport.STDIO,
            command=sys.executable,
            env={"API_KEY": "do-not-store"},
        )
    with pytest.raises(McpConfigurationError):
        McpServerConfig(
            server_id="url-secret",
            transport=McpTransport.STREAMABLE_HTTP,
            endpoint="https://user:password@example.com/mcp",
        )
    path = tmp_path / "mcp.yaml"
    path.write_text(
        "servers:\n"
        "  - server_id: file-config\n"
        "    transport: stdio\n"
        f"    command: '{sys.executable}'\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    assert load_mcp_config(path).server("file-config").enabled is False


def test_schema_normalization_typed_validation_and_stable_identity() -> None:
    limits = McpOutputLimits(max_schema_depth=4, max_schema_nodes=20)
    schema = normalize_json_schema(
        {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 1}},
            "required": ["count"],
            "description": "DATA_ONLY",
        },
        limits,
    )
    validate_arguments(schema, {"count": 2})
    with pytest.raises(McpContractError):
        validate_arguments(schema, {"count": 0})
    first = stable_tool_identity("server-a", "tool/name")
    assert first == stable_tool_identity("server-a", "tool/name")
    assert first != stable_tool_identity("server-b", "tool/name")
    with pytest.raises(McpContractError):
        normalize_json_schema({"type": "string"}, limits)


def test_output_is_bounded_hashed_and_data_only() -> None:
    result = normalize_result(
        {
            "content": [{"type": "text", "text": "x" * 200}],
            "structuredContent": {"payload": "y" * 200, "api_key": "never-persist"},
            "isError": False,
        },
        McpOutputLimits(
            max_text_chars=20,
            max_structured_bytes=40,
            max_persisted_bytes=4096,
        ),
    )
    assert result["authority"] == "DATA_ONLY"
    assert result["truncated"] is True
    assert result["content"][0]["text"] == "x" * 20
    assert len(result["result_sha256"]) == 64
    assert result["structured_content"]["truncated"] is True
    small = normalize_result(
        {"content": [], "structuredContent": {"api_key": "never-persist"}},
        McpOutputLimits(max_persisted_bytes=4096),
    )
    assert small["structured_content"]["api_key"] == "[REDACTED]"


def test_real_stdio_discovery_projection_read_resource_disconnect_reconnect(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    backend = FabricMcpBackend(database, _config(tmp_path))
    try:
        connected, projected = backend.connect_server("v12-test")
        assert connected.state is McpSessionState.READY
        assert connected.protocol_version
        assert connected.remote_name_data_only == "jarvis-v12-disposable"
        assert len(projected) == 4
        identities = {
            item.tool_name: (item.capability_id, item.action_kind)
            for item in backend.client.tools("v12-test")
        }
        echo = _action(backend, "echo")
        ctx = ExecutorContext(database, tmp_path / "owned", mcp_backend=backend)
        result = execute_action(
            database,
            ctx,
            _intent("v12.read", echo),
            echo,
            {"arguments": {"text": "real", "repeat": 2}},
        )
        assert result.success is True
        assert result.receipt["state"] == "VERIFIED"
        assert result.receipt["observed"]["authority"] == "DATA_ONLY"
        metadata = capability_metadata(database, result.capability_id)
        assert metadata is not None
        assert metadata.provenance["source"] == "mcp.discovery"
        assert metadata.provenance["remote_content_authority"] == "DATA_ONLY"
        resources = backend.client.resources("v12-test")
        assert [item.uri for item in resources] == ["jarvis://v12/status"]
        resource = backend.client.read_resource("v12-test", resources[0].uri)
        assert resource["authority"] == "DATA_ONLY"
        assert resource["result_sha256"]
        disconnected = backend.disconnect_server("v12-test")
        assert disconnected.state is McpSessionState.DISCONNECTED
        assert disconnected.last_error is None
        assert all(
            observed_readiness(database, item.capability_id).state is Readiness.DEGRADED
            for item in projected
        )
        reconnected, projected_again = backend.reconnect_server("v12-test")
        assert reconnected.state is McpSessionState.READY
        assert identities == {
            item.tool_name: (item.capability_id, item.action_kind)
            for item in backend.client.tools("v12-test")
        }
        assert all(
            observed_readiness(database, item.capability_id).state is Readiness.READY
            for item in projected_again
        )
    finally:
        backend.close()


def test_exact_tool_allowlist_projects_disabled_tools_as_degraded(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    base = _config(tmp_path).server("v12-test")
    config = McpBackendConfig((replace(base, allowed_tools=("echo",)),))
    backend = FabricMcpBackend(database, config)
    try:
        _health, projected = backend.connect_server("v12-test")
        assert [item.tool_name for item in backend.client.tools("v12-test")] == ["echo"]
        assert len(backend.client.tools("v12-test", include_disabled=True)) == 4
        states = {
            item.action_kind: observed_readiness(database, item.capability_id).state
            for item in projected
        }
        assert states[_action(backend, "echo")] is Readiness.READY
        assert sum(state is Readiness.DEGRADED for state in states.values()) == 3
    finally:
        backend.close()


def test_invalid_arguments_and_read_timeout_fail_without_effect(tmp_path: Path) -> None:
    database = _database(tmp_path)
    backend = FabricMcpBackend(database, _config(tmp_path))
    try:
        backend.connect_server("v12-test")
        ctx = ExecutorContext(database, tmp_path / "owned", mcp_backend=backend)
        echo = _action(backend, "echo")
        invalid = execute_action(
            database,
            ctx,
            _intent("v12.invalid", echo),
            echo,
            {"arguments": {}},
        )
        assert invalid.claimed.value == "FAILED"
        assert invalid.receipt["error"]["class"] == "McpContractError"
        secret = execute_action(
            database,
            ctx,
            _intent("v12.secret", echo),
            echo,
            {"arguments": {"text": "token=do-not-forward", "repeat": 1}},
        )
        assert secret.claimed.value == "FAILED"
        assert secret.receipt["error"]["class"] == "McpContractError"
        remote_failure = execute_action(
            database,
            ctx,
            _intent("v12.remote-failure", echo),
            echo,
            {"arguments": {"text": "x", "repeat": 99}},
        )
        assert remote_failure.claimed.value == "FAILED"
        assert remote_failure.claimed_effect["error_code"] == "mcp_tool_error"
        assert (
            observed_readiness(database, remote_failure.capability_id).state
            is Readiness.DEGRADED
        )
        slow = _action(backend, "slow_echo")
        timed = execute_action(
            database,
            ctx,
            _intent("v12.timeout", slow),
            slow,
            {"arguments": {"text": "late", "delay_ms": 1000}, "timeout_ms": 50},
        )
        assert timed.claimed.value == "FAILED"
        assert timed.verified.value == "MATCH"
        assert timed.receipt["error"]["class"] == "mcp_timeout"
        assert not any(
            event.event_type == "system.action_started"
            for event in database.events(readonly=True)
            if event.payload.get("kind") == slow
        )
    finally:
        backend.close()


def test_mutation_uses_canonical_effect_readback_and_replay(tmp_path: Path) -> None:
    database = _database(tmp_path)
    backend = FabricMcpBackend(database, _config(tmp_path))
    try:
        backend.connect_server("v12-test")
        root = tmp_path / "disposable"
        ctx = ExecutorContext(
            database,
            tmp_path / "owned",
            mcp_backend=backend,
            mcp_mutation_verifier=_readback_verifier(root),
        )
        action = _action(backend, "write_disposable")
        payload = {
            "arguments": {"relative_path": "effect.txt", "content": "once\n"},
            "target_ref": str(root / "effect.txt"),
            "before_state_ref": "absent",
        }
        first = execute_action(
            database, ctx, _intent("v12.effect", action), action, payload
        )
        replay = execute_action(
            database, ctx, _intent("v12.effect", action), action, payload
        )
        assert first.success is True
        assert first.receipt["state"] == "VERIFIED"
        assert first.claimed_effect["readback_verified"] is True
        assert replay.success is True
        assert replay.claimed_effect["replayed"] is True
        effect_id = first.receipt["effect_id"]
        assert effect_state(database, effect_id)["status"] == COMPLETED
        relevant = [
            event
            for event in database.events(readonly=True)
            if event.payload.get("action_key") == effect_id
        ]
        assert (
            sum(event.event_type == "system.action_started" for event in relevant) == 1
        )
        assert (
            sum(event.event_type == "system.action_completed" for event in relevant)
            == 1
        )
    finally:
        backend.close()


def test_mutation_without_independent_readback_never_verifies(tmp_path: Path) -> None:
    database = _database(tmp_path)
    backend = FabricMcpBackend(database, _config(tmp_path))
    try:
        backend.connect_server("v12-test")
        root = tmp_path / "disposable"
        ctx = ExecutorContext(database, tmp_path / "owned", mcp_backend=backend)
        action = _action(backend, "write_disposable")
        result = execute_action(
            database,
            ctx,
            _intent("v12.unverified", action),
            action,
            {
                "arguments": {"relative_path": "unverified.txt", "content": "data"},
                "target_ref": str(root / "unverified.txt"),
                "before_state_ref": "absent",
            },
        )
        assert result.claimed.value == "COMPLETED"
        assert result.verified.value == "UNKNOWN"
        assert result.success is False
        assert result.receipt["state"] == "UNKNOWN"
    finally:
        backend.close()


def test_stop_cancels_inflight_mutation_and_freezes_effect(tmp_path: Path) -> None:
    database = _database(tmp_path)
    backend = FabricMcpBackend(database, _config(tmp_path))
    try:
        backend.connect_server("v12-test")
        root = tmp_path / "disposable"
        stop_path = tmp_path / "EMERGENCY_STOP"
        target = root / "stopped.txt"
        ctx = ExecutorContext(
            database,
            tmp_path / "owned",
            mcp_backend=backend,
            mcp_mutation_verifier=_readback_verifier(root),
        )
        action = _action(backend, "slow_write_disposable")
        results: list[Any] = []

        def invoke() -> None:
            results.append(
                execute_action(
                    database,
                    ctx,
                    _intent("v12.stop", action),
                    action,
                    {
                        "arguments": {
                            "relative_path": target.name,
                            "content": "must not land",
                            "delay_ms": 1500,
                        },
                        "target_ref": str(target),
                        "before_state_ref": "absent",
                        "stop_path": str(stop_path),
                        "timeout_ms": 4000,
                    },
                )
            )

        thread = threading.Thread(target=invoke, daemon=True)
        thread.start()
        time.sleep(0.2)
        stop_path.write_text("test STOP\n", encoding="utf-8")
        thread.join(timeout=8)
        stop_path.unlink(missing_ok=True)
        assert not thread.is_alive()
        assert len(results) == 1
        result = results[0]
        assert result.claimed.value == "UNKNOWN"
        assert result.success is False
        assert effect_state(database, result.receipt["effect_id"])["status"] == FROZEN
        assert target.exists() is False
    finally:
        backend.close()


def test_tool_list_change_adds_and_degrades_stable_catalog_entry(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    first = FabricMcpBackend(database, _config(tmp_path))
    try:
        first.connect_server("v12-test")
        assert "extra_echo" not in {
            item.tool_name for item in first.client.tools("v12-test")
        }
    finally:
        first.close()
    second = FabricMcpBackend(database, _config(tmp_path, extra_tool=True))
    try:
        second.connect_server("v12-test")
        extra = next(
            item
            for item in second.client.tools("v12-test")
            if item.tool_name == "extra_echo"
        )
        assert (
            observed_readiness(database, extra.capability_id).state is Readiness.READY
        )
        extra_id = extra.capability_id
    finally:
        second.close()
    third = FabricMcpBackend(database, _config(tmp_path))
    try:
        third.connect_server("v12-test")
        assert observed_readiness(database, extra_id).state is Readiness.DEGRADED
    finally:
        third.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_http_server(tmp_path: Path, port: int) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["JARVIS_MCP_DISPOSABLE_ROOT"] = str(tmp_path / "http-disposable")
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(SERVER_SCRIPT),
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return process
        except OSError:
            if process.poll() is not None:
                raise AssertionError("disposable HTTP MCP server exited early")
            time.sleep(0.05)
    process.terminate()
    process.wait(timeout=5)
    raise AssertionError("disposable HTTP MCP server did not become ready")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_real_streamable_http_disappearance_and_reconnect(tmp_path: Path) -> None:
    port = _free_port()
    process = _start_http_server(tmp_path, port)
    backend: FabricMcpBackend | None = None
    try:
        database = _database(tmp_path)
        backend = FabricMcpBackend(
            database,
            _config(
                tmp_path,
                transport="streamable_http",
                endpoint=f"http://127.0.0.1:{port}/mcp",
            ),
        )
        health, projected = backend.connect_server("v12-test")
        assert health.state is McpSessionState.READY
        assert len(projected) == 4
        action = _action(backend, "echo")
        result = execute_action(
            database,
            ExecutorContext(database, tmp_path / "owned", mcp_backend=backend),
            _intent("v12.http", action),
            action,
            {"arguments": {"text": "http", "repeat": 1}},
        )
        assert result.success is True
        assert result.receipt["state"] == "VERIFIED"
        identities = {
            item.tool_name: (item.capability_id, item.action_kind)
            for item in backend.client.tools("v12-test")
        }
        _stop_process(process)
        with pytest.raises(McpServerUnavailable):
            backend.refresh_server("v12-test")
        assert all(
            observed_readiness(database, item.capability_id).state is Readiness.DEGRADED
            for item in projected
        )
        process = _start_http_server(tmp_path, port)
        recovered, recovered_projection = backend.reconnect_server("v12-test")
        assert recovered.state is McpSessionState.READY
        assert recovered.last_error is None
        assert identities == {
            item.tool_name: (item.capability_id, item.action_kind)
            for item in backend.client.tools("v12-test")
        }
        assert all(
            observed_readiness(database, item.capability_id).state is Readiness.READY
            for item in recovered_projection
        )
    finally:
        if backend is not None:
            backend.close()
        _stop_process(process)
