# V8 Hybrid Memory — Test Traceability Matrix

Every relevant laboratory test / audit finding maps to a production invariant,
a production test, and a result. Ported INTENT, not code.

## Migration

| Lab test / audit finding | Production invariant | Production test | Result |
|---|---|---|---|
| lab migration tests; FINAL_AUDIT/08 schema delta | v33 additive: 6 tier_c tables, no Tier A/B change | `test_hybrid_memory_migration.py` (forward, idempotent ×2, partial-apply, rollback, additive-only, interruption, locked DB) | PASS |
| R2 mission §15 | migration rehearsed on empty/populated/live copies | `scripts/v8_migration_rehearsal.py` (9 scenarios) | PASS |

## Lifecycle / security (audit repairs H1, H2, C9-2, C9-4, L1, L2)

| Lab test / audit finding | Production invariant | Production test | Result |
|---|---|---|---|
| H1 lifecycle validation bypass | raw transitions to ACCEPTED/CORE/EXTERNAL/PROCEDURAL require a stamp | `test_raw_accept_requires_validation_stamp`, `test_poison_blocked_at_gate_with_audit_event` | PASS |
| H2/C9-2 secrets in git | full-projection commit gate (memories + procedures.body + blocks.value) | `test_secret_commit_gate_full_projection` | PASS |
| lab poison quarantine | quarantine → ARCHIVED, excluded from recall, revision recorded | `test_quarantine_archives_and_excludes` | PASS |
| lab revision history | every transition writes a revision (event-derived) | `test_revision_history_preserved` | PASS |
| L1 manifest column injection | manifest keys are data, never SQL (whitelist) | `test_manifest_column_whitelist` | PASS |
| L2 malformed expiry | recall degrades, never crashes | `test_malformed_expiry_degrades_not_crashes` | PASS |
| H3 git/DB divergence | DivergenceError on drift; force explicit | `test_divergence_detected_on_revert` | PASS |
| C9-4 markerless revert | fail-closed unless force | `test_markerless_target_fails_closed` | PASS |
| lab rollback history | A→B→C→rollback preserved | `test_rollback_history_preserved` | PASS |
| lab rebuild from git | empty-DB rebuild works | `test_empty_db_rebuild_works` | PASS |
| audit "git never truth for Tier A" | git dir holds tier_c manifests only | `test_git_is_never_truth_for_tier_a` | PASS |

## Recall / blocks / procedures

| Lab test / audit finding | Production invariant | Production test | Result |
|---|---|---|---|
| lab recall metadata contract | relevance/confidence/freshness/evidence refs returned | `test_recall_returns_metadata_and_excludes_irrelevant` | PASS |
| lab stale flag | expired experience labeled STALE, returned, demoted | `test_stale_memory_flagged_but_returned` | PASS |
| lab dedup boundary (D3) | lexical dedup at the GATE (0.4 threshold); semantic NOT claimed | `test_duplicate_gate_blocks_promotion_and_preserves_state` | PASS |
| lab consolidation | legacy near-duplicates merge; provenance/history preserved | `test_consolidation_merges_legacy_duplicates_preserving_history` | PASS |
| lab blocks budget/read-only | char budgets + read_only enforced | `test_blocks_budget_and_read_only` | PASS |
| lab procedures | trigger-based retrieval + lifecycle states | `test_procedure_search_and_lifecycle` | PASS |

## Context compiler / verifier independence

| Lab test / audit finding | Production invariant | Production test | Result |
|---|---|---|---|
| audit question F (budget starvation) | governance/directives/evidence never starved; Tier C dropped first | `test_tier_c_dropped_before_tier_a_under_starvation` | PASS |
| audit question E / lab verifier isolation | verifier receives NO Tier C recall | `test_verifier_receives_no_tier_c_recall` | PASS |
| mission §11 | builder claims labeled `UNVERIFIED_BUILDER_CLAIM`, never evidence | `test_builder_claims_labeled_not_evidence` | PASS |
| audit question B (false memory) | Tier A verdict wins; Tier C advisory only | `test_false_memory_cannot_override_verdict` | PASS |
| STOP determinism | STOP_EVERYTHING surfaces in governance regardless of memory | `test_stop_blocks_autonomy_in_context`, `test_stop_remains_deterministic_with_poison_present` | PASS |
| priority order §7 | mandatory sections always present; drops recorded | `test_priority_order_and_governance_survival`, `test_context_budget_records_drops` | PASS |

## Mission §17 adversarial battery (production paths)

| Attack class | Probe | Result |
|---|---|---|
| Truth integrity (6 attack memories) | T1 | PASS |
| Lifecycle bypass | T2 | PASS |
| Builder/verifier contamination | T3 | PASS |
| Secret persistence | T4 | PASS |
| Prompt injection (8 variants) | T5 | PASS |
| Freshness | T6 | PASS |
| Malformed timestamps | T7 | PASS |
| Contradiction preservation (+antonym limitation) | T8 / T8b | PASS / INFO |
| Context starvation | T9 | PASS |
| Duplicates | T10 | PASS |
| Git/DB divergence | T11 | PASS |
| Markerless revert | T12 | PASS |
| Restart/recovery | T13 | PASS |

## Worker context integration

| Requirement | Production test | Result |
|---|---|---|
| classic manifest contract unchanged | `tests/foundry/test_worker_context.py` (existing, untouched) | PASS |
| hybrid manifest delivered + reconstructable | `scripts/v8_canary.py` (manifest_contract step) | PASS |
| hybrid record in the ledger, worker_manifests untouched | canary + code review | PASS |
