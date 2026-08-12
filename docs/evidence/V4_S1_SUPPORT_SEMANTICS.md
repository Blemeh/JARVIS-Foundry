# V4-S1 — Support Semantics + Provenance Hygiene (DIR-2026-08-12 items 4–5)

**Slice:** V4_CONTINUOUS_AUTONOMY — the package's named FIRST MISSION
**Commit:** `982f472` (feat(v4-s1): source-kind-aware claim support +
provenance hygiene)
**Date:** 2026-08-13
**Status:** CLOSED — independent verifier **PASS** (deleg_12682a02,
2026-08-13T00:53+02:00, 17 probes; 5 info-severity findings — all
documented legacy contracts or safe-direction behavior (registry-only
mode, case-insensitive FS with content verification, symlink probe
blocked by Windows privilege but guard code-verified, repo basename
collision = false negative never fabricated support, working-tree doc);
verdict JSON archived at `var/evidence/v4-s1-support/verdict-deleg_
12682a02.json`, sha256 7e79fd57…).
**LIVE CLOSE:** schema 16→17; atomic projection rebuild over 14,945
events (converged 766 knowledge_claims run_id rows + 14 agent_runs
monotonicity rows; reconcile consistent, 0 mismatches); support re-scan
**953/954 SUPPORTED (1 unsupported — was 894)**: the false premise is
re-measured; cand-e93f2a83a072 + cand-00d0b3a03872 superseded
additively; ledger `verification.passed:verdict-v4-s1-support:round1` +
`evidence.created:v4-s1-support-close` (events 14,945→14,953, schema
17); daemon healthy, heartbeats resumed within 2 beats. Backup:
`var/evidence/v4-s1-support/live-before-s1-close.db` (sha256
641b98f5…).

## Problem (measured, not assumed)

1. `note_support` only knew vault sources: a claim was SUPPORTED iff its
   source event mapped to an ACTIVE `vault_files` row. Live counts (954
   claims = 26 vault + 766 handoff + 69 repo + 93 system): the
   maintenance signal therefore counted 894/920 claims UNSUPPORTED —
   yet every referenced source EXISTS (repo 69/69 shas in the bounded
   log, system 93/93 events on the ledger, handoff 18/18 files in
   docs/handoffs, vault 25/26 line hashes still present). The old
   check's false-positive rate was ~99.9%; two open maintenance
   candidates (cand-e93f2a83a072 "813", cand-00d0b3a03872 "853") rest on
   the false premise.
2. 766 live claims carry the malformed doubled run_id `handoff:handoff:`
   (the ingestion generator prefixed the kind twice for directory
   sources).

## Repair

1. **Source-kind-aware support** (`vault_retrieval.py`): `note_support`/
   `batch_support` resolve SUPPORT by kind — vault (registry ACTIVE;
   with the root: on-disk hash == index hash AND the referenced line
   hash exists), handoff/research (file exists + line hash; dir missing
   = UNSUPPORTED), repo (commit in the bounded git log, depth 2000,
   basename match), system (event sequence on the ledger); unknown or
   unverifiable = UNSUPPORTED. Per-call cache: each file read/extracted
   once, each repo log fetched once. Path-escape guards on every file
   resolution.
2. **Threading**: `retrieve_vault`, `detect_research_items`,
   `_maintenance_candidates` take the roots; `run_one_loop` gained
   `research_dirs` and passes everything through — the loop's signals
   are honest again.
3. **Provenance hygiene**: `normalize_source_run_id` applied at the
   PROJECTION boundary (derived rows clean, event payloads untouched,
   rebuilds deterministic). The generator is intentionally NOT changed:
   altering the payload shape would break the content-derived dedup key
   for re-ingested files (WP9 slice-4 lesson).
4. **Additive supersession**: migration v17 rebuilds `candidates` with
   the status CHECK widened by `SUPERSEDED` + `superseded_by`;
   `discovery.candidate_superseded` event + projection; idempotent
   `supersede_candidate`; selection scans NEW/SCORED only, so a
   superseded candidate is never executed. The two false-premise open
   candidates are superseded at the live close with the re-scan
   evidence as the reason.

## Tests (21 new)

- `test_support_semantics.py` (18): per-kind support matrices
  (vault ACTIVE/changed-line/missing-registry; handoff exists/changed/
  missing/no-dir; repo present/unknown-sha/no-repo; system present/
  missing; unknown prefix; batch over mixed kinds), run_id
  normalization (doubled handoff/research fixed, single prefixes
  untouched, historical payload preserved).
- `test_candidate_supersession.py` (3): supersede flips + excludes from
  selection; idempotent re-delivery; v16→v17 migration (v16 CHECK
  rejection A/B, rows preserved, widened CHECK accepts SUPERSEDED).

## Evidence

- Full suite: 462 passed EXIT=0 (was 441) — artifact regenerated at
  `var/evidence/v4-s1-support/suite-v4-s1.txt`.
- Live re-scan + supersession + v17 application are the LIVE-CLOSE
  step, rehearsed on a copy first (see the close record).
- Next: independent adversarial verifier (freeze discipline).
