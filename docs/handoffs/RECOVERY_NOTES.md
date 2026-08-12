# Durable Recovery Notes — Authoritative Resume Point

Updated: 2026-08-12T09:05+02:00 (scheduled continuation run — step-9 close)
Session: Hermes cron continuation (automated), START BUILD active.

## READ FIRST

1. `PROJECT_STATE.md` — live state record (v15).
2. `docs/evidence/WP8_DAY1_REPORT.md` — day-1 gate report (12 cycles).
3. `docs/handoffs/CHECKPOINT_2026-08-12T0905.md` — newest durable checkpoint.
4. `docs/evidence/WP8_F2_FAULT_INJECTION.md` — step-9 (F2-active) record.
5. `docs/handoffs/CHECKPOINT_2026-08-12T0850.md` — sibling slice's day-1 close.
6. This file — milestone log + resume mechanics.

Repository truth beats chat summaries. Re-verify state at session start
(`git status`, `tasklist | grep -i python`, task state, DB quick_check).

## Current Truth (verified 2026-08-12 ~09:05)

- **Runtime healthy:** task `JARVIS-Foundry-Supervisor` Running; daemon
  `supervisor-54a43fb2` (same instance since 06:03 local), heartbeats every
  30 s, exactly one singleton lease, reconcile consistent (1090 events,
  quick_check ok, `doctor` clean, STOP false, no active workers).
- **WP0–WP7 PASS** (WP7 activated; recovery repairs verified).
- **WP8 day 1 of 7 CLOSED** — clock started 2026-08-12 (CLOCK_START.json +
  ledger). **12 verified cycles**: steps 1,2,3(G6), F1, 5, 6(F14/F15), 7,
  8(F3 machinery + installer fix), FIND-02, step-10 (F2-live), F2 (gateway
  idle/independence), step-9 (F2-active — THIS slice).
- **Step-9 (this slice) = F2-active fault injection:** staged Hermes gateway
  (isolated hermes_cli package + isolated HERMES_HOME) EXTERNALLY terminated
  mid-session (taskkill /F /T on real PID 33092) while dispatch in flight →
  honest FAILED `hermes_oneshot_failed`, run row FAILED, role lease released,
  fresh-worker resume COMPLETED, live gateway untouched (tasklist before/after
  diff), live daemon heartbeats uninterrupted. 3 regression tests
  (`tests/foundry/test_role_gateway_kill.py`); Foundry suite 229 passed
  (201.38s); mission 40; estop 24. Independent verifier deleg_40968b66
  **PASS 0 findings**. Commit d063511; ledger rows 1083–1090; Obsidian
  02/04/07 governed writes. Bundle: `var/evidence/wp8-gate/step-9/` (manifest
  9 artifacts).
- **Concurrency incident (2026-08-12):** two cron slices ran the F2 scenario
  in parallel (~08:25–08:50). Managed with non-colliding IDs (step-9 vs
  step-10; F2-active vs F2/F2-live; FIND-2026-08-12-03 supersede), preserved
  artifacts (sibling's f3-live-probe.json left in step-9 dir, excluded from
  its manifest), merged day-1 report, git reconciled (HEAD 147ea5c → d063511,
  no conflicts). **Lesson: before choosing step/verdict/finding IDs, check
  for sibling activity (recent files/commits/ledger rows).**
- Repos: Foundry HEAD `d063511` (this slice's commit; pending the docs
  commit for this checkpoint — see below). Mission HEAD `290265d` (clean).
- Live DB: `C:\Users\b\AppData\Local\hermes\foundry\foundry.db` (WAL;
  content is the invariant, hashes change with normal runtime events).

## Milestones Completed (this slice, 2026-08-12 08:15–09:10)

- [x] Fresh-state verification (git, task, daemon, DB 975→993 events, ledger,
      no STOP, no strays).
- [x] Built the staged-gateway harness + 3 F2 regression tests (mid-session
      kill, idle independence, resume); 3/3 green.
- [x] Live F2 experiment on a CONSISTENT SNAPSHOT of the live DB: A idle
      COMPLETED / B active external taskkill → FAILED hermes_oneshot_failed /
      C resume COMPLETED; role leases 0; quick_check ok.
- [x] Full Foundry suite 229 passed; mission 40; estop 24; tasklist diff
      proves the live gateway/daemon untouched.
- [x] Evidence doc WP8_F2_FAULT_INJECTION.md; bundle manifest (9 artifacts).
- [x] Commit d063511 (tests + F2 doc). Independent verifier deleg_40968b66
      PASS 0 findings (F2-1 informational: ledger events pending → appended
      in the same transition).
- [x] Ledger rows 1083–1090 (step-9 chain + fault_injection.completed
      F2-active + verdict-step-9 PASS + commit event); Obsidian 02/04/07.
- [x] Reconciled with the sibling slice's day-1 close: merged
      WP8_DAY1_REPORT.md (12 cycles), PROJECT_STATE v15, CURRENT_WORK_PACKAGE,
      this file, CHECKPOINT_2026-08-12T0905.md — one coherent transition.
- [ ] F5–F13 on schedule (days 2–5): F12/F13 day 2–3; F5/F6/F7 day 4;
      F8–F11 day 5 (per plan §3).
- [ ] F4 remains BLOCKED/owner-resumable (physical reboot not executed by
      the agent; scripted verification ready in F4_RESTART_HANDOFF).

## How To Resume (if this session is interrupted)

1. Read the READ FIRST list, then re-verify: `git status --short`,
   `tasklist | grep -i python`, `schtasks /query /tn JARVIS-Foundry-Supervisor`,
   live DB quick_check + reconcile (`jarvis doctor`).
2. If the Supervisor is not running: `schtasks /run /tn
   JARVIS-Foundry-Supervisor` (the repeating trigger also self-heals within
   ~2 min of any death). Never start a second daemon manually — the
   singleton lease prevents duplicates.
3. Continue the gate plan: next fault injections per
   `docs/evidence/WP8_GATE_EXECUTION_PLAN.md` §3 (F12/F13 day 2–3 first, then
   F5/F6/F7 day 4, F8–F11 day 5). ≥10 verified cycles is already satisfied
   (12); keep the discipline: builder → fresh verifier → commit only after
   PASS; freeze every accepted step into `var/evidence/wp8-gate/step-<n>/`.
4. Check for sibling activity before choosing new step/verdict/finding IDs
   (recent commits, files, ledger rows) — concurrency incident 2026-08-12.
5. Never run duplicate builders. Never edit live Hermes or `.h` beyond
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
- Evidence bundles: `var/evidence/wp1-gate/` … `var/evidence/wp8-gate/`
  (step-9 bundle is the newest; sibling's f3-live-probe.json preserved
  inside it but excluded from its manifest).

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
reboot happens; never backdate it. The concurrency incident is documented,
not hidden — future slices must check for siblings before choosing IDs.
