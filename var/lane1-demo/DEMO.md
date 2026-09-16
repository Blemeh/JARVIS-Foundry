# Lane 1 demo — unattended browser operator (2026-09-16)

This demo proves the lane's product path end to end against a **real local
HTTP application** with real cookies, redirects, session expiry, downloads,
and an upload acknowledgement. It is not a mock and it never touches live
JARVIS.

## Run it

```powershell
cd <worktree>
$env:PYTHONPATH="$PWD\src"
python var/lane1-demo/demo.py
```

Expected tail: `DEMO_PASS: 34/34 checks passed`. The run is disposable: it
recreates `var/lane1-demo/work/` and rewrites `var/lane1-demo/receipts/`.

Optional: run the app alone for manual inspection:

```powershell
python var/lane1-demo/app.py --port 8791
```

## What the demo exercises

| # | Check | Authority used |
|---|-------|----------------|
| 1 | Playwright health + local app started | connector health |
| 2 | login, persisted storage state + metadata | connector session |
| 3 | ungranted mutation is `BLOCKED` (`owner_browser_authorization_required`) | Fabric executor |
| 3 | `browser.web.inspect` verified `MATCH`, exposes role/name targets | Fabric read capability |
| 4 | granted unattended submit, application readback verified | Fabric + `BrowserExecutionAuthorization` |
| 5 | download receipt path/size/sha256 re-verified from disk | Fabric + effect journal |
| 6 | upload selected -> acknowledged -> server-side hash match | Fabric + acknowledgement postcondition |
| 7 | session restored after the browser process is closed | persisted storage state |
| 8 | expiry classified `session_expired` (no blind retry), then reauth provider recovers | reauth seam |
| 9 | one-session ordered steps: navigate/authenticate/inspect/fill/submit/readback/download | `session.run_steps` |
| 10 | popup tab observed and switched deterministically | context-level routing + `switch_page` |

## Receipts

`receipts/*.json` are sanitized: no cookies, no storage-state contents, no
credentials, no raw page text. `12-summary.json` lists every check.

`demo-run.log` is the full console transcript of the final run.

## Boundary notes

- The grant (`BrowserExecutionAuthorization`) is owner configuration injected
  into `ExecutorContext`. A storage state proves session state, never
  authorization; without the grant every mutation is refused with
  `owner_browser_authorization_required`.
- The demo app is a toy: no passwords, localhost only, process-memory state.
  Toy login is deliberate; the product reauth seam is a callable provider, not
  credentials in a mission payload.
- `run_steps` is a connector-level convenience over the existing single-action
  methods; Fabric composition still uses one journaled action per step
  (`run_workflow`), and this lane added no second orchestrator.
