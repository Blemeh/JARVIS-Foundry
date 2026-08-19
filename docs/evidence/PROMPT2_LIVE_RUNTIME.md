# PROMPT 2 OF 4 — LIVE RUNTIME

Finite live-runtime report. Prompt 1 was not rerun. Goal 4 was not continued. Prompt 3 / M6 was not started.

```text
PROMPT:
2 OF 4 — LIVE RUNTIME

SOURCE PROMOTION: VERIFIED_EXACT (C:\temp\jarvis-prompt1-m2 matched CASE_A_KERNEL.patch; committed then cherry-picked)
M3 VERDICT: PASS
M4 VERDICT: PASS
M5 VERDICT: PASS (repair batch 2026-08-19T18:55Z)

CANONICAL START SHA: 0b45d9ee799d1c5de38f8c307fc0455783aca80d
CANONICAL FINAL SHA: b59836772d948bd7b2507949c42f3c7dfa75afaf
origin/main:         b59836772d948bd7b2507949c42f3c7dfa75afaf

M2 PROMOTION COMMIT: 857640913bda04f51d97bf6d79be2e83b73d9c38
CANONICAL SOURCE:    b59836772d948bd7b2507949c42f3c7dfa75afaf
LIVE RELEASE BEFORE: developer checkout 0b45d9e via .venv
CURRENT LIVE RELEASE: b59836772d948bd7b2507949c42f3c7dfa75afaf
PREVIOUS RELEASE:    adada7f31ac515cfac9782ace95f744b631f76f5
LIVE DB:             C:\Users\b\AppData\Local\hermes\foundry\foundry.db
SCHEMA:              v34 (migrated from v33 after clone proof)

TASK SCHEDULER TARGET: C:\Users\b\AppData\Local\JARVIS\bin\jarvis-supervisor.cmd --home "C:\Users\b\AppData\Local\hermes\foundry" --spawn-worker-loop --worker-loop-config "C:\Users\b\AppData\Local\hermes\foundry\prompt2-worker-routing.yaml"
SUPERVISOR PID:        29416 (child 29580)
SUPERVISOR EXECUTABLE: C:\Users\b\AppData\Local\JARVIS\releases\b59836772d948bd7b2507949c42f3c7dfa75afaf\.venv\Scripts\python.exe -m jarvis_foundry.deployment.launcher

PRODUCTION MISSION PATH COUNT: 1
AUTONOMOUS LIVE MISSION CYCLES: 3 (A, B-with-repair, C) then COMPLETE
WORKER RUNS:               8 agent_run.started (A builder+verifier; B builder+verifier FAIL + repair+verifier; C builder+verifier)
FRESH VERIFIER RUNS:       4 started / 3 PASS / 1 FAIL
REPAIR CYCLES:             1 automatic
NEXT-ACTION CONTINUATIONS: 2 unattended (A→B, B→C)

STOP: PASS (F1 freeze then resume)
SUPERVISOR RESTART: PASS (hard kill; PT1M relaunch of stable launcher; new instance 28195bfb)
RUNTIME RESTART: PASS (same)
WORKER CRASH: PASS (owned pid killed after PARTIAL write; repair + fresh verifier)
NETWORK/PROVIDER: PASS (LAB scripted Hermes; no live provider)
FALSE SUCCESS: PASS (DEFECT caught by verifier)
DUPLICATE EVENT: PASS (idempotent replay + EventConflict on tamper)
STALE LEASE: PASS (singleton reacquired by new instance)
POST-PASS RESTART: PASS (next_action survived A PASS into B)
WINDOWS RESTART: BLOCKED_OWNER_ENVIRONMENT (active owner Cursor session; not rebooted)
ROLLBACK: PASS (CURRENT↔PREVIOUS pointer swap; both v34; live DB not downgraded)

L1 COMPONENT: PASS
L2 COMPOSED: PASS
L3 RUNTIME: PASS (immutable release + stable launcher)
L4 AUTONOMOUS OUTCOME: PASS (live 15-point kernel)
L5 RECOVERY: PASS
L6 UNATTENDED: NOT YET GATED

LIVE SYSTEM MUTATED: YES

INDEPENDENT VERIFIER: PASS (docs/evidence/PROMPT2_RECOVERY/M5_INDEPENDENT_VERIFIER.json)
UNRESOLVED CRITICAL: none
UNRESOLVED HIGH: none
EXTERNAL BLOCKERS: NONE

ADVANCED CAPABILITIES:
FROZEN / UNCHANGED

VERDICT: PASS
PROMPT 3 READY: YES
```

Do not begin M6.

## Collision / stop-before-edit

Goal-4 dirty tree `C:\temp\JARVIS-Runtime-Live-Activation` was parked (last source write ~15:35). Not overwritten.

Live Supervisor was STOP'd, task disabled, `schtasks /End` used. Accidental `C:\Users\b\foundry.db` from `$HOME` shadowing was deleted; live STOP was then written to the real Foundry home.

## Source promotion

M2 worktree diff **byte-identical** to `docs/evidence/PROMPT1_RECOVERY/CASE_A_KERNEL.patch` (`fc` exit 0). Classification **VERIFIED_EXACT**. Cherry-pick onto main as `8576409`. Equivalence tests `tests/foundry/test_prompt1_m2_kernel.py` 4/4 PASS. Pushed `origin/main` (fast-forward, no force).

## M3

Install prefix `%LOCALAPPDATA%\JARVIS` with `bin\jarvis-supervisor.cmd`, `CURRENT`, `releases\<sha>\.venv` + `RELEASE.json`. Durable state remains `C:\Users\b\AppData\Local\hermes\foundry`.

Clone migrate: v33 → v34, integrity ok, 0 reconcile mismatches, 116772 events. Live stayed v33 until M4. Smoke: release import path, launcher `--validate-only` and `--once` on disposable home. Live v33 start fail-closed. Developer checkout start against live home fail-closed after CURRENT existed.

## M4

Live migrated to v34. Mission `mission-prompt2-live` in registered worktree `C:\temp\jarvis-prompt2-live-mission`. LAB scripted Hermes on HybridWorkerRoleAdapter path (no live Hermes profile). Autonomously completed A/B-repair/C. Artifacts `step_a.md`/`step_b.md`/`step_c.md`.

## M5

Original probes: `docs/evidence/PROMPT2_RECOVERY/M5_PROBES.json`.
Repair batch: `docs/evidence/PROMPT2_RECOVERY/M5_REPAIR.json` and `M5_INDEPENDENT_VERIFIER.json`.
PREVIOUS is the exact prior immutable v34 release `adada7f`. Rollback and return-to-CURRENT were executed live. Worker crash after PARTIAL write was proven on the installed CURRENT python.

## Tests

Focused M5 rollback/crash + supervisor/runtime regressions: PASS.
FULL `pytest tests/foundry tests/convergence`: **1414 collected, EXIT=0**.
