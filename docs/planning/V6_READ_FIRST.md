# V6 READ FIRST — CONTINUOUS SELF-EVOLUTION

**Document role:** mandatory anti-drift entry point for every V6 worker

**Status:** CANONICAL PRE-EXECUTION PLAN — V6 NOT ACTIVATED BY THIS FILE

**Prepared:** 2026-08-13

**Owner control:** authenticated STOP, veto, pause, redirect, constraints, and
priority changes always win

This document prevents a new Hermes or other agent session from interpreting
an old transcript, a passing test, a candidate's own report, or an incomplete
status file as permission or evidence to promote self-modifying code.

## 1. Required reading order

Before planning, editing, creating a worktree, running a migration, starting a
candidate, or changing any release pointer, read completely:

1. `PROJECT_STATE.md`;
2. `IDEA.md`;
3. `docs/governance/AUTONOMY_MANDATE.md`;
4. `docs/architecture/00_READ_FIRST.md`;
5. `docs/architecture/01_NORTH_STAR.md`;
6. `docs/architecture/02_ARCHITECTURE_CONTRACT.md`, especially sections 17
   and 21;
7. `docs/architecture/05_POST_V1_ROADMAP.md`, section 9;
8. `docs/evidence/V5_ACCEPTANCE.md`;
9. `docs/handoffs/V6_HANDOFF.md`;
10. `docs/handoffs/RECOVERY_NOTES.md`;
11. `docs/planning/V6_MASTER_PLAN.md`;
12. `docs/planning/V6_MASTER_PROMPT.md`;
13. `docs/planning/REQUIREMENTS_TRACEABILITY.md`;
14. `docs/planning/RISK_REGISTER.md`;
15. `docs/planning/DECISION_LOG.md`;
16. `AGENTS.md` and `HERMES.md`.

Then inspect current Git status, diff, commits, worktrees, processes, STOP and
veto state, database identity/schema, runtime health, evidence artifacts, and
parallel branches. Repository and live evidence override remembered status.

## 2. Frozen handoff facts to re-verify

The planning baseline observed when this brief was written was:

- V5 checkpoint `598024d` on `main`;
- V5 final close `06ae51b`;
- V5 final verifier `deleg_686f3477`, structured PASS;
- schema v20 and 26,356 live ledger events at the close;
- 710 tests passing, exit 0;
- `conn-fs` was the only real READY connector;
- six online connector kinds were BLOCKED/resumable;
- provider reconciliation was NOT_TESTED;
- V6 had not started.

These facts may be stale by the time a new session starts. Re-verify them.

The main worktree also contained four owner-owned untracked V5 files when this
brief was prepared:

- `docs/handoffs/V5_SOL_HIGH_HARDENING_PROMPT.md`;
- `docs/handoffs/V5_SOL_HIGH_HARDENING_REPORT.md`;
- `docs/planning/V5_MASTER_PROMPT.md`;
- `docs/planning/V5_READ_FIRST.md`.

Never reset, clean, stash, move, overwrite, or accidentally commit them.

Parallel V4 work also existed under `codex/v4-command-center` and
`codex/v4-v5-integrated`. Discover its current state before choosing V6 paths.
V6 must not reset, rebase, merge, delete, or overwrite that work.

## 3. What V6 means

V6 is Continuous Self-Evolution. JARVIS must identify a measured weakness or
opportunity, form a falsifiable hypothesis, build an isolated candidate,
independently verify it, run it in shadow and then bounded canary conditions,
promote it only after the correct risk-class evidence gate passes, measure the
real outcome, and automatically roll back degradation.

The loop is:

```text
MEASURE
→ HYPOTHESIZE
→ CLASSIFY RISK
→ FREEZE BASELINE AND SUCCESS CRITERIA
→ BUILD ISOLATED CANDIDATE
→ TEST
→ INDEPENDENTLY VERIFY
→ SHADOW
→ CANARY
→ PROMOTE OR REJECT
→ MEASURE OUTCOME
→ KEEP, REPAIR, OR ROLL BACK
→ LEARN
→ CONTINUE
```

V6 is not generic refactoring, uncontrolled recursive self-modification, a
test-count exercise, or permission to touch every system. No measured problem
means no candidate. Honest WAITING is valid.

## 4. Permanent integrity rules

A candidate cannot:

- certify or promote itself;
- lower its risk class;
- change frozen success criteria after seeing results;
- weaken STOP, vetoes, evidence, redaction, audit, action control, or rollback;
- forge owner directives;
- delete immutable evidence or failed release history;
- overwrite the only recoverable live copy;
- mutate live state during shadow mode;
- expand its own canary traffic, budget, duration, or capability scope;
- control the external mechanism that terminates it;
- turn unknown or unavailable evidence into PASS.

The independent Supervisor stays outside live and candidate release slots.
Freeze precedes rollback. Executable rollback is incomplete if state or schema
cannot also be restored.

## 5. Risk classes

- **R0:** documentation and non-governing prompts.
- **R1:** skills and bounded agent behavior.
- **R2:** retrieval, scheduler, mission logic, tools, workflows, knowledge
  behavior, and state projections.
- **R3:** Supervisor, STOP/veto, action control, credentials, ledger, database
  schema, migrations, release controller, rollback controller, or integrity
  rules.

Touching an R3 surface makes the candidate R3 unless an external deterministic
classifier proves a stricter interpretation from the actual diff and behavior.
Unknown classification fails closed to the highest plausible class.

## 6. Construction boundary

V6 construction must begin in an isolated sibling worktree/branch from the
verified V5 baseline. It must use separate runtime homes, virtual environment,
database copies, release roots, connector roots, logs, ports, and evidence.

Do not develop candidates in the checkout used by the running Supervisor or
Hermes. Do not use the live database as a test fixture. Do not modify `.h` or
the live Hermes installation unless a separately isolated candidate and its
risk gate explicitly require it.

## 7. Truth boundaries carried forward

- V5 BLOCKED connectors remain BLOCKED until real interfaces, credentials, and
  the proven activation gate exist.
- Local simulations are not provider-side reconciliation.
- The original roadmap V4 Command Center/Voice/Briefings track remains
  separately visible until its own acceptance gate passes.
- V6 cannot claim missing V4 capabilities as incidental V6 completion.
- V1's waived operational history remains recorded honestly.
- Historical event discrepancies are corrected additively, never rewritten.

## 8. Start condition

This file and the other V6 planning files do not start V6. A fresh session
starts V6 only when it receives the authenticated owner prompt from
`V6_MASTER_PROMPT.md` or an equivalent newer directive.

After that directive, the session follows `V6_MASTER_PLAN.md` from S0 through
S13 without asking for routine approval between slices. It stops only for an
authenticated owner control, a genuine technical blocker, an unrepaired
severity-one integrity risk, or the final V6 gate and V7 handoff.
