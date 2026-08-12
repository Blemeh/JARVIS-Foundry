# JARVIS Foundry — FULL CONTINUATION HANDOFF (F4 restart) 2026-08-12T03:15+02:00

Author: JARVIS executive continuation run (24h duty window)
Purpose: paste-ready continuation prompt. This handoff is written BEFORE the
F4 Windows-restart test (the reboot ends this session).

---

## ═══ PASTE-READY CONTINUATION PROMPT (for the next executive session) ═══

You are the executive continuation agent for JARVIS Foundry (WP8 seven-day
acceptance gate, day 1). A prior executive session executed the F4
Windows-restart fault test: it rebooted this machine at ~2026-08-12 03:15
local after preparing the before-state. YOUR FIRST JOB: verify the restart
recovery, then continue the gate.

MANDATORY READ ORDER (canonical): docs/governance/AUTONOMY_MANDATE.md,
docs/architecture/00_READ_FIRST.md, 01-05, IDEA.md, PROJECT_STATE.md,
AGENTS.md, docs/evidence/WP8_ENTRY_PRECONDITIONS.md,
docs/evidence/WP8_G6_FAILURE_INJECTION.md, docs/handoffs/CHECKPOINT_2026-08-12T0225.md,
var/evidence/wp8-gate/f4-restart/F4_BEFORE_STATE.json, this file.

STEP 1 — F4 RESTART RECOVERY VERIFICATION (the test outcome):
1. `schtasks /query /fo LIST /v /tn JARVIS-Foundry-Supervisor` (via
   powershell -File var/evidence/task_state2.ps1) — task must be enabled
   and its state Ready/Running.
2. `powershell -File var/evidence/list_python_procs.ps1 | grep -i foundry`
   — the daemon must be running WITHOUT any Hermes/gateway dependency.
3. Ledger check: `.venv/Scripts/python.exe -c` query on
   C:\Users\b\AppData\Local\hermes\foundry\foundry.db — expect a NEW
   supervisor.started (new instance id) + heartbeats at 30s cadence +
   exactly ONE lease + reconcile consistent + PRAGMA quick_check ok
   (WAL recovery after the unclean shutdown).
4. Compare against F4_BEFORE_STATE.json (db sha fb557d93… was BEFORE;
   events 284 then — now more; supervisor_state instance id must differ
   from supervisor-7341b577).
5. Record the F4 result: `fault_injection.completed` event
   (test_id F4, verdict PASS if the daemon auto-started and heartbeats;
   FAIL/BLOCKED otherwise with the exact reason). If the logon trigger did
   not fire (no interactive logon after reboot), run
   `schtasks /Run /TN JARVIS-Foundry-Supervisor` once and record that the
   time trigger resumed ticking — the daemon must then start; that is the
   documented rollback path (installer is idempotent if a reinstall is
   ever needed).
6. Update Obsidian 07 - Daily State + 02 - Active Mission (governed
   writes via the ObsidianService, FRONTMATTER contract, expected_hash
   from OBSIDIAN_V1_INITIAL_SNAPSHOT.json pattern).

STEP 2 — CONTINUE THE GATE from the 6 verified cycles:
- Closed cycles: 1 (foundation, 893eec8), 2 (scheduler core, 508ca5f),
  3 (G6 injection→FAIL→repair→PASS, 5b01665), F1 (live kill→auto-restart,
  PASS), 5 (daemon loop resilience, Foundry 751ef83), 6 (F14/F15
  hardening, a5d3f4c). Mission suite: 35 tests. Foundry suite: 215 tests
  (204+9+2 obs regressions... verify: `pytest tests/foundry -q`).
- Ledger: ~342 events, reconcile consistent. Live daemon BEFORE reboot:
  supervisor-7341b577 (repaired loop code).
- Next cycles (in order):
  a) F2-class: kill a builder mid-step (mission loop shape) — adapt to
     the actual loop; record + verify step state recovery.
  b) F3: staged gateway kill — the dispatch path uses CLI subprocesses
     (gateway-independent); prove with a STAGED gateway, NEVER the live
     one (killing the live gateway kills this session).
  c) Step-7: mission README/usage documentation + AC5 CLI contract doc
     (the remaining AC5 surface) + determinism/seed notes.
  d) Continue ≥10 verified cycles total; then full reconciliation,
     freeze evidence bundle (var/evidence/wp8-gate/), independent final
     review (fresh verifier on the WHOLE gate record), gate report.
- The step-6 verifier flagged ONE adjacent pre-existing out-of-scope leak
  vector (detail in its report; likely error messages echoing the roster
  PATH — read deleg_59821c9a's transcript
  C:\Users\b\AppData\Local\hermes\cache\delegation\live\deleg_59821c9a\task-0.log
  for the exact wording) — assess and record whether it needs a fix.
- Daemon/kill discipline: the venv python.exe is a launcher — its child
  (base interpreter) and the launcher are ONE daemon (parent-child pair);
  a tree kill (taskkill /T) may leave the Task Scheduler instance state
  stale ("Running") — clear with Stop-ScheduledTask, next tick restarts.
  LeaseUnavailable on a live lease is correct backoff (exit 1), not a bug.
- Checkpoint every 60-90 min; write a fresh handoff before ~120k tokens.

OWNER INPUT REQUIRED: NONE (authenticated START BUILD mandate; veto/STOP
sovereign at any moment).

═══ END PASTE-READY PROMPT ═══

## Before-State Summary (recorded in F4_BEFORE_STATE.json)

- Task: JARVIS-Foundry-Supervisor enabled; LogonTrigger + TimeTrigger
  (PT1M/P30D, IgnoreNew); task XML exported to
  var/evidence/wp8-gate/f4-restart/task-export.xml.
- DB: foundry.db sha256 fb557d93cd43de88 (284 events at prep; 342 at
  handoff); WAL files present; quick_check ok throughout.
- Daemon: supervisor-7341b577, RUNNING, heartbeats 30s, one lease.
- Foundry repo HEAD: 751ef83 (clean). Mission repo HEAD: a5d3f4c (clean).
- Rollback: triggers auto-restart; manual: schtasks /Run; reinstall:
  `./.venv/Scripts/python.exe -m jarvis_foundry.cli.main install --v1`.

## Verification Commands (post-reboot)

```bash
cd "C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry"
powershell -NoProfile -ExecutionPolicy Bypass -File var/evidence/task_state2.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File var/evidence/list_python_procs.ps1 | grep -i foundry
./.venv/Scripts/python.exe -m jarvis_foundry.cli.main doctor
./.venv/Scripts/python.exe -c "import sqlite3; con=sqlite3.connect(r'C:\Users\b\AppData\Local\hermes\foundry\foundry.db'); print(con.execute('SELECT instance_id, status, last_heartbeat_at FROM supervisor_state').fetchone()); print(con.execute('SELECT owner_id FROM leases').fetchone()); print(con.execute('SELECT COUNT(*) FROM events').fetchone())"
```

## Honesty Rule

The F4 verdict is PASS only if the daemon auto-started after reboot without
manual intervention and heartbeats are flowing. Record exactly what
happened; never backdate. If this machine did NOT reboot (session survived
uninterrupted), record F4 as NOT_TESTED with the reason and re-plan.
