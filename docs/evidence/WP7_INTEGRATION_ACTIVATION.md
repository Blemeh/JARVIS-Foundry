# WP7 Evidence — V1 Integration And Activation

Status: **PASS + ACTIVATED** (gate closed 2026-08-11, continuation run)
Updated: 2026-08-11

## Gate Close Record

- Two verification rounds: deleg_9c19e04a (round 1) FAIL on F5 (uninitialized-
  DB crash) + two hardening observations — all repaired with regression
  tests (F5 self-healing initialize, XML escaping, DEGRADED fail-loud);
  deleg_efb7bafc (round 2) **PASS 7/7** (all defects reproduced fixed, no new
  defects). One further pre-existing CLI bug (bare `jarvis <cmd>` crashed on
  the SUPPRESSed --home) found during activation, fixed with a regression
  test; freeze refreshed (194 tests).
- Executive re-verification: full suite **194 passed** fresh; Hermes estop
  baseline 24 passed; canonical DBs byte-unchanged; zero leftover tasks.
- Frozen snapshot: var/evidence/wp7-gate/ (snapshot sha256
  `c1b29c22fbfe2994ee0c3c33862d3b5afacd3fa7f6c5b3e5c2c581757fb50ee5`).
- **Activation**: GATE_VERDICT.json latch written; `jarvis install --v1` +
  `jarvis activate --v1` + `schtasks /run` — the real V1 Supervisor task
  `JARVIS-Foundry-Supervisor` is installed, enabled, and RUNNING (lease held,
  heartbeats every 30 s, supervisor_state RUNNING).
- Full detail: `docs/evidence/WP7_GATE_REPORT.md`.

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

## Test Inventory (194-test suite; WP7 = 14 new)

- `test_installer.py` (13): full staging lifecycle on the REAL host
  (install → disabled → activated → daemon restart → STOP freeze → resume →
  uninstall, events recorded), idempotent uninstall, existing-task refusal +
  force replace, real-name guard (refused without verdict / FAIL verdict /
  malformed verdict; allowed on PASS), scope-proof inventory diff (host fully
  restored), profile write/remove, task XML contents (absolute paths, hidden,
  logon trigger, restart), uninitialized-DB uninstall (F5), XML escaping
  (`&`-home registers), DEGRADED exit 1.
- `test_cli.py` (1 new): bare commands without `--home` resolve the default
  home instead of crashing (activation-path bug).

## Verification

- Round 1 (deleg_9c19e04a): FAIL on F5 + hardening observations — repaired
  (`subagent-summary-0-20260811_230823_045734.txt`).
- Round 2 (deleg_efb7bafc): **PASS 7/7** — all repaired, no new defects
  (`subagent-summary-0-20260811_231941_931961.txt`).
- Full suite: **194 passed** (fresh reproduction, frozen state).

## Remaining Work

- WP7 gate closed (PASS), V1 Supervisor installed + activated. Next: WP8
  (seven-day acceptance) — select + register the highest-value reversible
  proving project, run the G1-G9 / F1-F15 battery over a seven-day window
  with forced restarts and at least ten useful verified cycles; the final
  acceptance verdict is an owner-gated hold point (trust change + V2 queue).
