# V6 Lane Job Queue (2026-08-23)

Serial implementation queue. Each job: inspect → reproduce gap → implement →
focused tests → adversarial cases → regression → disposable proof → diff/secret
review → commit → evidence update. Status updated in place.

| # | Job | Status | Evidence |
|---|---|---|---|
| V6-1 | Current evolution pipeline truth | DONE | INITIAL_STATE.md findings F1–F7; EVOLUTION_CAPABILITY_MAP.md |
| V6-2 | Weakness/opportunity detector over durable outcomes | DONE   | — |
| V6-3 | Improvement hypothesis record (reuse records.py; prove dedupe) | DONE   | — |
| V6-4 | Baseline + counterfactual measurement (UNKNOWN/INSUFFICIENT_SAMPLE explicit) | DONE   | — |
| V6-5 | Experiment plan compiler (risk-driven bounds, fail closed R3) | DONE   | — |
| V6-6 | Candidate builder hardening (attacks) | DONE   | — |
| V6-7 | Shadow mode as first-class experiment | DONE   | — |
| V6-8 | Benchmark / evaluation engine (typed, no single scalar) | DONE   | — |
| V6-9 | Independent verifier orchestration (kill F1 fabricated verdict) | DONE   | — |
| V6-10 | Canary plan / disposable canary execution | DONE   | — |
| V6-11 | Promotion eligibility decision (typed, no live promotion) | DONE   | — |
| V6-12 | Bad-candidate factory | DONE   | — |
| V6-13 | Disposable promotion + rollback loop | DONE   | — |
| V6-14 | Outcome-feedback loop (calibration, cooldown, bounded selection) | DONE   | — |
| V6-15 | Continuous improvement selector (WAITING allowed) | DONE   | — |
| V6-16 | Multi-cycle autonomous evolution harness | DONE   | — |
| V6-17+ | Next real gap from evidence | DONE   | — |

## Standing rules for every job

- Frozen base 01db32c; never touch main, live DB, Task Scheduler, CURRENT/PREVIOUS.
- All experiments on disposable roots under the lane worktree or var/v6-runtime/.
- No billable provider calls; verifier identity uses local policy identities.
- Commit with explicit paths after `git diff --cached --check`.
- A deliberately bad candidate that gets rejected is a PASS of the rejection
  machinery; never repair it into passing.
