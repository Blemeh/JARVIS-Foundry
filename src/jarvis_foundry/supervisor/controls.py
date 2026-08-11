"""Local STOP controls: emergency hotkey and tray icon (Windows).

Both controls are fully independent of Hermes, LLMs, providers, and the
network: they write the deterministic sentinel file through
``EmergencyStop``. A hidden message-only window hosts a ``RegisterHotKey``
hotkey and the tray icon callbacks on a dedicated thread.

Non-Windows platforms raise ``NotImplementedError``; the V1 target is Windows.
"""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from pathlib import Path

from .estop import EmergencyStop

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", ctypes.c_wchar * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", ctypes.c_wchar * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", ctypes.c_wchar * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    # messages
    WM_HOTKEY = 0x0312
    WM_COMMAND = 0x0111
    WM_DESTROY = 0x0002
    WM_RBUTTONUP = 0x0205
    WM_LBUTTONUP = 0x0202
    WM_CONTEXTMENU = 0x007B
    WM_APP_TRAY = 0x8000
    WM_APP_CLOSE = 0x8001

    # identifiers
    ID_HOTKEY_STOP = 1
    ID_TRAY_STOP = 1001
    ID_TRAY_RESUME = 1002
    ID_TRAY_EXIT = 1003
    IDI_APPLICATION = 32512
    IDC_ARROW = 32512

    MOD_ALT = 0x0001
    MOD_NOREPEAT = 0x4000

    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004
    NIM_ADD = 0x00000000
    NIM_DELETE = 0x00000002
    NIM_SETVERSION = 0x00000004
    NOTIFYICON_VERSION_4 = 4
    MF_STRING = 0x00000000
    TPM_RETURNCMD = 0x0100
    TPM_RIGHTBUTTON = 0x0002
    HWND_MESSAGE = ctypes.c_void_p(-3)

    def _makeintresource(value: int) -> wintypes.LPCWSTR:
        """MAKEINTRESOURCE-style pointer for LoadIcon/LoadCursor."""
        return ctypes.cast(ctypes.c_void_p(value), wintypes.LPCWSTR)

    # declarations
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    user32.LoadIconW.restype = wintypes.HICON
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.LoadCursorW.restype = wintypes.HANDLE
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = wintypes.LPARAM
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DefWindowProcW.restype = wintypes.LPARAM
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostQuitMessage.restype = None
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.CreatePopupMenu.restype = wintypes.HANDLE
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.AppendMenuW.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.TrackPopupMenu.restype = wintypes.UINT
    user32.TrackPopupMenu.argtypes = [
        wintypes.HANDLE, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.LPVOID,
    ]
    user32.DestroyMenu.restype = wintypes.BOOL
    user32.DestroyMenu.argtypes = [wintypes.HANDLE]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
else:
    user32 = None
    kernel32 = None
    WNDPROC = None


class StopControlError(RuntimeError):
    pass


class StopControls:
    """Hidden window hosting a STOP hotkey and a tray STOP control.

    Both write the same deterministic sentinel through ``EmergencyStop``.
    ``start()`` launches the window thread; ``close()`` tears it down.
    """

    def __init__(
        self,
        stop_path: str | Path,
        *,
        hotkey: int = 0x7B,  # VK_F12
        hotkey_modifiers: int | None = None,
        tray: bool = True,
    ) -> None:
        if sys.platform != "win32":
            raise NotImplementedError("STOP controls are Windows-only")
        self.estop = EmergencyStop(stop_path)
        self.hotkey = hotkey
        self.hotkey_modifiers = MOD_ALT | MOD_NOREPEAT if hotkey_modifiers is None else hotkey_modifiers
        self.enable_tray = tray
        self._thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._ready = threading.Event()
        self._thread_error: BaseException | None = None
        self._window_class: str | None = None
        self._hinstance = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread_error = None
        self._thread = threading.Thread(target=self._run, name="jarvis-stop-controls", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise StopControlError("STOP control window did not become ready")
        if self._thread_error is not None:
            raise StopControlError(f"STOP control failed to start: {self._thread_error}") from self._thread_error

    def close(self) -> None:
        if self._thread is None or self._hwnd is None:
            return
        user32.PostMessageW(self._hwnd, WM_APP_CLOSE, 0, 0)
        self._thread.join(timeout=10)
        self._thread = None
        self._hwnd = None

    def __enter__(self) -> "StopControls":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- window thread -----------------------------------------------------

    def _run(self) -> None:
        try:
            self._run_window()
        except BaseException as error:  # propagate thread failures to start()
            self._thread_error = error
            self._ready.set()

    def _run_window(self) -> None:
        hinstance = kernel32.GetModuleHandleW(None)
        self._hinstance = hinstance
        class_name = f"JarvisStopControl{threading.get_ident()}"

        wndproc = WNDPROC(self._wndproc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = wndproc
        wc.hInstance = hinstance
        wc.lpszClassName = class_name
        wc.hCursor = user32.LoadCursorW(None, _makeintresource(IDC_ARROW))
        wc.hIcon = user32.LoadIconW(None, _makeintresource(IDI_APPLICATION))
        wc.hIconSm = user32.LoadIconW(None, _makeintresource(IDI_APPLICATION))
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise StopControlError(f"RegisterClassExW failed (error {ctypes.get_last_error()})")

        hwnd = user32.CreateWindowExW(
            0, class_name, "JARVIS Stop Control", 0,
            0, 0, 0, 0, HWND_MESSAGE, None, hinstance, None,
        )
        if not hwnd:
            user32.UnregisterClassW(class_name, hinstance)
            raise StopControlError(f"CreateWindowExW failed (error {ctypes.get_last_error()})")
        self._window_class = class_name
        self._hwnd = hwnd

        if not user32.RegisterHotKey(hwnd, ID_HOTKEY_STOP, self.hotkey_modifiers, self.hotkey):
            user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(class_name, hinstance)
            raise StopControlError(f"RegisterHotKey failed, key in use (error {ctypes.get_last_error()})")

        if self.enable_tray:
            icon = NOTIFYICONDATAW()
            icon.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            icon.hWnd = hwnd
            icon.uID = 1
            icon.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            icon.uCallbackMessage = WM_APP_TRAY
            icon.hIcon = user32.LoadIconW(None, _makeintresource(IDI_APPLICATION))
            icon.szTip = "JARVIS Foundry — STOP control"
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(icon)):
                user32.UnregisterHotKey(hwnd, ID_HOTKEY_STOP)
                user32.DestroyWindow(hwnd)
                user32.UnregisterClassW(class_name, hinstance)
                raise StopControlError(f"Shell_NotifyIconW failed (error {ctypes.get_last_error()})")
            version = wintypes.DWORD(NOTIFYICON_VERSION_4)
            shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(icon))

        self._ready.set()

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    # -- window procedure --------------------------------------------------

    def _wndproc(self, hwnd, msg, wparam, lparam) -> int:
        if msg == WM_HOTKEY and wparam == ID_HOTKEY_STOP:
            self.estop.activate("emergency hotkey pressed", source="hotkey")
            return 0
        if msg == WM_COMMAND:
            self._on_command(wparam & 0xFFFF)
            return 0
        if msg == WM_APP_TRAY:
            if lparam & 0xFFFF in (WM_RBUTTONUP, WM_CONTEXTMENU, WM_LBUTTONUP):
                self._show_tray_menu(hwnd, lparam)
            return 0
        if msg == WM_APP_CLOSE:
            self._destroy(hwnd)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_tray_menu(self, hwnd, lparam) -> None:
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, ID_TRAY_STOP, "Stop JARVIS")
        user32.AppendMenuW(menu, MF_STRING, ID_TRAY_RESUME, "Resume JARVIS")
        user32.AppendMenuW(menu, MF_STRING, ID_TRAY_EXIT, "Exit control")
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        command = user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, hwnd, None
        )
        user32.DestroyMenu(menu)
        if command:
            self._on_command(command)

    def _on_command(self, command: int) -> None:
        if command == ID_TRAY_STOP:
            self.estop.activate("tray STOP selected", source="tray")
        elif command == ID_TRAY_RESUME:
            self.estop.clear()
        elif command == ID_TRAY_EXIT:
            self._destroy(self._hwnd)

    def _destroy(self, hwnd) -> None:
        if self.enable_tray:
            icon = NOTIFYICONDATAW()
            icon.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            icon.hWnd = hwnd
            icon.uID = 1
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(icon))
        if hwnd:
            user32.UnregisterHotKey(hwnd, ID_HOTKEY_STOP)
            user32.DestroyWindow(hwnd)
        if self._window_class and self._hinstance:
            user32.UnregisterClassW(self._window_class, self._hinstance)
        user32.PostQuitMessage(0)
