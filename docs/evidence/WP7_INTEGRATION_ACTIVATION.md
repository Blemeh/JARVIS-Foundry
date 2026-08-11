# WP7 Evidence — V1 Integration And Activation

Status: **UNDER VERIFICATION** (independent verifier deleg_9c19e04a in flight)
Updated: 2026-08-11

## Objective (CURRENT_WORK_PACKAGE.yaml)

JARVIS can install itself as a Windows logon scheduled task running the
independent Supervisor (no Hermes dependency for STOP/recovery), expose CLI +
tray/hotkey STOP, and uninstall cleanly — clean install, disabled state,
activation, restart, STOP, resume, and uninstall all reproducible. Real
installation happens only after this gate passes (the START BUILD event
already authorized activation).

## Implementation

| Module | Responsibility |
|--------|----------------|
| `src/jarvis_foundry/installer.py` | `Installer`: Windows scheduled-task lifecycle via schtasks (XML: logon trigger, hidden window, restart-on-failure PT1M×3, `MultipleInstancesPolicy=IgnoreNew`, absolute python path, explicit absolute `--home` argument), Foundry profile (`foundry-profile.yaml`: task name, homes, start record, roles), real-name guard (`JARVIS-Foundry-Supervisor` refused until `var/evidence/wp7-gate/GATE_VERDICT.json` says PASS), scope-proof host inventory (before/after task diff — only its own task may appear/disappear), idempotent uninstall, durable `installation.*` events |
| `src/jarvis_foundry/supervisor/run.py` | Headless daemon (`python -m jarvis_foundry.supervisor.run --home …`): initialize → reconcile → start (RUNNING or FROZEN honoring an existing STOP sentinel) → heartbeat loop (reap workers, re-freeze on sentinel) → clean stop releasing the lease; `--once` for a single cycle (tests/gate probes) |
| `src/jarvis_foundry/cli/main.py` | `jarvis install [--task NAME] [--v1] [--force]`, `uninstall`, `activate`, `deactivate`, `status` (now reports installation state), `doctor` |
| `src/jarvis_foundry/paths.py` | `FoundryPaths.hermes_home()` resolution for the profile |

## Task XML (spec §3.3 mapping)

- trigger at owner logon → `<LogonTrigger>`
- restart after failure 1/5/15 backoff → `<RestartOnFailure Interval=PT1M Count=3>`
  (Task Scheduler cannot express escalating intervals; documented deviation)
- hidden window → `<Hidden>true</Hidden>`
- absolute executable path → `<Command>` = absolute python
- explicit deterministic environment → absolute `--home` argument (the XML
  schema has no EnvironmentVariables node; HERMES_HOME recorded in the
  profile; the daemon never touches the live Hermes home)
- max one running instance → `<MultipleInstancesPolicy>IgnoreNew</…>`
- no Hermes Desktop requirement → daemon is a standalone process

## Safety Invariants

- Real V1 task name is guarded by the gate verdict file; staging names for
  all gate tests; every test cleans up its task in a finally block.
- Scope proof: host task inventory diff — the installer may only add/remove
  its own task name; profile is the single JARVIS-owned file (removed on
  uninstall).
- Uninstall idempotent (absent task is fine); `schtasks /end` before delete.
- Canonical DBs and live Hermes never touched; no V1 installation before the
  gate.

## Test Inventory (190-test suite; WP7 = 10 new)

- `test_installer.py` (10): full staging lifecycle on the REAL host
  (install → disabled → activated → uninstall, events recorded), idempotent
  uninstall, existing-task refusal + force replace, real-name guard (refused
  without verdict / FAIL verdict / malformed verdict; allowed on PASS),
  scope-proof inventory diff (host fully restored after uninstall), profile
  write/remove, task XML contents (absolute paths, hidden, logon trigger,
  restart), headless daemon cycles (start → restart → STOP freeze → resume,
  3 started + 2 frozen events).

## Verification

- Independent verifier deleg_9c19e04a (fresh isolated session, adversarial):
  verdict pending — will be integrated here on completion.
- Full suite: **190 passed** (fresh reproduction, this session).

## Remaining Work

- Integrate verifier report; repair any FAIL findings; re-run suites.
- Freeze wp7-gate bundle; write WP7_GATE_REPORT.md; record WP7 PASS; then
  the activation step: install the real V1 task and enable it (START BUILD
  authorized), update PROJECT_STATE, activate WP8.
