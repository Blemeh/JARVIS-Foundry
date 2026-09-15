# Lane 2 orientation

Probed on this Windows 11 PC before product changes. Canonical source base `4b2ae28`. Isolated worktree only.

## Existing authority (reuse, do not replace)

- `WindowsOperatorProvider` owns exact-identity UIA actions: click, replace_text, type, select, wait, assert.
- `_UIAutomationBackend` is selected lazily on first `_backend()` call: injected backend wins, else `import uiautomation` on `os.name == "nt"`.
- Fabric verbs live in `computer_operator.dispatch_computer_operator`. Process launch/open/focus/close remain `PersonalOperatorConnector`.
- Receipts already redact control string values to `value_sha256` / `value_length`.
- `ProcessIdentityMismatch` re-checks pid + create_time + executable immediately before mutation.
- Unexpected dialogs freeze the result as `UNKNOWN` / `unexpected_dialog` and require reconcile. Do not weaken this.

## Dependency

- Declared: `uiautomation==2.0.29; sys_platform == 'win32'` in `pyproject.toml`.
- Missing from the default interpreter at session start. Installed into `C:\Python313` and import-proven (`ControlFromHandle`, `SendKeys`, `SetClipboardText`).

## Real Notepad on this PC

`C:\Windows\System32\notepad.exe` is an App Execution Alias. `CreateProcess` returns a stub PID whose image is already:

`C:\Program Files\WindowsApps\Microsoft.WindowsNotepad_11.2607.14.0_x64__8wekyb3d8bbwe\Notepad\Notepad.exe`

The visible window is a **later same-exe process**, not the stub, and not a child of the stub. Terminating the stub leaves the window process alive. If a Notepad instance already exists, opening a file **reuses that instance** (title changes, launch PID stays windowless).

Window (application):

- class `Notepad`
- title `{filename} - Notepad`
- no AutomationId
- runtime id `(42, hwnd)`

Noise windows owned by the same PID (must not count as the unique HWND):

- `GDI+ Hook Window Class` (visible)
- `MSCTFIME UI` / `IME`

Document surface:

- `DocumentControl` name `Text editor`, class `RichEditD2DPT`
- Value pattern present. File `JARVIS lane2 hello\n` read back as `JARVIS lane2 hello\r`.

Menus:

- `MenuItemControl` name/automation id `File`, `Edit`, `View`

## Other apps on this PC

- Calculator: `C:\Windows\System32\calc.exe` exists (likely Store alias, treat like Notepad).
- Explorer: `C:\Windows\explorer.exe` (do not bind the shell PID).
- Word: `C:\Program Files (x86)\Microsoft Office\Office16\WINWORD.EXE`
- Excel: `C:\Program Files (x86)\Microsoft Office\Office16\EXCEL.EXE`
- WordPad: not present.

## Product gaps this lane must close

1. Bind the window-owning process at launch, refuse pre-existing single-instance reuse.
2. Enumerate application windows without helper HWND noise.
3. High-level `open_artifact_and_assert` over existing Fabric/provider, not a second catalog.
4. Document-control discovery + newline-normalized readback.
5. Locale aliases as sequential exact names, not vision.
6. Clipboard paste into a bound control only.
7. Real Notepad / multi-app / death / dialog proofs in `var/lane2-demo`.
