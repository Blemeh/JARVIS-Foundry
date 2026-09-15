# Lane 2 demo

Real Windows UI Automation against this PC. Isolated worktree only. Live JARVIS was not touched.

## What it does

`demo.py` uses `PersonalOperatorConnector` plus `WindowsOperatorProvider` / `open_artifact_and_assert`.

1. Writes `hello.txt` and a unique per-run copy (Win11 Notepad restores dirty tabs).
2. Launches Notepad through the allowlisted `C:\Windows\System32\notepad.exe` alias.
3. Binds the **window-owning** process identity, not the alias stub PID.
4. Reads the `DocumentControl` named `Text editor` and asserts the text hash.
5. `replace_text` via Value pattern.
6. Tries File Open via named UIA aliases.
7. Tries `type` and clipboard `paste` into the bound document control.
8. Opens Word on a lane-owned DOCX when present.
9. Focuses Word, then records Notepad/Word PIDs (no identity swap).
10. Kills the demo Notepad PID and proves `ProcessIdentityMismatch`.
11. Launches a separate Explorer process for the demo folder.
12. Closes every process this demo registered.

## Latest real run

See `DEMO_RESULT.json`.

Proven on this PC:

- Notepad `open_artifact_and_assert` with matching `expected_sha256` / `observed_sha256`
- `desktop.control.replace_text`
- Word launch + distinct PID from Notepad
- Stale identity after kill (`process_identity_unavailable`)
- Explorer `/separate` launch and close
- Owner shell Explorer PID left running

Not proven on this PC (code exists, live UIA did not complete):

- Native File Open dialog (Win11 Notepad File flyout did not expose an exact `Open` control)
- `type` / `paste` readback (`timeout_after_possible_effect` after dispatch)
- Word document-tree inspect (window class/title filter missed the live Word surface)
- Focus return to Notepad after Word focus

## Run

From the lane worktree, with `uiautomation==2.0.29` installed:

```text
python var/lane2-demo/demo.py
```

Do not run it against an already-open owner Notepad. The operator refuses single-instance reuse.
