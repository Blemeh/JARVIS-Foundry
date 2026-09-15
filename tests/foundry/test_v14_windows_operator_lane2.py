"""Lane 2 proofs for Windows operator surface extensions."""

from __future__ import annotations

import hashlib
from pathlib import Path

from jarvis_foundry.connectors.windows_operator import (
    BackendControl,
    BackendWindow,
    ControlSelector,
    OperatorAction,
    ProcessIdentity,
    ProcessSnapshot,
    TargetMatchError,
    VisibleWindow,
    WindowsOperatorProvider,
    normalize_document_text,
    resolve_launch_window_owner,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric.computer_operator import (
    COMPUTER_OPERATOR_ACTIONS,
    dispatch_computer_operator,
    open_artifact_and_assert,
)
from jarvis_foundry.fabric.executors import ExecutorContext
from jarvis_foundry.fabric.loop import seed_builtins


class _Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _FakeBackend:
    def __init__(self, process: ProcessSnapshot) -> None:
        self.process = process
        self.windows = [
            BackendWindow(
                ref=100,
                hwnd=100,
                title="hello.txt - Editor",
                class_name="EditorWindow",
                automation_id="EditorMain",
                runtime_id=(1, 100),
                enabled=True,
                visible=True,
                is_dialog=False,
            )
        ]
        self.document_value = "hello"
        self.clipboard = ""
        self.mutations: list[tuple[str, str]] = []

    def process_snapshot(self, pid: int) -> ProcessSnapshot | None:
        return self.process if pid == self.process.pid else None

    def list_windows(self, pid: int) -> tuple[BackendWindow, ...]:
        if pid != self.process.pid:
            return ()
        return tuple(self.windows)

    def list_controls(self, window: BackendWindow) -> tuple[BackendControl, ...]:
        del window
        return (
            BackendControl(
                ref="doc",
                control_type="DocumentControl",
                name="Text editor",
                automation_id="",
                class_name="RichEditD2DPT",
                runtime_id=(2, 1),
                enabled=True,
                visible=True,
                focused=True,
                value=self.document_value,
                selected=None,
            ),
            BackendControl(
                ref="file",
                control_type="MenuItemControl",
                name="File",
                automation_id="File",
                class_name="MenuBarItem",
                runtime_id=(2, 2),
                enabled=True,
                visible=True,
                focused=False,
                value=None,
                selected=None,
            ),
        )

    def invoke(self, control: BackendControl, *, timeout_s: float) -> None:
        del timeout_s
        self.mutations.append(("click", str(control.ref)))

    def set_value(
        self, control: BackendControl, value: str, *, timeout_s: float
    ) -> None:
        del timeout_s
        self.mutations.append(("replace", str(control.ref)))
        self.document_value = value

    def type_text(
        self, control: BackendControl, value: str, *, timeout_s: float
    ) -> None:
        del timeout_s
        self.mutations.append(("type", str(control.ref)))
        self.document_value += value

    def select(
        self,
        control: BackendControl,
        value: str | None,
        *,
        timeout_s: float,
    ) -> None:
        del control, value, timeout_s
        raise AssertionError("select was not expected")

    def set_clipboard_text(self, text: str) -> None:
        self.clipboard = text

    def get_clipboard_text(self) -> str:
        return self.clipboard

    def paste_text(self, control: BackendControl, *, timeout_s: float) -> None:
        del control, timeout_s
        self.mutations.append(("paste", self.clipboard))
        self.document_value = self.clipboard


class _FakeConnector:
    def __init__(self, tmp_path: Path, process: ProcessIdentity) -> None:
        self.root = tmp_path
        self.record = {
            "process_id": process.logical_id,
            "pid": process.pid,
            "creation_identity": {
                "create_time": process.create_time,
                "exe": process.executable,
            },
            "executable": process.executable,
            "args": [],
            "operation": "desktop.open",
        }

    def desktop_open(
        self,
        process_id: str,
        application: str,
        target: str,
        *,
        args: object = (),
        cwd: str = ".",
        idempotency_key: str,
        show_window: bool = True,
    ) -> dict[str, object]:
        del application, args, cwd, show_window
        path = Path(target).resolve()
        content = path.read_bytes()
        self.record.update(
            {
                "process_id": process_id,
                "opened_identity": {
                    "path": str(path),
                    "kind": "file",
                    "size_bytes": len(content),
                    "sha256": f"sha256:{hashlib.sha256(content).hexdigest()}",
                },
                "idempotency_key": idempotency_key,
            }
        )
        return {
            "status": "COMPLETED",
            "operation": "desktop.open",
            "request_id": idempotency_key,
            "target_identity": dict(self.record),
            "result": {
                "process_id": process_id,
                "pid": self.record["pid"],
                "state": "running",
            },
            "readback": {"alive": True},
        }

    def get_validated_process_record(self, process_id: str) -> dict[str, object]:
        if process_id != self.record["process_id"]:
            raise AssertionError(process_id)
        return dict(self.record)


def _process() -> ProcessIdentity:
    return ProcessIdentity(
        logical_id="lane2-editor",
        pid=4242,
        create_time=987654321,
        executable=r"C:\Apps\Editor.exe",
    )


def test_resolve_launch_window_owner_binds_fresh_hwnd() -> None:
    before = (
        VisibleWindow(hwnd=1, pid=10, title="other.txt - Notepad", class_name="Notepad"),
    )
    after = (
        *before,
        VisibleWindow(hwnd=2, pid=11, title="hello.txt - Notepad", class_name="Notepad"),
    )
    status, window = resolve_launch_window_owner(
        before, after, title_contains="hello.txt"
    )
    assert status == "bound"
    assert window is not None
    assert window.pid == 11


def test_resolve_launch_window_owner_refuses_title_reuse() -> None:
    before = (
        VisibleWindow(hwnd=1, pid=10, title="other.txt - Notepad", class_name="Notepad"),
    )
    after = (
        VisibleWindow(hwnd=1, pid=10, title="hello.txt - Notepad", class_name="Notepad"),
    )
    status, window = resolve_launch_window_owner(
        before, after, title_contains="hello.txt"
    )
    assert status == "reused"
    assert window is not None
    assert window.pid == 10


def test_helper_windows_do_not_make_main_window_ambiguous() -> None:
    process = _process()
    backend = _FakeBackend(
        ProcessSnapshot(
            pid=process.pid,
            create_time=process.create_time,
            executable=process.executable,
        )
    )
    backend.windows.append(
        BackendWindow(
            ref=101,
            hwnd=101,
            title="GDI+ Window (Notepad.exe)",
            class_name="GDI+ Hook Window Class",
            automation_id="",
            runtime_id=(1, 101),
            enabled=True,
            visible=True,
            is_dialog=False,
        )
    )
    provider = WindowsOperatorProvider(process, backend=backend)
    observed = provider.observe_main_window(title_contains="hello.txt")
    assert observed.identity.hwnd == 100
    assert observed.identity.class_name == "EditorWindow"


def test_find_document_control_and_normalized_replace() -> None:
    process = _process()
    backend = _FakeBackend(
        ProcessSnapshot(
            pid=process.pid,
            create_time=process.create_time,
            executable=process.executable,
        )
    )
    backend.document_value = "hello\r"
    provider = WindowsOperatorProvider(
        process, backend=backend, clock=_Clock(), sleep=_Clock().sleep
    )
    window = provider.observe_main_window().identity
    document = provider.find_document_control(window)
    assert document.selector.name == "Text editor"
    receipt = provider.perform(
        OperatorAction(
            action_id="replace",
            kind="replace_text",
            window=window,
            control=document.selector,
            value="hello\n",
            timeout_s=1,
        )
    )
    assert receipt.outcome == "COMPLETED"
    assert normalize_document_text("hello\r") == "hello\n"


def test_paste_sets_clipboard_only_on_bound_control() -> None:
    process = _process()
    backend = _FakeBackend(
        ProcessSnapshot(
            pid=process.pid,
            create_time=process.create_time,
            executable=process.executable,
        )
    )
    provider = WindowsOperatorProvider(
        process, backend=backend, clock=_Clock(), sleep=_Clock().sleep
    )
    window = provider.observe_main_window().identity
    document = provider.find_document_control(window)
    receipt = provider.perform(
        OperatorAction(
            action_id="paste",
            kind="paste",
            window=window,
            control=document.selector,
            value="pasted",
            timeout_s=1,
        )
    )
    assert receipt.outcome == "COMPLETED"
    assert backend.clipboard == "pasted"
    assert backend.document_value == "pasted"
    assert ("paste", "pasted") in backend.mutations


def test_process_identity_mismatch_still_blocks_mutation() -> None:
    process = _process()
    backend = _FakeBackend(
        ProcessSnapshot(
            pid=process.pid,
            create_time=process.create_time,
            executable=process.executable,
        )
    )
    backend.process = ProcessSnapshot(
        pid=process.pid,
        create_time=process.create_time + 1,
        executable=process.executable,
    )
    provider = WindowsOperatorProvider(process, backend=backend)
    try:
        provider.observe_main_window()
    except Exception as error:
        assert error.__class__.__name__ == "ProcessIdentityMismatch"
    else:
        raise AssertionError("expected ProcessIdentityMismatch")


def test_open_artifact_and_assert_helper_and_fabric_dispatch(
    tmp_path: Path,
) -> None:
    process = _process()
    owned = tmp_path / "owned"
    owned.mkdir()
    artifact = owned / "hello.txt"
    artifact.write_text("hello", encoding="utf-8")
    backend = _FakeBackend(
        ProcessSnapshot(
            pid=process.pid,
            create_time=process.create_time,
            executable=process.executable,
        )
    )
    connector = _FakeConnector(owned, process)
    result = open_artifact_and_assert(
        connector,  # type: ignore[arg-type]
        process_id=process.logical_id,
        executable=process.executable,
        path=str(artifact),
        expected="hello",
        idempotency_key="lane2-open-1",
        backend=backend,
    )
    assert result["claimed"] == "COMPLETED"
    assert result["readback_verified"] is True
    assert result["process"]["pid"] == process.pid
    assert result["window"]["identity"]["hwnd"] == 100
    assert result["control"]["name"] == "Text editor"

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    ctx = ExecutorContext(
        database=database,
        owned_root=owned,
        operator_connector=connector,
        windows_operator_backend=backend,
    )
    dispatched = dispatch_computer_operator(
        ctx,
        "desktop.open_artifact_and_assert",
        {
            "process_id": process.logical_id,
            "executable": process.executable,
            "path": str(artifact),
            "expected": "hello",
            "idempotency_key": "lane2-open-2",
        },
    )
    assert dispatched is not None
    assert dispatched["claimed"] == "COMPLETED"
    assert "desktop.open_artifact_and_assert" in COMPUTER_OPERATOR_ACTIONS
    assert "desktop.control.paste" in COMPUTER_OPERATOR_ACTIONS


def test_named_alias_resolution() -> None:
    process = _process()
    backend = _FakeBackend(
        ProcessSnapshot(
            pid=process.pid,
            create_time=process.create_time,
            executable=process.executable,
        )
    )
    provider = WindowsOperatorProvider(process, backend=backend)
    window = provider.observe_main_window().identity
    file_menu = provider.resolve_named_control(
        window,
        names=("Fichier", "File"),
        control_type="MenuItemControl",
        automation_ids=("File",),
    )
    assert file_menu.selector.name == "File"
    try:
        provider.resolve_named_control(
            window,
            names=("Nope",),
            control_type="MenuItemControl",
        )
    except TargetMatchError as error:
        assert error.code == "control_not_found"
    else:
        raise AssertionError("expected alias miss")
