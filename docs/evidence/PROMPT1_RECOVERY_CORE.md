# PROMPT 1 OF 4 — RECOVERY CORE

Finite product-recovery report. Goal-4 was not continued. Goal 5 was not created. Live JARVIS was not deployed or retargeted. M3 was not started.

M0 and M1 were completed earlier in this recovery attempt. M2 implemented CASE A in a disposable worktree because live Supervisor imports the canonical checkout.

```text
PROMPT:
1 OF 4 — RECOVERY CORE

M0 VERDICT: PASS
M1 VERDICT: PASS
M2 VERDICT: PASS

CANONICAL START SHA: 0b45d9ee799d1c5de38f8c307fc0455783aca80d
CANONICAL END SHA:   0b45d9ee799d1c5de38f8c307fc0455783aca80d
origin/main:         b8f7642e504d27bb3459f3a68c4e20e315e32293

LIVE RELEASE BEFORE: developer checkout 0b45d9e via .venv (monitor name still c60db92)
LIVE RELEASE AFTER:  unchanged
LIVE DB/SCHEMA:      C:\Users\b\AppData\Local\hermes\foundry\foundry.db / v33

RUNTIME DECISION:
CASE A

AUTHORITATIVE RUNTIME ENTRYPOINT:
  python -m jarvis_foundry.runtime.worker_loop
  → next_executable_contract
  → MissionLoop.run_until_settled
  → HybridWorkerRoleAdapter
  → WorkerRouter (OpenHands pinned NOT_READY)
  → Hermes adapter
  Supervisor remains deterministic STOP/lease/reap only.

PRODUCTION MISSION PATH COUNT: 1

AUTONOMOUS MISSION CYCLES: 3 isolated (A, B-with-repair, C) then COMPLETE
WORKER RUNS:               A builder+verifier; B builder+verifier FAIL + repair+verifier; C builder+verifier
FRESH VERIFIER RUNS:       4 PASS + 1 FAIL (independent disk inspect, not builder claim)
REPAIR CYCLES:             1 (automatic)
NEXT-ACTION CONTINUATIONS: 2 unattended (A→B, B→C) then durable COMPLETE

L1 COMPONENT: PASS
L2 COMPOSED:  PASS
L3 RUNTIME:   PASS isolated (live still watchdog-only)
L4 AUTONOMOUS OUTCOME: PASS isolated 15-point kernel
L5 RECOVERY:  PASS
L6 UNATTENDED: PASS isolated (no owner next-step/repair/verifier)

STOP STATE: OFF
LIVE SYSTEM MUTATED: NO

INDEPENDENT VERIFIER: PASS (docs/evidence/PROMPT1_RECOVERY/M2_INDEPENDENT_VERIFIER.json)
UNRESOLVED CRITICAL: none in isolated kernel; live-import collision remains for landing the patch on canonical main
UNRESOLVED HIGH:
  - CASE A patch is in worktree C:\temp\jarvis-prompt1-m2, not on live main
  - live schema v33 still lacks mission_step_contracts (isolated proof used v34)
  - local main still ahead of origin/main by 3; CURRENT_STATE.md HEAD pin still stale
EXTERNAL BLOCKERS: NONE

ADVANCED CAPABILITIES:
FROZEN / UNCHANGED
  Hybrid Workers (gateway kept; OpenHands frozen not deleted)
  Hybrid Memory / Tier C
  Eyes / perception
  Workflow Discovery
  OpenAdapt / procedural learning
  Command Center
  V6 self-evolution / proving-window machinery

VERDICT: PASS
PROMPT 2 READY: YES
```

Do not begin M3.

---

## Collision handling

YES, still present; **sidestepped for M2**.

- Live Supervisor continues: `.venv\python.exe -m jarvis_foundry.supervisor.run --home C:\Users\b\AppData\Local\hermes\foundry`
- Live checkout `src/` was **not** edited
- Implementation: `git worktree add -b repair/prompt1-m2-case-a C:\temp\jarvis-prompt1-m2 0b45d9e`
- Goal-4 dirty tree `C:\temp\JARVIS-Runtime-Live-Activation` left in place

## CASE A diff

`docs/evidence/PROMPT1_RECOVERY/CASE_A_KERNEL.patch`

New tests (evidence copy): `docs/evidence/PROMPT1_RECOVERY/test_prompt1_m2_kernel.py`

Worktree files changed (not in live checkout):

- `src/jarvis_foundry/mission/contracts.py`
- `src/jarvis_foundry/mission/loop.py`
- `src/jarvis_foundry/runtime/worker_loop.py`
- `src/jarvis_foundry/workers/evidence.py`
- `src/jarvis_foundry/workers/mission_bridge.py`
- `tests/foundry/test_prompt1_m2_kernel.py`

## Did next_action persistence + evidence isolation unblock Action B and repair?

**YES.**

M1: after PASS, next cycle WAITED; Action B evidence-mismatched on leftover Step-A files.

M2 isolated: after Step A PASS, `next_action` became Step B instructions automatically. Step B ran, first builder planted DEFECT, verifier FAIL recorded `repair_requirements`, repair wrote correct bytes, fresh verifier PASS, next_action became Step C, Step C PASS, mission COMPLETE from durable contracts. A fresh Python process reopened the DB and reconstructed COMPLETE without chat context.

## Commands

```text
git worktree add -b repair/prompt1-m2-case-a C:\temp\jarvis-prompt1-m2 0b45d9ee799d1c5de38f8c307fc0455783aca80d
$env:PYTHONPATH = "C:\temp\jarvis-prompt1-m2\src;C:\temp\jarvis-prompt1-m2\tests\foundry"
C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry\.venv\Scripts\python.exe -m pytest tests/foundry/test_prompt1_m2_kernel.py
C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry\.venv\Scripts\python.exe -m pytest tests/foundry tests/convergence
```

Full suite: **1404 collected, EXIT=0**.

## Code NOT changed in the live checkout

All live `src/`, installer, worker YAML, Task Scheduler, live DB, live Supervisor process. Live docs added under `docs/evidence/PROMPT1_RECOVERY/` and append-only `RECOVERY_BASELINE.md` only.

## 15-point map (isolated)

1. ORIENTING seeded  
2. Action A `step_a.md`  
3. `evidence.created`  
4. `verification.passed`  
5. ACTION_SELECTION with next_action = Step B  
6. Action B dispatched  
7. reversible DEFECT on first builder attempt  
8. `verification.failed`  
9. `repair_requirements: rebuild the step artifact and re-verify`  
10. REPAIRING automatic  
11. repair worker overwrite  
12. fresh `verification.passed`  
13. next_action = Step C  
14. Step C  
15. `COMPLETE` + `mission.completed` from durable contracts  

Prompt 2 may land the patch when the live-import / Goal-4 writer collision is serialized. Do not begin M3.
