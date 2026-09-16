"""Accessibility-first, exact-identity Windows application operations.

This provider owns bounded observation and UI Automation actions only. Policy,
planning, effect journaling, and process lifecycle remain with their existing
authorities. ``uiautomation`` is imported only when the default Windows backend
is first used; tests and alternate providers can inject the narrow backend
protocol below.
"""
from __future__ import annotations

import importlib
import hashlib
import math
import ntpath
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, Sequence, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
ActionOutcome: TypeAlias = Literal[
    "COMPLETED", "FAILED", "UNKNOWN", "CANCELLED"
]

_ACTION_KINDS = frozenset(
    {"click", "replace_text", "type", "select", "wait", "assert", "paste"}
)
_MUTATING_ACTIONS = frozenset(
    {"click", "replace_text", "type", "select", "paste"}
)
HELPER_WINDOW_CLASSES = frozenset(
    {
        "GDI+ Hook Window Class",
        "MSCTFIME UI",
        "IME",
        "tooltips_class32",
        "Auto-Suggest Dropdown",
        "ComboLBox",
        "_SearchEditBoxFakeWindow",
        "WorkerW",
        "Microsoft.UI.Content.PopupWindowSiteBridge",
        "MsoCommandBar",
        "MsoCommandBarShadow",
        "OfficeTooltip",
        "Net UI Tool Window",
        "MsoSplash",
    }
)
APPLICATION_FRAME_CLASSES = frozenset(
    {
        "Notepad",
        "OpusApp",
        "XLMAIN",
        "CabinetWClass",
        "ExploreWClass",
    }
)
DOCUMENT_CONTROL_TYPES = frozenset(
    {
        "DocumentControl",
        "EditControl",
        "Document",
        "Edit",
    }
)
DOCUMENT_NAME_ALIASES = (
    "Text editor",
    "Text Editor",
    "Document",
    "RichEdit Control",
)
CONTROL_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "file_menu": ("File", "Fichier", "Datei"),
    "open_item": ("Open", "Open...", "Ouvrir", "Ouvrir...", "Öffnen"),
    "save_as_item": (
        "Save as",
        "Save As",
        "Save as...",
        "Save As...",
        "Enregistrer sous",
        "Speichern unter",
    ),
    "file_name": ("File name", "File name:", "Nom du fichier", "Dateiname"),
    "open_button": ("Open", "Ouvrir", "Öffnen"),
    "save_button": ("Save", "Enregistrer", "Speichern"),
    "yes_button": ("Yes", "Oui", "Ja"),
    "word_wrap": ("Word wrap", "Word Wrap", "Retour automatique à la ligne"),
}
_CONDITION_FIELDS = frozenset(
    {
        "window_exists",
        "control_exists",
        "enabled",
        "visible",
        "focused",
        "value",
        "selected",
    }
)


class WindowsOperatorError(RuntimeError):
    """A fail-closed Windows operator boundary error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WindowsOperatorUnavailable(WindowsOperatorError):
    """The real Windows accessibility backend is unavailable."""


class ProcessIdentityMismatch(WindowsOperatorError):
    """The live PID no longer has the admitted process identity."""


class TargetMatchError(WindowsOperatorError):
    """A window or control target resolved to zero or multiple matches."""


def is_helper_window_class(class_name: str) -> bool:
    """Return whether a native class is a non-application helper HWND."""

    return str(class_name or "") in HELPER_WINDOW_CLASSES


def prefer_application_windows(
    windows: Sequence[Any],
    *,
    title_contains: str | None = None,
    class_name: str | None = None,
) -> list[Any]:
    """Reduce a window list to the unique application frame when possible."""

    visible = [
        window
        for window in windows
        if getattr(window, "visible", True)
        and getattr(window, "enabled", True)
        and not is_helper_window_class(getattr(window, "class_name", ""))
        and not getattr(window, "is_dialog", False)
        and (
            class_name is None
            or getattr(window, "class_name", "") == class_name
        )
    ]
    if title_contains:
        titled = [
            window
            for window in visible
            if title_contains.casefold()
            in str(getattr(window, "title", "")).casefold()
        ]
        if titled:
            visible = titled
    framed = [
        window
        for window in visible
        if getattr(window, "class_name", "") in APPLICATION_FRAME_CLASSES
    ]
    if len(framed) == 1:
        return framed
    if framed:
        visible = framed
    titled = [
        window
        for window in visible
        if str(getattr(window, "title", "")).strip()
    ]
    if len(titled) == 1:
        return titled
    return titled or list(visible)


def normalize_document_text(text: str) -> str:
    """Normalize UIA/file newlines for document equality."""

    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True, slots=True)
class VisibleWindow:
    """One top-level window snapshot used for launch-time identity binding."""

    hwnd: int
    pid: int
    title: str
    class_name: str

    def key(self) -> tuple[int, int]:
        return (self.hwnd, self.pid)


def resolve_launch_window_owner(
    before: Sequence[VisibleWindow],
    after: Sequence[VisibleWindow],
    *,
    title_contains: str,
) -> tuple[str, VisibleWindow | None]:
    """Choose a newly appeared application window, or refuse reuse.

    Returns ``(status, window)`` where status is ``bound``, ``reused``,
    ``missing``, or ``ambiguous``.
    """

    marker = title_contains.casefold()

    def matches(window: VisibleWindow) -> bool:
        return (
            not is_helper_window_class(window.class_name)
            and marker in window.title.casefold()
        )

    before_keys = {window.key() for window in before}
    candidates = [window for window in after if matches(window)]
    if not candidates:
        return "missing", None
    fresh = [window for window in candidates if window.key() not in before_keys]
    if len(fresh) == 1:
        return "bound", fresh[0]
    if len(fresh) > 1:
        return "ambiguous", None
    return "reused", candidates[0]


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Durable identity of one exact OS process."""

    logical_id: str
    pid: int
    create_time: int
    executable: str

    def __post_init__(self) -> None:
        if not self.logical_id.strip():
            raise ValueError("logical_id is required")
        if self.pid <= 0:
            raise ValueError("pid must be positive")
        if self.create_time <= 0:
            raise ValueError("create_time must be positive")
        if not self.executable.strip():
            raise ValueError("executable is required")

    def to_dict(self) -> dict[str, JsonScalar]:
        return {
            "logical_id": self.logical_id,
            "pid": self.pid,
            "create_time": self.create_time,
            "executable": self.executable,
        }


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    """Live backend observation used to verify a ``ProcessIdentity``."""

    pid: int
    create_time: int
    executable: str


@dataclass(frozen=True, slots=True)
class WindowSelector:
    """Exact-equality selector for one top-level window."""

    hwnd: int | None = None
    title: str | None = None
    class_name: str | None = None
    automation_id: str | None = None
    runtime_id: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not any(
            (
                self.hwnd is not None,
                self.title is not None,
                self.class_name is not None,
                self.automation_id is not None,
                bool(self.runtime_id),
            )
        ):
            raise ValueError("window selector requires at least one exact field")

    def to_dict(self) -> dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "class_name": self.class_name,
            "automation_id": self.automation_id,
            "runtime_id": list(self.runtime_id),
        }


@dataclass(frozen=True, slots=True)
class WindowIdentity:
    """Exact top-level window identity observed under one process."""

    process: ProcessIdentity
    hwnd: int
    title: str
    class_name: str
    automation_id: str
    runtime_id: tuple[int, ...]
    observation_generation: int

    def __post_init__(self) -> None:
        if self.hwnd <= 0:
            raise ValueError("window hwnd must be positive")
        if not self.runtime_id and not self.automation_id:
            raise ValueError(
                "window identity requires an automation id or runtime id"
            )
        if self.observation_generation <= 0:
            raise ValueError("observation_generation must be positive")

    def to_selector(self) -> WindowSelector:
        return WindowSelector(
            hwnd=self.hwnd,
            class_name=self.class_name or None,
            automation_id=self.automation_id or None,
            runtime_id=self.runtime_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "process": self.process.to_dict(),
            "hwnd": self.hwnd,
            "title": self.title,
            "class_name": self.class_name,
            "automation_id": self.automation_id,
            "runtime_id": list(self.runtime_id),
            "observation_generation": self.observation_generation,
        }


@dataclass(frozen=True, slots=True)
class WindowObservation:
    """Live state attached to an exact top-level window identity."""

    identity: WindowIdentity
    enabled: bool
    visible: bool
    is_dialog: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "enabled": self.enabled,
            "visible": self.visible,
            "is_dialog": self.is_dialog,
        }


@dataclass(frozen=True, slots=True)
class ControlSelector:
    """Exact-equality accessibility selector with no coordinate fallback."""

    control_type: str | None = None
    name: str | None = None
    automation_id: str | None = None
    class_name: str | None = None
    runtime_id: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not any(
            (
                self.control_type is not None,
                self.name is not None,
                self.automation_id is not None,
                self.class_name is not None,
                bool(self.runtime_id),
            )
        ):
            raise ValueError("control selector requires at least one exact field")

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_type": self.control_type,
            "name": self.name,
            "automation_id": self.automation_id,
            "class_name": self.class_name,
            "runtime_id": list(self.runtime_id),
        }


@dataclass(frozen=True, slots=True)
class ControlObservation:
    """JSON-safe readback for one exact accessibility control."""

    window: WindowIdentity
    selector: ControlSelector
    enabled: bool
    visible: bool
    focused: bool
    value: JsonScalar
    selected: bool | None

    def to_dict(self) -> dict[str, Any]:
        value = self.value
        value_sha256: str | None = None
        value_length: int | None = None
        if isinstance(value, str):
            value_sha256 = hashlib.sha256(value.encode("utf-8")).hexdigest()
            value_length = len(value)
            value = None
        return {
            "window": self.window.to_dict(),
            "selector": self.selector.to_dict(),
            "enabled": self.enabled,
            "visible": self.visible,
            "focused": self.focused,
            "value": value,
            "value_sha256": value_sha256,
            "value_length": value_length,
            "selected": self.selected,
        }


@dataclass(frozen=True, slots=True)
class ControlCondition:
    """One exact postcondition or observation predicate."""

    field: str
    expected: JsonScalar

    def __post_init__(self) -> None:
        if self.field not in _CONDITION_FIELDS:
            raise ValueError(
                f"condition field must be one of {sorted(_CONDITION_FIELDS)}"
            )
        if self.field in {"window_exists", "control_exists"} and not isinstance(
            self.expected, bool
        ):
            raise ValueError(f"{self.field} requires a boolean expectation")

    def to_dict(self) -> dict[str, JsonScalar]:
        return {"field": self.field, "expected": self.expected}


@dataclass(frozen=True, slots=True)
class ExpectedDialog:
    """A dialog which is explicitly expected after a mutating action."""

    selector: WindowSelector
    timeout_s: float = 5.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.timeout_s, (int, float))
            or isinstance(self.timeout_s, bool)
            or not math.isfinite(float(self.timeout_s))
            or self.timeout_s <= 0
            or self.timeout_s > 60
        ):
            raise ValueError("dialog timeout_s must be within (0, 60]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "selector": self.selector.to_dict(),
            "timeout_s": float(self.timeout_s),
        }


@dataclass(frozen=True, slots=True)
class OperatorAction:
    """One bounded accessibility operation against an exact window."""

    action_id: str
    kind: str
    window: WindowIdentity
    control: ControlSelector | None = None
    readback_control: ControlSelector | None = None
    value: str | None = None
    condition: ControlCondition | None = None
    timeout_s: float = 5.0
    expected_dialog: ExpectedDialog | None = None

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise ValueError("action_id is required")
        if self.kind not in _ACTION_KINDS:
            raise ValueError(f"unsupported operator action {self.kind!r}")
        if (
            not isinstance(self.timeout_s, (int, float))
            or isinstance(self.timeout_s, bool)
            or not math.isfinite(float(self.timeout_s))
            or self.timeout_s <= 0
            or self.timeout_s > 60
        ):
            raise ValueError("timeout_s must be within (0, 60]")
        if self.kind in _MUTATING_ACTIONS and self.control is None:
            raise ValueError(f"{self.kind} requires a control selector")
        if self.kind in {"replace_text", "type", "paste"}:
            if not isinstance(self.value, str):
                raise ValueError(f"{self.kind} requires text")
            if len(self.value) > 4096:
                raise ValueError("text must be at most 4096 characters")
        if (
            self.kind == "type"
            and (
                self.condition is None
                or self.condition.field != "value"
                or not isinstance(self.condition.expected, str)
            )
        ):
            raise ValueError(
                "type requires an explicit final value condition"
            )
        if self.kind in {"wait", "assert"} and self.condition is None:
            raise ValueError(f"{self.kind} requires a condition")
        if (
            self.condition is not None
            and self.condition.field != "window_exists"
            and self.readback_control is None
            and self.control is None
        ):
            raise ValueError(
                f"condition {self.condition.field!r} requires a control"
            )
        if (
            self.kind == "click"
            and self.condition is None
            and self.expected_dialog is None
        ):
            raise ValueError(
                "click requires a deterministic condition or expected dialog"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind,
            "window": self.window.to_dict(),
            "control": self.control.to_dict() if self.control else None,
            "readback_control": (
                self.readback_control.to_dict()
                if self.readback_control
                else None
            ),
            "value": self.value,
            "condition": self.condition.to_dict() if self.condition else None,
            "timeout_s": float(self.timeout_s),
            "expected_dialog": (
                self.expected_dialog.to_dict()
                if self.expected_dialog is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class ActionReceipt:
    """Durable-safe provider result; UNKNOWN always requires reconciliation."""

    action_id: str
    kind: str
    outcome: ActionOutcome
    process: ProcessIdentity
    window: WindowIdentity
    control: ControlSelector | None
    before: ControlObservation | None = None
    after: ControlObservation | None = None
    dialog: WindowObservation | None = None
    error_code: str | None = None
    detail: str | None = None
    effect_possible: bool = False
    reconciliation_required: bool = False
    reconciled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind,
            "outcome": self.outcome,
            "process": self.process.to_dict(),
            "window": self.window.to_dict(),
            "control": self.control.to_dict() if self.control else None,
            "before": self.before.to_dict() if self.before else None,
            "after": self.after.to_dict() if self.after else None,
            "dialog": self.dialog.to_dict() if self.dialog else None,
            "error_code": self.error_code,
            "detail": self.detail,
            "effect_possible": self.effect_possible,
            "reconciliation_required": self.reconciliation_required,
            "reconciled": self.reconciled,
        }


@dataclass(frozen=True, slots=True)
class BackendWindow:
    """Internal backend window record. ``ref`` never crosses evidence."""

    ref: object = field(compare=False, repr=False)
    hwnd: int
    title: str
    class_name: str
    automation_id: str
    runtime_id: tuple[int, ...]
    enabled: bool
    visible: bool
    is_dialog: bool = False


@dataclass(frozen=True, slots=True)
class BackendControl:
    """Internal backend control record. ``ref`` never crosses evidence."""

    ref: object = field(compare=False, repr=False)
    control_type: str
    name: str
    automation_id: str
    class_name: str
    runtime_id: tuple[int, ...]
    enabled: bool
    visible: bool
    focused: bool
    value: JsonScalar = None
    selected: bool | None = None


class AccessibilityBackend(Protocol):
    """Minimal live accessibility surface required by the provider."""

    def process_snapshot(self, pid: int) -> ProcessSnapshot | None: ...

    def list_windows(self, pid: int) -> Sequence[BackendWindow]: ...

    def list_controls(self, window: BackendWindow) -> Sequence[BackendControl]: ...

    def invoke(self, control: BackendControl, *, timeout_s: float) -> None: ...

    def set_value(
        self, control: BackendControl, value: str, *, timeout_s: float
    ) -> None: ...

    def type_text(
        self, control: BackendControl, value: str, *, timeout_s: float
    ) -> None: ...

    def select(
        self,
        control: BackendControl,
        value: str | None,
        *,
        timeout_s: float,
    ) -> None: ...

    def set_clipboard_text(self, text: str) -> None: ...

    def get_clipboard_text(self) -> str: ...

    def paste_text(
        self, control: BackendControl, *, timeout_s: float
    ) -> None: ...


class _UIAutomationBackend:
    """Thin ``uiautomation`` adapter with no coordinate-based actions."""

    def __init__(self, automation: Any, *, max_controls: int = 2048) -> None:
        self._auto = automation
        self._max_controls = max_controls

    def process_snapshot(self, pid: int) -> ProcessSnapshot | None:
        if os.name != "nt":
            return None
        import ctypes
        from ctypes import wintypes

        query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(query_limited_information, False, pid)
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return None
            create_time = (
                int(creation.dwHighDateTime) << 32
            ) | int(creation.dwLowDateTime)
            return ProcessSnapshot(
                pid=pid,
                create_time=create_time,
                executable=buffer.value,
            )
        finally:
            kernel32.CloseHandle(handle)

    @staticmethod
    def _property(control: Any, name: str, default: Any) -> Any:
        try:
            return getattr(control, name)
        except Exception:  # noqa: BLE001 - inaccessible UIA property
            return default

    def _pattern(self, control: Any, pattern_name: str) -> Any | None:
        try:
            pattern_id = getattr(self._auto.PatternId, pattern_name)
            return control.GetPattern(pattern_id)
        except Exception:  # noqa: BLE001 - unsupported UIA pattern
            return None

    def _runtime_id(self, control: Any) -> tuple[int, ...]:
        try:
            return tuple(int(value) for value in control.GetRuntimeId() or ())
        except Exception:  # noqa: BLE001 - stale UIA element
            return ()

    @staticmethod
    def _top_level_window_handles(pid: int) -> tuple[int, ...]:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        handles: list[int] = []

        @callback_type
        def collect(hwnd: int, _parameter: int) -> bool:
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(
                hwnd,
                ctypes.byref(process_id),
            )
            if int(process_id.value) == pid:
                handles.append(int(hwnd))
            return True

        if not user32.EnumWindows(collect, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return tuple(handles)

    def list_windows(self, pid: int) -> Sequence[BackendWindow]:
        try:
            handles = self._top_level_window_handles(pid)
        except Exception as error:  # noqa: BLE001 - backend boundary
            raise WindowsOperatorUnavailable(
                "uiautomation_unavailable",
                f"could not enumerate top-level windows: {type(error).__name__}",
            ) from error
        windows: list[BackendWindow] = []
        for hwnd in handles:
            try:
                control = self._auto.ControlFromHandle(hwnd)
            except Exception:  # noqa: BLE001 - inaccessible native window
                continue
            if int(self._property(control, "ProcessId", 0) or 0) != pid:
                continue
            class_name = str(self._property(control, "ClassName", "") or "")
            if is_helper_window_class(class_name):
                continue
            window_pattern = self._pattern(control, "WindowPattern")
            is_modal = bool(
                self._property(window_pattern, "IsModal", False)
                if window_pattern is not None
                else False
            )
            windows.append(
                BackendWindow(
                    ref=control,
                    hwnd=hwnd,
                    title=str(self._property(control, "Name", "") or ""),
                    class_name=class_name,
                    automation_id=str(
                        self._property(control, "AutomationId", "") or ""
                    ),
                    runtime_id=self._runtime_id(control),
                    enabled=bool(
                        self._property(control, "IsEnabled", False)
                    ),
                    visible=not bool(
                        self._property(control, "IsOffscreen", True)
                    ),
                    is_dialog=is_modal or class_name == "#32770",
                )
            )
        return windows

    def _control(self, control: Any) -> BackendControl:
        value: JsonScalar = None
        value_pattern = self._pattern(control, "ValuePattern")
        if value_pattern is not None:
            raw_value = self._property(value_pattern, "Value", None)
            if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
                value = raw_value
            else:
                value = str(raw_value)
        text_pattern = self._pattern(control, "TextPattern")
        if text_pattern is not None:
            try:
                document_range = getattr(text_pattern, "DocumentRange", None)
                raw_text = (
                    document_range.GetText(-1)
                    if document_range is not None
                    else None
                )
            except Exception:  # noqa: BLE001 - Text pattern is optional
                raw_text = None
            if isinstance(raw_text, str) and raw_text:
                value = raw_text
        selected: bool | None = None
        selection_pattern = self._pattern(control, "SelectionItemPattern")
        if selection_pattern is not None:
            selected = bool(
                self._property(selection_pattern, "IsSelected", False)
            )
        if selected is None:
            toggle_pattern = self._pattern(control, "TogglePattern")
            if toggle_pattern is not None:
                state = self._property(toggle_pattern, "ToggleState", None)
                token = str(state)
                selected = token in {"1", "On"} or token.endswith("On")
        return BackendControl(
            ref=control,
            control_type=str(
                self._property(control, "ControlTypeName", "") or ""
            ),
            name=str(self._property(control, "Name", "") or ""),
            automation_id=str(
                self._property(control, "AutomationId", "") or ""
            ),
            class_name=str(self._property(control, "ClassName", "") or ""),
            runtime_id=self._runtime_id(control),
            enabled=bool(self._property(control, "IsEnabled", False)),
            visible=not bool(self._property(control, "IsOffscreen", True)),
            focused=bool(
                self._property(control, "HasKeyboardFocus", False)
            ),
            value=value,
            selected=selected,
        )

    def list_controls(self, window: BackendWindow) -> Sequence[BackendControl]:
        queue: deque[Any] = deque()
        try:
            queue.extend(window.ref.GetChildren())
        except Exception as error:  # noqa: BLE001 - stale UIA window
            raise WindowsOperatorUnavailable(
                "accessibility_tree_unavailable",
                f"could not inspect window controls: {type(error).__name__}",
            ) from error
        controls: list[BackendControl] = []
        while queue:
            if len(controls) >= self._max_controls:
                raise WindowsOperatorUnavailable(
                    "accessibility_tree_too_large",
                    f"control inspection exceeded {self._max_controls} nodes",
                )
            control = queue.popleft()
            controls.append(self._control(control))
            try:
                queue.extend(control.GetChildren())
            except Exception:  # noqa: BLE001 - leaf or stale child
                continue
        return controls

    def invoke(self, control: BackendControl, *, timeout_s: float) -> None:
        del timeout_s
        invoke = self._pattern(control.ref, "InvokePattern")
        if invoke is not None:
            invoke.Invoke()
            return
        toggle = self._pattern(control.ref, "TogglePattern")
        if toggle is not None:
            toggle.Toggle()
            return
        legacy = self._pattern(control.ref, "LegacyIAccessiblePattern")
        if legacy is not None and hasattr(legacy, "DoDefaultAction"):
            legacy.DoDefaultAction()
            return
        raise WindowsOperatorError(
            "unsupported_accessibility_pattern",
            "control exposes no Invoke, Toggle, or default-action pattern",
        )

    def set_value(
        self, control: BackendControl, value: str, *, timeout_s: float
    ) -> None:
        del timeout_s
        pattern = self._pattern(control.ref, "ValuePattern")
        if pattern is None:
            raise WindowsOperatorError(
                "unsupported_accessibility_pattern",
                "control exposes no Value pattern",
            )
        if bool(self._property(pattern, "IsReadOnly", True)):
            raise WindowsOperatorError(
                "control_read_only", "control Value pattern is read-only"
            )
        pattern.SetValue(value)
        try:
            control.ref.SetFocus()
            self._auto.SendKeys("{Ctrl}{End}", waitTime=0.02)
        except Exception:  # noqa: BLE001 - caret placement is best-effort
            pass

    def type_text(
        self, control: BackendControl, value: str, *, timeout_s: float
    ) -> None:
        del timeout_s
        try:
            focused = bool(control.ref.SetFocus())
            has_focus = bool(
                self._property(control.ref, "HasKeyboardFocus", False)
            )
            if not focused or not has_focus:
                raise WindowsOperatorError(
                    "focus_not_verified",
                    "exact control focus could not be verified before typing",
                )
            self._auto.SendKeys(
                value,
                interval=0.01,
                waitTime=0,
                charMode=True,
            )
        except WindowsOperatorError:
            raise
        except Exception as error:  # noqa: BLE001 - input boundary
            raise WindowsOperatorError(
                "typing_failed",
                f"accessibility typing failed: {type(error).__name__}",
            ) from error

    def select(
        self,
        control: BackendControl,
        value: str | None,
        *,
        timeout_s: float,
    ) -> None:
        del timeout_s
        if value is not None and value not in {
            control.name,
            control.automation_id,
        }:
            raise WindowsOperatorError(
                "select_value_mismatch",
                "select value must identify the exact selected control",
            )
        pattern = self._pattern(control.ref, "SelectionItemPattern")
        if pattern is None:
            raise WindowsOperatorError(
                "unsupported_accessibility_pattern",
                "control exposes no SelectionItem pattern",
            )
        pattern.Select()

    def set_clipboard_text(self, text: str) -> None:
        try:
            self._auto.SetClipboardText(text)
        except Exception as error:  # noqa: BLE001 - clipboard boundary
            raise WindowsOperatorError(
                "clipboard_write_failed",
                f"clipboard text could not be set: {type(error).__name__}",
            ) from error

    def get_clipboard_text(self) -> str:
        try:
            value = self._auto.GetClipboardText()
        except Exception as error:  # noqa: BLE001 - clipboard boundary
            raise WindowsOperatorError(
                "clipboard_read_failed",
                f"clipboard text could not be read: {type(error).__name__}",
            ) from error
        return "" if value is None else str(value)

    def paste_text(
        self, control: BackendControl, *, timeout_s: float
    ) -> None:
        del timeout_s
        try:
            focused = bool(control.ref.SetFocus())
            has_focus = bool(
                self._property(control.ref, "HasKeyboardFocus", False)
            )
            if not focused or not has_focus:
                raise WindowsOperatorError(
                    "focus_not_verified",
                    "exact control focus could not be verified before paste",
                )
            self._auto.SendKeys("{Ctrl}a", interval=0.01, waitTime=0.02)
            self._auto.SendKeys("{Ctrl}v", interval=0.01, waitTime=0.05)
        except WindowsOperatorError:
            raise
        except Exception as error:  # noqa: BLE001 - input boundary
            raise WindowsOperatorError(
                "paste_failed",
                f"accessibility paste failed: {type(error).__name__}",
            ) from error


class _StopDuringWait(RuntimeError):
    pass


class _UnexpectedDialog(RuntimeError):
    def __init__(self, observation: WindowObservation | None = None) -> None:
        super().__init__("an unexpected dialog appeared")
        self.observation = observation


class WindowsOperatorProvider:
    """Bounded accessibility operations for one exact admitted process."""

    def __init__(
        self,
        process: ProcessIdentity,
        *,
        backend: AccessibilityBackend | None = None,
        stop_check: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_s: float = 0.05,
    ) -> None:
        if (
            not isinstance(poll_interval_s, (int, float))
            or isinstance(poll_interval_s, bool)
            or not math.isfinite(float(poll_interval_s))
            or poll_interval_s <= 0
            or poll_interval_s > 1
        ):
            raise ValueError("poll_interval_s must be within (0, 1]")
        self.process = process
        self._backend_instance = backend
        self._stop_check = stop_check or (lambda: False)
        self._clock = clock
        self._sleep = sleep
        self._poll_interval_s = float(poll_interval_s)
        self._observation_generation = 0

    def _backend(self) -> AccessibilityBackend:
        if self._backend_instance is not None:
            return self._backend_instance
        if os.name != "nt":
            raise WindowsOperatorUnavailable(
                "windows_only",
                "the default accessibility backend is available only on Windows",
            )
        try:
            automation = importlib.import_module("uiautomation")
        except ImportError as error:
            raise WindowsOperatorUnavailable(
                "uiautomation_not_installed",
                "uiautomation is required for the live Windows backend",
            ) from error
        self._backend_instance = _UIAutomationBackend(automation)
        return self._backend_instance

    def _stopped(self) -> bool:
        try:
            return bool(self._stop_check())
        except Exception:  # noqa: BLE001 - STOP checks fail closed
            return True

    @staticmethod
    def _same_executable(expected: str, actual: str) -> bool:
        return ntpath.normcase(ntpath.normpath(expected)) == ntpath.normcase(
            ntpath.normpath(actual)
        )

    def _verify_process(self) -> ProcessSnapshot:
        snapshot = self._backend().process_snapshot(self.process.pid)
        if snapshot is None:
            raise ProcessIdentityMismatch(
                "process_identity_unavailable",
                f"PID {self.process.pid} could not be verified",
            )
        if (
            snapshot.pid != self.process.pid
            or snapshot.create_time != self.process.create_time
            or not self._same_executable(
                self.process.executable, snapshot.executable
            )
        ):
            raise ProcessIdentityMismatch(
                "process_identity_mismatch",
                "live PID creation time or executable no longer matches",
            )
        return snapshot

    def _next_generation(self) -> int:
        self._observation_generation += 1
        return self._observation_generation

    @staticmethod
    def _window_matches(
        window: BackendWindow, selector: WindowSelector
    ) -> bool:
        return (
            (selector.hwnd is None or window.hwnd == selector.hwnd)
            and (selector.title is None or window.title == selector.title)
            and (
                selector.class_name is None
                or window.class_name == selector.class_name
            )
            and (
                selector.automation_id is None
                or window.automation_id == selector.automation_id
            )
            and (
                not selector.runtime_id
                or window.runtime_id == selector.runtime_id
            )
        )

    @staticmethod
    def _window_fingerprint(window: BackendWindow) -> tuple[Any, ...]:
        return (
            window.hwnd,
            window.title,
            window.class_name,
            window.automation_id,
            window.runtime_id,
        )

    @staticmethod
    def _control_matches(
        control: BackendControl, selector: ControlSelector
    ) -> bool:
        return (
            (
                selector.control_type is None
                or control.control_type == selector.control_type
            )
            and (selector.name is None or control.name == selector.name)
            and (
                selector.automation_id is None
                or control.automation_id == selector.automation_id
            )
            and (
                selector.class_name is None
                or control.class_name == selector.class_name
            )
            and (
                not selector.runtime_id
                or control.runtime_id == selector.runtime_id
            )
        )

    def _window_observation(
        self, window: BackendWindow
    ) -> WindowObservation:
        if window.hwnd <= 0 or (
            not window.runtime_id and not window.automation_id
        ):
            raise TargetMatchError(
                "window_identity_incomplete",
                "top-level window lacks an exact accessibility identity",
            )
        identity = WindowIdentity(
            process=self.process,
            hwnd=window.hwnd,
            title=window.title,
            class_name=window.class_name,
            automation_id=window.automation_id,
            runtime_id=window.runtime_id,
            observation_generation=self._next_generation(),
        )
        return WindowObservation(
            identity=identity,
            enabled=window.enabled,
            visible=window.visible,
            is_dialog=window.is_dialog,
        )

    def _control_observation(
        self, window: WindowIdentity, control: BackendControl
    ) -> ControlObservation:
        selector = ControlSelector(
            control_type=control.control_type,
            name=control.name,
            automation_id=control.automation_id,
            class_name=control.class_name,
            runtime_id=control.runtime_id,
        )
        return ControlObservation(
            window=window,
            selector=selector,
            enabled=control.enabled,
            visible=control.visible,
            focused=control.focused,
            value=control.value,
            selected=control.selected,
        )

    def _one_window(
        self,
        selector: WindowSelector,
        *,
        not_found_code: str = "window_not_found",
        ambiguous_code: str = "window_ambiguous",
    ) -> tuple[BackendWindow, WindowObservation]:
        matches = [
            window
            for window in self._backend().list_windows(self.process.pid)
            if self._window_matches(window, selector)
        ]
        if not matches:
            raise TargetMatchError(
                not_found_code, "no top-level window matched the exact selector"
            )
        if len(matches) != 1:
            raise TargetMatchError(
                ambiguous_code,
                f"{len(matches)} top-level windows matched the exact selector",
            )
        return matches[0], self._window_observation(matches[0])

    def _exact_window(
        self, identity: WindowIdentity
    ) -> tuple[BackendWindow, WindowObservation]:
        if identity.process != self.process:
            raise ProcessIdentityMismatch(
                "process_identity_mismatch",
                "window identity belongs to a different process",
            )
        return self._one_window(identity.to_selector())

    def _one_control(
        self,
        window: BackendWindow,
        window_identity: WindowIdentity,
        selector: ControlSelector,
    ) -> tuple[BackendControl, ControlObservation]:
        matches = [
            control
            for control in self._backend().list_controls(window)
            if self._control_matches(control, selector)
        ]
        if not matches:
            raise TargetMatchError(
                "control_not_found",
                "no control matched the exact accessibility selector",
            )
        if len(matches) != 1:
            raise TargetMatchError(
                "control_ambiguous",
                f"{len(matches)} controls matched the exact selector",
            )
        return (
            matches[0],
            self._control_observation(window_identity, matches[0]),
        )

    def observe(self, selector: WindowSelector) -> WindowObservation:
        """Observe exactly one top-level window or reject the target."""
        self._verify_process()
        return self._one_window(selector)[1]

    def inspect(
        self,
        window: WindowIdentity,
        selector: ControlSelector | None = None,
    ) -> tuple[ControlObservation, ...]:
        """Inspect the bounded accessibility tree under an exact window."""
        self._verify_process()
        live_window, window_observation = self._exact_window(window)
        controls = tuple(self._backend().list_controls(live_window))
        if selector is not None:
            matches = tuple(
                control
                for control in controls
                if self._control_matches(control, selector)
            )
            if not matches:
                raise TargetMatchError(
                    "control_not_found",
                    "no control matched the exact accessibility selector",
                )
            if len(matches) != 1:
                raise TargetMatchError(
                    "control_ambiguous",
                    f"{len(matches)} controls matched the exact selector",
                )
            controls = matches
        return tuple(
            self._control_observation(
                window_observation.identity, control
            )
            for control in controls
        )

    def observe_main_window(
        self,
        *,
        title_contains: str | None = None,
        class_name: str | None = None,
    ) -> WindowObservation:
        """Observe the unique application window for this process."""

        self._verify_process()
        windows = prefer_application_windows(
            self._backend().list_windows(self.process.pid),
            title_contains=title_contains,
            class_name=class_name,
        )
        if not windows:
            raise TargetMatchError(
                "window_not_found",
                "no application window matched the exact process",
            )
        if len(windows) != 1:
            raise TargetMatchError(
                "window_ambiguous",
                f"{len(windows)} application windows matched the process",
            )
        return self._window_observation(windows[0])

    def resolve_named_control(
        self,
        window: WindowIdentity,
        *,
        names: Sequence[str],
        control_type: str | None = None,
        automation_ids: Sequence[str] = (),
    ) -> ControlObservation:
        """Return the first uniquely matching alias, else fail closed."""

        last_error: TargetMatchError | None = None
        selectors: list[ControlSelector] = []
        for automation_id in automation_ids:
            selectors.append(
                ControlSelector(
                    control_type=control_type,
                    automation_id=automation_id,
                )
            )
        for name in names:
            selectors.append(
                ControlSelector(control_type=control_type, name=name)
            )
        for selector in selectors:
            try:
                matches = self.inspect(window, selector)
            except TargetMatchError as error:
                last_error = error
                continue
            if len(matches) == 1:
                return matches[0]
            last_error = TargetMatchError(
                "control_ambiguous",
                f"{len(matches)} controls matched alias {selector.to_dict()}",
            )
        raise last_error or TargetMatchError(
            "control_not_found",
            "no control matched the supplied exact name aliases",
        )

    def find_control_on_any_window(
        self,
        *,
        names: Sequence[str],
        control_type: str | None = None,
        automation_ids: Sequence[str] = (),
    ) -> ControlObservation:
        """Resolve a named control under any visible application window."""

        self._verify_process()
        last_error: TargetMatchError | None = None
        for window in self._backend().list_windows(self.process.pid):
            if is_helper_window_class(window.class_name) or not window.visible:
                continue
            observation = self._window_observation(window)
            try:
                return self.resolve_named_control(
                    observation.identity,
                    names=names,
                    control_type=control_type,
                    automation_ids=automation_ids,
                )
            except TargetMatchError as error:
                last_error = error
                continue
        raise last_error or TargetMatchError(
            "control_not_found",
            "no visible window contained the named control",
        )

    def find_document_control(
        self,
        window: WindowIdentity,
        selector: ControlSelector | None = None,
    ) -> ControlObservation:
        """Locate the unique document/edit surface under one window."""

        if selector is not None:
            matches = self.inspect(window, selector)
            if len(matches) != 1:
                raise TargetMatchError(
                    "control_ambiguous",
                    "document selector did not resolve to one control",
                )
            return matches[0]
        controls = self.inspect(window)
        valued = [
            control
            for control in controls
            if control.enabled
            and control.visible
            and isinstance(control.value, str)
            and (
                (control.selector.control_type or "") in DOCUMENT_CONTROL_TYPES
                or (control.selector.name or "") in DOCUMENT_NAME_ALIASES
            )
        ]
        named = [
            control
            for control in valued
            if (control.selector.name or "") in DOCUMENT_NAME_ALIASES
        ]
        pool = named or valued
        if not pool:
            raise TargetMatchError(
                "control_not_found",
                "no document/edit control with a Value pattern was found",
            )
        if len(pool) != 1:
            raise TargetMatchError(
                "control_ambiguous",
                f"{len(pool)} document/edit controls matched",
            )
        return pool[0]

    def find_named_item(
        self,
        window: WindowIdentity,
        names: Sequence[str],
        *,
        control_types: Sequence[str] = (
            "ListItemControl",
            "DataItemControl",
            "TreeItemControl",
        ),
    ) -> ControlObservation:
        """Resolve a unique named list/tree item under one window."""

        last_error: TargetMatchError | None = None
        for control_type in control_types:
            try:
                return self.resolve_named_control(
                    window,
                    names=names,
                    control_type=control_type,
                )
            except TargetMatchError as error:
                last_error = error
                continue
        raise last_error or TargetMatchError(
            "control_not_found",
            "no named list or tree item matched",
        )

    def invoke_file_dialog(
        self,
        window: WindowIdentity,
        *,
        path: str,
        action: str = "open",
        timeout_s: float = 15.0,
        action_id: str = "file-dialog",
    ) -> ActionReceipt:
        """Open or Save As via named UIA aliases, then assert the landed path.

        ``action`` is ``open`` or ``save_as``. The filename is committed with
        keyboard paste of the full path because COMDLG32 ignores ValuePattern
        SetValue. Receipt ``after.value`` is not raw path text; callers should
        use window title / document readback.
        """

        if action not in {"open", "save_as"}:
            raise ValueError("file dialog action must be open or save_as")
        item_key = "open_item" if action == "open" else "save_as_item"
        confirm_key = "open_button" if action == "open" else "save_button"
        try:
            try:
                file_menu = self.resolve_named_control(
                    window,
                    names=CONTROL_NAME_ALIASES["file_menu"],
                    control_type="MenuItemControl",
                    automation_ids=("File",),
                )
            except TargetMatchError:
                file_menu = self.find_control_on_any_window(
                    names=CONTROL_NAME_ALIASES["file_menu"],
                    control_type="MenuItemControl",
                    automation_ids=("File",),
                )
                window = file_menu.window
            item_names = CONTROL_NAME_ALIASES[item_key]
            last = self.perform(
                OperatorAction(
                    action_id=f"{action_id}:menu",
                    kind="click",
                    window=window,
                    control=file_menu.selector,
                    readback_control=ControlSelector(
                        control_type="MenuItemControl",
                        name=item_names[0],
                    ),
                    condition=ControlCondition("control_exists", True),
                    timeout_s=min(timeout_s, 15.0),
                )
            )
            if last.outcome != "COMPLETED":
                for name in item_names[1:]:
                    last = self.perform(
                        OperatorAction(
                            action_id=f"{action_id}:menu:{name}",
                            kind="click",
                            window=window,
                            control=file_menu.selector,
                            readback_control=ControlSelector(
                                control_type="MenuItemControl",
                                name=name,
                            ),
                            condition=ControlCondition("control_exists", True),
                            timeout_s=min(timeout_s, 8.0),
                        )
                    )
                    if last.outcome == "COMPLETED":
                        break
            if last.outcome != "COMPLETED":
                return last
            try:
                item = self.resolve_named_control(
                    window,
                    names=item_names,
                    control_type="MenuItemControl",
                )
            except TargetMatchError:
                try:
                    item = self.find_control_on_any_window(
                        names=item_names,
                        control_type="MenuItemControl",
                    )
                except TargetMatchError:
                    item = self.find_control_on_any_window(
                        names=item_names,
                        control_type="ButtonControl",
                    )
            last = self.perform(
                OperatorAction(
                    action_id=f"{action_id}:item",
                    kind="click",
                    window=item.window,
                    control=item.selector,
                    expected_dialog=ExpectedDialog(
                        WindowSelector(class_name="#32770"),
                        timeout_s=min(timeout_s, 15.0),
                    ),
                    timeout_s=min(timeout_s, 20.0),
                )
            )
            if last.outcome != "COMPLETED" or last.dialog is None:
                return last
            dialog_window = last.dialog.identity
            name_box = self.resolve_named_control(
                dialog_window,
                names=CONTROL_NAME_ALIASES["file_name"],
                control_type="EditControl",
                automation_ids=(
                    ("1001", "1148") if action == "save_as" else ("1148", "1001")
                ),
            )
            # COMDLG32 File name Edit ValuePattern.SetValue updates UIA but not
            # the pending path. Keyboard paste is what the dialog actually saves.
            typed = self.perform(
                OperatorAction(
                    action_id=f"{action_id}:filename",
                    kind="paste",
                    window=dialog_window,
                    control=name_box.selector,
                    value=path,
                    timeout_s=min(timeout_s, 10.0),
                )
            )
            if typed.outcome == "FAILED":
                return typed
            if typed.outcome != "COMPLETED" and not typed.effect_possible:
                return typed
            try:
                if action == "open":
                    confirm = self.resolve_named_control(
                        dialog_window,
                        names=CONTROL_NAME_ALIASES[confirm_key],
                        control_type="SplitButtonControl",
                        automation_ids=("1",),
                    )
                else:
                    confirm = self.resolve_named_control(
                        dialog_window,
                        names=CONTROL_NAME_ALIASES[confirm_key],
                        control_type="ButtonControl",
                        automation_ids=("1",),
                    )
            except TargetMatchError:
                confirm = self.resolve_named_control(
                    dialog_window,
                    names=CONTROL_NAME_ALIASES[confirm_key],
                    control_type=(
                        "ButtonControl" if action == "open" else "SplitButtonControl"
                    ),
                    automation_ids=("1",),
                )
            last = self.perform(
                OperatorAction(
                    action_id=f"{action_id}:confirm",
                    kind="click",
                    window=dialog_window,
                    control=confirm.selector,
                    condition=ControlCondition("window_exists", False),
                    timeout_s=min(timeout_s, 15.0),
                )
            )
            if (
                last.outcome == "UNKNOWN"
                and last.error_code == "unexpected_dialog"
                and last.dialog is not None
                and last.dialog.identity.class_name == "#32770"
                and "confirm" in last.dialog.identity.title.casefold()
            ):
                confirm_text = self._dialog_plain_text(last.dialog.identity)
                intended = ntpath.basename(path)
                if intended.casefold() not in confirm_text.casefold():
                    return self._receipt(
                        OperatorAction(
                            action_id=f"{action_id}:overwrite",
                            kind="assert",
                            window=last.dialog.identity,
                            condition=ControlCondition("window_exists", True),
                            timeout_s=min(timeout_s, 5.0),
                        ),
                        "FAILED",
                        dialog=last.dialog,
                        error_code="wrong_overwrite_target",
                        detail=(
                            "Confirm Save As named a different file than the "
                            "path the operator submitted"
                        ),
                    )
                yes = self.resolve_named_control(
                    last.dialog.identity,
                    names=CONTROL_NAME_ALIASES["yes_button"],
                    control_type="ButtonControl",
                    automation_ids=("6", "Yes"),
                )
                last = self.perform(
                    OperatorAction(
                        action_id=f"{action_id}:overwrite",
                        kind="click",
                        window=last.dialog.identity,
                        control=yes.selector,
                        condition=ControlCondition("window_exists", False),
                        timeout_s=min(timeout_s, 10.0),
                    )
                )
            if last.outcome != "COMPLETED":
                return last
            stem = ntpath.splitext(ntpath.basename(path))[0]
            landed_deadline = self._clock() + min(float(timeout_s), 8.0)
            while self._clock() < landed_deadline:
                try:
                    main = self.observe_main_window(title_contains=stem)
                    if stem.casefold() in main.identity.title.casefold():
                        return last
                except TargetMatchError:
                    pass
                if os.path.isfile(path):
                    return last
                self._sleep(min(self._poll_interval_s, 0.2))
            return self._receipt(
                OperatorAction(
                    action_id=f"{action_id}:landed",
                    kind="assert",
                    window=window,
                    condition=ControlCondition("window_exists", True),
                    timeout_s=min(timeout_s, 5.0),
                ),
                "FAILED",
                error_code="landed_path_mismatch",
                detail="file dialog closed without the expected window title or file",
            )
        except TargetMatchError as error:
            return self._receipt(
                OperatorAction(
                    action_id=action_id,
                    kind="click",
                    window=window,
                    control=ControlSelector(name="File"),
                    timeout_s=min(timeout_s, 5.0),
                ),
                "FAILED",
                error_code=error.code,
                detail=self._safe_detail(error),
            )

    def _dialog_plain_text(self, window: WindowIdentity) -> str:
        """Concatenated visible text on one dialog, for overwrite targeting."""

        parts: list[str] = []
        for control in self.inspect(window):
            name = str(control.selector.name or "").strip()
            if name:
                parts.append(name)
            if isinstance(control.value, str) and control.value.strip():
                parts.append(control.value.strip())
        return "\n".join(parts)

    def set_clipboard_text(self, text: str) -> None:
        if not isinstance(text, str) or len(text) > 4096:
            raise ValueError("clipboard text must be a string of at most 4096 characters")
        self._verify_process()
        self._backend().set_clipboard_text(text)

    def get_clipboard_text(self) -> str:
        self._verify_process()
        return self._backend().get_clipboard_text()

    @staticmethod
    def _safe_detail(error: BaseException | str) -> str:
        text = str(error).replace("\r", " ").replace("\n", " ").strip()
        return text[:300] or type(error).__name__

    def _receipt(
        self,
        action: OperatorAction,
        outcome: ActionOutcome,
        *,
        before: ControlObservation | None = None,
        after: ControlObservation | None = None,
        dialog: WindowObservation | None = None,
        error_code: str | None = None,
        detail: str | None = None,
        effect_possible: bool = False,
        reconciled: bool = False,
    ) -> ActionReceipt:
        return ActionReceipt(
            action_id=action.action_id,
            kind=action.kind,
            outcome=outcome,
            process=self.process,
            window=action.window,
            control=action.control,
            before=before,
            after=after,
            dialog=dialog,
            error_code=error_code,
            detail=detail,
            effect_possible=effect_possible,
            reconciliation_required=outcome == "UNKNOWN",
            reconciled=reconciled,
        )

    def _condition_state(
        self, action: OperatorAction, condition: ControlCondition
    ) -> tuple[bool, ControlObservation | None]:
        self._verify_process()
        try:
            live_window, window_observation = self._exact_window(action.window)
        except TargetMatchError as error:
            if (
                error.code == "window_not_found"
                and condition.field == "window_exists"
            ):
                return condition.expected is False, None
            raise
        if condition.field == "window_exists":
            return condition.expected is True, None
        selector = action.readback_control or action.control
        assert selector is not None
        try:
            _control, observation = self._one_control(
                live_window,
                window_observation.identity,
                selector,
            )
        except TargetMatchError as error:
            if (
                error.code == "control_not_found"
                and condition.field == "control_exists"
            ):
                return condition.expected is False, None
            raise
        if condition.field == "control_exists":
            actual: JsonScalar = True
        else:
            actual = getattr(observation, condition.field)
        if (
            condition.field == "value"
            and isinstance(actual, str)
            and isinstance(condition.expected, str)
        ):
            return (
                normalize_document_text(actual)
                == normalize_document_text(condition.expected)
            ), observation
        return actual == condition.expected, observation

    def _new_dialogs(
        self, baseline: frozenset[tuple[Any, ...]]
    ) -> tuple[BackendWindow, ...]:
        return tuple(
            window
            for window in self._backend().list_windows(self.process.pid)
            if window.is_dialog
            and self._window_fingerprint(window) not in baseline
        )

    def _wait_for_condition(
        self,
        action: OperatorAction,
        condition: ControlCondition,
        *,
        deadline: float,
        baseline_dialogs: frozenset[tuple[Any, ...]] | None = None,
        honor_stop: bool = True,
    ) -> ControlObservation | None:
        while True:
            if honor_stop and self._stopped():
                raise _StopDuringWait
            if baseline_dialogs is not None:
                new_dialogs = self._new_dialogs(baseline_dialogs)
                if new_dialogs:
                    raise _UnexpectedDialog(
                        self._window_observation(new_dialogs[0])
                    )
            matched, observation = self._condition_state(action, condition)
            if matched:
                return observation
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise TimeoutError("condition was not observed before timeout")
            self._sleep(min(self._poll_interval_s, remaining))

    def _wait_for_dialog(
        self,
        expected: ExpectedDialog,
        *,
        deadline: float,
        baseline: frozenset[tuple[Any, ...]],
        honor_stop: bool = True,
    ) -> WindowObservation:
        dialog_deadline = min(
            deadline, self._clock() + float(expected.timeout_s)
        )
        while True:
            if honor_stop and self._stopped():
                raise _StopDuringWait
            self._verify_process()
            new_dialogs = self._new_dialogs(baseline)
            matches = tuple(
                window
                for window in new_dialogs
                if self._window_matches(window, expected.selector)
            )
            unexpected = tuple(
                window for window in new_dialogs if window not in matches
            )
            if unexpected:
                raise _UnexpectedDialog(
                    self._window_observation(unexpected[0])
                )
            if len(matches) > 1:
                raise TargetMatchError(
                    "dialog_ambiguous",
                    f"{len(matches)} dialogs matched the exact selector",
                )
            if len(matches) == 1:
                return self._window_observation(matches[0])
            remaining = dialog_deadline - self._clock()
            if remaining <= 0:
                raise TimeoutError("expected dialog was not observed")
            self._sleep(min(self._poll_interval_s, remaining))

    @staticmethod
    def _inferred_condition(
        action: OperatorAction, before: ControlObservation
    ) -> ControlCondition | None:
        if action.condition is not None:
            return action.condition
        if action.kind == "replace_text":
            return ControlCondition("value", action.value)
        if action.kind == "paste":
            return ControlCondition("value", action.value)
        if action.kind == "select":
            return ControlCondition("selected", True)
        return None

    def perform(self, action: OperatorAction) -> ActionReceipt:
        """Perform one bounded action and independently read it back."""
        deadline = self._clock() + float(action.timeout_s)
        if self._stopped():
            return self._receipt(
                action,
                "CANCELLED",
                error_code="stop_requested",
                detail="STOP was active before the operator action",
            )
        before: ControlObservation | None = None
        try:
            self._verify_process()
            live_window, window_observation = self._exact_window(action.window)
            if action.kind in {"wait", "assert"}:
                assert action.condition is not None
                if action.kind == "assert":
                    matched, after = self._condition_state(
                        action, action.condition
                    )
                    if not matched:
                        return self._receipt(
                            action,
                            "FAILED",
                            after=after,
                            error_code="assertion_failed",
                            detail="the asserted accessibility state did not match",
                        )
                    return self._receipt(
                        action, "COMPLETED", after=after, reconciled=True
                    )
                try:
                    after = self._wait_for_condition(
                        action, action.condition, deadline=deadline
                    )
                except _StopDuringWait:
                    return self._receipt(
                        action,
                        "CANCELLED",
                        error_code="stop_requested",
                        detail="STOP became active during the wait",
                    )
                except TimeoutError as error:
                    return self._receipt(
                        action,
                        "FAILED",
                        error_code="condition_timeout",
                        detail=self._safe_detail(error),
                    )
                return self._receipt(
                    action, "COMPLETED", after=after, reconciled=True
                )

            assert action.control is not None
            live_control, before = self._one_control(
                live_window,
                window_observation.identity,
                action.control,
            )
            condition = self._inferred_condition(action, before)
            if condition is not None:
                matched_before, observed_before = self._condition_state(
                    action, condition
                )
                if matched_before:
                    return self._receipt(
                        action,
                        "COMPLETED",
                        before=before,
                        after=observed_before,
                        reconciled=True,
                    )
            baseline = frozenset(
                self._window_fingerprint(window)
                for window in self._backend().list_windows(self.process.pid)
                if window.is_dialog
            )
            if self._stopped():
                return self._receipt(
                    action,
                    "CANCELLED",
                    before=before,
                    error_code="stop_requested",
                    detail="STOP was active before mutation",
                )

            # Revalidate immediately before every mutation. Resolving the UIA
            # target above is not process-liveness authority.
            self._verify_process()
            remaining = deadline - self._clock()
            if remaining <= 0:
                return self._receipt(
                    action,
                    "FAILED",
                    before=before,
                    error_code="timeout_before_effect",
                    detail="the action deadline expired before dispatch",
                )
            effect_dispatched = True
            try:
                if action.kind == "click":
                    self._backend().invoke(live_control, timeout_s=remaining)
                elif action.kind == "replace_text":
                    self._backend().set_value(
                        live_control, action.value or "", timeout_s=remaining
                    )
                elif action.kind == "type":
                    self._backend().type_text(
                        live_control, action.value or "", timeout_s=remaining
                    )
                elif action.kind == "select":
                    self._backend().select(
                        live_control, action.value, timeout_s=remaining
                    )
                elif action.kind == "paste":
                    self._backend().set_clipboard_text(action.value or "")
                    self._backend().paste_text(
                        live_control, timeout_s=remaining
                    )
            except Exception as error:  # noqa: BLE001 - effect may have landed
                return self._receipt(
                    action,
                    "UNKNOWN",
                    before=before,
                    error_code=(
                        error.code
                        if isinstance(error, WindowsOperatorError)
                        else "action_ambiguous"
                    ),
                    detail=self._safe_detail(error),
                    effect_possible=effect_dispatched,
                )

            if self._clock() >= deadline:
                return self._receipt(
                    action,
                    "UNKNOWN",
                    before=before,
                    error_code="timeout_after_possible_effect",
                    detail="the action deadline expired after dispatch",
                    effect_possible=True,
                )
            if self._stopped():
                return self._receipt(
                    action,
                    "UNKNOWN",
                    before=before,
                    error_code="stop_after_possible_effect",
                    detail="STOP became active after dispatch",
                    effect_possible=True,
                )

            dialog: WindowObservation | None = None
            if action.expected_dialog is not None:
                try:
                    dialog = self._wait_for_dialog(
                        action.expected_dialog,
                        deadline=deadline,
                        baseline=baseline,
                    )
                except _UnexpectedDialog as error:
                    return self._receipt(
                        action,
                        "UNKNOWN",
                        before=before,
                        dialog=error.observation,
                        error_code="unexpected_dialog",
                        detail="a different dialog appeared after dispatch",
                        effect_possible=True,
                    )
                except _StopDuringWait:
                    return self._receipt(
                        action,
                        "UNKNOWN",
                        before=before,
                        error_code="stop_after_possible_effect",
                        detail="STOP became active while awaiting the dialog",
                        effect_possible=True,
                    )
                except (TimeoutError, TargetMatchError) as error:
                    return self._receipt(
                        action,
                        "UNKNOWN",
                        before=before,
                        error_code=(
                            error.code
                            if isinstance(error, TargetMatchError)
                            else "expected_dialog_timeout"
                        ),
                        detail=self._safe_detail(error),
                        effect_possible=True,
                    )
            else:
                new_dialogs = self._new_dialogs(baseline)
                if new_dialogs:
                    return self._receipt(
                        action,
                        "UNKNOWN",
                        before=before,
                        dialog=self._window_observation(new_dialogs[0]),
                        error_code="unexpected_dialog",
                        detail="an undeclared dialog appeared after dispatch",
                        effect_possible=True,
                    )

            after: ControlObservation | None = None
            if condition is not None:
                try:
                    after = self._wait_for_condition(
                        action,
                        condition,
                        deadline=deadline,
                        baseline_dialogs=(
                            None
                            if action.expected_dialog is not None
                            else baseline
                        ),
                    )
                except _UnexpectedDialog as error:
                    return self._receipt(
                        action,
                        "UNKNOWN",
                        before=before,
                        dialog=error.observation,
                        error_code="unexpected_dialog",
                        detail="an undeclared dialog appeared during readback",
                        effect_possible=True,
                    )
                except _StopDuringWait:
                    return self._receipt(
                        action,
                        "UNKNOWN",
                        before=before,
                        dialog=dialog,
                        error_code="stop_after_possible_effect",
                        detail="STOP became active during readback",
                        effect_possible=True,
                    )
                except (TimeoutError, WindowsOperatorError) as error:
                    return self._receipt(
                        action,
                        "UNKNOWN",
                        before=before,
                        dialog=dialog,
                        error_code=(
                            error.code
                            if isinstance(error, WindowsOperatorError)
                            else "timeout_after_possible_effect"
                        ),
                        detail=self._safe_detail(error),
                        effect_possible=True,
                    )
            elif dialog is None:
                return self._receipt(
                    action,
                    "UNKNOWN",
                    before=before,
                    error_code="readback_required",
                    detail="mutating actions require a postcondition or dialog",
                    effect_possible=True,
                )
            return self._receipt(
                action,
                "COMPLETED",
                before=before,
                after=after,
                dialog=dialog,
                effect_possible=True,
                reconciled=True,
            )
        except WindowsOperatorError as error:
            return self._receipt(
                action,
                "FAILED",
                before=before,
                error_code=error.code,
                detail=self._safe_detail(error),
            )
        except TimeoutError as error:
            return self._receipt(
                action,
                "UNKNOWN",
                before=before,
                error_code="timeout_after_possible_effect",
                detail=self._safe_detail(error),
                effect_possible=True,
            )
        except Exception as error:  # noqa: BLE001 - provider boundary
            return self._receipt(
                action,
                "UNKNOWN",
                before=before,
                error_code="action_ambiguous",
                detail=type(error).__name__,
                effect_possible=True,
            )

    def reconcile(self, action: OperatorAction) -> ActionReceipt:
        """Read back a prior UNKNOWN action without dispatching it again."""
        deadline = self._clock() + float(action.timeout_s)
        try:
            self._verify_process()
            _live_window, _window_observation = self._exact_window(
                action.window
            )
            dialog: WindowObservation | None = None
            if action.expected_dialog is not None:
                dialog_deadline = min(
                    deadline,
                    self._clock() + float(action.expected_dialog.timeout_s),
                )
                while True:
                    self._verify_process()
                    matches = tuple(
                        window
                        for window in self._backend().list_windows(
                            self.process.pid
                        )
                        if window.is_dialog
                        and self._window_matches(
                            window, action.expected_dialog.selector
                        )
                    )
                    if len(matches) > 1:
                        raise TargetMatchError(
                            "dialog_ambiguous",
                            f"{len(matches)} dialogs matched the exact selector",
                        )
                    if len(matches) == 1:
                        dialog = self._window_observation(matches[0])
                        break
                    remaining = dialog_deadline - self._clock()
                    if remaining <= 0:
                        raise TimeoutError("expected dialog was not observed")
                    self._sleep(min(self._poll_interval_s, remaining))

            before = None
            if action.control is not None:
                live_window, window_observation = self._exact_window(
                    action.window
                )
                try:
                    _control, before = self._one_control(
                        live_window,
                        window_observation.identity,
                        action.control,
                    )
                except TargetMatchError as error:
                    if not (
                        action.condition is not None
                        and error.code == "control_not_found"
                        and action.condition.field == "control_exists"
                        and action.condition.expected is False
                    ):
                        raise
            condition = (
                self._inferred_condition(action, before)
                if before is not None
                else action.condition
            )
            after: ControlObservation | None = None
            if condition is not None:
                after = self._wait_for_condition(
                    action,
                    condition,
                    deadline=deadline,
                    honor_stop=False,
                )
            elif dialog is None:
                return self._receipt(
                    action,
                    "UNKNOWN",
                    before=before,
                    error_code="readback_required",
                    detail="no deterministic readback was declared",
                    effect_possible=True,
                )
            return self._receipt(
                action,
                "COMPLETED",
                before=before,
                after=after,
                dialog=dialog,
                effect_possible=True,
                reconciled=True,
            )
        except _StopDuringWait:
            return self._receipt(
                action,
                "CANCELLED",
                error_code="stop_requested",
                detail="STOP became active during reconciliation",
            )
        except (TimeoutError, WindowsOperatorError) as error:
            return self._receipt(
                action,
                "UNKNOWN",
                error_code=(
                    error.code
                    if isinstance(error, WindowsOperatorError)
                    else "not_observed"
                ),
                detail=self._safe_detail(error),
                effect_possible=True,
            )


__all__ = [
    "AccessibilityBackend",
    "ActionReceipt",
    "BackendControl",
    "BackendWindow",
    "CONTROL_NAME_ALIASES",
    "ControlCondition",
    "ControlObservation",
    "ControlSelector",
    "DOCUMENT_CONTROL_TYPES",
    "DOCUMENT_NAME_ALIASES",
    "ExpectedDialog",
    "HELPER_WINDOW_CLASSES",
    "OperatorAction",
    "ProcessIdentity",
    "ProcessIdentityMismatch",
    "ProcessSnapshot",
    "TargetMatchError",
    "VisibleWindow",
    "WindowIdentity",
    "WindowObservation",
    "WindowSelector",
    "WindowsOperatorError",
    "WindowsOperatorProvider",
    "WindowsOperatorUnavailable",
    "is_helper_window_class",
    "normalize_document_text",
    "resolve_launch_window_owner",
]
