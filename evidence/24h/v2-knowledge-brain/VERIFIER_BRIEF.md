# VERIFIER BRIEF — WP-24H-V2KB (V2 Knowledge Brain typed records)

## Role
You are an INDEPENDENT ADVERSARIAL VERIFIER. Your job is to DISPROVE the
completion claim. You are not a second builder. Do not trust any prose in
this brief or in the evidence bundle: re-derive everything you can from
the repository, the ledger, and fresh command output.

## Model lock (NON-NEGOTIABLE)
This verification must run on OpenCode x-preview / 0x-alpha only
(model id x-preview-f-free where exposed). If your runtime cannot prove
that model identity, STOP and return verdict NOT_TESTED with reason
"model lock unverifiable". Never silently fall back to another model.

## Frozen review target
Repo/worktree: C:\temp\JARVIS-24H-V2-Knowledge-Brain
Branch: candidate/24h-v2-knowledge-brain
Pinned SLICE commit: e79a8a5961e90f67cda499db12b056d2cf759d31
(also recorded in evidence/24h/v2-knowledge-brain/RUNS.md
"FROZEN COMMIT"). Verify:
1. `git rev-parse e79a8a5...` resolves and `git merge-base --is-ancestor
   e79a8a5961e90f67cda499db12b056d2cf759d31 HEAD` succeeds;
2. `git diff --name-only e79a8a5961e90f67cda499db12b056d2cf759d31 HEAD`
   contains ONLY paths under evidence/24h/ (docs/evidence bookkeeping
   commits after the freeze are permitted; any source/test change after
   the freeze voids certification => FAIL with the actual SHAs).
Base for diffs: 7ac0b2373c8d4c74924fcea15c028c4f6e9cdc3c.

## Governing authority (read first)
1. evidence/24h/v2-knowledge-brain/WORK_PACKAGE.md (this package's contract)
2. docs/architecture/05_POST_V1_ROADMAP.md section 5 (V2 exit evidence)
3. AGENTS.md (engineering standard, precedence)
4. docs/CURRENT_STATE.md (current-truth doc; note the CANDIDATE NOTE)

## Claim under review
The worktree implements a gate-ready V2 typed-records vertical slice:
(a) six typed record kinds persisted via knowledge.claim_recorded;
(b) immutable evidence links (knowledge.evidence_linked) flipping
UNSUPPORTED -> SUPPORTED with no delete/rewrite event type existing;
(c) typed entities goal/project/person/organization/system with clean
validation; (d) bounded deterministic retrieval by kind/confidence band/
time window/source/entity; (e) schema v34->v35 additive migration with
idempotency, legacy compatibility, interrupted recovery, restore equality,
zero data loss; (f) mandatory battery green: conflicting claims visible,
evidence-or-unsupported invariant, concurrent owner/JARVIS edits preserve
both meanings, duplicate ingestion idempotent, interrupted writes recover,
restore reproduces bytes AND semantics, malicious note content produces no
directives/control changes; (g) full suites green at the pinned commit.

## Required independent probes (run them yourself; do not trust logs)
1. `git -C <worktree> rev-parse HEAD` equals the pinned FROZEN COMMIT.
2. `git diff --stat 7ac0b23` — inventory stays inside WORK_PACKAGE §6
   allowed paths. Any file outside the lane => FAIL (scope violation).
3. Run focused suites fresh:
   PYTHONPATH=src python -m pytest tests/foundry/test_knowledge_records.py
   tests/foundry/test_knowledge_v35_durability.py -q   (expect 33 passed)
4. Run the migration-compat gate:
   PYTHONPATH=src python -m pytest tests/foundry/test_migrations.py
   tests/foundry/test_crash_recovery.py tests/foundry/
   test_hybrid_memory_migration.py tests/foundry/test_doc_staleness_check.py -q
5. Spot-prove negative behavior by writing and running ONE throwaway probe
   script (delete it afterward):
   - registering entity_type "galaxy" raises ValueError (not IntegrityError);
   - evidence_linked to a missing claim_id raises ValueError and the
     transaction leaves NO partial row;
   - re-applying an identical evidence link dedupes (no second row).
6. Ledger vocabulary check: in a scratch DB built via FoundryDatabase +
   the slice events, assert NO event type outside knowledge.* was produced
   by the knowledge path, and directives table stays empty after ingesting
   directive-shaped note text through knowledge_ingestion.ingest_documents.
7. Read MIGRATIONS[35]: confirm additive-only shape (no DROP of data
   without rebuild copy; legacy rows preserved via INSERT..SELECT), the
   SCHEMA_VERSION constant bumped in the SAME file, and no semicolons
   inside `--` comments (statement splitter hazard).
8. Secret scan: run scripts/v2kb_final_gate.py; expect verdict PASS.

## Evidence available (verify hashes/content yourself)
- evidence/24h/v2-knowledge-brain/WORK_PACKAGE.md (contract)
- evidence/24h/v2-knowledge-brain/RUNS.md (command log + exits)
- evidence/24h/v2-knowledge-brain/diff_review.json (secret scan report)

## Verdict rules
- Produce EXACTLY one verdict: PASS | FAIL | BLOCKED | NOT_TESTED.
- Any scope violation, wrong failure reason, fabricated evidence,
  weakened test, or unproven mandatory battery item => FAIL with the
  exact reproduction.
- Only a genuine unavailable dependency justifies BLOCKED.
- If you could not complete verification, return NOT_TESTED (no trust).
- Write your report EARLY and atomically to:
  evidence/24h/v2-knowledge-brain/VERIFIER.json
  Schema:
  {
    "verdict": "PASS|FAIL|BLOCKED|NOT_TESTED",
    "reviewed_commit": "<sha>",
    "model": "<your model id>",
    "probes_run": [{"name": "...", "result": "PASS|FAIL", "detail": "..."}],
    "findings": [{"severity": "HIGH|MEDIUM|LOW", "detail": "..."}],
    "notes": "..."
  }
  Then ALSO print the same JSON as the last line of your final message.
