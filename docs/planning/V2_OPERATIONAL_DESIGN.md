# WP11 V2-OPERATIONAL — Slice Design

Final 2026-08-12 (activated with WP11; drafted while the slice-2 verifier was in flight). Authoritative
requirements: OWNER_DIRECTIVE_2026-08-12_ALIGNMENT.md Phase 4 A–G + V2
OPERATIONAL GATE criteria 1–16. Built on the WP9 engineering core.

## Shared discipline per slice

- TDD: contract tests FIRST (red against current schema), then migration +
  module + projections, then green.
- New projection tables need FIVE touch points: migration DDL,
  PROJECTION_TABLES, reconcile key-column map, database.row() allowlist,
  database.all()/count() allowlists.
- Events: append-only, content-derived idempotency keys (never per-run
  uuids in dedup-key payloads), F9 rollback discipline.
- Schema: one additive migration per slice (12, 13, 14, 15). CHECK-status
  changes need the CREATE-copy-DROP-RENAME rebuild (v9 pattern) — avoid
  unless needed.
- Live DB stays v10 during development; gate evidence applies documented
  initialize() steps with before/after records (G3 discipline).
- Slice docs: docs/evidence/WP11_SLICE*_*.md with status lines + counts.

## Slice A — vault indexing (vault_indexer.py, migration 12)

Tables:
- vault_files(rel_path TEXT PK, content_sha256 TEXT NOT NULL, size_bytes
  INT, mtime TEXT, first_seen TEXT, last_synced TEXT, status TEXT NOT NULL
  DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','REMOVED')))
- vault_sync_state(sync_id TEXT PK, root TEXT NOT NULL, started_at TEXT,
  completed_at TEXT, files_indexed INT, files_changed INT, files_removed
  INT, cursor TEXT)

Module VaultIndexer:
- sync_vault(db, root, subset=None, limit=None) → sync report. Walks
  *.md under subset (bounded; default governed area), hashes each file
  (sha256 of bytes), compares against vault_files:
  - new → INSERT + vault.file_indexed event (rel_path, content_sha256,
    size_bytes, mtime; idempotency key vault.file_indexed:<rel_path>:<sha>)
  - changed (same path, different sha) → UPDATE + vault.file_indexed
    (history of prior hash kept in events; row keeps latest)
  - unchanged → skip
  - seen-before-now-missing → status REMOVED + vault.file_removed event
- Restart-safe: each file op is its own event append (no giant txn); a
  crash mid-sync leaves partial rows; re-running completes the rest
  (hash comparison makes re-runs idempotent). sync_id recorded per run;
  vault.sync_completed event with counts (per-run uuid OK here — it is a
  run record, never a dedup key).
- Untrusted content: note text is NEVER parsed for control; a note
  containing "STOP"/veto words produces only file-index rows (criterion
  13 probe).

Tests (test_vault_indexer.py, ~10): fresh temp vault sync → rows+events;
change file → changed; delete → REMOVED; re-sync no-op; restart mid-sync
(partial sync_state + re-run) → complete, no duplicates; hash correctness
(known bytes); subset bound respected; STOP-text note → no control events;
rebuild determinism; reconcile key columns.

## Slice B — whole-vault retrieval (vault_retrieval.py)

No new tables (uses vault_files + knowledge_claims + knowledge_entities
+ knowledge_contradictions + index state). Reads note TEXT from disk via
vault_files path allowlist (never raw user paths).

Module:
- retrieve_vault(connection, vault_root, query, *, mission_id=None,
  entity=None, claim_type=None, min_confidence=None, source=None,
  time_from=None, time_to=None, limit=50) → envelope:
  {claims: [...retrieve() with filters...], notes: [matching vault notes
  with path, content_sha256, mtime, matched_tokens, score, stale_flag,
  support: SUPPORTED|UNSUPPORTED, contradictions: [...]]}
- Note matching: deterministic token scoring — query canonicalized into
  tokens; note text canonicalized; score = matched-token count; require
  ALL tokens present (AND) for inclusion; rank by score desc, then
  mtime desc; cap at limit. Bounded: tokenize caps, max notes scanned
  per query (index has only hashes; scan reads note bytes — bounded
  subset + cap).
- Filters: mission_id (claims only — notes are not mission-owned; the
  envelope separates them, cross-mission leakage measured on CLAIMS;
  notes carry source paths so a note-derived claim's mission is visible),
  entity (resolve_entity → claims_for_entity → claim ids → retrieve by
  ids; notes linked via claim source_event_id == note rel_path),
  time (claim fresh_until/recorded window; note mtime window),
  confidence (min), source (source_type/source_ref prefix on claims;
  vault sub-path prefix on notes), type (claim_type).
- Envelope always surfaces: provenance (source_event_id, source_run_id,
  path, hash), stale flags, contradiction pairs (by claim ids), and
  per-claim support (SUPPORTED when source_event_id points to an ACTIVE
  vault_files row whose current sha matches the claim's source_run_id
  hash; else UNSUPPORTED) — gate criterion 3.
- evaluate_retrieval(db, vault_root, labeled_probes) → metrics:
  {precision, recall, false_positives, false_negatives, leakage}
  where leakage = returned rows whose mission_id != probe mission.
  Deterministic, used by tests AND gate evidence on real data (bounded
  labeled probe set built from real notes).

Tests (~12): seeded temp vault + claims; AND-token ranking; each filter;
FP/FN metrics on labeled set; leakage probe (mission A query never
returns mission B claims; notes of mission B not linked); support
SUPPORTED/UNSUPPORTED transitions (file changed → claims unsupported);
stale surfaced; contradictions surfaced; limit cap; path allowlist
(traversal attempts rejected).

## Slice C — continuous ingestion (live_ingestion.py)

Module LiveIngestion:
- run_ingestion_cycle(db, sources, *, max_items=50, max_bytes=200_000)
  → cycle report {cycles: {source: {items_seen, items_new,
  claims_created, errors, cursor}}}.
- Source adapters (each bounded + cursor + retry-safe):
  - vault_source(root, subset): delta = files with last_synced < mtime
    (or never indexed) → extract statements → ingest via
    ingest_documents (source_type='vault', source_ref=rel_path,
    event_id=rel_path:linehash). Per-file SAVEPOINT isolation (a
    poisoned file counts as error, others proceed).
  - repo_source(repo_path): git log --format=%H %s since cursor →
    commit subjects as claims (source_type='repo',
    source_ref='<repo>:<sha>') — bounded: max_items commits per cycle.
  - handoff_source(dir): *.md under docs/handoffs (bounded) with mtime
    delta → statements as claims (source_type='handoff').
  - research_artifact_source(dir): var/evidence/*.md (bounded glob
    subset) → claims (source_type='research').
  - event_source(db): live ledger events of meaningful types
    (verification.passed/failed, evidence.created, mission.completed,
    step.completed, fault_injection.completed) → claims
    (source_type='system', statement = "<type> <key> at <seq>",
    claim_type='fact') — cursor = last processed event sequence,
    per-event idempotency key = event_source:<seq>.
- Deterministic statement extraction from markdown: split lines; skip
  frontmatter block, headings, empty lines, list markers stripped;
  statement = line text trimmed, capped 300 chars; claim_id from
  content hash (existing _doc_hash pattern); linehash in source.event_id.
- Resource use measured: per-cycle items/bytes/elapsed recorded in
  ingestion.completed payload + cycle report (criterion 14).
- Retry/restart: duplicate cycle → zero new claims (source-hash dedup);
  partial failure → FAILED per-file errors recorded, cycle completes;
  crash mid-cycle → cursor at last completed file, re-run resumes.

Tests (~12): each adapter with temp fixtures (temp vault dir, git init
temp repo with commits, temp handoffs, temp evidence, temp DB with
events); statement extraction (frontmatter/headings skipped); per-file
savepoint isolation (poisoned file); duplicate cycle no-op; crash-restart
resume; max_items/max_bytes enforced; cursor correctness.

## Slice D — research queues (research_queue.py, migration 13)

Tables:
- research_queue(item_id TEXT PK, claim_id TEXT NOT NULL, reason TEXT
  NOT NULL, evidence_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT
  'OPEN' CHECK(status IN ('OPEN','CANDIDATE_CREATED','REJECTED')),
  created_at TEXT, updated_at TEXT, last_event_id TEXT)

Module:
- detect_research_items(db, *, weak_threshold=0.2, now=None) → scan
  knowledge_claims; item when:
  - STALE: fresh_until < now (reason 'STALE')
  - CONTRADICTED: claim in a contradiction row (reason 'CONTRADICTED')
  - UNSUPPORTED: support status unsupported (reason 'UNSUPPORTED')
  - WEAK: confidence < weak_threshold (reason 'WEAK')
  Dedup: one item per claim_id per reason (idempotency key
  research.queue_item_created:<claim_id>:<reason>); re-scan no-op.
  Low-value rejection: reason WEAK and confidence < 0.1 → status
  REJECTED immediately (recorded, no candidate) — deterministic.
- generate_research_candidates(db) → for each OPEN item: build candidate
  via discovery_candidates API (source_type='research_queue',
  source_ref=item_id, objective = claim statement, evidence = reason +
  claim provenance) — candidate pipeline dedups by canonical objective
  against candidates AND missions; resolved item → CANDIDATE_CREATED
  (or REJECTED when the candidate pipeline returns DUPLICATE with an
  existing mission — the mission covers it).
- Events: research.queue_item_created, research.queue_item_resolved
  (status + target candidate_id).

Tests (~10): stale → item; contradicted → item; unsupported → item;
weak-low → REJECTED; dedup re-scan; candidate generation wires to
discovery; duplicate objective already a mission → item resolved REJECTED
with reference; rebuild determinism; allowlists/reconcile entries.

## Slice E — real worker context (worker_context.py, migration 14)

Tables:
- worker_manifests(manifest_id TEXT PK, run_id TEXT NOT NULL, worker
  TEXT NOT NULL, mission_id TEXT NOT NULL, manifest_hash TEXT NOT NULL,
  sources_json TEXT NOT NULL, truncation_json TEXT NOT NULL, created_at
  TEXT, updated_at TEXT, last_event_id TEXT)

Module:
- deliver_worker_context(db, vault_root, *, run_id, worker, mission_id,
  objective, max_chars=8000) → manifest record: builds the context from
  build_context_manifest (claims) + retrieve_vault (notes) + conflict
  list; assembles a bounded payload; records row + worker.manifest_delivered
  event (manifest_id, run_id, worker, mission_id, manifest_hash, sources
  [claim source ids + note paths], truncated flag + counts).
- reconstruct_context(db, run_id) → rebuilds the EXACT payload from the
  recorded sources (claims by ids from DB, notes by path from vault,
  conflicts by ids) WITHOUT chat history; returns
  {manifest_hash, matches_recorded: bool} — proves replacement workers
  reconstruct identical context (criterion: replacement workers).
- Integration point: roles/adapter dispatch attaches the manifest
  payload as instructions for the worker run; agent_runs gains
  context_manifest_hash already (WP4) — reuse.

Tests (~9): assembly deterministic (same inputs → same hash); delivery
recorded (row + event, sources + truncation exact); reconstruction
byte-identical; truncation recorded; manifest delivered to a REAL worker
run (staged home, bounded oneshot via adapter — gate-time live proof,
unit test uses stub); missing sources after rollback → reconstruction
still deterministic (marks missing); allowlists.

## Slice F — governed consolidation (consolidation.py, migration 15)

Tables:
- consolidation_ops(op_id TEXT PK, note_path TEXT NOT NULL, kind TEXT
  NOT NULL, status TEXT NOT NULL DEFAULT 'PROPOSED'
  CHECK(status IN ('PROPOSED','VERIFIED','ACCEPTED','REJECTED',
  'ROLLED_BACK')), before_hash TEXT, after_hash TEXT,
  original_backup_path TEXT, verified_by TEXT, created_at TEXT,
  updated_at TEXT, last_event_id TEXT)

Module:
- propose_consolidation(db, writer, note_path, new_content, kind) →
  op row PROPOSED; checks the note is inside the governed write root;
  reads current bytes (expected hash) — does NOT write yet.
- verify_consolidation(db, op_id, *, verifier=None) → deterministic
  meaning check: statement preservation — every statement extracted
  from the original note (same extractor as slice C) must canonicalize
  to a statement present in the new content; frontmatter contract
  intact (policy FRONTMATTER keys); byte-size bound; returns VERIFIED/
  REJECTED with per-statement report. Independent verifier (subagent)
  can be recorded in verified_by for gate evidence.
- accept_consolidation(db, writer, op_id) → writes via ObsidianService
  (expected_hash = before hash → concurrent edits CONFLICT, both
  preserved — criterion 10), after_hash recorded, status ACCEPTED,
  consolidation.accepted event; vault index refreshed for the note.
- rollback_consolidation(db, writer, op_id, reason) → byte restore via
  ObsidianService.rollback (refuses if note changed again — never
  clobbers newer edits), status ROLLED_BACK, consolidation.rolled_back
  event; semantic: claims derived from the note re-flagged per
  knowledge_snapshots.semantic_rollback discipline.
- Events: consolidation.proposed/verified/accepted/rejected/rolled_back
  (per-op idempotency keys).

Tests (~12): propose on governed path only (outside → error); concurrent
edit between propose and accept → CONFLICT with both versions preserved;
verification rejects dropped statements; verification passes preserved
statements; accept writes + after hash; rollback restores bytes; rollback
refuses when note changed after accept; idempotency; rebuild; allowlists.

## Slice G — integrated rollback (integrated_rollback.py)

Module IntegratedRollback (orchestrates existing pieces):
- run(db, writer, *, mission_id, note_path, mutate_fn) → demo record:
  1. create_snapshot (existing) — ledger + file
  2. mutate_fn() (caller-supplied; gate uses a consolidation that drops
     a statement)
  3. degradation detection: verify_snapshot (still ok — snapshot is
     pre-mutation by definition) + statement-preservation check on the
     note + manifest sanity (claims for mission still resolvable);
     detector reports DEGRADED with reason
  4. freeze: op row status FREEZED (new status in consolidation_ops? —
     reuse ROLLED_BACK flow; freeze = no further accept/ingest for the
     affected note until rollback completes; implement as a guard flag
     in the rollback record + refusal in accept paths)
  5. byte restoration: ObsidianService.rollback (note bytes) + semantic
     rollback of claims derived from the note (append-only flags)
  6. projection rebuild: rebuild projections from ledger (existing
     rebuild machinery) — deterministic
  7. semantic reconciliation: re-derive contradictions/support for the
     mission, compare claim set to snapshot's, record
     rollback.completed {before_hashes, after_hashes, restored_claims,
     reconciled: true}
- Never replaces the live DB while the daemon is active: the DB leg is
  semantic (flags + rebuild), file leg is byte restore. Guard asserted
  in code (refuse restore-by-copy paths on live DB).
- Events: rollback.triggered, rollback.completed (criterion 11/12).

Tests (~10): full E2E on temp vault + temp DB: snapshot → mutation →
degrade detected → freeze enforced (accept refused) → bytes restored →
projections rebuilt → reconcile consistent → no duplicate mutation on
re-run (idempotency); restart mid-flow (crash after freeze, before
restore) → re-run completes exactly once; live-DB replace guard.

## V2 operational gate — evidence assembly (after slices A–G)

1. Documented live initialize() v10 → v15 (before/after schema record;
   G3 discipline).
2. Run VaultIndexer on C:\Users\b\Blendi\JARVIS\V1 (bounded governed
   subset; whole-vault read-only pass optional) → real vault_files rows
   + vault.file_indexed events.
3. Run LiveIngestion cycles (vault + handoffs + repo + system events)
   → meaningful live knowledge_claims (criterion 1+2).
4. Retrieval probes on live data (bounded labeled set) → envelope with
   provenance/stale/contradictions/support + FP/FN/leakage metrics
   (criteria 3,4,5).
5. Research queue on live claims → real candidates (criterion 7).
6. One REAL bounded worker dispatch (Hermes adapter oneshot, staged
   home, tiny mission) with deliver_worker_context manifest →
   worker_manifests row + event (criterion 8).
7. Governed consolidation on a scratch governed note (criterion 9) +
   concurrent-edit conflict probe (criterion 10) + full G rollback demo
   (criterion 11) + restart no-duplicate probe (criterion 12).
8. STOP/veto-outside-content probe on live note (criterion 13).
9. Resource metrics from cycle reports (criterion 14).
10. Full suite EXIT=0 (310 + new) (criterion 15).
11. FREEZE → independent adversarial verifier reproduces the claims
    (criterion 16) → ledger close
    (verification.passed:verdict-v2-operational + evidence.created) →
    PROJECT_STATE v19 V2 ELIGIBLE.

## Risk notes

- Live initialize() is the riskiest step: apply ONLY at gate evidence,
  before/after recorded, daemon health checked after (it reads the same
  schema — v11-capable code already; v12–15 additive).
- Obsidian governed area currently has "V1 1" stray dir (duplication
  artifact) — never touched by any slice (subset = JARVIS/V1 only).
- Note text extraction edge cases (frontmatter, code fences) pinned by
  tests.
