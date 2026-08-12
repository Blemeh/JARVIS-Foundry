# V4-S6 — Bounded, Fair Candidate Scoring + Doc Reconciliation (DIR items 7–8)

**Slice:** V4_CONTINUOUS_AUTONOMY — truth-critical reliability
**Commit:** <pending — code commit for the scoring context>
**Date:** 2026-08-13
**Status:** IMPLEMENTED — independent verifier <PENDING>

## Problem (observed, not assumed)

1. `score_objective` was a pure function of the objective text: no
   freshness, source trust, evidence richness, previous-attempt
   cooldown or mission-similarity consideration — the directive's
   "bounded and fair evaluation" factors were absent.
2. The live loop re-scores SCORED candidates every cycle; that is only
   safe while the score is context-free (identical payloads dedupe).
   Any context-aware formula MUST freeze recorded scores, or age/attempt
   drift would EventConflict the live loop.

## Repair

1. **Context factors** (`score_objective`, extended — all bounded):
   freshness (exponential decay, 1-week horizon, ≤ +0.05), cooldown for
   previous attempts (cap 5, ≤ −0.15), source trust (vault/system 1.0,
   repo 0.95, handoffs/maintenance 0.9, research_queue 0.85; |Δ| ≤
   0.02), evidence richness (cap 5 items, ≤ +0.05), mission-similarity
   penalty (cap 3, ≤ −0.06). The base [0,1] calibration and the
   selection threshold (min_score 0.35) are UNCHANGED — the bar is the
   same, the order is fairer; thresholds are never lowered to
   manufacture work.
2. **Frozen recorded scores**: `score_candidate` returns the stored
   `candidate_scores` value for SCORED rows without recomputing or
   appending — the scored event is append-once, and the live loop's
   per-cycle re-scan cannot conflict. Context is computed from the
   candidate's own DB state (generated-event age, source, evidence
   length, same-objective missions/candidates, token-similar missions)
   and recorded in the scored event's weights for auditability.
3. **Doc reconciliation (R8)**: PROJECT_STATE, CURRENT_WORK_PACKAGE,
   DECISION_LOG and RISK_REGISTER updated to match live truth (schema
   v17, 462+ tests, five slices closed, support 953/954, 780-row drift
   resolved). Historical statements remain historical.

## Tests (10 new — tests/foundry/test_scoring_context.py)

freshness decay (exact delta), cooldown + clamp-at-cap, source-trust
ordering, evidence bonus + clamp, similarity penalty, extreme-input
boundedness, threshold guard (0.35 never lowered), DB-context wiring
(previous attempt + source + evidence reflected in the recorded
weights), **frozen-scored regression** (context drift after scoring
never changes the score nor appends a second event), determinism
(fresh + frozen paths agree; one scored event).

## Evidence

- Focused suites green (scoring, supersession, v3 loop, discovery,
  support semantics, exactly-once, routing).
- Full suite artifact regenerated at `var/evidence/v4-s6-scoring/`.
- Next: independent adversarial verifier (freeze discipline), ledger
  close after PASS.
