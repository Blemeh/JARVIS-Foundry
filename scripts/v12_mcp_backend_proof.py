"""Real disposable MCP -> Fabric -> effect/receipt/reconnect proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import COMPLETED, effect_state
from jarvis_foundry.fabric import (
    ExecutorContext,
    Readiness,
    TaskIntent,
    execute_action,
    observed_readiness,
)
from jarvis_foundry.mcp_backend import FabricMcpBackend, McpBackendConfig


def _verifier(disposable_root: Path):
    root = disposable_root.resolve()

    def verify(context: Mapping[str, Any]) -> Mapping[str, Any]:
        arguments = context["arguments"]
        target = (root / str(arguments["relative_path"])).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return {
                "readback_verified": False,
                "reconciled": False,
                "readback": {"status": "MISMATCH", "matches": False},
            }
        expected = str(arguments["content"])
        if not target.is_file():
            return {
                "readback_verified": False,
                "reconciled": True,
                "readback": {"status": "NOT_OBSERVED", "matches": False},
            }
        actual = target.read_text(encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return {
            "readback_verified": actual == expected,
            "reconciled": True,
            "after_state_ref": f"sha256:{digest}",
            "readback": {
                "status": "MATCH" if actual == expected else "MISMATCH",
                "path": str(target),
                "sha256": digest,
                "matches": actual == expected,
            },
            "evidence_refs": [f"file:sha256:{digest}"],
            "acceptance_evidence": {
                "authority": "disposable-filesystem-readback",
                "sha256": digest,
            },
            "provider_receipt": {
                "provider_id": "mcp:disposable-filesystem",
                "provider_ref": f"file:sha256:{digest}",
            },
        }

    return verify


def _intent(task_id: str, action_kind: str) -> TaskIntent:
    return TaskIntent(task_id=task_id, required_actions=(action_kind,))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    output_root = Path(args.output_root).resolve()
    if output_root.exists():
        raise SystemExit("output root must not already exist")
    output_root.mkdir(parents=True)
    disposable_root = output_root / "disposable"
    owned_root = output_root / "owned"
    disposable_root.mkdir()
    owned_root.mkdir()
    database = FoundryDatabase(output_root / "foundry.db")
    database.initialize()
    server_script = Path(__file__).with_name("v12_disposable_mcp_server.py").resolve()
    config = McpBackendConfig.from_mapping(
        {
            "servers": [
                {
                    "server_id": "v12-disposable",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["-u", str(server_script)],
                    "env": {"JARVIS_MCP_DISPOSABLE_ROOT": str(disposable_root)},
                    "request_timeout_s": 5,
                    "default_unattended": "ALLOWED",
                    "tools": {
                        "echo": {"semantic": "read"},
                        "slow_echo": {"semantic": "read"},
                        "write_disposable": {
                            "semantic": "write",
                            "limitations": ["disposable proof root only"],
                        },
                        "slow_write_disposable": {
                            "semantic": "write",
                            "limitations": ["disposable proof root only"],
                        },
                        "append_then_delay_disposable": {
                            "semantic": "write",
                            "limitations": ["disposable proof root only"],
                        },
                        "error_after_write_disposable": {
                            "semantic": "write",
                            "limitations": ["disposable proof root only"],
                        },
                    },
                }
            ],
        }
    )
    report: dict[str, Any] = {
        "proof": "JARVIS_V12_MCP_BACKEND_REAL_SERVER",
        "passed": False,
        "server_script": str(server_script),
    }
    backend = FabricMcpBackend(database, config)
    try:
        connected, projected = backend.connect_server("v12-disposable")
        descriptors = backend.client.tools("v12-disposable")
        by_name = {item.tool_name: item for item in descriptors}
        stable_before = {
            name: (item.capability_id, item.action_kind)
            for name, item in by_name.items()
        }
        context = ExecutorContext(
            database=database,
            owned_root=owned_root,
            mcp_backend=backend,
            mcp_mutation_verifier=_verifier(disposable_root),
        )
        echo = by_name["echo"]
        read_result = execute_action(
            database,
            context,
            _intent("v12.mcp.read", echo.action_kind),
            echo.action_kind,
            {"arguments": {"text": "jarvis", "repeat": 2}},
        )
        resources = [
            item.as_dict() for item in backend.client.resources("v12-disposable")
        ]
        resource_result = backend.read_resource(
            "v12-disposable", "jarvis://v12/status"
        )
        slow = by_name["slow_echo"]
        timeout_result = execute_action(
            database,
            context,
            _intent("v12.mcp.timeout", slow.action_kind),
            slow.action_kind,
            {"arguments": {"text": "late", "delay_ms": 1000}, "timeout_ms": 100},
        )
        write = by_name["write_disposable"]
        write_payload = {
            "arguments": {
                "relative_path": "proof.txt",
                "content": "canonical effect\n",
            },
            "target_ref": str(disposable_root / "proof.txt"),
            "before_state_ref": "disposable:absent",
        }
        write_result = execute_action(
            database,
            context,
            _intent("v12.mcp.write", write.action_kind),
            write.action_kind,
            write_payload,
        )
        replay_result = execute_action(
            database,
            context,
            _intent("v12.mcp.write", write.action_kind),
            write.action_kind,
            write_payload,
        )
        effect_id = str(write_result.receipt["effect_id"])
        write_state = effect_state(database, effect_id)
        mutation_resource_readback = backend.read_resource(
            "v12-disposable", "jarvis://v12/status"
        )
        landed = by_name["append_then_delay_disposable"]
        landed_target = disposable_root / "landed-once.txt"
        landed_payload = {
            "arguments": {
                "relative_path": landed_target.name,
                "content": "landed exactly once\n",
                "delay_ms": 1000,
            },
            "target_ref": str(landed_target),
            "before_state_ref": "disposable:absent",
            "timeout_ms": 100,
        }
        landed_result = execute_action(
            database,
            context,
            _intent("v12.mcp.timeout-after-effect", landed.action_kind),
            landed.action_kind,
            landed_payload,
        )
        landed_effect_id = str(landed_result.receipt["effect_id"])
        landed_state = effect_state(database, landed_effect_id)
        reconcile_stop = output_root / "RECONCILE_STOP"
        reconcile_stop.write_text("reconcile only under STOP\n", encoding="utf-8")
        landed_replay = execute_action(
            database,
            context,
            _intent("v12.mcp.timeout-after-effect", landed.action_kind),
            landed.action_kind,
            {**landed_payload, "stop_path": str(reconcile_stop)},
        )
        reconcile_stop.unlink(missing_ok=True)
        stop_path = output_root / "EMERGENCY_STOP"
        stopped_target = disposable_root / "must-not-exist.txt"
        stop_results: list[Any] = []
        stop_errors: list[str] = []

        def invoke_until_stop() -> None:
            try:
                slow_write = by_name["slow_write_disposable"]
                stop_results.append(
                    execute_action(
                        database,
                        context,
                        _intent("v12.mcp.stop", slow_write.action_kind),
                        slow_write.action_kind,
                        {
                            "arguments": {
                                "relative_path": stopped_target.name,
                                "content": "must not land",
                                "delay_ms": 2000,
                            },
                            "target_ref": str(stopped_target),
                            "before_state_ref": "disposable:absent",
                            "stop_path": str(stop_path),
                            "timeout_ms": 5000,
                        },
                    )
                )
            except BaseException as error:  # noqa: BLE001 - proof captures exact failure
                stop_errors.append(repr(error))

        stop_thread = threading.Thread(target=invoke_until_stop, daemon=True)
        stop_thread.start()
        entered = disposable_root / ".slow-write-entered"
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and not entered.is_file():
            time.sleep(0.01)
        if not entered.is_file():
            stop_errors.append("disposable MCP mutation never entered provider")
        stop_path.write_text("V12 disposable STOP proof\n", encoding="utf-8")
        stop_thread.join(timeout=10)
        stop_path.unlink(missing_ok=True)
        stop_result = stop_results[0] if stop_results else None
        disconnected = backend.disconnect_server("v12-disposable")
        readiness_disconnected = {
            item.capability_id: observed_readiness(
                database, item.capability_id
            ).state.value
            for item in projected
        }
        reconnected, projected_after = backend.reconnect_server("v12-disposable")
        stable_after = {
            item.tool_name: (item.capability_id, item.action_kind)
            for item in backend.client.tools("v12-disposable")
        }
        readiness_reconnected = {
            item.capability_id: observed_readiness(
                database, item.capability_id
            ).state.value
            for item in projected_after
        }
        events = list(database.events(readonly=True))
        effect_events = [
            event for event in events
            if str(event.payload.get("action_key") or "") == effect_id
        ]
        effect_event_types = [event.event_type for event in effect_events]
        effect_event_counts = {
            event_type: effect_event_types.count(event_type)
            for event_type in sorted(set(effect_event_types))
        }
        expected_terminal_sequence = [
            "system.action_started",
            "system.action_completed",
            "effect.provider_receipt",
            "effect.readback_observed",
            "effect.reconciled",
            "effect.acceptance_recorded",
            "effect.terminal_truth",
        ]
        report.update(
            {
                "transport": "stdio",
                "connected": connected.as_dict(),
                "projected_capabilities": [item.as_dict() for item in projected],
                "real_tools": sorted(by_name),
                "read_invocation": read_result.as_dict(),
                "resources": resources,
                "resource_read": resource_result,
                "timeout": timeout_result.as_dict(),
                "mutating_invocation": write_result.as_dict(),
                "mutating_replay": replay_result.as_dict(),
                "effect_state": write_state,
                "mutation_resource_readback": mutation_resource_readback,
                "timeout_after_effect": landed_result.as_dict(),
                "timeout_after_effect_state": landed_state,
                "timeout_after_effect_replay_under_stop": landed_replay.as_dict(),
                "timeout_after_effect_target": landed_target.read_text(
                    encoding="utf-8"
                ),
                "stop_result": stop_result.as_dict()
                if stop_result is not None
                else None,
                "stop_errors": stop_errors,
                "stop_target_exists": stopped_target.exists(),
                "disconnect": disconnected.as_dict(),
                "readiness_after_disconnect": readiness_disconnected,
                "reconnect": reconnected.as_dict(),
                "readiness_after_reconnect": readiness_reconnected,
                "stable_identities_before": stable_before,
                "stable_identities_after": stable_after,
                "event_count": len(events),
                "effect_event_types": effect_event_types,
                "effect_event_counts": effect_event_counts,
                "expected_terminal_sequence": expected_terminal_sequence,
                "duplicate_effects": max(
                    0, effect_event_counts.get("system.action_started", 0) - 1
                ),
                "accepted_effect_unverified": not (
                    write_state.get("accepted") is True
                    and write_state.get("terminal_outcome") == COMPLETED
                ),
            }
        )
        report["passed"] = all(
            (
                connected.state.value == "READY",
                len(projected) == 6,
                read_result.success,
                read_result.receipt.get("state") == "VERIFIED",
                resource_result.get("authority") == "DATA_ONLY",
                timeout_result.claimed.value == "FAILED",
                write_result.success,
                write_result.receipt.get("state") == "VERIFIED",
                replay_result.success,
                replay_result.claimed_effect.get("performed") is False,
                replay_result.claimed_effect.get("terminal_truth") is True,
                write_state.get("status") == COMPLETED,
                write_state.get("accepted") is True,
                write_state.get("terminal_outcome") == COMPLETED,
                effect_event_types == expected_terminal_sequence,
                effect_event_counts.get("system.action_started") == 1,
                effect_event_counts.get("system.action_completed") == 1,
                effect_event_counts.get("effect.terminal_truth") == 1,
                landed_result.success,
                landed_result.claimed_effect.get("journal_state") == "FROZEN",
                landed_state.get("accepted") is True,
                landed_state.get("terminal_outcome") == COMPLETED,
                landed_replay.success,
                landed_replay.claimed_effect.get("performed") is False,
                landed_target.read_text(encoding="utf-8")
                == "landed exactly once\n",
                not stop_errors,
                stop_result is not None and stop_result.claimed.value == "UNKNOWN",
                stop_result is not None
                and stop_result.receipt.get("state") == "UNKNOWN",
                not stopped_target.exists(),
                disconnected.state.value == "DISCONNECTED",
                all(
                    value == Readiness.DEGRADED.value
                    for value in readiness_disconnected.values()
                ),
                reconnected.state.value == "READY",
                all(
                    value == Readiness.READY.value
                    for value in readiness_reconnected.values()
                ),
                stable_before == stable_after,
                (disposable_root / "proof.txt").read_text(encoding="utf-8")
                == "canonical effect\n",
            )
        )
    finally:
        backend.close()
    destination = output_root / "V12_MCP_BACKEND_REAL_PROOF.json"
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "evidence": str(destination),
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
