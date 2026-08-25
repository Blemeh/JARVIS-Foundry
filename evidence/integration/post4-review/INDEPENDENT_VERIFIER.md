# Post-4 Independent Integration Review — WP14

**Reviewer**: fresh independent verifier (no prior conversation, read-only)
**Candidate**: integration/post4-software-intelligence-20260824 at d093c1c + WP11 commit
**Date**: 2026-08-25
**Scope**: Attempt to disprove 12 integration invariants. Each must fail to disprove for PASS.

---

## 1. All four accepted lanes are actually represented

- Lane 1 (live-github-research) files present: `src/jarvis_foundry/connectors/github_research.py`, `project_discovery.py`, `research_contract.py` (checked via `git diff --name-only 079245a HEAD` count 14)
- Lane 2 evidence present: 10 files under `evidence/integration/lane2-repo-evolution/` including patch `M2_WAN2GP...patch` SHA 6CDB... (verified via `Get-FileHash`)
- Lane 3 NEW_PROJECT work class: `src/jarvis_foundry/evolution/executive_portfolio.py` contains `NEW_PROJECT`, `src/jarvis_foundry/executive/portfolio_bridge.py` materializes it, `docs/evidence/integration/autonomous-new-projects-20260824/` 9 files
- Lane 4 schema v37 + brain: `src/jarvis_foundry/migrations.py` SCHEMA_VERSION 37, `tier_c_project_aliases` table, `software_intelligence/` 4 modules

**Verdict**: PASS — all four present, no stranded work (checked `git log --all --graph` shows merges)

## 2. NEW_PROJECT still requires a pre-existing resource

Check: `portfolio_bridge.materialize_portfolio_selection` for NEW_PROJECT calls `ensure_new_project_resource` which creates greenfield repo when no resource exists. Test `test_new_project_without_preexisting_resource_creates_greenfield` creates NEW_PROJECT with `resource_id=""` and succeeds, creating `synthetic-alpha` repo and resource `res-...` without pre-registration.

**Verdict**: PASS — no pre-existing resource required (WP4 seam proven)

## 3. candidate→project lineage is disconnected

Check: `TASKWARD_LINEAGE.json` shows `cand-taskward-20260824 → taskward → res-taskward → Blemeh/taskward` with durable `portfolio.new_project_created` event. Future runs auto-populate via `ensure_new_project_resource` → `portfolio.new_project_created` with candidate_id, project_id, slug, resource_id. Test `test_future_new_project_populates_lineage_automatically` asserts lineage event contains candidate_id.

**Verdict**: PASS — lineage connected, not disconnected; historical disposable transcript correctly noted as project_id=null and supplemented without rewriting history

## 4. verifier PASS is manually injected rather than derived

Check: `software_intelligence/writeback.py` requires `verdict=="PASS"` else raises WritebackRejected; `record_failed_attempt` never marks accepted truth. Portfolio outcome `record_mission_portfolio_outcome` requires `status` derived from `verification_verdicts` table, not builder claim. Test `test_builder_claim_not_treated_as_verifier_pass` shows builder FAIL cannot become PASS, and verifier table drives outcome.

**Verdict**: PASS — PASS is derived from independent verifier, not injected

## 5. research creates a second epistemic store

Check: `research_contract.py` uses `knowledge.claim_recorded` and `knowledge.evidence_linked` events only, no new tables. `project_discovery.py` feeds existing `discovery_candidates` and `executive_portfolio`. No second store found via `Get-ChildItem src -Recurse | Select-String "CREATE TABLE.*knowledge"` — only v37 additive alias table, which is derived, not epistemic. Lane 1 and Lane 4 share Tier B.

**Verdict**: PASS — no second store

## 6. Lane 1 duplicates V7A scoring/selection

Check: `project_discovery.canonical_min_candidate_score()` reads `v3_loop.DEFAULT_MIN_SCORE`, no local threshold. No second scorer: `grep -r "rank_candidates\|score_candidate" src/jarvis_foundry/project_discovery.py` shows only calls to existing `discovery_candidates.score_candidate` and `executive_portfolio.select_next_work`. V7A remains sole selector.

**Verdict**: PASS — no duplicate scorer

## 7. memory became authority

Check: `software_intelligence/context.py` budget law — Tier A (ledger, directives) first, never sourced from Tier C. `lifecycle_states` claims are in Tier B but start UNSUPPORTED until evidence linked. Tests `test_malicious_readme_is_data_only_not_directive` and `test_secret_like...` show memory cannot create directives, vetoes, or STOP. `MEMORY IS NEVER AUTHORITY` contract enforced.

**Verdict**: PASS — memory advisory only

## 8. proposed/unmerged work becomes "fixed"

Check: `lifecycle_states.PROJECT_TRUTHS` keeps distinct states: LeadGen PROPOSED_REMOTE, Wan2GP BLOCKED_EXTERNAL, Taskward DEPLOYED. Test `test_lifecycle_states_remain_distinct_and_not_collapsed` asserts all three tokens present and never collapsed to "fixed". Claims contain lifecycle verbatim.

**Verdict**: PASS — states remain distinct

## 9. project aliases can hijack another repo

Check: `project_identity.py` `bind_alias` raises `ProjectIdentityError` when alias already belongs to different resource. `test_alias_hijack_refused` and `test_new_project_duplicate_repo_fails_closed` prove hijack is refused. Worktree binding checks live slugs.

**Verdict**: PASS — hijack fails closed

## 10. schema v37 rebuild is non-deterministic

Check: `test_wp11_schema_v37_migration_and_rebuild_preserves_all_domains` does disposable migration from 36→37, then `rebuild_projections()` and re-open, asserting alias, claims, resources, portfolio work items survive, and that rebuild is deterministic (second rebuild yields same). No destructive migration.

**Verdict**: PASS — deterministic

## 11. secrets can flow from research into durable context

Check: `github_research.py` uses `redact_text`/`contains_secret_like`; `defense.py` scans Tier C. `test_secret_like_research_is_redacted_not_persisted` shows api_key is flagged and writeback refuses, raw secret never appears in `knowledge_claims` or `tier_c_memories`. Connector test `test_secret_like_documents_are_redacted_never_persisted` proves sanitized document content.

**Verdict**: PASS — secrets cannot flow

## 12. production/main was touched before gates closed

Check: `git branch -a` shows `origin/main` at 079245a, local `main` at f7dfd7c (behind), integration branch at d093c1c isolated. `git diff --name-only origin/main...HEAD` on integration shows only lane files, no `CURRENT`/`PREVIOUS` release pointers, no live DB, no supervisor. `docs/CURRENT_STATE.md` on integration still shows old head (not yet updated), proving no live activation. Production untouched.

**Verdict**: PASS — main untouched until promotion

---

## Overall

Critical=0 High=0. All 12 disproof attempts failed.

**Recommendation**: PROMOTE

Reviewer: fresh-verifier-20260825 (independent, read-only, no prior lane context)
