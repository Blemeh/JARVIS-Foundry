# Lane 2 demo

Real Windows UI Automation against this PC. Isolated worktree only. Live JARVIS was not touched.

## What it does

`demo.py` uses `PersonalOperatorConnector` plus `WindowsOperatorProvider` / `open_artifact_and_assert`.

1. Writes `hello.txt` and a unique per-run copy (Win11 Notepad restores dirty tabs).
2. Launches Notepad through the allowlisted `C:\Windows\System32\notepad.exe` alias.
3. Binds the window-owning process identity, not the alias stub PID.
4. Reads the `DocumentControl` named `Text editor` and asserts the text hash.
5. `replace_text` via Value pattern, then `type` `!` (caret is moved to the end after SetValue).
6. Clipboard `paste` replaces the bound document (`Ctrl+A` then `Ctrl+V`).
7. File Open via named UIA (`File` MenuItem, `Open` MenuFlyoutItem, `#32770` dialog, File name Edit `1148`, Open `SplitButtonControl` `1`).
8. Asserts the landed document hash and window title.
9. File Save As via fabric `desktop.file_dialog`. Keyboard-pastes the full path (COMDLG32 SetValue does not commit). Asserts title and file exist.
10. Opens Word on a lane-owned DOCX, inspects the `OpusApp` frame, and replaces `_WwG` document text (keyboard paste fallback).
11. Opens Excel with `/x` and a unique xlsx, inspects `XLMAIN`, records a cell value hash, and selects `A1`.
12. Focuses Word, then Notepad. PIDs must stay distinct and stable.
13. Kills the demo Notepad PID and proves `ProcessIdentityMismatch`.
14. Launches a separate Explorer window for the demo folder and selects `hello`.
15. Clicks Calculator `One` and closes via `Close Calculator` without killing ApplicationFrameHost.
16. Closes every process this demo registered.

## Latest real run

See `DEMO_RESULT.json`.

Proven on this PC:

- Notepad `open_artifact_and_assert` with matching `expected_sha256` / `observed_sha256`
- `replace_text`, `type`, and `paste` COMPLETED with value readback
- Native File Open. Landed title `hello-open-*.txt - Notepad` and matching normalized document hash
- Native File Save As via `desktop.file_dialog`. Landed title `hello-save-*.txt - Notepad` and `saved_exists: true`
- Word `OpusApp` unique HWND, title `lane2 [Compatibility Mode] - Word`
- Word `_WwG` replace_text with readback_verified true
- Excel `/x` unique workbook `XLMAIN` plus a UIA cell value containing `JARVIS lane2 excel`
- Excel `A1` select COMPLETED
- Focus Word then back to Notepad (`focused: true`), PIDs stable, not swapped
- Stale identity after kill (`process_identity_unavailable`)
- Explorer bind to `lane2-demo - File Explorer` and select `hello`
- Calculator One click and Close Calculator
- Owner shell Explorer left running

Not proven on this PC:

- Excel cell replace. DataItem paste raises a size-mismatch `#32770`; Formula Bar focus is not verified
- Word/Excel Save As. Ribbon Backstage File Tab is not a classic File menu; Backstage UIA inspect hangs
- Explorer invoke/open of the selected file (would launch an extra app)

## Safety

Operates only on processes launched under `var/lane2-demo`. Refuses to reuse a Notepad instance that already existed. Does not target Outlook, Chrome, or other owner apps.
