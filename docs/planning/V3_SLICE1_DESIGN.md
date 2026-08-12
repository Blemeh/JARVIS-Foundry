# V3 DISCOVERY AND BUILDING — Slice-1 Design (candidate pipeline)

> **HISTORICAL DESIGN DOC.** Implemented as schema v10 (the design's
> schema-v6 section was superseded during implementation — see
> docs/evidence/WP10_SLICE1_CANDIDATES.md). Per
> DIR-2026-08-12-master-plan-alignment, engineering-only.

Design only; implementation starts after V2's gate PASS (05 §6 Entry:
"V2 PASS queues V3 automatically"). Bounded, deterministic, test-driven.

## Scope (first bounded slice)

The DISCOVERY LOOP: candidate generation → evidence + duplicate
detection → calibrated scoring → mission creation → outcome tracking
(prediction vs reality). No push/deploy chains in slice-1 (those need
the owner's release policy; recorded as slice-2 with verified
toolchains).

## Schema v6 (migrations.py, migration 6)

- `candidates(candidate_id TEXT PK, source_type TEXT, source_ref TEXT,
  title TEXT, objective TEXT, evidence_json TEXT, duplicate_of TEXT,
  score_json TEXT, decision TEXT CHECK(decision IN
  ('PENDING','SELECTED','REJECTED','DUPLICATE')), created_from TEXT,
  last_event_id, updated_at)`
- `candidate_scores(candidate_id TEXT PK, fit REAL, value REAL,
  info_gain REAL, urgency REAL, confidence REAL, effort REAL, cost REAL,
  risk REAL, reversibility REAL, opportunity_cost REAL, total REAL,
  calibrated_at TEXT)` — the calibrated scoring record (registry-driven
  weights, deterministic).

PROJECTION_TABLES + reconcile key-column map += both.

## Events

- `candidate.generated` — {candidate_id, source_type, source_ref, title,
  objective, evidence, scoring inputs} (idempotency key per source_ref +
  canonical title hash; identical re-delivery deduped, changed payload
  EventConflict).
- `candidate.decided` — {candidate_id, decision SELECTED|REJECTED|
  DUPLICATE, duplicate_of?, reason} (validation: PENDING → decided;
  DUPLICATE requires duplicate_of).
- `candidate.mission_created` — {candidate_id, mission_id} (links the
  scored candidate to the created mission; projection validates the
  mission exists — F9 pattern).

## Duplicate detection (deterministic)

Canonical title/objective fingerprint (lowercase, whitespace/punctuation
collapse — reuse knowledge.canonicalize_statement) against existing
candidates + active missions; exact-fingerprint → DUPLICATE (recorded,
never silently dropped).

## Calibrated scoring (registry-driven, deterministic)

Weights registry (documented defaults, overridable via config event):
fit .20, value .20, info_gain .10, urgency .10, confidence .10, effort
.05 (inverted), cost .05 (inverted), risk .05 (inverted), reversibility
.05, opportunity_cost .10 (inverted). Each input 0..1; total = weighted
sum; the score record stores every input + the weights used
(reproducible). SELECTED = total >= threshold AND fit >= fit_min AND
confidence >= confidence_min (documented gates).

## Outcome tracking (prediction vs reality)

`candidate.outcome_recorded` — {candidate_id, mission_id, outcome 0..1,
measured_at} + projection `candidate_outcomes(candidate_id, mission_id,
predicted_total, outcome, calibration_error)` — calibration error =
predicted - outcome (per-mission record; aggregated stats query).

## Tests (test_candidate_pipeline.py — 10 tests, TDD)

1. candidate.generated → row with evidence + inputs.
2. identical re-delivery deduped; changed payload conflicts.
3. duplicate detection: same canonical title → DUPLICATE + duplicate_of
   (both candidates visible).
4. duplicate vs active mission objective → DUPLICATE.
5. scoring: registry weights reproduce the documented totals
   (hand-computed fixture).
6. decision gates: below confidence_min → REJECTED with reason.
7. candidate.decided validation: decided-from-PENDING only; DUPLICATE
   without duplicate_of → ValueError + rollback.
8. candidate.mission_created: mission missing → rollback; present →
   created_from link.
9. outcome: calibration error computed; aggregate stats query bounded.
10. rebuild + reconcile deterministic (v6 key columns).

## Integration

- Supervisor next-action selection consumes the top scored SELECTED
  candidate when no mission is active (V3 continuous operation).
- Evidence: every candidate row links source_ref + evidence_json;
  rejection reasons recorded (no performative activity).

## Verification (per V3 exit evidence, first slice)

Slice-1 closes with: full suite green + test_candidate_pipeline green +
staged pipeline smoke (throwaway DB) + independent verifier (fresh
adversarial session) + ledger records. Same discipline as V1/V2.
