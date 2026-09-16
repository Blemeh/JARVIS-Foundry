# Lane 1 orientation — browser/web execution surface (base 4b2ae28)

Facts needed for implementation only. Verified by reading the canonical source
in this worktree.

## Canonical implementation (do not replace, do not revive older paths)

- `src/jarvis_foundry/connectors/web_research.py` (3343 lines) owns the real
  browser: `PlaywrightBrowserConnector` (line 352) and
  `PlaywrightBrowserSession` (line 774). `connectors/browser.py` is the older
  structural `BrowserConnector`; it is a different (non-Playwright) test
  substrate and is not the product path.
- Session lifecycle already exists: `session()`, `__enter__/__exit__`,
  `_prepare_session_state` (restore + binding/hash checks), `_persist_session_state`
  (atomic save, AUTHENTICATED only), `session_status`, `check_session`,
  `session_info`, `ensure_authenticated`, `navigate`, `discover_target`,
  `wait_for_target`, `readback`, `execute`, `reconcile`.
- Actions implemented by `execute`: click, fill, select, check, submit,
  download, upload. Download receipt carries path/size/sha256 + readback.
  Upload requires an explicit acknowledgement postcondition and hash-bound
  browser readback.
- Ambiguity is already classification-first: `target_drift`, `timeout_*`,
  `*_ambiguous`, `stop_after_effect`; `reconcile` re-reads, never repeats.

## Fabric surface today

- `fabric/loop.py`: specs `cap.browser.web.read|wait|reconcile` (ALLOWED,
  READ) and `cap.browser.web.execute` (EXTERNAL_EFFECT,
  `UnattendedMode.INTERACTIVE_REQUIRED`, line 259). `select_capability`
  (line 420) skips INTERACTIVE_REQUIRED when `intent.unattended` -> autonomous
  missions can never select browser mutations (the lane's core gap).
- `fabric/executors.py`: `browser_read` (746), `browser_execute` (815),
  `browser_reconcile` (1110), `browser_wait` (1438); HANDLERS at 2369-2372.
  `browser_execute` journals through `execute_fabric_effect` with
  `kind=browser.web.execute`, `system_id=browser`, `target_ref=url`.
- `fabric/loop.py::_verify_action` verifies each action kind from the claimed
  receipt (download reads the downloaded file back from the owned root;
  upload verifies source bytes + acknowledgement readback).
- Existing owner-authorization pattern to reuse: `gmail.send` and
  `github.pull_request.publish` are catalog-ALLOWED but their executors return
  BLOCKED unless an injected `ExecutorContext` grant
  (`GmailSendAuthorization`, `GitHubRepositoryAuthorization`) is present.

## Existing tests (must keep passing after the grant requirement)

- `tests/foundry/test_v10_a_web_research.py` (8), `test_v10_exit_browser.py` (7),
  `test_v13_browser_download.py` (4), `test_v13_browser_upload_wait.py` (11),
  `test_release_packaging_browser.py` (1), `test_browser_connector.py` (3).
- Fabric execute sites that construct `ExecutorContext` directly:
  test_v10_exit_browser.py:432,590; test_v13_browser_download.py:~174;
  test_v13_browser_upload_wait.py:~387,~454.
- Tests skip when `connector.health()["available"]` is false; Playwright
  1.62.0 + chromium are present on this host.

## Real gaps this lane closes

1. Unattended eligibility: flip `cap.browser.web.execute` to ALLOWED behind an
   exact owner grant (`BrowserExecutionAuthorization`) injected in
   `ExecutorContext`; storage state is session proof, never authorization.
2. Target discovery: no read path exposes the bounded accessibility structure,
   so a mission cannot pick role/name for fill/click -> add read-only
   `browser.web.inspect`.
3. Reauth seam: connector supports `reauthenticate`, but Fabric cannot supply
   it (payload callables are not a product API) -> inject via ExecutorContext.
4. One-session multi-step: fabric runs one session per action; add connector
   `run_steps` for ordered bounded steps on one session.
5. Tabs/frames: absent (`new_page` only at session open) -> add deterministic
   tab wait/select and frame targeting if time remains.

## Command used for focused proof

```powershell
$env:PYTHONPATH="C:\temp\JARVIS\max-lane-1-v14-browser-20260915\src"
python -m pytest -q tests/foundry/<file>
```

PYTHONPATH is required: the host Python has an editable install of a sibling
lane worktree.
