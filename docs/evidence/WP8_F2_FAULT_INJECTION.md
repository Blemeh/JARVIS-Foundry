# WP8 F2 Fault Injection — Hermes Gateway Termination (Staged)

Status: **PASS** (reproduced; evidence bundle `var/evidence/wp8-gate/step-9`)
Date: 2026-08-12 (day 1 of the seven-day gate)
Plan mapping: `docs/evidence/WP8_GATE_EXECUTION_PLAN.md` §3 — F2 | Hermes
gateway termination | Supervisor healthy; Hermes loss detected; lease/partial
evidence preserved; resume per policy without losing mission truth.

## 1. Scenario (plan F2)

Terminate Hermes while a worker is idle and while a worker is active. In this
architecture "Hermes" appears at exactly one boundary for Foundry: the role
adapter (`HermesOneshotAdapter`) launches one isolated session per dispatch as
a bounded `python -m hermes_cli.main` subprocess through the isolated
development checkout (`.h`) with an isolated `HERMES_HOME`. The Supervisor
daemon itself is Hermes-independent by design (`hermes_required_for_supervisor:
false` — verified again this step).

**Staging rule (contract): the live gateway is NEVER terminated.** The
injection is executed against a STAGED gateway: a `hermes_cli` package that
mirrors the real CLI surface (`-z VALUE`, `--provider`, `--model`,
`--usage-file`; verified against `.h hermes_cli/_parser.py`), placed in
`var/evidence/wp8-gate/step-9/staged-gateway/`, dispatched through the REAL
`HermesOneshotAdapter` with an isolated HERMES_HOME
(`var/evidence/wp8-gate/step-9/staging/hermes-home`). The gateway process is
terminated with a real external `taskkill /F /T` on its actual PID — the
faithful "Hermes terminated by an outside actor".

## 2. Injection Runs (fresh, reproduced this run)

All phases ran against a CONSISTENT SNAPSHOT COPY of the live Foundry DB
(sqlite backup API, `staging/foundry.db`); the live DB was never mutated by
the experiment.

| Phase | Injection | Expected | Actual | Recovery evidence |
|---|---|---|---|---|
| A (idle) | gateway up, worker idle; dispatch `run-f2-idle-1` | COMPLETED (healthy baseline) | **COMPLETED**, verdict PASS, 0.16 s | `agent_run.started/completed` events in snapshot |
| B (active) | gateway process EXTERNALLY killed mid-session (`taskkill /F /T /PID 33092`, rc 0, "SUCCESS … terminated") while dispatch `run-f2-kill-1` in flight | FAILED, gateway loss detected, lease released, no hang/fabrication | **FAILED** `hermes_oneshot_failed`, verdict NOT_TESTED (honest), 0.86 s | run row FAILED; `agent_run.failed` event; role leases 0; quick_check ok |
| C (resume) | gateway restored; fresh worker `run-f2-kill-2`, same step | COMPLETED, mission truth intact | **COMPLETED**, verdict PASS, 0.15 s | exactly one fresh COMPLETED run; failed run stays FAILED; reconcile consistent |

Snapshot ledger after the experiment: `agent_run.started` ×3, `agent_run.
heartbeat` ×3, `agent_run.completed` ×2, `agent_run.failed` ×1; the ONLY lease
row is the copied live `supervisor:singleton`; `role:*` leases = 0;
`PRAGMA quick_check` ok. Full summary:
`var/evidence/wp8-gate/step-9/f2-experiment-summary.json`.

## 3. Live Runtime Untouched (proved, not assumed)

- `tasklist` snapshot diff before/after the experiment: NO long-lived process
  changed; only transient bash (this session's own shells) and memory-count
  noise. No live gateway/daemon process was created, killed, or altered.
- Live daemon `supervisor-54a43fb2` heartbeats continued every 30 s across the
  experiment window (live event count 991 -> 993, both `supervisor.heartbeat`
  rows; doctor after: quick_check ok, reconcile consistent, STOP false,
  active_workers [], `hermes_required_for_supervisor: false`).
- The experiment's dispatcher pointed at the staged root + isolated
  HERMES_HOME; the started marker proves the subprocess saw exactly that home
  (`started["home"] == …/staging/hermes-home`).

## 4. Regression Tests (new, committed)

`tests/foundry/test_role_gateway_kill.py` — 3 tests, hermetic (tmp staged
gateway, no live system touched):

1. `test_f2_staged_gateway_killed_mid_session_honest_failure_and_release` —
   gateway session terminated mid-flight (kill-file -> `os._exit(17)`, the
   same boundary effect as an external kill): dispatch FAILED
   `hermes_oneshot_failed`, run row FAILED, lease released, loss durable as
   `agent_run.failed`, quick_check ok.
2. `test_f2_gateway_loss_while_worker_idle_supervisor_healthy` — idle: the
   daemon has no Hermes dependency (heartbeats RUNNING, doctor flag False,
   no stale rows); the first needed dispatch fails honestly instead of
   hanging or fabricating.
3. `test_f2_resume_after_gateway_loss_fresh_worker_receives_task` — after
   loss, a FRESH worker (new run_id, same step) receives the reconciled task
   and completes; failed run stays FAILED; exactly one COMPLETED; reconcile
   consistent.

Focused: `pytest tests/foundry/test_role_gateway_kill.py` -> **3 passed in
2.64 s**. Full suite: **229 passed in 201.38 s** (exit 0;
`var/evidence/wp8-gate/step-9/foundry-suite.txt`). Mission suite (wfm-
optimizer, untouched): **40 passed**. Hermes estop baseline: **24 passed**.

## 5. Findings

None. The behavior under test was already correct: the adapter treats a
non-zero gateway exit as an honest session failure (`hermes_oneshot_failed`),
the dispatcher releases the role lease in a `finally`, the run projection
marks FAILED, and a fresh worker resumes. F2 is now covered by regression
tests and by a reproduced end-to-end staged-kill experiment. No production
code change was required this step (test/evidence-only step).

## 6. Independent Verification

- Verifier (fresh isolated session, same-provider limitation recorded per
  contract §14.3; transcript in the delegation cache): verdict + findings
  recorded in the ledger via the canonical verification machinery
  (`verification.started` / `verification.passed`).
- The verifier re-ran the focused tests, inspected the experiment summary,
  the staged gateway artifact, the tasklist before/after diff, and the live
  doctor/status state, and reviewed the bundle hashes.

## 7. Ledger / Obsidian

- Events appended to the LIVE ledger (source `wp8-executive`):
  `step.created` (step-9), `step.ready`, `evidence.created` (bundle
  wp8-step-9), `fault_injection.completed` (test_id F2, expected/observed/
  evidence/notes), `step.completed`, `verification.started`,
  `verification.passed`, `git.local_commit_created`.
- V1 Obsidian projection updated after the accepted step (governed writes,
  snapshot before/after in the ledger).

## 8. Rollback

- This step changed ONLY tests + evidence artifacts (no `src/` change, no
  schema change, no live-DB mutation). Rollback = revert the commit
  (`tests/foundry/test_role_gateway_kill.py` + bundle removal); the staged
  gateway is inert (never enabled outside this bundle).
- Freeze before any rollback (WP2 machinery); no pushes during V1.
