"""Fabric-level V13 proofs for exact computer operation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from jarvis_foundry.connectors.windows_operator import (
    BackendControl,
    BackendWindow,
    ProcessSnapshot,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import FROZEN, effect_state
from jarvis_foundry.fabric.catalog import get_spec, register_capability
from jarvis_foundry.fabric.computer_operator import (
    COMPUTER_OPERATOR_ACTIONS,
    PERSONAL_OPERATOR_SHARED_CAPABILITY_IDS,
    computer_operator_specs,
    dispatch_computer_operator,
)
from jarvis_foundry.fabric.contracts import system_id_for_action
from jarvis_foundry.fabric.executors import ExecutorContext
from jarvis_foundry.fabric.loop import execute_action, seed_builtins
from jarvis_foundry.fabric.model import (
    ClaimOutcome,
    TaskIntent,
    VerifyVerdict,
)


@dataclass
class _ControlState:
    value: str = "before"


class _FakeConnector:
    def __init__(self) -> None:
        self.focused = True
        self.steal_focus_after_focus = False
        self.record = {
            "process_id": "editor-main",
            "pid": 4242,
            "create_time": 987654321,
            "creation_identity": {
                "create_time": 987654321,
                "exe": r"C:\Apps\Editor.exe",
            },
            "executable": r"C:\Apps\Editor.exe",
            "args": [],
            "operation": "process.launch",
        }

    def get_validated_process_record(self, process_id: str) -> dict[str, Any]:
        if process_id != self.record["process_id"]:
            raise AssertionError(f"unexpected process id {process_id}")
        return dict(self.record)

    def inspect_process(self, process_id: str) -> dict[str, Any]:
        self.get_validated_process_record(process_id)
        return {
            "status": "COMPLETED",
            "result": {
                "process_id": process_id,
                "state": "running",
                "matches": True,
            },
            "readback": {"alive": True},
        }

    def observe_process_window(self, process_id: str) -> dict[str, Any]:
        self.get_validated_process_record(process_id)
        return {
            "status": "COMPLETED",
            "result": {
                "process_id": process_id,
                "window_handle": 100,
                "focused": self.focused,
            },
            "readback": {
                "alive": True,
                "window_handle": 100,
                "visible_window_count": 1,
                "foreground_window": 100 if self.focused else 999,
            },
        }

    def desktop_open(
        self,
        process_id: str,
        _application: str,
        target: str,
        *,
        args: Any,
        cwd: str,
        idempotency_key: str,
        show_window: bool,
    ) -> dict[str, Any]:
        path = Path(target).resolve()
        content = path.read_bytes()
        opened_identity = {
            "ref": path.name,
            "path": str(path),
            "exists": True,
            "kind": "file",
            "size_bytes": len(content),
            "sha256": f"sha256:{hashlib.sha256(content).hexdigest()}",
        }
        self.record.update({
            "process_id": process_id,
            "args": [*list(args), str(path)],
            "cwd": cwd,
            "operation": "desktop.open",
            "opened_identity": opened_identity,
            "show_window": show_window,
        })
        return {
            "status": "COMPLETED",
            "operation": "desktop.open",
            "request_id": idempotency_key,
            "target_identity": dict(self.record),
            "source_identity": opened_identity,
            "result": {
                "process_id": process_id,
                "state": "running",
                "matches": True,
            },
            "readback": {"alive": True},
        }

    def focus_process(self, process_id: str) -> dict[str, Any]:
        self.get_validated_process_record(process_id)
        receipt = {
            "status": "COMPLETED",
            "operation": "desktop.focus",
            "result": {"process_id": process_id, "focused": True},
            "readback": {
                "alive": True,
                "window_handle": 100,
                "foreground_window": 100,
            },
        }
        if self.steal_focus_after_focus:
            self.focused = False
        return receipt


class _FakeBackend:
    def __init__(self) -> None:
        self.process = ProcessSnapshot(
            pid=4242,
            create_time=987654321,
            executable=r"C:\Apps\Editor.exe",
        )
        self.control = _ControlState()
        self.mutations: list[str] = []
        self.type_calls: list[str] = []
        self.fail_after_set = False

    def process_snapshot(self, pid: int) -> ProcessSnapshot | None:
        return self.process if pid == self.process.pid else None

    def list_windows(self, pid: int) -> tuple[BackendWindow, ...]:
        if pid != self.process.pid:
            return ()
        return (
            BackendWindow(
                ref="window",
                hwnd=100,
                title="Editor",
                class_name="EditorWindow",
                automation_id="EditorMain",
                runtime_id=(1, 100),
                enabled=True,
                visible=True,
            ),
        )

    def list_controls(
        self, window: BackendWindow,
    ) -> tuple[BackendControl, ...]:
        if window.hwnd != 100:
            return ()
        return (
            BackendControl(
                ref="document",
                control_type="EditControl",
                name="Document",
                automation_id="DocumentText",
                class_name="Edit",
                runtime_id=(2, 1),
                enabled=True,
                visible=True,
                focused=True,
                value=self.control.value,
                selected=None,
            ),
        )

    def invoke(self, control: BackendControl, *, timeout_s: float) -> None:
        raise AssertionError("click was not expected")

    def set_value(
        self, control: BackendControl, value: str, *, timeout_s: float,
    ) -> None:
        assert control.automation_id == "DocumentText"
        assert timeout_s > 0
        self.mutations.append(value)
        self.control.value = value
        if self.fail_after_set:
            raise RuntimeError("simulated transport loss after effect")

    def type_text(
        self, control: BackendControl, value: str, *, timeout_s: float,
    ) -> None:
        assert timeout_s > 0
        self.type_calls.append(value)
        self.control.value += value

    def select(
        self,
        control: BackendControl,
        value: str | None,
        *,
        timeout_s: float,
    ) -> None:
        raise AssertionError("select was not expected")


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    return database


def _context(
    tmp_path: Path,
    database: FoundryDatabase,
    backend: _FakeBackend,
    connector: _FakeConnector | None = None,
) -> ExecutorContext:
    return ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned",
        operator_connector=connector or _FakeConnector(),
        windows_operator_backend=backend,
    )


def _intent(action_kind: str, suffix: str = "one") -> TaskIntent:
    return TaskIntent(
        task_id=f"task.v13.{suffix}",
        required_actions=(action_kind,),
    )


def _observe_window(
    database: FoundryDatabase,
    context: ExecutorContext,
) -> dict[str, Any]:
    result = execute_action(
        database,
        context,
        _intent("desktop.window.observe", "observe"),
        "desktop.window.observe",
        {
            "process_id": "editor-main",
            "window_selector": {"automation_id": "EditorMain"},
        },
    )
    assert result.success is True
    return dict(result.observed["window"]["identity"])


def _replace_payload(window: dict[str, Any], value: str) -> dict[str, Any]:
    return {
        "process_id": "editor-main",
        "target": "editor-main/DocumentText",
        "window": window,
        "control": {"automation_id": "DocumentText"},
        "value": value,
        "timeout_s": 1,
    }


def test_v13_specs_are_additive_and_use_one_computer_system(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    specs = seed_builtins(database)
    actions = {spec.action_kind for spec in specs}

    assert COMPUTER_OPERATOR_ACTIONS <= actions
    assert (
        system_id_for_action(
            "desktop.control.replace_text",
            "editor-main/DocumentText",
        )
        == "computer"
    )
    assert system_id_for_action("local.process.launch", "editor-main") == "computer"


def test_shared_personal_operator_ids_converge_to_one_computer_authority(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    shared = tuple(
        spec
        for spec in computer_operator_specs()
        if spec.capability_id in PERSONAL_OPERATOR_SHARED_CAPABILITY_IDS
    )
    for spec in shared:
        register_capability(
            database,
            replace(
                spec,
                version="1.0.0",
                executor="personal_operator",
            ),
            note="Personal Operator input seed",
        )

    seed_builtins(database)

    for spec in shared:
        active = get_spec(database, spec.capability_id)
        assert active is not None
        assert active.executor == "computer_operator"
        assert active.action_kind == spec.action_kind

    for spec in shared:
        if get_spec(database, spec.capability_id) is None:
            register_capability(
                database,
                replace(spec, executor="personal_operator"),
                note="late Personal Operator seed",
            )
    assert all(
        get_spec(database, spec.capability_id).executor == "computer_operator"
        for spec in shared
    )


def test_desktop_open_verifies_owned_target_and_live_window(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    backend = _FakeBackend()
    connector = _FakeConnector()
    context = _context(tmp_path, database, backend, connector)
    target = context.owned_root / "report.txt"
    target.write_text("owned report\n", encoding="utf-8")

    result = execute_action(
        database,
        context,
        _intent("desktop.open", "open"),
        "desktop.open",
        {
            "process_id": "editor-main",
            "application": r"C:\Apps\Editor.exe",
            "target": str(target),
            "cwd": ".",
            "show_window": True,
            "timeout_s": 1,
        },
    )

    assert result.success is True
    assert result.verified is VerifyVerdict.MATCH
    assert result.observed["readback_verified"] is True
    assert (
        result.observed["window_observation"]["result"]["window_handle"]
        == 100
    )


def test_desktop_focus_verifier_rejects_focus_stolen_after_dispatch(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    backend = _FakeBackend()
    connector = _FakeConnector()
    connector.steal_focus_after_focus = True
    context = _context(tmp_path, database, backend, connector)

    result = execute_action(
        database,
        context,
        _intent("desktop.focus", "focus-stolen"),
        "desktop.focus",
        {"process_id": "editor-main"},
    )

    assert result.claimed is ClaimOutcome.COMPLETED
    assert result.verified is VerifyVerdict.MISMATCH
    assert result.success is False


def test_fabric_type_requires_and_reconciles_declared_final_value(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    backend = _FakeBackend()
    context = _context(tmp_path, database, backend)
    window = _observe_window(database, context)
    base = {
        "process_id": "editor-main",
        "target": "editor-main/DocumentText",
        "window": window,
        "control": {"automation_id": "DocumentText"},
        "value": " typed",
        "timeout_s": 1,
    }

    refused = execute_action(
        database,
        context,
        _intent("desktop.control.type", "type-refused"),
        "desktop.control.type",
        base,
    )
    completed = execute_action(
        database,
        context,
        _intent("desktop.control.type", "type-complete"),
        "desktop.control.type",
        {
            **base,
            "condition": {"field": "value", "expected": "before typed"},
        },
    )

    assert refused.claimed is ClaimOutcome.FAILED
    assert refused.observed["error_code"] == "operator_contract_error"
    assert completed.success is True
    assert completed.verified is VerifyVerdict.MATCH
    assert backend.type_calls == [" typed"]


def test_exact_ui_mutation_is_journaled_once_and_replayed_without_redispatch(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    backend = _FakeBackend()
    context = _context(tmp_path, database, backend)
    window = _observe_window(database, context)
    payload = _replace_payload(window, "sensitive-document-value")
    intent = _intent("desktop.control.replace_text", "replace")

    first = execute_action(
        database,
        context,
        intent,
        "desktop.control.replace_text",
        payload,
    )
    replay = execute_action(
        database,
        context,
        intent,
        "desktop.control.replace_text",
        payload,
    )

    assert first.success is True
    assert replay.success is True
    assert backend.mutations == ["sensitive-document-value"]
    assert replay.observed["replayed"] is True
    action_key = str(first.observed["action_key"])
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT system_id, status FROM system_actions "
            "WHERE action_key = ?",
            (action_key,),
        ).fetchone()
        event_evidence = "\n".join(
            str(item[0])
            for item in connection.execute(
                "SELECT payload_json FROM events "
                "WHERE json_extract(payload_json, '$.action_key') = ?",
                (action_key,),
            ).fetchall()
        )
    assert tuple(row) == ("computer", "COMPLETED")
    assert "sensitive-document-value" not in event_evidence


def test_action_cannot_override_trusted_operator_policy(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    backend = _FakeBackend()
    context = _context(tmp_path, database, backend)

    result = dispatch_computer_operator(
        context,
        "desktop.window.observe",
        {
            "process_id": "editor-main",
            "window_selector": {"automation_id": "EditorMain"},
            "operator_executable_allowlist": [r"C:\Untrusted.exe"],
        },
    )

    assert result is not None
    assert result["claimed"] == "BLOCKED"
    assert result["error_code"] == "policy_override_refused"


def test_unknown_effect_reconciles_under_stop_without_a_second_mutation(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    backend = _FakeBackend()
    context = _context(tmp_path, database, backend)
    window = _observe_window(database, context)
    payload = _replace_payload(window, "landed-before-timeout")
    intent = _intent("desktop.control.replace_text", "ambiguous")
    backend.fail_after_set = True

    first = execute_action(
        database,
        context,
        intent,
        "desktop.control.replace_text",
        payload,
    )

    assert first.claimed is ClaimOutcome.UNKNOWN
    assert first.verified is VerifyVerdict.UNKNOWN
    assert first.observed["journal_state"] == FROZEN
    assert backend.mutations == ["landed-before-timeout"]
    action_key = str(first.observed["action_key"])

    backend.fail_after_set = False
    stop_path = tmp_path / "EMERGENCY_STOP"
    stop_path.write_text("owner stop\n", encoding="utf-8")
    blocked = execute_action(
        database,
        context,
        _intent("desktop.control.replace_text", "blocked"),
        "desktop.control.replace_text",
        {
            **_replace_payload(window, "must-not-run"),
            "stop_path": str(stop_path),
        },
    )
    assert blocked.claimed is ClaimOutcome.BLOCKED

    reconciled = execute_action(
        database,
        context,
        _intent("desktop.action.reconcile", "reconcile"),
        "desktop.action.reconcile",
        {
            **payload,
            "stop_path": str(stop_path),
            "original_effect_id": action_key,
            "original_action_kind": "desktop.control.replace_text",
            "original_action_id": first.observed["operator_receipt"]["action_id"],
        },
    )

    assert reconciled.success is True
    assert reconciled.observed["accepted"] is True
    assert reconciled.observed["terminal_truth"] is True
    assert backend.mutations == ["landed-before-timeout"]
    state = effect_state(database, action_key)
    assert state["status"] == FROZEN
    assert state["accepted"] is True
    assert state["terminal_outcome"] == "COMPLETED"
