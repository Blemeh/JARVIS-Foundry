# V4-S5 — Real Monitors Registry (DIR-2026-08-12 item 6)

**Slice:** V4_CONTINUOUS_AUTONOMY — truth-critical reliability
**Commit:** `fea9167` (feat(v4-s5): durable monitor registry wired into
the continuous loop) → **repaired at `19fbb17`** (fix(v4-s5-r1): poll
integrity)
**Date:** 2026-08-13
**Status:** ROUND 1 VERIFIER **FAIL** (deleg_71efdc9e — real findings,
repaired) → ROUND 2 VERIFIER <PENDING at 19fbb17>

## Round-1 findings (deleg_71efdc9e) and the repair

1. **HIGH (P6c)** — repeated polls/identical cycles INCREASED selectable
   candidates: the poll feed ran through `generate_candidate` per
   monitor; duplicate rows were inserted NEW (projection hardcoded
   'NEW') and `_top_scorable` selected them — two real duplicate
   missions with identical objectives were demonstrated. **Repair:**
   the poll block now runs BEFORE the queue conversion with a feed
   filtered to OPEN + non-empty + NOT-yet-covered items
   (`covered_objective`); `poll_monitor` itself skips covered and empty
   objectives; `discovery.candidate_generated` duplicates land
   **REJECTED** (recorded, never selectable) — the projection-level
   root cause.
2. **MEDIUM** — queue-REJECTED low-value items resurrected through the
   feed. **Repair:** status filter (OPEN only); regression proves only
   the genuinely-OPEN UNSUPPORTED signal surfaces.
3. **LOW** — `discovery.monitor_polled` for an unknown monitor appended
   silently. **Repair:** projection guard raises ValueError → the
   append fails atomically.
4. **LOW** — empty objectives fabricated candidates. **Repair:**
   `poll_monitor` skips them.
5. **INFO** — crash-retried polls appended twice; no UNIQUE constraint.
   **Repair:** poll key = loop_id (crash-retry dedup); migration v18
   `UNIQUE(source_kind, source_ref)` on monitors (SCHEMA_VERSION 18).

7 new integrity tests (tests/foundry/test_monitor_poll_integrity.py);
2 contract updates (duplicates never rows / never selectable);
suite 478 → 485 passed EXIT=0 at 19fbb17.

## Evidence

- R1 suite artifact: `var/evidence/v4-s5-monitors/suite-v4-s5.txt`
  (468 EXIT=0 at fea9167 — note: generated while the S6 scoring edit
  was in flight, flagged by the verifier).
- R2 suite artifact: `var/evidence/v4-s5-monitors/suite-v4-s5-r1.txt`
  (485 EXIT=0 at 19fbb17).
- Verdicts archived: `verdict-deleg_71efdc9e.json` (FAIL, 15 probes,
  sha256 e6ddd7ce…).
- Next: round-2 independent verifier; ledger close after PASS.
