# LANE 2 MASTER PROMPT — V14 Windows Desktop Operator (finish V13 UIA)

**Paste this entire file as the first message in a new Grok chat.**  
**Preferred model: Grok. Fallback: OpenCode.**

---

You are a principal JARVIS engineer. Tonight you **build a lot** of real Windows operator capability. JARVIS is a trusted personal system on this owner’s Windows PC. Do not turn the lane into a security review, an RPA platform pitch, or a giant fake-backend unit-test farm. Make it **drive a real Windows app**, then more apps.

Keep secrets out of git. Do not touch live JARVIS home `C:\Users\b\AppData\Local\hermes\foundry`. Do not STOP/activate the Supervisor. Isolated worktree only.

If a slice works, immediately do the next one. Do not wait.

## Mission

V13 shipped `WindowsOperatorProvider` + Fabric `desktop.*` verbs. Tests mostly **fake** the UIA backend. That is why V14 still feels unstarted: the owner cannot say “open this file in Notepad and prove the text is there.”

You will make desktop operation **real** for at least Notepad (and WordPad or Excel if present): launch, identify the window, type or open a file, read the control back, handle a file dialog, recover if the window dies.

## Current truth

- Base + live: `4b2ae28da582b369982661c2a1d2b1792e2f48cb`
- Worktree:

```powershell
New-Item -ItemType Directory -Force -Path C:\temp\JARVIS | Out-Null
cd "C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry"
git fetch origin
git worktree add -b candidate/lane-2-v14-windows-operator-20260915 "C:\temp\JARVIS\lane-2-v14-windows-operator-20260915" 4b2ae28da582b369982661c2a1d2b1792e2f48cb
cd "C:\temp\JARVIS\lane-2-v14-windows-operator-20260915"
```

## What already exists — extend it, do not rewrite

Primary files:

- `src/jarvis_foundry/connectors/windows_operator.py`
  - `ProcessIdentity` (pid, create_time, executable) — keep this, it is the identity spine
  - `WindowSelector` / `WindowIdentity` / `ControlSelector`
  - `WindowsOperatorProvider` with real `_UIAutomationBackend` when `uiautomation` is installed
  - `ActionReceipt`
- `src/jarvis_foundry/fabric/computer_operator.py`
  - `computer_operator_specs()`, `dispatch_computer_operator()`, `verify_computer_operator()`
  - verbs: `local.process.launch|inspect|close`, `desktop.open|focus|close`, `desktop.window.observe`, `desktop.control.inspect|click|replace_text|type|select|wait|assert`, `desktop.action.reconcile`
- `src/jarvis_foundry/connectors/personal_operator.py` — process launch/identity
- `src/jarvis_foundry/fabric/personal_operator.py` — older desktop.open/focus/close; computer_operator now owns the rich verbs. Do not fork a third operator.

OpenAdapt under `procedural_learning/` is **lab substrate**. Do not make it the product operator.

Stay out of Playwright, GitHub write, voice, Command Center, self-expansion.

## Product you must ship tonight

A script the owner could run:

```text
write C:\temp\JARVIS\lane-2-...\var\hello.txt
launch notepad
open that file (or type the contents)
assert the Edit control contains the text
close notepad
```

Then the same idea with a **file dialog** (Notepad Open, or a tiny WinForms/tk helper if you must). Then handoff: Notepad stays open, focus another allow-listed window, come back, identity still matches.

## Backlog — keep going

### Slice 1 — Real UIA path is the default on Windows

- `WindowsOperatorProvider._backend()` already loads `uiautomation`. Make dispatch actually get there in a demo, not only via injected fakes.
- If `uiautomation` is missing, add it to the worktree/dev extra and use it. This machine is Windows.

### Slice 2 — Open document helper

- New high-level helper on the provider or computer_operator dispatch (prefer extending dispatch payloads, not a new catalog world):
  - `desktop.open` a file with a given executable (`notepad.exe`, `wordpad.exe`)
  - wait until the window title/process identity matches
  - `desktop.control.assert` on the document surface
- Reuse `ProcessIdentity` fail when PID is reused with a different create_time. That already exists — use it, don’t rebuild.

### Slice 3 — File dialog

- After Notepad is up: File → Open, set filename, confirm.
- Receipt must include the path that landed, not “clicked something.”
- UIA names/automation ids will be English on this machine; bind them explicitly, with a small alias table if the OS locale differs. Do not build a computer-vision RPA.

### Slice 4 — Type + readback

- `desktop.control.replace_text` / `type` then `inspect`/`assert` the same control.
- This is the V14 close-scenario piece: later a DOCX/XLSX is created by General Work (`artifact.docx.compose` / `artifact.xlsx.create` in `fabric/general_work.py`). Your job is **open that artifact and prove the window**.

### Slice 5 — App died / dialog popped / STOP

- If Notepad is killed mid-action, return a clear FROZEN/FAILED receipt with identity mismatch — the code already raises `ProcessIdentityMismatch`. Surface it cleanly through `dispatch_computer_operator`.
- Unexpected dialog: `_UnexpectedDialog` already exists. Make a real path: dismiss or report, don’t hang.

### Slice 6 — Multi-app handoff

- Launch Notepad + Calculator (or a second Notepad). Focus A, focus B, focus A. Identities must not swap.
- This is composition fuel for Lane 4. Export a tiny Python helper `open_artifact_and_assert(path, exe)` that Lane 4 can call conceptually (same payloads).

### Slice 7 — If still going

- Excel/WordPad if installed.
- Clipboard paste into the asserted control.
- `scripts/lane2_windows_operator_demo.py` that the owner can run after you.

## How to work

- Prefer adding methods next to `WindowsOperatorProvider` and payload branches in `dispatch_computer_operator`.
- Do not create `DesktopAgent3000`.
- Do not spray the owner’s running Outlook/Chrome. Only processes **you launched** from the worktree demo folder.
- A couple of tests that lock identity-mismatch and the helper API: fine. Do not rewrite `test_v13_windows_operator.py` into a novel.
- Policy-override refusal in `dispatch_computer_operator` (`policy_override_refused`) — leave it. Don’t add more policy.

## Demo to leave

`var/lane2-demo/` with `hello.txt`, `demo.py`, `DEMO.md` (what you actually ran on this PC).

## Final report

```text
VERDICT:
BASE: 4b2ae28da582b369982661c2a1d2b1792e2f48cb
FINAL:
BRANCH: candidate/lane-2-v14-windows-operator-20260915
WORKTREE: C:\temp\JARVIS\lane-2-v14-windows-operator-20260915
BUILT:
REAL APP PROOFS (Notepad etc.):
STILL STUBBY:
LIVE JARVIS: not touched
NEXT:
```
