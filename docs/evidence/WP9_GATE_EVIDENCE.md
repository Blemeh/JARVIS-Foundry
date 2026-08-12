# WP9 GATE — V2 exit evidence mapping (05 §5 exit criteria → evidence)

Draft — finalized when slice-5's verifier lands and the WP9 gate review
dispatches.

## 05 §5 V2 exit criteria → evidence

| Criterion | Evidence | Verifier | Ledger |
|---|---|---|---|
| Knowledge brain functions with provenance, confidence, freshness, contradiction | slice-1: knowledge_claims (source_run_id/event_id, confidence, fresh_until, status; contradiction pairs CONTRADICTED); tests 10/10; docs/evidence/WP9_SLICE1_EPISTEMIC_CORE.md | deleg_548b6bb6 (round-2c PASS; rounds 1a/1b/2b FAIL → repaired) | 3731–3735 |
| Whole-vault retrieval | slice-2: retrieve() (type filter, confidence ordering, freshness, limit) — tests 9/9; WP9_SLICE2_RETRIEVAL.md | deleg_7c4759fa (round-2 PASS; round-1 FAIL S2-g → repaired) | 4089–4091 |
| Bounded context manifests | slice-2: build_context_manifest (hints, STALE explicit, conflicts mission-scoped, explicit truncation, stable hash) — same verifier | deleg_7c4759fa | 4089–4091 |
| Entity resolution | slice-3: registry + aliases + validated links + deterministic resolve; tests 8/8; WP9_SLICE3_ENTITIES.md | deleg_01ebd46f (FIRST-round PASS) | 4202–4203 |
| Continuous ingestion | slice-4: ingest_documents (source-hash dedup, deterministic payloads, retry-safe); tests 10/10; WP9_SLICE4_INGESTION.md | deleg_3cce471c (rounds 1/2/2b FAIL → repaired ×3) | 4476–4478 |
| Snapshots / backup / restore / semantic rollback | slice-5: create/verify/readonly-restore/rollback (append-only; content-sha256 verification); tests 8/8; WP9_SLICE5_SNAPSHOTS.md | deleg_23953faa (round-2 PASS; round-1 FAIL F1/F2/F7 → repaired fc6a8e1) | 4667–4669 |
| Schema deployment path | migrations v5→v9; reconcile v9 keys + PROJECTION_TABLES covered by tests | (covered by each slice verifier) | — |
| Regression | full suite EXIT=0 (293 expected); knowledge 45/45; every slice verifier re-ran the suite artifact | each slice verifier | — |

## Honesty notes

- **CORRECTED (gate review deleg_d0a4244d G3):** no knowledge DATA was
  ever written to the live ledger (zero knowledge.* events; all
  knowledge_* tables EMPTY) — but the slice-closure tooling's
  initialize() DID apply the schema migrations to the live daemon DB
  (v5→v9, same-second with the closure ledger events; the daemon never
  restarted; gapless heartbeats; daemon healthy). The earlier "live DB
  untouched (v4)" claim was wrong; corrected in the ledger
  (evidence.created:wp9-gate-truth-correction, seq 4789) and in every
  slice doc. Additive DDL only; no rollback migration needed.
- Every slice carries its full verifier lineage (FAIL rounds + repairs +
  regressions) in its evidence doc — no hidden failures. Archived
  verifier JSONs cover all rounds (incl. the previously unarchived
  bb94028c + cd7da327 FAIL JSONs). Note: the archived slice-2 round-2
  JSON records its T3 FAIL (artifact read mid-regeneration); the ledger
  PASS event (4090) carries the documented resolution (fresh
  stable-path artifact EXIT=0) — see WP9_SLICE2_RETRIEVAL.md.
- All slice experiments ran on throwaway/staging DBs; the live daemon
  was never stopped or restarted; no knowledge data written live.
- Residuals (recorded): past-tense normalization out of scope (slice-1);
  bounded inflection map; first-registered-wins ambiguity (slice-3);
  whole-batch dedup semantics (slice-4); restore is read-only
  verification, not live-DB replacement (slice-5 — a live restore
  requires the daemon-stopped maintenance path, recorded for the
  maintenance playbook).

## Next (exact)

1. Slice-5 verifier verdict → close (ledger + doc).
2. WP9 gate review (independent adversarial session over this mapping +
   the five slice evidence docs + full suite) → verification.passed:
   verdict-wp9-gate on PASS.
3. PROJECT_STATE v17 (V2 accepted) + CURRENT_WORK_PACKAGE → WP10/V3
   activation (candidate pipeline — design temp/V3_SLICE1_DESIGN.md).
4. Handoff/checkpoint update.
