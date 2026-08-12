# WP10 — V3 DISCOVERY AND BUILDING, Slice-1: Candidate Pipeline

Status: **IMPLEMENTED** (2026-08-12; 9/9 tests green; independent
verifier <PENDING>). WP10 ACTIVE (docs/planning/CURRENT_WORK_PACKAGE.yaml).

## Delivered (bounded slice-1 scope)

1. **Schema v10** (migrations.py, migration 10): `candidates`
   (candidate_id PK, mission_id, source, objective, objective_canonical,
   evidence_json, status NEW/SCORED/APPROVED/REJECTED/EXECUTED,
   duplicate_of, created_from) with canonical + mission indexes;
   `candidate_scores` (candidate_id PK, score, weights_json).
2. **Events** (projections.py handlers): discovery.candidate_generated,
   discovery.candidate_scored, discovery.candidate_approved,
   discovery.candidate_rejected, discovery.candidate_outcome_recorded.
3. **`discovery_candidates.py`**:
   - `generate_candidate` — records with evidence; DUPLICATE detection
     via the canonicalized objective against existing candidates AND
     missions (canonical-to-canonical; duplicate_of recorded, nothing
     created).
   - `score_candidate` — calibrated deterministic registry weights
     (value/information-gain/urgency positive; effort/cost penalties;
     score in [0,1]; same inputs → same score; higher-value objectives
     score higher).
   - `approve_candidate` — validates the candidate exists and is
     scorable, then appends discovery.candidate_approved +
     mission.created (the mission carries created_from=candidate:<id>).
   - `reject_candidate` / `record_outcome` — status transitions with
     reasons (outcome tracking: prediction vs reality in evidence).
4. Registries updated: PROJECTION_TABLES, reconcile key-column map,
   row/all/count allowlists (candidates + candidate_scores).

## Evidence

- Tests: `tests/foundry/test_discovery_candidates.py` — 9 tests (red
  before implementation, green after): generation row; canonicalized
  duplicate detection (case/whitespace/punctuation); duplicate vs
  EXISTING MISSIONS; scoring determinism + registry calibration
  (higher-value > lower-value); approval → mission.created with the
  candidate id; rejection status; outcome tracking (EXECUTED +
  prediction/reality); rebuild determinism; schema version == code.
- V1/V2 regression: knowledge 45/45 + all pre-existing tests green
  (expect full suite EXIT=0, 302 tests).
- Full suite: <PENDING> → var/evidence/wp10-slice1/suite-slice1.txt.

## Live-DB impact

Schema v10 applies to the live DB on the next initialize() (the
established closure/deployment path; candidates are written only to
throwaway temp DBs in the tests — zero discovery events on the live
ledger).

## Next

- Independent verifier for slice-1 (fresh adversarial session).
- Slice-2 per the V3 design: monitored sources (recurring candidate
  generation feeds), mission execution via the established teams
  machinery, outcome tracking wiring on mission.completed.
