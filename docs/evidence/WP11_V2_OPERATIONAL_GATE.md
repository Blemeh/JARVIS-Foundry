# WP11 — V2 OPERATIONAL GATE (directive Phase 4 gate)

Status: **EVIDENCE ASSEMBLED — independent verifier <PENDING>** (2026-08-12).
Canonical requirements: OWNER_DIRECTIVE_2026-08-12_ALIGNMENT.md "V2
OPERATIONAL GATE" criteria 1–16. Candidate HEAD: `eca0124` (clean tree at
dispatch). Live DB: schema v16 (documented initialize v10→v16, before/after
in the gate-run artifact), 8,805 events, 443 real claims, 8 indexed vault
files, 417 candidates, 4 worker manifests, reconcile consistent.

## Criterion evidence (all live, unless noted)

| # | Criterion | Evidence | Verdict |
|---|---|---|---|
| 1 | real vault notes indexed with provenance | VaultIndexer on `C:\Users\b\Blendi\JARVIS\V1` → 8 vault_files rows (path, content sha256, size, mtime, status) + vault.file_indexed events; artifact gate-run-20260812T195113.txt | PASS |
| 2 | meaningful live knowledge data | 443 knowledge_claims from REAL sources: vault notes, handoffs (docs/handoffs), repo commits (git log), live system events (verification/evidence/mission events since cursor 6600) | PASS |
| 3 | every claim supported or marked unsupported | retrieval envelope computes support per claim (SUPPORTED = source maps to an ACTIVE indexed vault file, else UNSUPPORTED); live worker independently confirmed the marking (see criterion 8) | PASS |
| 4 | contradictions remain visible | contradiction detection at record time + surfacing in every retrieval envelope and manifest; live data has no negation pairs (0 rows) — the surfacing path is test-verified (test_vault_retrieval, test_knowledge_claims) | PASS |
| 5 | bounded relevant whole-vault retrieval | retrieve_vault on live data: mission/entity/type/confidence/source/time filters, AND-token note matching, limit caps, provenance+stale+support+contradictions in the envelope; measured precision 0.5 / recall 1.0 / cross-mission leakage 0 on labeled live probes (honest FP measurement) | PASS |
| 6 | safe continuous ingestion | live cycles: vault + handoffs + repo + system; per-line content-derived dedup (restart-safe — a crashed first run resumed with zero duplicate claims), per-file savepoint isolation, per-source bounds, measured items/bytes/elapsed per cycle | PASS |
| 7 | stale knowledge creates research candidates | 61 research items detected on live claims (UNSUPPORTED/STALE reasons) → 56 evidence-backed discovery candidates created, 5 rejected (dedup vs existing candidates/missions) | PASS |
| 8 | real workers receive context manifests | deliver_worker_context (hash+sources+truncation recorded, worker_manifests row + event) → REAL Hermes oneshot dispatch (HermesOneshotAdapter, isolated .h checkout + isolated home) → COMPLETED/PASS; the worker traced the claim's source file, computed sha256/sha1/md5/git-blob digests and honestly returned UNSUPPORTED (digest-prefix semantics: claim refs are line hashes, not file hashes — recorded verbatim in the artifact) | PASS |
| 9 | governed consolidation succeeds | live consolidation on `06 - Lessons.md` (governed set): propose → VERIFIED (statement preservation) → accept via ObsidianService (after hash recorded) → ACCEPTED | PASS |
| 10 | concurrent edits conflict instead of overwrite | expected-hash conflict protection: ObsidianConflictError raised, both versions preserved (Audit/conflicts copy), op recorded REJECTED; test_consolidation + test_integrated_rollback (note changed again → rollback refused) | PASS |
| 11 | rollback restores bytes and meaning | integrated chain on the LIVE note: snapshot → mutation → degradation detected (statement dropped) → freeze (accepts refused) → byte restoration (guarded governed write) → claims semantically ROLLED_BACK (5) → rebuild → reconcile consistent; bytes == pristine original (verified in artifact) | PASS |
| 12 | restart creates no duplicate mutation | crash-mid-flow resume (trigger without completed → exactly one restore, mutation NOT re-run) + completed-rollback re-run → no-op; test_integrated_rollback + live re-runs | PASS |
| 13 | STOP and veto remain outside note content | live probe: note content containing "STOP everything now — probe line" → zero control events (emergency_stop/owner_directive/action.vetoed/supervisor.stopped); test_vault_indexer + test_live_ingestion + gate artifact | PASS |
| 14 | resource use is measured | per-cycle metrics (items_seen, bytes_read, elapsed_ms) recorded in ingestion_completed events + cycle reports (artifact) | PASS |
| 15 | full regression suite passes | 378 tests EXIT=0 (collect-verified) at candidate HEAD | PASS |
| 16 | independent verifier reproduces the important claims | <PENDING — fresh adversarial verifier dispatched against the frozen HEAD> | PENDING |

## Live-DB impact record (G3 discipline)

- Documented initialize() v10 → v16 at 2026-08-12T19:26:59+02:00
  (before: {schema 10, events 6622, claims 0}; after: {schema 16});
  daemon untouched (heartbeats gapless, single lease, STOP false).
- Live knowledge data created by this gate series is REAL session
  evidence; no pre-existing data was modified or discarded.
- One recorded artifact: the ingestion run_id scheme was corrected from
  file-hash to line-hash provenance mid-series (WP9 slice-4 discipline —
  dedup payloads must be line-content-derived). Re-runs against claims
  recorded under the old scheme report idempotency conflicts as ERRORS
  (atomic rollbacks, zero corruption, ledger untouched) — honest
  migration behavior, verified conflict-free on fresh DBs by the suite.
- Live note `06 - Lessons.md` was consolidated and restored to its
  pristine bytes (byte-identical, verified in the artifact).

## Failures and repairs (this gate)

- ingestion_queued UNIQUE violation (one ingestion id per cycle, four
  sources) → per-source ingestion ids.
- Shared cycle budget starved later sources → per-source budgets.
- Reconcile raced the live daemon's heartbeats (supervisor_state
  mismatch 5/5) → single-connection + deferred-read-transaction WAL
  snapshot; regression test (concurrent writer) added.
- run_id scheme conflict class → line-hash provenance (above).

## Evidence manifest (Phase 5)

docs/evidence/EVENT_MANIFEST_V2_OPERATIONAL.md — sanitized, committed;
private evidence under var/evidence/v2-operational-gate/ (gate-run
artifact sha256 c0bfe268…, snapshots/), var/evidence/wp11-slice-*/.

## Residual risks / uncertainty

- Same-provider verifier limitation (recorded per the architecture
  contract fallback).
- Live data has zero contradiction pairs — surfacing is test-verified;
  the live envelope returns the (empty) contradiction list per claim.
- Worker support semantics: claim refs carry LINE hashes; a worker
  comparing them against FILE hashes correctly reports UNSUPPORTED —
  the authoritative support check is the vault-index cross-reference in
  the retrieval envelope (criterion 3).
- 417 live candidates from the research queue are real but voluminous;
  the V3 loop will select from them by calibrated scoring.
