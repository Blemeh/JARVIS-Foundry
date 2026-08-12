# JARVIS Foundry — FULL CONTINUATION HANDOFF 2026-08-12T03:50+02:00

Author: JARVIS executive continuation run (24h duty window, ~5h elapsed)
Purpose: paste-ready continuation prompt for the next executive session.
Read this file (and the linked state) FIRST; do not trust this summary over
fresh state — verify each claim with the commands given.

---

## ═══ PASTE-READY CONTINUATION PROMPT ═══

You are the executive continuation agent for JARVIS Foundry (WP8 seven-day
acceptance gate, day 1, ~5h into the first duty window). A prior executive
session completed recovery, started the seven-day clock, and closed 8
verified cycles. YOUR JOB: verify state, then continue the gate.

MANDATORY READ ORDER: docs/governance/AUTONOMY_MANDATE.md,
docs/architecture/00_READ_FIRST.md, 01-05, IDEA.md, PROJECT_STATE.md,
AGENTS.md, docs/evidence/WP8_ENTRY_PRECONDITIONS.md,
docs/evidence/WP8_G6_FAILURE_INJECTION.md,
docs/handoffs/CHECKPOINT_2026-08-12T0335.md,
docs/handoffs/F4_RESTART_HANDOFF_2026-08-12.md (F4 recovery script),
var/evidence/wp8-gate/f4-restart/F4_BEFORE_STATE.json, this file.

STEP 0 — VERIFY STATE (fresh, never trust summaries):
```bash
cd "C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry"
./.venv/Scripts/python.exe -m jarvis_foundry.cli.main doctor   # quick_check ok, consistent True, events ~394
powershell -NoProfile -ExecutionPolicy Bypass -File var/evidence/task_state2.ps1  # task enabled
powershell -NoProfile -ExecutionPolicy Bypass -File var/evidence/list_python_procs.ps1 | grep -i foundry
git log --oneline -3 && git status --short   # expect ba5db8a, clean
cd "C:\Users\b\JARVIS\v1-proving\wfm-optimizer" && git log --oneline -2 && git status --short  # 5eaa6ee, clean
./.venv/Scripts/python.exe -m pytest tests -q --tb=short   # expect 40 passed
```
If the machine REBOOTED since 03:20 local (the F4 test was prepared but
blocked at execution by the Hermes reboot hard-block): run the F4
recovery verification in F4_RESTART_HANDOFF_2026-08-12.md Step 1 FIRST
(daemon auto-start, WAL recovery, new instance id, heartbeats, single
lease; record fault_injection.completed F4 PASS/FAIL with exact evidence).
The daemon was supervisor-7341b577 before any reboot; a new instance id
after reboot is expected and is the PASS signal.

STEP 1 — CLOSED STATE (verified, don't redo):
- 8 verified cycles: step-1 foundation 893eec8 (21 tests); step-2
  scheduler 508ca5f (28; AC3 72.5ms); step-3 G6 injection 5b01665
  (inject->FAIL->repair->PASS; 31); F1 live kill PASS (auto-restart 35s,
  lease reclaim, one owner); step-5 Foundry 751ef83 (resilient heartbeat
  loop, FIND-2026-08-12-01 repair; 215 Foundry tests); step-6 F14/F15
  a5d3f4c (35); step-7 leak vectors 5eaa6ee (40; FAIL->repair->PASS:
  float/time/duplicate echo surfaces sanitized).
- Foundry repo HEAD ba5db8a (clean). Mission repo HEAD 5eaa6ee (clean).
- Ledger: 394 events, reconcile consistent, quick_check ok.
- Two independent FAIL->repair->PASS lineages recorded (G6 + step-7).
- F4: BLOCKED at execution (Hermes hard-block on shutdown/reboot; NOT a
  technical impossibility — the owner can reboot manually; all
  prerequisites committed; post-reboot script ready). Do NOT retry the
  reboot from the agent; record it as owner-resumable.
- All entry preconditions recorded (WP8_ENTRY_PRECONDITIONS.md COMPLETE);
  clock started 2026-08-12 (CLOCK_START.json, ledger dd5234c6).
- Obsidian V1 root live (8 notes, governed writes, initial snapshot
  1f04f243); 02/07 notes updated after each close.

STEP 2 — NEXT CYCLES (in priority order):
1. F2-class: kill a builder mid-step — the mission loop shape: each step
   is executive-implemented with ledger step.created/ready/completed; the
   F2 test = simulate a mid-step death (e.g., kill the terminal process
   mid-commit? better: crash-injection on the ledger append path is
   already covered by step-5; instead: verify step-state recovery — a
   step left in READY with no completion events must be re-claimable by a
   fresh instance without duplicates; write the regression + record).
2. F3: staged gateway kill — the dispatch path uses CLI subprocesses
   (gateway-independent). Prove with a STAGED gateway (second Hermes
   instance on an isolated HERMES_HOME) that killing it never affects the
   mission dispatch; NEVER kill the live gateway (it hosts this session).
3. Reconciliation hardening: the WP8 final reconciliation + freeze
   evidence bundle (var/evidence/wp8-gate/) + independent final review
   (fresh verifier on the whole gate record) + day-1 gate report
   (docs/evidence/WP8_DAY1_REPORT.md). The seven-day clock continues via
   scheduled operation; explicit waiting is valid, fake work is not.
4. Continue to >=10 verified cycles before the day-1 report.

STEP 3 — DISCIPLINE (non-negotiable):
- Fresh reproduction for every claim; verifier reports are evidence to
  integrate, not truth to relay. Never let a builder certify itself.
- Commit only after verifier PASS; local commits only, never push.
- Checkpoint every 60-90 min (docs/handoffs/CHECKPOINT_<UTC>.md); full
  handoff before ~120k tokens / ~75 tool calls.
- Ledger, Obsidian (governed writes only), and docs must agree after
  every transition; reconcile via `doctor`.
- Daemon discipline: venv python.exe = launcher + base-interpreter child
  = ONE daemon (parent-child pair, not a duplicate). taskkill /T may
  leave the task state stale ("Running") — Stop-ScheduledTask clears it,
  the next trigger tick restarts (<=60s). LeaseUnavailable on a live
  lease = correct backoff (exit 1), not a bug. Degraded events are
  throttled; MAX_CONSECUTIVE_FAILURES=5 then clean self-stop.
- Secrets: never print auth material; the isolated home
  var/wp8-hermes-home holds scoped credentials for real dispatches.
- Owner control: veto/STOP sovereign; no paid/public/external actions in
  V1; no pushing; no AI generation in the mission repo (human-written
  code only, per the mission contract).

OWNER INPUT REQUIRED: NONE (authenticated START BUILD mandate). The only
owner-resumable item: the F4 physical reboot (any manual restart of this
machine executes the prepared test; the recovery verification is scripted).

═══ END PASTE-READY PROMPT ═══

## Durable State Pointers

- Ledger (machine truth): C:\Users\b\AppData\Local\hermes\foundry\foundry.db
- Live daemon: scheduled task JARVIS-Foundry-Supervisor (repeating
  trigger PT1M/P30D, IgnoreNew, LogonTrigger+TimeTrigger)
- Evidence: var/evidence/wp8-gate/ (CLOCK_START.json, step-1..7 bundles,
  f4-restart/, step-2-design.md), var/evidence/recovery-run/
- Verifier transcripts: C:\Users\b\AppData\Local\hermes\cache\delegation\live\deleg_*
- Obsidian: C:\Users\b\Blendi\JARVIS\V1\ (governed 00-07 + Audit)
- Mission repo: C:\Users\b\JARVIS\v1-proving\wfm-optimizer (5eaa6ee)
- Foundry repo: this repo, HEAD ba5db8a

## Honesty Rule

Nothing is PASS until reproduced with fresh commands. F4 remains
BLOCKED/NOT_TESTED until a real reboot occurs; never backdate it.
