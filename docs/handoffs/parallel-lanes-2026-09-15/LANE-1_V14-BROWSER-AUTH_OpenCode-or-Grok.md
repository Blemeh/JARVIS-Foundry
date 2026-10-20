# LANE 1 MASTER PROMPT — V14 Browser Operator (finish what V13 left unfinished)

**Paste this entire file as the first message in a new OpenCode or Grok chat.**  
**Preferred model: OpenCode. Fallback: Grok.**

---

You are a principal JARVIS engineer. Tonight you **build a lot**. You are not an auditor, not a security reviewer, not a test-count farmer. JARVIS is a **trusted local-first personal system for one owner**. Do not add permission frameworks, public-internet hardening, compliance, or “fail closed” theater. Keep secrets out of git. Do not smash the live Supervisor/DB. Everything else: **ship working operator capability**.

If you finish a slice, immediately start the next slice in this file. Do not stop to ask permission. Do not write a 40-file test suite. One or two proofs that the feature actually runs are enough.

## Mission

V13 already has Playwright. It is **not** a finished Digital Operator.

Make JARVIS actually **use the browser unattended** on the owner’s Windows machine: keep sessions, click, fill, download, upload, recover from expiry, and hand a receipt to Fabric — without the owner sitting in the loop.

## Current truth (do not argue with stale docs)

- Repo: `C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry`
- Base + live CURRENT: `4b2ae28da582b369982661c2a1d2b1792e2f48cb`
- Schema 40. V13 Personal Executive is already installed. You are extending it.
- `docs/CURRENT_STATE.md` still talks about V10. Ignore it.
- Isolated worktree (mandatory so you do not collide with five other lanes):

```powershell
New-Item -ItemType Directory -Force -Path C:\temp\JARVIS | Out-Null
cd "C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry"
git fetch origin
git worktree add -b candidate/lane-1-v14-browser-auth-20260915 "C:\temp\JARVIS\lane-1-v14-browser-auth-20260915" 4b2ae28da582b369982661c2a1d2b1792e2f48cb
cd "C:\temp\JARVIS\lane-1-v14-browser-auth-20260915"
```

Work only in that worktree. Commit on that branch. Do not merge to main. Do not push unless the owner later says so. Do not touch `C:\Users\b\AppData\Local\hermes\foundry`.

## Architecture you must preserve (this is not a rewrite)

One spine: World Model → Executive → Fabric → MissionLoop → effects → independent verifier → continue.

- Connector you own: `src/jarvis_foundry/connectors/web_research.py`
  - `PlaywrightBrowserConnector` (`conn-browser-playwright-v10`)
  - `PlaywrightBrowserSession`
  - `PlaywrightTarget`
  - methods: `health()`, `read()`, `execute()` (click/fill/select/download/upload already sketched)
  - `storage_state` persist/restore already exists — **make it the default working path**, not a museum piece
- Fabric verbs already registered in `src/jarvis_foundry/fabric/loop.py` `seed_builtins`:
  - `browser.web.read`
  - `browser.web.wait`
  - `browser.web.reconcile`
  - `browser.web.execute` — currently `unattended=INTERACTIVE_REQUIRED` so **autonomous missions skip it**. That is the V13 leftover. Fix it for this owner system: when a session/`storage_state` is present, execute must be selectable unattended.
- Dead path. Do not revive: `connectors/browser.py`, V5 `ComposedMissionHarness`, `conn-browser-v5`.

You may edit `web_research.py`, the browser blocks in `fabric/loop.py` / `fabric/executors.py`, and tests/scripts. Stay out of Windows UIA, GitHub write, `voice.py`, Command Center UI, `self_expansion.py` — other lanes own those.

## What “done enough for tonight” looks like

A disposable local HTML app (you create it) plus Playwright that can:

1. Open the page, fill a form, click, read the result.
2. Save `storage_state`, kill the browser, restore the session, still be “logged in” on that toy page.
3. Download a file to a worktree-owned folder and hash it.
4. Upload a file and see an on-page acknowledgement.
5. Survive a fake session expiry (toy page clears cookie / storage) by logging in again from stored credentials **for that toy page only**, then continue.
6. Expose that as Fabric `browser.web.execute` that a later compose lane can call without `INTERACTIVE_REQUIRED` blocking it.

That is product. That is V14-A.

## Backlog — implement in this order, keep going

### Slice 1 — Unattended execute is real

- Find `seed_builtins` cap `cap.browser.web.execute` in `fabric/loop.py`. It is `INTERACTIVE_REQUIRED`. For a trusted personal operator this is wrong once a session exists.
- Change the product behavior: execute is usable unattended when `storage_state` exists or the target is local/owner-configured. Do not invent a new consent service.
- Wire `PlaywrightBrowserConnector.execute` actions (`click`, `fill`, `select`, `download`, `upload`, and any wait/readback already in the session class) so a Fabric payload can drive them end-to-end.
- Inspect `execute()` around line 2268. Download/upload already have destination/readback logic. Finish whatever is stubby, broken, or unreachable from Fabric.

### Slice 2 — Session is durable

- `storage_state` save/load should be the normal path, not an edge case.
- Add `PlaywrightBrowserSession` helpers if missing: `open`, `restore`, `save`, `close`, `expired`.
- After restore, `read()` must show the same logged-in toy page.

### Slice 3 — Multi-step page workflow

- One helper (not a second orchestrator) that runs an ordered list of execute actions on one session: fill → click → wait → read.
- Return one receipt with final URL, title, and extracted text.

### Slice 4 — Download / upload that a mission can use

- Download lands under the worktree or a caller-supplied owned directory, with size + sha256 on the receipt (verifier in `fabric/loop.py` already expects this shape for download).
- Upload takes a real file path and waits for acknowledgement.

### Slice 5 — Expiry recovery

- If the toy app returns a login page, re-auth from a payload `credentials` block for **that local app**, save state, continue. This is owner-internal. Do not build OAuth/IdP.

### Slice 6 — If you still have time

- PDF/print save, new-tab wait, iframe click, basic “page changed under me” re-read.
- A tiny `scripts/lane1_browser_operator_demo.py` that runs slices 1–5 against the toy page with no live JARVIS.

## How to work (anti-drift)

- Read the current functions before writing new ones. Extend `PlaywrightBrowserConnector` / `PlaywrightBrowserSession`. Do not start `BrowserEngine2`.
- Copy existing receipt/evidence field names. The verifier already checks `download.path`, `sha256`, `size_bytes`, `readback_verified`.
- Do not add host-allowlist product features. `allowed_hosts` already exists; do not expand it into a security program. For the demo, `allow_insecure_localhost=True` is fine.
- Do not touch Gmail.
- Do not rewrite MissionLoop.
- Tests: keep or add **a few** that lock the new behavior. Delete nothing that already passes unless it is wrong. Do not spend the night on pytest volume.
- If Playwright browsers are missing, install them in the worktree venv / document the command and still complete the code path.

## Demo you must leave behind

`C:\temp\JARVIS\lane-1-v14-browser-auth-20260915\var\lane1-demo\` with:

- `index.html` toy app (login + download + upload + expiry button)
- `demo.py` that drives it
- `DEMO.md` with what ran and what is still stubby

## Final report

```text
VERDICT:
BASE: 4b2ae28da582b369982661c2a1d2b1792e2f48cb
FINAL:
BRANCH: candidate/lane-1-v14-browser-auth-20260915
WORKTREE: C:\temp\JARVIS\lane-1-v14-browser-auth-20260915
BUILT (list working capabilities, not file counts):
STILL STUBBY:
DEMO:
LIVE JARVIS: not touched
NEXT:
```
