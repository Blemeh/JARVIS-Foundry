# V4-S4 — Exactly-Once Verifier Recovery (DIR-2026-08-12 item 3)

**Slice:** V4_CONTINUOUS_AUTONOMY / truth-critical reliability — exactly-once
recovery
**Commit:** `f1dd903` (fix(v4-s4): exactly-once verifier recovery)
**Date:** 2026-08-13
**Status:** CLOSED — independent verifier **PASS** (deleg_a3e54630,
2026-08-13T00:32+02:00, 10 probes executed; 3 info-severity findings —
docs-only HEAD move verified empty src/tests diff, LIKE-pattern id
collision probe confirmed distinct ids, scratch artifacts in Temp only;
verdict JSON archived at `var/evidence/v4-s4-exactly-once/verdict-deleg_
a3e54630.json`, sha256 8cc17f04…). Ledger:
`verification.passed:verdict-v4-s4-exactly-once:round1` +
`evidence.created:v4-s4-exactly-once-close` (schema 16).

## Problem (observed, not assumed)

A mission pass interrupted AFTER its builder completed but BEFORE
verification finished could not be resumed generically under the same
run_id:

- the builder replay path existed (dispatcher `_replay_completed`:
  COMPLETED row + identical context manifest hash → no re-run), but
- the verifier leg re-dispatched under the SAME verifier run id
  (`<run_id>-verify`): the `agent_run.started` idempotency key already
  existed → **EventConflict** crash (reproduced as the RED test), and
- the interrupted pass's `verification.started` verdict row dangled in
  STARTED forever (no terminal event, no correction).

The directive: "If verification is interrupted after a builder completed,
resume verification without rerunning the builder. Make recovery generic,
durable and state-aware. Prove actual external mutation count equals one.
Do not treat multiple builder executions as exactly-once replay."

## Repair (`mission/loop.py` + `mission/verification.py`)

Between the VERIFYING advance and `start_verification`:

1. **Dangling-verdict correction** — `dangling_verdict_for_run(db,
   verifier_run_id)` finds a STARTED verdict tied to the pass's verifier
   run; the resumed pass records `verification.blocked` additively
   (reason "verification pass interrupted before its outcome was
   recorded; superseded by the resumed pass"). History preserved; the
   projection terminates the row.
2. **Fresh verifier run id** — when the pass's verifier run row exists
   WITHOUT a terminal status (interrupted RUNNING or reaped FAILED),
   the verifier dispatches under `<run_id>-verify-r<N>` (N = count of
   existing `<run_id>-verify%` rows — collision-free, deterministic).
   Reusing the old id would either EventConflict on the started key or
   have its completion blocked by the S3 terminal guard (a reaped row
   stays FAILED while the loop advances — contradictory).
3. **Replay stays the rule** — a COMPLETED verifier row replays through
   the dispatcher (no new session); the builder replays from the ledger
   in every resume. External mutation count == 1 (proven: single
   builder started event + byte-identical deliverable after resume).

## Tests (3 new — tests/foundry/test_exactly_once_resume.py)

- interrupted-verifier resume: PASSED outcome, exactly one builder
  execution (started-event count + byte-identical deliverable), fresh
  verifier id COMPLETED, dangling verdict BLOCKED, fresh verdict PASS.
- reaped-verifier-row resume: fresh id used, old row stays FAILED
  (monotonic), new row COMPLETED.
- completed-verifier replay: NO new verifier session, replayed PASS
  decides, dangling verdict still corrected.

RED evidence: the pre-fix code crashed with
`EventConflict: idempotency conflict for agent_run.started:run-x1-verify:
vertex_ai` on the interrupted-verifier resume, and the dangling verdict
stayed STARTED.

## Evidence

- Full suite: 441 passed EXIT=0 (was 438) — artifact regenerated at
  `var/evidence/v4-s4-exactly-once/suite-v4-s4.txt`.
- Next: independent adversarial verifier (freeze discipline), ledger
  close events after PASS.
