# V6-S8 — ATOMIC PROMOTION AND AUTOMATIC ROLLBACK

```text
STATUS: PASS
PHASE: V6_CONTINUOUS_SELF_EVOLUTION
SLICE: S8 — atomic promotion and automatic rollback
OBJECTIVE: promotion requires the full gate chain (candidate slot,
  trusted independent PASS verdict, measured improvement, successful
  canary for R2/R3) and applies the slot transition ATOMICALLY
  (promotion_intent + slot batch + promoted in ONE transaction,
  crash-recoverable). Rollback is freeze-first, uses the trigger
  vocabulary, restores the exact previous release, quarantines the
  failed release, and records the reason. Candidates can never
  promote or roll back their own release.
BASE COMMIT: 9507c50 (S7 close)
CANDIDATE RELEASE: none (machinery slice)
RISK CLASS: R3-class machinery (promotion/rollback control) built and
  verified inside the isolated V6 construction; live surfaces untouched
HYPOTHESIS: n/a (machinery; exercised by the S11 ladder)
BASELINE METRIC: full suite 844 EXIT=0 at S7 close
SUCCESS THRESHOLD: gates refuse missing pieces (slot, trusted verdict,
  measured improvement, R2+ canary); R0/R1 promote without canary; R2
  requires a SUCCESSFUL canary; frozen/STOP/veto block promotion;
  atomic single-batch transitions; crash recovery re-drives INTENT
  promotions idempotently; rollback freezes first, restores previous,
  quarantines the failed release, uses the trigger vocabulary, refuses
  without previous, reconciles post-rollback; candidates cannot
  promote/roll back; all five projection touch points wired; suite
  green
GUARDRAILS: isolated runtime DB only (var/v6-runtime/v6.db, now schema
  v28); live DB stays v20; no live writes; controller identity checks;
  per-round event namespacing so repeated promotion/rollback rounds
  re-apply instead of being deduped as stale re-deliveries (while a
  crashed re-delivery of the SAME round still dedupes)
ACTIONS TAKEN: TDD red (16 tests) -> implemented evolution/promote.py
  (promote_release, rollback_release, freeze_active, unfreeze_active,
  is_frozen, recover_promotions, promotion_status, PromoteError) ->
  migration v28 (promotions + release_control; the control row is
  created LAZILY by the first frozen/unfrozen event so reconcile stays
  event-lineage-clean) + PROJECTION_TABLES + reconcile key map +
  database row/all/count allowlists -> focused green (140 total across
  S1-S8) -> runtime DB migrated to v28 (reconcile consistent) -> full
  suite
FILES CHANGED: src/jarvis_foundry/evolution/promote.py (new);
  tests/foundry/test_evolution_promote.py (new, 16 tests);
  src/jarvis_foundry/migrations.py (SCHEMA_VERSION 28 + migration 28);
  src/jarvis_foundry/projections.py (release.promotion_intent/
  promoted/rolled_back/frozen/unfrozen handlers, PROJECTION_TABLES);
  src/jarvis_foundry/reconcile.py (key map); src/jarvis_foundry/
  database.py (row/all/count allowlists)
LIVE SYSTEMS CHANGED: NONE (verified read-only at close time)
TESTS: 16 new S8 tests + S1-S7 regression (124) + FULL SUITE 860 passed
  EXIT=0 (376.66s; var/evidence/v6-s8/suite-v6-s8-full.txt,
  sha256 71aaad3f4912d06455440d3fb602179551dac12296d831e7362b5d443f62ced9)
VERIFIER: <fill after dispatch>
EVIDENCE: var/evidence/v6-s8/ (suite artifacts, verdict JSON);
  var/v6-runtime/v6.db (isolated runtime, schema v28, consistent)
CANARY: none (machinery slice)
PROMOTION OR ROLLBACK: none
COST AND RESOURCE USE: local only (pytest runs)
UNRESOLVED RISKS: same-provider verifier limitation (D-031); the
  rollback lineage requires a PROMOTED promotion record for the live
  release (genesis releases set via set_slot have no promotion record
  and cannot be rolled back — correct, since there is nothing to
  restore); post-rollback reconcile inconsistency raises a severity-one
  PromoteError after the batch committed (audit-visible, never silent)
NEXT AUTONOMOUS ACTION: S9 — schema/state compatibility and restoration
OWNER INPUT REQUIRED: NONE
```

## Design

`src/jarvis_foundry/evolution/promote.py`:

- `promote_release(...)` — gate chain: controller identity; STOP;
  veto_check; frozen state; manifest exists; candidate slot; trusted
  PASS verdict (is_trusted_verdict with the request's builder
  identity); R2/R3 require a SUCCESSFUL canary; the lab comparison
  (baseline vs candidate runs) must record improvement_met=True and no
  guardrail violations. The transition is ONE append_events batch:
  release.promotion_intent -> slot_changed(previous := old live) ->
  slot_changed(live := candidate) -> slot_changed(candidate := clear)
  -> release.promoted. Slot/freeze events are NAMESPACED by the
  promotion id so a NEW round always re-applies while a crashed
  re-delivery of the SAME round dedupes.
- `rollback_release(..., trigger, reason, freeze=True)` — trigger
  vocabulary enforced; live + previous required; the lineage of the
  live release (latest PROMOTED promotion) is resolved and marked
  ROLLED_BACK; one atomic batch: release.frozen (freeze precedes
  rollback) -> quarantine := failed live -> live := previous ->
  previous := clear -> release.rolled_back. Post-rollback reconcile;
  inconsistency raises a severity-one PromoteError.
- `recover_promotions(...)` — INTENT promotions are re-driven to
  PROMOTED by re-applying the same namespaced batch (idempotent).
- `freeze_active` / `unfreeze_active` / `is_frozen` — release_control
  singleton row created lazily by the first event.

Projection `promotions` (migration v28, status CHECK INTENT/PROMOTED/
ROLLED_BACK, trigger/reason/finished_at) and `release_control`
(singleton id=1, frozen flag) wired into PROJECTION_TABLES, the
reconcile key map, and database row/all/count allowlists. Handlers
validate mandatory keys, the trigger vocabulary, and status
transitions (INTENT-only promote; INTENT/PROMOTED-only rollback)
inside the append transaction.

## Tests (16)

gates refuse missing pieces (R2 without canary); candidate slot
required; FAIL verdict refuses; R0 promote without canary succeeds
(previous retained, candidate cleared); R2 with a FAILED canary
refuses; R2 with a successful canary promotes; frozen/STOP/veto block;
candidate cannot promote; atomic single batch (event-count exact);
crash recovery re-applies an INTENT (idempotent, PROMOTED, pointers
correct); rollback restores previous + quarantines + ROLLED_BACK +
frozen; rollback without previous refuses; trigger vocabulary enforced
(all 11 triggers accepted in sequence with unfreeze/re-candidate
rounds); post-rollback reconcile consistent; candidate cannot roll
back; projection/schema integrity (pinned to the imported
SCHEMA_VERSION constant >= 28; lazy release_control row).
