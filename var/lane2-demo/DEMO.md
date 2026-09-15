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
9. Tries Save As. Extra undeclared dialogs fail closed (`unexpected_dialog`).
10. Opens Word on a lane-owned DOCX and inspects the `OpusApp` frame.
11. Opens Excel with `/x` and a unique xlsx, inspects `XLMAIN`, and records a cell value hash when UIA exposes it.
12. Focuses Word, then Notepad. PIDs must stay distinct and stable.
13. Kills the demo Notepad PID and proves `ProcessIdentityMismatch`.
14. Launches a separate Explorer process for the demo folder.
15. Closes every process this demo registered.

## Latest real run

See `DEMO_RESULT.json`.

Proven on this PC:

- Notepad `open_artifact_and_assert` with matching `expected_sha256` / `observed_sha256`
- `replace_text`, `type`, and `paste` COMPLETED with value readback
- Native File Open. Landed title `hello-open-*.txt - Notepad` and matching normalized document hash
- Word `OpusApp` unique HWND, title `lane2 [Compatibility Mode] - Word`
- Excel `/x` unique workbook `XLMAIN` plus a UIA cell value containing `JARVIS lane2 excel`
- Focus Word then back to Notepad (`focused: true`), PIDs stable, not swapped
- Stale identity after kill (`process_identity_unavailable`)
- Explorer `/separate` launch and close
- Owner shell Explorer left running

Not proven on this PC:

- Save As. `unexpected_dialog` after the Save As item click (fail closed, not hung)
- Explorer file select/rename inside the demo folder
- Calculator
- Word body text via UIA (frame and pane names are proven; document text is not)

## Safety

Operates only on processes launched under `var/lane2-demo`. Refuses to reuse a Notepad instance that already existed. Does not target Outlook, Chrome, or other owner apps.
