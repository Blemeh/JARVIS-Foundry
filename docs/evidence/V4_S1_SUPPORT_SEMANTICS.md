# V4-S1 — Support Semantics + Provenance Hygiene (DIR-2026-08-12 items 4–5)

**Slice:** V4_CONTINUOUS_AUTONOMY — the package's named FIRST MISSION
(support-semantics repair discovered by the V3 loops)
**Commit:** <PENDING>
**Date:** 2026-08-12
**Status:** PLANNED — live data sized; implementation after S2/S3/S4

## Problem (measured, not assumed — read-only live probes 2026-08-12T23:40–23:55)

1. `note_support` (vault_retrieval.py:39-52) marks a claim SUPPORTED only
   when its source event maps to an ACTIVE `vault_files` row. Claims from
   handoff/repo/system sources can never be supported by that check.
   Live counts: 954 claims = 26 vault + 766 handoff + 69 repo + 93
   system. The maintenance signal (v3_loop._maintenance_candidates) uses
   the same vault-only SQL → 894 claims falsely UNSUPPORTED →
   maintenance missions (cand-c41b7e04dd81, cand-767d55ed2d5b EXECUTED;
   cand-e93f2a83a072 "813", cand-00d0b3a03872 "853" still SCORED/OPEN).
2. Malformed provenance: 766 live claims carry `handoff:handoff:FILE.md`
   run_ids (live_ingestion._source_run_id double-prefixes when the rel
   path already carries the kind prefix).

## Honest re-scan results (what the fix will produce)

- handoff: 766 claims / 18 unique files — ALL 18 files exist in
  docs/handoffs (existence check); line-hash re-derivation to verify.
- repo: 69/69 commit shas present in the bounded git log.
- system: 93/93 event sequences present on the ledger.
- vault: 25/26 line-hash matches; 1 changed line; 0 missing files.
- Post-fix unsupported ≈ 1 (the changed vault line) vs 894 before —
  the old check's false-positive rate was ~99.9%.

## Repair

1. `note_support(connection, claim, *, vault_root, handoff_dir,
   research_dirs, repo_paths)` — kind-aware (vault registry ACTIVE +
   line-hash; handoff/research file exists + line-hash; repo commit in
   bounded git log; system event on ledger); batched variant with a
   per-call file cache for retrieve_vault + detect_research_items.
2. `_source_run_id` — no double prefix (`handoff:FILE.md:sha`).
3. Projection normalization at the knowledge_claims boundary
   (deterministic; rebuild reproduces clean rows; history untouched).
4. `_maintenance_candidates` — kind-aware signal (roots threaded through
   run_one_loop); honest no-op when nothing clears the bar.
5. Migration v17: candidates CHECK widened with 'SUPERSEDED' +
   `superseded_by TEXT` (table rebuild; rehearsed on a live-DB COPY
   first — never the live DB until verified); new event
   `discovery.candidate_superseded` (additive correction); the two open
   false-premise candidates (e93f2a83a072, 00d0b3a03872) superseded
   with the re-scan evidence as reason.
6. Live close: re-scan support on the LIVE claims (staged ledger
   appends), ledger evidence events, doc reconciliation.

## Tests (planned)

- per-kind support matrices (vault ACTIVE/missing/changed; handoff
  exists/changed/missing/dir-None; repo present/absent/unknown-repo;
  system present/absent; unknown prefix)
- run_id format regression; projection normalization regression
- maintenance signal: seeded false-UNSUPPORTED claims stop generating
  candidates after the fix; genuine unsupported still detected
- migration v17 on a v16 copy (data preserved, CHECK widened,
  SUPERSEDED accepted, EXECUTED/APPROVED rows intact)
- supersession event: projection flips SCORED→SUPERSEDED, selection
  scans exclude SUPERSEDED, dedup idempotent
- retrieve_vault/detect_research_items root threading
