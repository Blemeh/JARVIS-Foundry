# WP11 — V2-OPERATIONAL, Slice A: Real Obsidian Vault Indexing

Status: **IMPLEMENTED** (2026-08-12; 10/10 tests green; full suite 322
EXIT=0; ledger evidence.created:wp11-slice-a). Independent verification
happens at the 16-criterion V2 operational gate (directive Phase 4).

## Delivered (bounded slice-A scope)

1. **Schema v12** (migrations.py, migration 12): `vault_files`
   (rel_path PK, content_sha256, size_bytes, mtime, first_seen,
   last_synced, status CHECK('ACTIVE','REMOVED')) +
   `vault_sync_state` (sync_id PK, root, started_at, completed_at,
   counts, cursor).
2. **Events** (projections.py handlers): `vault.file_indexed` (upsert —
   history stays in events, rows keep latest), `vault.file_removed`
   (status REMOVED — append-only), `vault.sync_completed` (run record).
   All rows are EVENT-DERIVED (rebuild + reconcile reproduce them
   exactly).
3. **`vault_indexer.py`**: `sync_vault(db, root, subset=None)` —
   bounded subset walk (default governed area), sha256 + size + mtime
   per file, hash-compare idempotency, per-file event appends (no giant
   transaction → crash leaves partial-but-consistent state; re-run
   resumes), REMOVED detection, sync report.
4. Registries updated (all five touch points): PROJECTION_TABLES,
   reconcile key-column map (vault_files→rel_path,
   vault_sync_state→sync_id), database row/all/count allowlists.

## Contract tests (tests/foundry/test_vault_indexer.py, 10)

initial sync indexes files with correct hashes; index events emitted
with payload (path/hash/size/mtime); unchanged re-sync is a no-op (no
duplicate events); changed file updates hash; deleted file → REMOVED +
event; restart mid-sync resumes without duplicates (partial subset then
full); subset bound respected; **STOP-text note creates zero control
events** (criterion 13 discipline); rebuild + reconcile deterministic
(rows byte-identical, reconcile consistent); sync_state row recorded.

## Live-DB impact

Schema v12 applies on the NEXT documented initialize() (gate-evidence
step, G3 discipline). Zero vault events on the live ledger so far (all
tests use throwaway temp DBs and temp vaults).

## Next

Slice B — whole-vault retrieval (filters, provenance/stale/
contradictions/support surfacing, FP/FN/leakage measurement) per
docs/planning/V2_OPERATIONAL_DESIGN.md.
