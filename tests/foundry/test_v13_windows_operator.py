"""Focused V13 proofs for the accessibility-first Windows provider."""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass
from typing import Callable

import pytest

from jarvis_foundry.connectors.windows_operator import (
    BackendControl,
    BackendWindow,
    ControlCondition,
    ControlSelector,
    ExpectedDialog,
    OperatorAction,
    ProcessIdentity,
    ProcessSnapshot,
    TargetMatchError,
    WindowSelector,
    WindowsOperatorProvider,
    _UIAutomationBackend,
)


@dataclass
class _Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class _WindowState:
    hwnd: int
    title: str
    class_name: str
    automation_id: str
    runtime_id: tuple[int, ...]
    enabled: bool = True
    visible: bool = True
    is_dialog: bool = False


@dataclass
class _ControlState:
    ref: str
    control_type: str
    name: str
    automation_id: str
    class_name: str
    runtime_id: tuple[int, ...]
    enabled: bool = True
    visible: bool = True
    focused: bool = False
    value: str | None = None
    selected: bool | None = None


class _FakeBackend:
    def __init__(
        self,
        process: ProcessSnapshot,
        clock: _Clock | None = None,
    ) -> None:
        self.process = process
        self.clock = clock or _Clock()
        self.process_reads = 0
        self.process_sequence: list[ProcessSnapshot | None] = []
        self.windows: dict[int, _WindowState] = {}
        self.controls: dict[int, list[_ControlState]] = {}
        self.mutations: list[tuple[str, str, str | None]] = []
        self.on_invoke: Callable[[], None] | None = None
        self.on_set_value: Callable[[], None] | None = None

    def process_snapshot(self, pid: int) -> ProcessSnapshot | None:
        self.process_reads += 1
        if self.process_sequence:
            return self.process_sequence.pop(0)
        return self.process if pid == self.process.pid else None

    def list_windows(self, pid: int) -> tuple[BackendWindow, ...]:
        if pid != self.process.pid:
            return ()
        return tuple(
            BackendWindow(
                ref=state.hwnd,
                hwnd=state.hwnd,
                title=state.title,
                class_name=state.class_name,
                automation_id=state.automation_id,
                runtime_id=state.runtime_id,
                enabled=state.enabled,
                visible=state.visible,
                is_dialog=state.is_dialog,
            )
            for state in self.windows.values()
        )

    def list_controls(
        self, window: BackendWindow
    ) -> tuple[BackendControl, ...]:
        return tuple(
            BackendControl(
                ref=state.ref,
                control_type=state.control_type,
                name=state.name,
                automation_id=state.automation_id,
                class_name=state.class_name,
                runtime_id=state.runtime_id,
                enabled=state.enabled,
                visible=state.visible,
                focused=state.focused,
                value=state.value,
                selected=state.selected,
            )
            for state in self.controls.get(int(window.ref), ())
        )

    def _state(self, control: BackendControl) -> _ControlState:
        for controls in self.controls.values():
            for state in controls:
                if state.ref == control.ref:
                    return state
        raise AssertionError(f"missing fake control {control.ref}")

    def invoke(self, control: BackendControl, *, timeout_s: float) -> None:
        assert timeout_s > 0
        self.mutations.append(("click", str(control.ref), None))
        if self.on_invoke is not None:
            self.on_invoke()

    def set_value(
        self, control: BackendControl, value: str, *, timeout_s: float
    ) -> None:
        assert timeout_s > 0
        self.mutations.append(("replace_text", str(control.ref), value))
        self._state(control).value = value
        if self.on_set_value is not None:
            self.on_set_value()

    def type_text(
        self, control: BackendControl, value: str, *, timeout_s: float
    ) -> None:
        assert timeout_s > 0
        self.mutations.append(("type", str(control.ref), value))
        state = self._state(control)
        state.value = (state.value or "") + value

    def select(
        self,
        control: BackendControl,
        value: str | None,
        *,
        timeout_s: float,
    ) -> None:
        assert timeout_s > 0
        self.mutations.append(("select", str(control.ref), value))
        self._state(control).selected = True


@pytest.fixture()
def process() -> ProcessIdentity:
    return ProcessIdentity(
        logical_id="editor-main",
        pid=4242,
        create_time=987654321,
        executable=r"C:\Apps\Editor.exe",
    )


@pytest.fixture()
def backend(process: ProcessIdentity) -> _FakeBackend:
    fake = _FakeBackend(
        ProcessSnapshot(
            pid=process.pid,
            create_time=process.create_time,
            executable=process.executable,
        )
    )
    fake.windows[100] = _WindowState(
        hwnd=100,
        title="Editor",
        class_name="EditorWindow",
        automation_id="EditorMain",
        runtime_id=(1, 100),
    )
    fake.controls[100] = [
        _ControlState(
            ref="text",
            control_type="EditControl",
            name="Document",
            automation_id="DocumentText",
            class_name="Edit",
            runtime_id=(2, 1),
            focused=True,
            value="hello",
        ),
        _ControlState(
            ref="save",
            control_type="ButtonControl",
            name="Save",
            automation_id="SaveButton",
            class_name="Button",
            runtime_id=(2, 2),
        ),
        _ControlState(
            ref="choice",
            control_type="ListItemControl",
            name="Beta",
            automation_id="ChoiceBeta",
            class_name="ListItem",
            runtime_id=(2, 3),
            selected=False,
        ),
    ]
    return fake


def _provider(
    process: ProcessIdentity,
    backend: _FakeBackend,
    *,
    stop_check: Callable[[], bool] | None = None,
) -> WindowsOperatorProvider:
    return WindowsOperatorProvider(
        process,
        backend=backend,
        stop_check=stop_check,
        clock=backend.clock,
        sleep=backend.clock.sleep,
        poll_interval_s=0.1,
    )


def _window(
    provider: WindowsOperatorProvider,
) -> object:
    return provider.observe(
        WindowSelector(
            title="Editor",
            class_name="EditorWindow",
            automation_id="EditorMain",
        )
    ).identity


def test_live_backend_discovers_owned_dialogs_from_native_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PatternIds:
        WindowPattern = "window"

    class _WindowPattern:
        def __init__(self, modal: bool) -> None:
            self.IsModal = modal

    class _RawControl:
        def __init__(
            self,
            hwnd: int,
            title: str,
            class_name: str,
            automation_id: str,
            *,
            modal: bool,
        ) -> None:
            self.ProcessId = 4242
            self.NativeWindowHandle = hwnd
            self.Name = title
            self.ClassName = class_name
            self.AutomationId = automation_id
            self.IsEnabled = True
            self.IsOffscreen = False
            self._modal = modal

        def GetRuntimeId(self) -> tuple[int, int]:
            return (42, self.NativeWindowHandle)

        def GetPattern(self, pattern_id: str) -> _WindowPattern | None:
            if pattern_id == "window":
                return _WindowPattern(self._modal)
            return None

    controls = {
        100: _RawControl(
            100,
            "Editor",
            "EditorWindow",
            "EditorMain",
            modal=False,
        ),
        200: _RawControl(
            200,
            "Unexpected",
            "#32770",
            "",
            modal=True,
        ),
    }

    class _Automation:
        PatternId = _PatternIds()

        @staticmethod
        def ControlFromHandle(hwnd: int) -> _RawControl:
            return controls[hwnd]

    monkeypatch.setattr(
        _UIAutomationBackend,
        "_top_level_window_handles",
        staticmethod(lambda pid: (100, 200) if pid == 4242 else ()),
    )
    windows = _UIAutomationBackend(_Automation()).list_windows(4242)

    assert [window.hwnd for window in windows] == [100, 200]
    assert windows[0].is_dialog is False
    assert windows[1].is_dialog is True


def test_fake_backend_keeps_uiautomation_lazy_and_evidence_json_safe(
    monkeypatch: pytest.MonkeyPatch,
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "uiautomation":
            raise AssertionError("injected backends must not import uiautomation")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    provider = _provider(process, backend)
    observed = provider.observe(WindowSelector(hwnd=100))
    controls = provider.inspect(
        observed.identity,
        ControlSelector(automation_id="DocumentText"),
    )

    assert observed.identity.process == process
    assert observed.identity.runtime_id == (1, 100)
    assert observed.identity.observation_generation > 0
    assert len(controls) == 1
    assert controls[0].selector.runtime_id == (2, 1)
    evidence = json.loads(json.dumps(controls[0].to_dict()))
    assert evidence["value"] is None
    assert evidence["value_length"] == 5
    assert len(evidence["value_sha256"]) == 64
    assert set(ControlSelector.__dataclass_fields__) == {
        "control_type",
        "name",
        "automation_id",
        "class_name",
        "runtime_id",
    }


def test_zero_or_multiple_window_and_control_matches_are_rejected(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    provider = _provider(process, backend)
    with pytest.raises(TargetMatchError) as missing:
        provider.observe(WindowSelector(title="Missing"))
    assert missing.value.code == "window_not_found"

    backend.windows[101] = _WindowState(
        hwnd=101,
        title="Editor",
        class_name="EditorWindow",
        automation_id="EditorSecondary",
        runtime_id=(1, 101),
    )
    with pytest.raises(TargetMatchError) as windows:
        provider.observe(WindowSelector(title="Editor"))
    assert windows.value.code == "window_ambiguous"

    del backend.windows[101]
    window = _window(provider)
    backend.controls[100].append(
        _ControlState(
            ref="save-copy",
            control_type="ButtonControl",
            name="Save",
            automation_id="SaveButtonCopy",
            class_name="Button",
            runtime_id=(2, 4),
        )
    )
    with pytest.raises(TargetMatchError) as controls:
        provider.inspect(window, ControlSelector(name="Save"))
    assert controls.value.code == "control_ambiguous"


def test_process_identity_is_reverified_immediately_before_mutation(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    provider = _provider(process, backend)
    window = _window(provider)
    valid = backend.process
    backend.process_sequence = [
        valid,
        ProcessSnapshot(
            pid=process.pid,
            create_time=process.create_time + 1,
            executable=process.executable,
        ),
    ]
    receipt = provider.perform(
        OperatorAction(
            action_id="click-save",
            kind="click",
            window=window,
            control=ControlSelector(automation_id="SaveButton"),
            condition=ControlCondition("enabled", True),
        )
    )

    assert receipt.outcome == "FAILED"
    assert receipt.error_code == "process_identity_mismatch"
    assert backend.mutations == []
    assert backend.process_reads >= 3


@pytest.mark.parametrize(
    ("kind", "control", "value", "expected_value", "expected_selected"),
    [
        (
            "replace_text",
            ControlSelector(automation_id="DocumentText"),
            "replaced",
            "replaced",
            None,
        ),
        (
            "type",
            ControlSelector(automation_id="DocumentText"),
            " world",
            "hello world",
            None,
        ),
        (
            "select",
            ControlSelector(automation_id="ChoiceBeta"),
            "Beta",
            None,
            True,
        ),
    ],
)
def test_text_and_selection_actions_are_read_back(
    process: ProcessIdentity,
    backend: _FakeBackend,
    kind: str,
    control: ControlSelector,
    value: str,
    expected_value: str | None,
    expected_selected: bool | None,
) -> None:
    provider = _provider(process, backend)
    receipt = provider.perform(
        OperatorAction(
            action_id=f"action-{kind}",
            kind=kind,
            window=_window(provider),
            control=control,
            value=value,
            condition=(
                ControlCondition("value", expected_value)
                if kind == "type"
                else None
            ),
        )
    )

    assert receipt.outcome == "COMPLETED"
    assert receipt.reconciled is True
    assert receipt.reconciliation_required is False
    if expected_value is not None:
        assert receipt.after is not None
        assert receipt.after.value == expected_value
    if expected_selected is not None:
        assert receipt.after is not None
        assert receipt.after.selected is expected_selected
    json.dumps(receipt.to_dict())


def test_type_requires_explicit_final_value_before_dispatch(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    provider = _provider(process, backend)

    with pytest.raises(
        ValueError, match="explicit final value condition"
    ):
        OperatorAction(
            action_id="type-without-final-state",
            kind="type",
            window=_window(provider),
            control=ControlSelector(automation_id="DocumentText"),
            value=" world",
        )

    assert backend.mutations == []


def test_type_reconciliation_uses_declared_final_value_without_retyping(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    provider = _provider(process, backend)
    action = OperatorAction(
        action_id="type-with-final-state",
        kind="type",
        window=_window(provider),
        control=ControlSelector(automation_id="DocumentText"),
        value=" world",
        condition=ControlCondition("value", "hello world"),
    )

    backend.type_text(
        backend.list_controls(backend.list_windows(process.pid)[0])[0],
        " world",
        timeout_s=1.0,
    )
    reconciled = provider.reconcile(action)

    assert reconciled.outcome == "COMPLETED"
    assert reconciled.reconciled is True
    assert reconciled.after is not None
    assert reconciled.after.value == "hello world"
    assert backend.mutations == [("type", "text", " world")]


def test_click_without_semantic_readback_is_refused_before_dispatch(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    provider = _provider(process, backend)
    with pytest.raises(ValueError, match="deterministic condition"):
        OperatorAction(
            action_id="click-without-readback",
            kind="click",
            window=_window(provider),
            control=ControlSelector(automation_id="SaveButton"),
        )

    assert backend.mutations == []


def test_click_can_verify_a_distinct_readback_control(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    provider = _provider(process, backend)
    window = _window(provider)

    def add_saved_marker() -> None:
        backend.controls[100].append(
            _ControlState(
                ref="saved",
                control_type="TextControl",
                name="Saved",
                automation_id="SavedStatus",
                class_name="Text",
                runtime_id=(2, 9),
            )
        )

    backend.on_invoke = add_saved_marker
    receipt = provider.perform(
        OperatorAction(
            action_id="click-save",
            kind="click",
            window=window,
            control=ControlSelector(automation_id="SaveButton"),
            readback_control=ControlSelector(automation_id="SavedStatus"),
            condition=ControlCondition("control_exists", True),
        )
    )

    assert receipt.outcome == "COMPLETED"
    assert receipt.after is not None
    assert receipt.after.selector.automation_id == "SavedStatus"
    assert backend.mutations == [("click", "save", None)]


def test_wait_checks_stop_during_polling(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    checks = 0

    def stop() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 4

    provider = _provider(process, backend, stop_check=stop)
    receipt = provider.perform(
        OperatorAction(
            action_id="wait-for-change",
            kind="wait",
            window=_window(provider),
            control=ControlSelector(automation_id="DocumentText"),
            condition=ControlCondition("value", "never"),
            timeout_s=5,
        )
    )

    assert receipt.outcome == "CANCELLED"
    assert receipt.error_code == "stop_requested"
    assert backend.clock.now > 0
    assert backend.mutations == []


def test_assert_is_bounded_and_read_only(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    provider = _provider(process, backend)
    receipt = provider.perform(
        OperatorAction(
            action_id="assert-document",
            kind="assert",
            window=_window(provider),
            control=ControlSelector(automation_id="DocumentText"),
            condition=ControlCondition("value", "different"),
            timeout_s=1,
        )
    )

    assert receipt.outcome == "FAILED"
    assert receipt.error_code == "assertion_failed"
    assert backend.clock.now == 0
    assert backend.mutations == []


def test_expected_dialog_is_exact_and_unexpected_dialog_freezes_result(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    provider = _provider(process, backend)
    window = _window(provider)

    def add_expected() -> None:
        backend.windows[200] = _WindowState(
            hwnd=200,
            title="Confirm",
            class_name="#32770",
            automation_id="ConfirmDialog",
            runtime_id=(3, 1),
            is_dialog=True,
        )

    backend.on_invoke = add_expected
    expected = ExpectedDialog(
        WindowSelector(
            title="Confirm",
            class_name="#32770",
            automation_id="ConfirmDialog",
        )
    )
    completed = provider.perform(
        OperatorAction(
            action_id="open-confirm",
            kind="click",
            window=window,
            control=ControlSelector(automation_id="SaveButton"),
            expected_dialog=expected,
        )
    )
    assert completed.outcome == "COMPLETED"
    assert completed.dialog is not None
    assert completed.dialog.identity.hwnd == 200

    del backend.windows[200]

    def add_unexpected() -> None:
        backend.windows[201] = _WindowState(
            hwnd=201,
            title="Error",
            class_name="#32770",
            automation_id="ErrorDialog",
            runtime_id=(3, 2),
            is_dialog=True,
        )

    backend.on_invoke = add_unexpected
    unknown = provider.perform(
        OperatorAction(
            action_id="wrong-dialog",
            kind="click",
            window=window,
            control=ControlSelector(automation_id="SaveButton"),
            expected_dialog=expected,
        )
    )
    assert unknown.outcome == "UNKNOWN"
    assert unknown.error_code == "unexpected_dialog"
    assert unknown.dialog is not None
    assert unknown.dialog.identity.title == "Error"
    assert unknown.reconciliation_required is True


def test_timeout_after_possible_effect_is_unknown_then_reconciles_once(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    provider = _provider(process, backend)
    action = OperatorAction(
        action_id="slow-replace",
        kind="replace_text",
        window=_window(provider),
        control=ControlSelector(automation_id="DocumentText"),
        value="landed",
        timeout_s=0.5,
    )

    def expire_after_effect() -> None:
        backend.clock.now += 1

    backend.on_set_value = expire_after_effect
    unknown = provider.perform(action)
    assert unknown.outcome == "UNKNOWN"
    assert unknown.error_code == "timeout_after_possible_effect"
    assert unknown.effect_possible is True
    assert unknown.reconciliation_required is True
    assert backend.mutations == [("replace_text", "text", "landed")]

    backend.on_set_value = None
    reconciled = provider.reconcile(action)
    assert reconciled.outcome == "COMPLETED"
    assert reconciled.reconciled is True
    assert reconciled.after is not None
    assert reconciled.after.value == "landed"
    assert backend.mutations == [("replace_text", "text", "landed")]


def test_backend_error_after_dispatch_is_unknown(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    provider = _provider(process, backend)

    def fail_after_effect() -> None:
        raise RuntimeError("transport detached")

    backend.on_set_value = fail_after_effect
    receipt = provider.perform(
        OperatorAction(
            action_id="ambiguous-replace",
            kind="replace_text",
            window=_window(provider),
            control=ControlSelector(automation_id="DocumentText"),
            value="landed-before-error",
        )
    )

    assert receipt.outcome == "UNKNOWN"
    assert receipt.error_code == "action_ambiguous"
    assert receipt.effect_possible is True
    assert receipt.reconciliation_required is True
    assert backend.mutations == [
        ("replace_text", "text", "landed-before-error")
    ]


def test_stop_after_possible_effect_is_unknown(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    dispatched = False

    def stop() -> bool:
        return dispatched

    provider = _provider(process, backend, stop_check=stop)
    window = _window(provider)

    def mark_dispatched() -> None:
        nonlocal dispatched
        dispatched = True

    backend.on_set_value = mark_dispatched
    receipt = provider.perform(
        OperatorAction(
            action_id="stop-after-effect",
            kind="replace_text",
            window=window,
            control=ControlSelector(automation_id="DocumentText"),
            value="changed",
        )
    )

    assert receipt.outcome == "UNKNOWN"
    assert receipt.error_code == "stop_after_possible_effect"
    assert receipt.reconciliation_required is True
    assert backend.mutations == [("replace_text", "text", "changed")]


def test_reconciliation_remains_read_only_while_stop_is_active(
    process: ProcessIdentity,
    backend: _FakeBackend,
) -> None:
    provider = _provider(process, backend, stop_check=lambda: True)
    action = OperatorAction(
        action_id="reconcile-under-stop",
        kind="replace_text",
        window=_window(_provider(process, backend)),
        control=ControlSelector(automation_id="DocumentText"),
        value="hello",
    )

    receipt = provider.reconcile(action)

    assert receipt.outcome == "COMPLETED"
    assert receipt.reconciled is True
    assert receipt.after is not None
    assert receipt.after.value == "hello"
    assert backend.mutations == []
