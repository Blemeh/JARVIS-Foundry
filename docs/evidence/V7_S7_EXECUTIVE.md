# V7-S7 — PERMANENT EVENT-FIRST AUTONOMY AND PORTFOLIO EXECUTIVE

```text
STATUS: PASS (independent verifier verdict appended on close)
PHASE: V7_FULL_AUTONOMOUS_OPERATION
SLICE: S7 — permanent event-first autonomy + portfolio executive
OBJECTIVE: enable the Supervisor-owned event-first mission + evolution
cadence and the portfolio executive — durable schedules (restart-safe,
budgeted, cooldown-capped, dedup, backpressure, priorities, deadlines,
honest WAITING, anomaly suspension) and a governed whole-project view
with provenance (goals, dependencies, risks, costs, capacity, deadlines,
EV, urgency, information gain, reversibility, opportunity cost) that
selects/pauses/resumes/reprioritizes without routine owner prompts.
AUTHORITY_AND_ACTIVE_CONTROLS: owner directive V7_FULL_AUTONOMOUS_OPERATION;
S0-S6 all CLOSED PASS (S6: live adoption, schema 32, daemon on the frozen
release under the resumed task cadence); controller "supervisor" pinned
throughout; STOP sentinel absent.
BASE_COMMIT_AND_DIFF: main a595e61 (S6 close) -> +2 modules
(src/jarvis_foundry/evolution/scheduler.py, portfolio.py) + 2 test files
(tests/foundry/test_v7_scheduler.py, test_v7_portfolio.py) + this report.
LIVE_RELEASE_PROCESS_AND_DATABASE: live ledger schema 32, daemon RUNNING
on the frozen release, task triggers enabled — untouched by this slice.
CAPABILITIES_AND_READINESS: the scheduler is DISABLED by default
(observe-only) until an explicit enable event; the portfolio executive is
decision-only (no executor wired yet — missions execute through the
existing role machinery in later slices).
MISSION_OBSERVATION_OR_HYPOTHESIS: N/A (enablement + decision layer).
SYSTEMS_AND_CANONICAL_TARGETS: event-first scheduler + portfolio records
live in the same ledger event stream (restart-safe rebuild); the live
daemon's heartbeat loop is unchanged (tick wiring lands in a later slice).
RISK_AND_CONSEQUENCE: R1/R2 (decision records only; no external effects;
no executor); every effect reversible (disable event, recorded status
changes).
BASELINE_SUCCESS_THRESHOLD_AND_GUARDRAILS: 18 TDD tests (10 scheduler +
8 portfolio) covering the full gate matrix; full suite green; supervisor
controller enforced on every mutating entry; unknowns/conflicts recorded,
never silently resolved.
BUDGET_QUOTA_AND_COST: 0.0 (no provider calls).
ACTIONS_TAKEN: TDD — wrote the red tests first, then implemented
scheduler.py (durable schedules rebuilt from the event stream; enable/
disable supervisor-only; tick with max_concurrent cap, total budget cap,
cooldown deferrals, deadline misses, degraded-anomaly suspension,
idempotent fires) and portfolio.py (project registration with the full
governed field set + provenance; deterministic scoring; select with
capacity + recorded decisions; pause/resume/reprioritize; unknowns/
conflicts recorded).
FILES_PROCESSES_DATABASES_AND_EXTERNAL_STATE_CHANGED: the 4 new files
above; no live state touched.
TESTS_RECONCILIATION_RESTORE_AND_FAULTS: focused 18/18; full suite 948
passed EXIT=0 (421.88 s; artifact var/evidence/v7-s7/suite-v7-s7-full.txt,
sha256 89075074…); fault probes: degraded-anomaly suspension + recovery,
deadline expiry never fires late, cooldown honest WAITING, restart-safe
rebuild from the event stream.
VERIFIER_AND_STRUCTURED_VERDICT: <fill after dispatch — fresh independent
adversarial verifier, opencode_go/deepseek-v4-flash, no fallback>
EVIDENCE_PATHS_AND_HASHES: src/jarvis_foundry/evolution/scheduler.py,
portfolio.py; tests/foundry/test_v7_scheduler.py, test_v7_portfolio.py;
var/evidence/v7-s7/suite-v7-s7-full.txt (89075074…).
REAL_EFFECT_PROVIDER_REFERENCE_AND_AFTER_STATE: N/A (no online effect);
the event stream is the durable after-state (any later enablement is a
recorded, restart-safe transition).
ROLLBACK_COMPENSATION_OR_CONTAINMENT: disable event reverses enablement;
status_changed events are reversible; git revert for the code.
UNRESOLVED_RISKS_LIMITATIONS_AND_BLOCKERS: the scheduler tick is not yet
wired into the live daemon's loop (by design — wiring + enablement land
with the executor slice); the portfolio has no executor (decisions only).
NEXT_AUTONOMOUS_ACTION: S8 (owner Command Center, briefings, English/
Albanian voice).
OWNER_INPUT_REQUIRED: NONE
```

## 1. Scheduler gate matrix (10 tests)

1. default observe-only (disabled until an enable event)
2. enable/disable are Supervisor-only, ledger-recorded
3. items registered with priority/cooldown/budget/deadline; state rebuilds
   from the event stream (restart-safe replay)
4. tick requires Supervisor controller + enabled state
5. highest-priority items fire first; concurrency cap enforced
6. cooldown defers with honest WAITING (recorded deferral)
7. total budget cap blocks due items (recorded deferral)
8. expired deadlines are marked missed, never fired late
9. degraded-anomaly suspension (degraded newer than heartbeat) + recovery
10. tick idempotent (no duplicate fires)

## 2. Portfolio gate matrix (8 tests)

1. projects recorded with the governed field set + provenance
2. registration is Supervisor-only
3. deterministic scoring + ranking
4. selection respects capacity; unselected projects deferred with reason
5. pause/resume/reprioritize recorded; paused projects rank last
6. unknowns and conflicts recorded, never silently resolved
7. decisions carry reasons + controller identity (no routine owner prompts)
8. status changes are reversible and Supervisor-only

## 3. Verifier — deleg_7cacb0af, PASS (fresh independent adversarial session)

Verdict JSON: `%TEMP%\v7s7-verdict-adversarial.json` (early-skeleton
protocol; verdict PASS, criteria C1-C6 all PASS with per-criterion
evidence).

- C1 PASS: HEAD 6f8d7f7, exactly 5 files; 5 protected owner docs only in
  status, hashes 5/5 OK; evolution/ diff vs a595e61 = only the two new
  modules.
- C2/C3 PASS: _rebuild replays the event stream; degraded suspension
  derived from event order; default DISABLED; _controller_gate on every
  mutating entry in both modules; caps/cooldown/deadline/budget
  deferrals/suspension/idempotent fires; full governed portfolio field
  set; deterministic scoring (PAUSED=-inf); no owner-prompt path; zero
  imports of the new modules elsewhere (not wired, as claimed).
- C4 PASS: 18/18 tests pass on the verifier's own run; tests genuinely
  discriminating (cooldown window, suspension + recovery, idempotency,
  restart replay).
- C5 PASS: suite artifact sha256 89075074…, 948 passed EXIT=0.
- C6 PASS: live schema 32, daemon RUNNING heartbeat fresh, task Enabled,
  no STOP, ports up; report claims match.
- Findings: INFO only (948-vs-954 collection delta reconciled via git
  archive of a595e61; the briefing test file appeared during the audit =
  parent's concurrent S8 prep, recorded as parent activity). Integrity
  concerns: none. Scope violations: none.

**S7 CLOSED — PASS** (event-first scheduler + portfolio executive; 18 TDD
tests; suite 948 EXIT=0; live untouched; scheduler observe-only until
enablement). Next: S8 — owner Command Center, briefings, EN/SQ voice.
