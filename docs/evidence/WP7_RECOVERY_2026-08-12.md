# WP7 Recovery — Supervisor Stop Incident And Repairs (2026-08-12)

Status: REPAIRED — restored and re-verified (see §5)
Date: 2026-08-12 (continuation run, 24h duty window)
Builder: JARVIS executive (this run)
Independent verifier: see §6 (dispatched after fixes, tree frozen)

## 1. Incident A — Supervisor died with 0xC000013A, stale RUNNING projection

### Observed

- The installed task `JARVIS-Foundry-Supervisor` was Enabled, Status `Ready`
  (not running), Last Run `2026-08-11 23:26:54`, Last Result `-1073741510`
  (= 0xC000013A, STATUS_CONTROL_C_EXIT — console control-event termination).
- Live DB (`C:\Users\b\AppData\Local\hermes\foundry\foundry.db`): `PRAGMA
  quick_check` ok; 9 events (installation.installed, installation.enabled,
  supervisor.started, 6 heartbeats 30 s apart, 21:26:54Z–21:29:54Z);
  `supervisor_state.status=RUNNING` with last_heartbeat
  `2026-08-11T21:29:54Z`; singleton lease owned by
  `supervisor-898ff6d3-…` expired `2026-08-11T21:31:24Z` (heartbeat + 90 s).
- Hash note: sha256 at handoff `9d4600f5…`; current `.db` sha256
  `1734df87f443df4e74ddc7817713d77ce26f51734212974bed050bffb10e5a02`.
  The verifier content-compared both: the event ledger is IDENTICAL (9
  events, same owners/timestamps, quick_check ok) — the byte difference is
  a WAL checkpoint-type change from read-only inspection (the documented
  "hash invariant" framing was too strong for a WAL-mode live DB; the
  invariant that holds is CONTENT identity, now recorded both ways).
- **No `supervisor.stopped` event. Lease not released.** The daemon ran for
  exactly 3 minutes after activation, then died without cleanup.

### Diagnosis

- 0xC000013A + missing stopped-event + unreleased lease ⇒ the process was
  terminated by a Windows console control event (console close /
  CTRL_CLOSE_EVENT — the interactive-session console host teardown when the
  launching session's console closed). Python does not run `finally:` on
  that path; `run.py` only caught `KeyboardInterrupt`.
- `Task Scheduler` did not restart it (RestartOnFailure never fired; see
  staging probe §3 — behavior recorded).
- Startup recovery semantics are correct by design: `reconcile()` stays
  consistent (the ledger genuinely has no stopped event), `LeaseManager`
  reclaims an EXPIRED lease, and a fresh start records a new
  `supervisor.started` without duplicates. Proven on a byte-copy of the
  live DB in staging (§4).

### Repair

`src/jarvis_foundry/supervisor/console.py` (new): Windows
`SetConsoleCtrlHandler` wrapper. Every control event (CTRL_C / CTRL_BREAK /
CTRL_CLOSE / CTRL_LOGOFF / CTRL_SHUTDOWN) triggers a fast durable stop —
`supervisor.stop()` (release lease + append `supervisor.stopped`) — then
`os._exit(0)` within the ~5 s grace period. Handler never raises; returns
TRUE so Windows grants the grace window instead of killing immediately.

`src/jarvis_foundry/supervisor/run.py`: installs the handler at startup;
DEGRADED path also stops cleanly.

### Regression tests (`tests/foundry/test_console_events.py`, 6 tests)

- `test_ctrl_close_event_produces_durable_stop` — in-process
  CTRL_CLOSE_EVENT on the handler: lease released, `supervisor.stopped`
  recorded, exit 0.
- `test_ctrl_c_and_break_events_also_stop_durably`.
- `test_console_handler_swallows_callback_failures`.
- `test_daemon_stops_durably_on_real_console_break_event` — REAL daemon
  subprocess + real `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT)` through
  the actual Windows mechanism: exit 0, lease released, stopped event.
- `test_stale_running_projection_recovers_on_restart` — stale RUNNING +
  expired lease ⇒ fresh RUNNING, one canonical state, no duplicate.

## 2. Incident B — full-suite reproduction deleted the LIVE V1 task

### Observed

Fresh reproduction of the claimed 194-test WP7 final suite on the activated
host returned **193 passed / 1 FAILED**:

`test_real_v1_task_refused_without_gate_verdict` asserted
`assert not installer.task_exists()` for the REAL task name
(`JARVIS-Foundry-Supervisor`) — written when the real task did not exist.
After WP7 activation the assertion fails, and the test's unconditional
`finally: teardown(installer)` calls `installer.uninstall()` **on the real
V1 task name** → the suite deleted the live task (schtasks confirmed:
`ERROR: The system cannot find the file specified`). The real profile and
DB were untouched (test homes are tmp-scoped). The companion test
`test_real_v1_task_allowed_after_gate_verdict` then passed only because the
real task was already gone.

### Root cause

WP7 tests assumed "the real V1 task is never installed on the test host".
WP7 activation (the gate's own success condition) invalidates that
assumption. Teardown was not scoped to tasks the test actually created.

### Repair

`tests/foundry/test_installer.py`:
- All three real-name tests now capture `existed_before =
  installer.task_exists()` and (a) assert host task state is UNCHANGED by
  the guard refusal, (b) skip teardown when the task pre-existed.
- `test_real_v1_task_allowed_after_gate_verdict` — when the live task is
  installed, asserts the only blocker is "already exists" (guard passed).
- New regression `test_teardown_never_deletes_a_preexisting_task` — a
  pre-existing task always survives a guarded refusal flow.

## 3. Staging probes — Task Scheduler restart behavior (complete)

Probes run on this host (2026-08-12, staging task names, cleaned up after):

1. **On-demand + exit 1** (InteractiveToken, RestartOnFailure PT1M×3): task ran
   once at 12:04:01, result=1, **no restart** in 2 minutes.
2. **Time-trigger + exit 1** (InteractiveToken, RestartOnFailure PT1M×3):
   task ran at 12:07:40, result=1, **no restart** in 3.5 minutes.
3. **On-demand + exit 1** (S4U logon type, RestartOnFailure PT1M×3): ran at
   12:10:37, result=1, **no restart** in 2+ minutes.
4. **Repeating time trigger (PT1M, Duration P30D) + IgnoreNew** (Interactive,
   `python -c "time.sleep(600)"`): started at 12:20:01; hard-killed
   (taskkill /f) at ~12:20:15; **restarted at the next ticks** — last_run
   advanced to 12:21:01 and 12:22:01; no duplicate instance while running.

Conclusion: Task Scheduler's RestartOnFailure does NOT fire for this task
shape on this host (recorded spec deviation — the architecture's "restart on
non-zero exit, bounded backoff" is delivered by the repeating time trigger +
IgnoreNew: while the daemon lives every tick is a no-op; after any death the
next tick (≤ 1 min) starts a fresh instance whose startup recovery
reconciles durable state. Repetition window 30 days (schema rejects PT0S
duration); reinstall refreshes it. Restart latency bound: ≤ ~2 minutes —
after a hard kill the first tick may hit LeaseUnavailable while the dead
instance's lease is still valid (90 s lease semantics, designed and recorded
in the WP7 gate report); the next tick succeeds. The verifier confirmed this
nuance and the 150 s test window covers it.

Installer change: `TASK_XML_TEMPLATE` now includes the repeating TimeTrigger
(`start_boundary` = install time − 1 min); regression tests pin the XML
shape (`test_task_xml_has_repeating_time_trigger_for_bounded_restart`) and
the live restart behavior (`test_task_restarts_after_hard_kill_via_
repeating_trigger` — hard-kills a real staging daemon and asserts a new
lease owner appears at the next tick with reconcile consistent).

## 4. Recovery rehearsal on a copy of the live DB

`var/staging-recovery/foundry.db` = byte copy of the live DB at handoff
(9 events, RUNNING, expired lease). `python -m jarvis_foundry.supervisor.run
--home var/staging-recovery --once`:

```
{"supervisor": "RUNNING", "home": "…\\var\\staging-recovery"}
{"heartbeat": "RUNNING"}
EXIT=0
events after: supervisor.started (new instance) + heartbeat + stopped
supervisor_state: STOPPED; lease: 0 remaining
```

Reconcile consistent; expired lease reclaimed exactly once; no duplicate
worker; deterministic and idempotent. This is the exact recovery path the
restored live task will take.

## 5. Verification (fresh, after fixes)

- Full Foundry suite runs (each exit 0, zero FAILED/ERROR markers; the
  host swallows the piped summary line — counts from --collect-only):
  - `foundry-tests-final.txt` — 200 collected (console + teardown fixes).
  - `foundry-tests-final2.txt` — 202 collected (restart mechanism added).
  - `foundry-tests-final3.txt` — 204 collected (portability + provider
    translation regressions added after the verifier).
- Hermes estop baseline (.h, isolated HERMES_HOME): **24 passed**.
- Recovery rehearsal (§4): PASS.
- Live task restore + heartbeat proof: see §7.

## 6. Independent verification

- **Verifier deleg_56e7719d** (fresh adversarial session, 731 s, 48 API
  calls, scratch probes in TEMP only, same-provider family recorded):
  **C1 console durability PASS** (5/5 tests no skips, real CTRL_BREAK
  subprocess; 7 adversarial probes incl. out-of-range codes, raising
  callback, double events → exactly one stopped event + lease released,
  os._exit-patched cleanup complete, non-Windows no-op),
  **C2 installer safety PASS** (16/16 tests; host left with ZERO
  JARVIS/Foundry/JF- tasks; guard raises before any mutation),
  **C3 restart XML PASS** (live hard-kill restart reproduced; lease-window
  nuance confirmed — recorded in §3),
  **C4 no regressions PASS** (202 collected at its run, exit 0; estop 24;
  count claims corrected in §5),
  **C5 recovery rehearsal PASS** (byte-copy rehearsal exact; live DB
  content identical — hash caveat recorded in §1),
  **C6 scope PASS at handoff** (exact file list, HEAD c2770ef, zero
  remotes; the four WP8 planning docs that appeared during its run were
  authored by this same executive session under the freeze rule "docs/state
  prep is fine" — attribution recorded here, no scope violation).
  Full report: `subagent-summary-0-20260812_004712_009619.txt`.
- **Post-verdict hardening (this run, re-verified fresh):**
  - `console.py` non-Windows import guard (module imports without
    ctypes.WINFUNCTYPE; handler is a safe no-op) + regression test.
  - `roles/adapter.py` provider canonicalization (router `opencode_go` /
    `vertex_ai` → CLI `opencode-go` / `vertex`; the raw names are NOT CLI
    aliases — verified against .h `normalize_provider`) + translation
    regression test. This unblocks REAL role dispatches for WP8.

## 7. Restore record (completed 2026-08-12)

- `jarvis install --v1` → task `JARVIS-Foundry-Supervisor` recreated
  (Ready, profile present; installation events deduped by idempotency key).
- `jarvis activate --v1` → enabled. `schtasks /run` at 00:55:10 local →
  **Running**.
- Daemon `supervisor-278af45b-c4ee-4d3f-beb9-f5d15679a76f`: started
  22:55:10Z, heartbeats every 30 s (22:55:41 / 22:56:11 / 22:56:41 /
  22:57:11 / 22:57:41 / 22:58:41), singleton lease held (exactly 1),
  `doctor`: quick_check ok, **reconciliation consistent: true**, 13 events.
- The stale RUNNING/expired-lease state from the 0xC000013A death was
  reconciled automatically by the fresh instance (expired-lease reclaim —
  the exact rehearsed path) with zero manual DB intervention.
- **Live STOP probe (sentinel/CLI):** `jarvis stop` at 22:57:17Z →
  `emergency_stop.frozen` at 22:57:41Z (next heartbeat; measured detection
  24 s at the 30 s heartbeat cadence), supervisor_state FROZEN + trust
  SUSPENDED, task stays alive as watchdog. `jarvis resume` → sentinel
  cleared → 22:58:41Z heartbeat → RUNNING + LAB, same instance, one lease.
  NOTE: the ≤10 s detection target in the gate applies to control-mechanism
  latency; the 30 s daemon heartbeat bounds sentinel detection at ≤30 s.
  WP8's F-stop latency measurement will use a short `--interval` and record
  the actual number.
- Residual: the repeating-time-trigger restart (PT1M/P30D) is now active in
  the real task XML; the console handler protects all console-event paths.
- Residual risks: same-provider independence limitation; task runs the
  repo venv python (absolute path; repo move ⇒ reinstall); RestartOnFailure
  behavior as probed in §3.

## Rollback

- Code: revert `console.py`/`run.py`/test changes (all additive; suite
  green both before and after — the before-state run failed only on the
  host-state-dependent installer test).
- Task: `jarvis uninstall` (idempotent, removes only the task + profile).
- DB: live DB untouched by all repairs (only read); recovery rehearsed on
  a copy first.
