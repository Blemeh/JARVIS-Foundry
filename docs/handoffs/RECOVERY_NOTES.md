# Durable Recovery Notes — Authoritative Resume Point

Updated: 2026-08-12T06:45+02:00 (scheduled continuation run)
Session: Hermes cron continuation (automated), START BUILD active.

## READ FIRST

1. `PROJECT_STATE.md` — live state record (v13).
2. `docs/handoffs/CHECKPOINT_2026-08-12T0645.md` — newest durable checkpoint.
3. `docs/evidence/WP8_F3_FAULT_INJECTION.md` — step-8 (F3 + installer fix).
4. `docs/evidence/WP8_ENTRY_PRECONDITIONS.md` — WP8 gate preconditions.
5. This file — milestone log + resume mechanics.

Repository truth beats chat summaries. Re-verify state at session start
(`git status`, `tasklist | grep -i python`, task state, DB quick_check).

## Current Truth (verified 2026-08-12 ~06:45)

- **Runtime healthy:** task `JARVIS-Foundry-Supervisor` Running; daemon
  `supervisor-54a43fb2…` (restarted 06:03 local onto step-8 code),
  heartbeats every 30 s, exactly one singleton lease, reconcile consistent
  (799 events, quick_check ok, `doctor` clean).
- **WP0–WP7 PASS** (WP7 activated; recovery repairs verified).
- **WP8 day 1 of 7** — clock started 2026-08-12 (CLOCK_START.json + ledger
  dd5234c6/a66fc238/6c8870f2). **9 verified cycles closed** (steps 1,2,3,5,6,
  7,8 + F1 + clock start). **Step-8 closed this run:**
  - F3 fault injection (builder kill after mutation, before result): defect
    reproduced (mission stuck EXECUTING; reaper never wired into the
    supervisor) → repair (`recover_stale_mission_state` incident-scoped
    atomic resume, `append_events` batch API, supervisor `_reap_and_recover`
    with degraded guard) → independent verifier round-1 FAIL (F3-1..F3-4) →
    repair → round-2 PASS (0 findings). Foundry suite 226 passed.
  - FIND-2026-08-12-02: installer LogonTrigger without UserId → schtasks
    "Access is denied" from automated (cron) contexts. Fixed with explicit
    `<UserId>`; 9 baseline failures → 17/17 installer tests green here.
  - Evidence: `docs/evidence/WP8_F3_FAULT_INJECTION.md`,
    `var/evidence/wp8-gate/step-8/` (suite + focused + baseline artifacts,
    diag scripts, manifest). Foundry repo HEAD 9ff56e8 (clean). Mission repo
    HEAD 290265d (clean, 40 tests green).
- Live DB: `C:\Users\b\AppData\Local\hermes\foundry\foundry.db` (WAL;
  content is the invariant, hashes change with normal runtime events).

## Milestones Completed (this run, 2026-08-12 05:05–06:45)

- [x] Fresh-state verification (git, task, daemon, DB, ledger 597 events).
- [x] Baseline suite: 9 installer FAILURES reproduced ("Access is denied").
- [x] Bisected to LogonTrigger UserId; fixed installer + regression test;
      17/17 installer tests green (incl. real schtasks create in THIS
      automated context).
- [x] F3: reproduced stuck-EXECUTING defect; built recovery (incident-scoped
      atomic mission resume + reaper wiring + degraded guard); 9 new tests.
- [x] Full Foundry suite 226 passed; mission 40 passed; estop 24 passed.
- [x] Live daemon restarted onto the new code; heartbeats/lease/reconcile
      verified (supervisor-54a43fb2).
- [x] Independent verifier round-1 FAIL (F3-1..F3-4) → repaired → round-2
      PASS (0 findings); transcripts in the delegation cache.
- [x] Ledger events appended (step-8 chain, 786→799 events); Obsidian 02/07
      governed write-back; PROJECT_STATE v13, CURRENT_WORK_PACKAGE progress,
      RECOVERY_NOTES, checkpoint — one coherent transition.
- [ ] F2-class staged gateway kill (next cycle; dispatch is CLI-subprocess,
      gateway-independent by design — prove with a STAGED gateway, never the
      live one).
- [ ] F4 remains BLOCKED/owner-resumable (physical reboot not executed by
      the agent; scripted verification ready in F4_RESTART_HANDOFF).
- [ ] Reconciliation hardening + freeze + independent final review + day-1
      gate report (docs/evidence/WP8_DAY1_REPORT.md).

## How To Resume (if this session is interrupted)

1. Read the READ FIRST list, then re-verify: `git status --short`,
   `tasklist | grep -i python`, `schtasks /query /tn JARVIS-Foundry-Supervisor`,
   live DB quick_check + reconcile (`jarvis doctor`).
2. If the Supervisor is not running: `schtasks /run /tn
   JARVIS-Foundry-Supervisor` (the repeating trigger also self-heals within
   ~2 min of any death). Never start a second daemon manually — the
   singleton lease prevents duplicates.
3. Continue the gate plan: F2-class staged gateway kill, then reconciliation
   + freeze + day-1 report; ≥10 verified cycles before the report.
4. Never run duplicate builders. Never edit live Hermes or `.h` beyond
   read-only overlays. Never modify the canonical DBs under var/evidence.
   Freeze before rollback. No pushes during the V1 gate.

## Canonical Paths (NEVER mutate; use copies)

- `var/staging-foundation/foundry.db`, `var/cli-proof/foundry.db`,
  `var/final-proof/foundry.db`, `var/wp1-staging/foundry.db` — canonical
  gate evidence DBs (read-only, hashes in the wp1-gate bundle).
- `.h` — isolated Hermes development checkout (commit
  `244d296646909aca1dd16c9759491da0ef4cd163`).
- `var/staging-recovery/foundry.db` — live-DB copy used for the recovery
  rehearsal (content = incident state + one rehearsal cycle).
- Evidence bundles: `var/evidence/wp1-gate/` … `var/evidence/wp8-gate/`.

## Running Commands

```bash
cd "C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry"
./.venv/Scripts/python.exe -m pytest tests/foundry -q --tb=short
./.venv/Scripts/python.exe -m jarvis_foundry.cli.main doctor
./.venv/Scripts/python.exe -m jarvis_foundry.cli.main status
schtasks /run /tn JARVIS-Foundry-Supervisor   # restart the daemon if needed
# Hermes baseline:
cd .h && HERMES_HOME="<root>\var\wp1-hermes-home" PYTHONPATH="$PWD;C:\Users\b\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages" ../.test-venv/Scripts/python.exe -m pytest tests/test_estop.py -q
```

## Honesty Rule

Nothing is PASS until reproduced with fresh commands. Verifier reports are
evidence to integrate, not claims to relay. F4 stays BLOCKED until a real
reboot happens; never backdate it.
